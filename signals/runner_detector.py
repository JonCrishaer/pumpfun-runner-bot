"""
Runner Detector - Identify pump.fun tokens likely to be "runners"

A "runner" is a token that has momentum characteristics showing it may pump significantly
after graduation from the bonding curve. This detector combines:

1. Bonding curve progress (85-98%)
2. Volume surge (>75th percentile)
3. Holder count (>50)
4. Momentum signals (velocity, acceleration)
5. Developer activity patterns

Usage:
    >>> detector = RunnerDetector()
    >>> await detector.start()
    >>> # Monitors bonding curve updates and emits RunnerSignalEvent
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from statistics import median, stdev
from enum import Enum

from core.events import (
    BondingCurveUpdateEvent,
    SignalEvent,
    event_bus,
    EventPriority,
)
from core.config import get_settings

logger = logging.getLogger(__name__)


class SignalStrength(str, Enum):
    """Signal strength classification."""
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


@dataclass
class TokenMetrics:
    """Metrics tracking for a candidate token."""
    token_address: str
    first_seen: datetime
    progress_samples: List[float] = field(default_factory=list)
    holder_samples: List[int] = field(default_factory=list)
    volume_samples: List[float] = field(default_factory=list)
    timestamps: List[datetime] = field(default_factory=list)
    
    def add_sample(self, progress: float, holders: int, volume: float, timestamp: datetime):
        """Add a metric sample."""
        self.progress_samples.append(progress)
        self.holder_samples.append(holders)
        self.volume_samples.append(volume)
        self.timestamps.append(timestamp)
        
        # Keep only last 100 samples to avoid memory bloat
        if len(self.progress_samples) > 100:
            self.progress_samples.pop(0)
            self.holder_samples.pop(0)
            self.volume_samples.pop(0)
            self.timestamps.pop(0)
    
    def get_momentum_velocity(self) -> float:
        """Calculate bonding curve progress velocity (% per minute)."""
        if len(self.progress_samples) < 2:
            return 0.0
        
        time_delta = (self.timestamps[-1] - self.timestamps[0]).total_seconds() / 60  # minutes
        if time_delta == 0:
            return 0.0
        
        progress_delta = self.progress_samples[-1] - self.progress_samples[0]
        return progress_delta / time_delta
    
    def get_momentum_acceleration(self) -> float:
        """Calculate bonding curve progress acceleration (change in velocity)."""
        if len(self.progress_samples) < 5:
            return 0.0
        
        # Split into two time windows
        mid = len(self.progress_samples) // 2
        
        window1_time = (self.timestamps[mid - 1] - self.timestamps[0]).total_seconds() / 60
        window1_delta = self.progress_samples[mid - 1] - self.progress_samples[0]
        vel1 = window1_delta / window1_time if window1_time > 0 else 0
        
        window2_time = (self.timestamps[-1] - self.timestamps[mid]).total_seconds() / 60
        window2_delta = self.progress_samples[-1] - self.progress_samples[mid]
        vel2 = window2_delta / window2_time if window2_time > 0 else 0
        
        return vel2 - vel1
    
    def get_holder_growth_rate(self) -> float:
        """Calculate holder growth rate (holders per minute)."""
        if len(self.holder_samples) < 2:
            return 0.0
        
        time_delta = (self.timestamps[-1] - self.timestamps[0]).total_seconds() / 60
        if time_delta == 0:
            return 0.0
        
        holder_delta = self.holder_samples[-1] - self.holder_samples[0]
        return holder_delta / time_delta


class RunnerDetector:
    """Detects tokens likely to be 'runners' based on multi-factor analysis."""
    
    # Configuration thresholds
    MIN_PROGRESS = 0.85  # 85% bonding curve
    MAX_PROGRESS = 0.98  # But not too close to graduation
    MIN_HOLDERS = 50
    MIN_VOLUME_ACCELERATION = 2.0  # 2x volume surge
    MIN_MOMENTUM_VELOCITY = 0.01  # 1% progress per minute
    
    # Scoring weights
    WEIGHT_PROGRESS = 0.20
    WEIGHT_HOLDERS = 0.15
    WEIGHT_VOLUME = 0.25
    WEIGHT_MOMENTUM = 0.25
    WEIGHT_HOLDER_GROWTH = 0.15
    
    def __init__(self):
        """Initialize runner detector."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.tokens = {}  # Track tokens: {address: TokenMetrics}
        self.volume_percentiles = {}  # Recent volume stats for percentile calc
        self.running = False
        self.settings = get_settings()
        
        # Subscribe to bonding curve updates
        event_bus.on(BondingCurveUpdateEvent)(self._handle_curve_update)
    
    async def start(self):
        """Start the detector."""
        self.running = True
        self.logger.info("✅ Runner Detector started")
        
        # Cleanup old tokens periodically
        asyncio.create_task(self._cleanup_old_tokens())
    
    async def stop(self):
        """Stop the detector."""
        self.running = False
    
    async def _cleanup_old_tokens(self):
        """Remove tokens that haven't been updated in 30 minutes."""
        while self.running:
            try:
                now = datetime.utcnow()
                expired = []
                
                for address, metrics in self.tokens.items():
                    if not metrics.timestamps:
                        continue
                    
                    age = (now - metrics.timestamps[-1]).total_seconds() / 60
                    if age > 30:  # 30 minute inactivity
                        expired.append(address)
                
                for address in expired:
                    del self.tokens[address]
                    self.logger.debug(f"Removed expired token: {address}")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Error in cleanup: {e}")
    
    async def _handle_curve_update(self, event: BondingCurveUpdateEvent):
        """Handle bonding curve update event."""
        try:
            token_addr = event.token_address
            
            # Track metrics
            if token_addr not in self.tokens:
                self.tokens[token_addr] = TokenMetrics(
                    token_address=token_addr,
                    first_seen=datetime.utcnow(),
                )
            
            metrics = self.tokens[token_addr]
            metrics.add_sample(
                progress=event.progress_pct,
                holders=event.holders,
                volume=getattr(event, 'volume_24h', 0.0),
                timestamp=event.timestamp,
            )
            
            # Check if this is a runner
            is_runner, score, analysis = self._score_token(token_addr, metrics)
            
            if is_runner:
                signal = Signal(
                    token_address=token_addr,
                    signal_type="runner",
                    strength=self._strength_from_score(score),
                    confidence=score,
                    metadata=analysis,
                )
                
                signal_event = SignalEvent(
                    signal=signal,
                    source="runner_detector",
                )
                
                await event_bus.emit(signal_event, priority=EventPriority.HIGH)
                self.logger.info(
                    f"🏃 Runner detected: {token_addr[:8]}... "
                    f"(score={score:.2f}, progress={event.progress_pct:.2%})"
                )
        
        except Exception as e:
            self.logger.error(f"Error handling curve update: {e}")
    
    def _score_token(
        self, token_addr: str, metrics: TokenMetrics
    ) -> Tuple[bool, float, Dict]:
        """
        Score a token for runner characteristics.
        
        Returns:
            (is_runner, score_0_to_1, analysis_dict)
        """
        analysis = {}
        
        if not metrics.progress_samples:
            return False, 0.0, analysis
        
        current_progress = metrics.progress_samples[-1]
        current_holders = metrics.holder_samples[-1] if metrics.holder_samples else 0
        
        # 1. Progress score
        progress_score = self._score_progress(current_progress)
        analysis["progress_score"] = progress_score
        analysis["current_progress"] = current_progress
        
        # 2. Holders score
        holders_score = self._score_holders(current_holders)
        analysis["holders_score"] = holders_score
        analysis["current_holders"] = current_holders
        
        # 3. Volume score
        volume_score = self._score_volume(metrics.volume_samples)
        analysis["volume_score"] = volume_score
        
        # 4. Momentum score
        momentum_score = self._score_momentum(metrics)
        analysis["momentum_score"] = momentum_score
        analysis["momentum_velocity"] = metrics.get_momentum_velocity()
        analysis["momentum_acceleration"] = metrics.get_momentum_acceleration()
        
        # 5. Holder growth score
        holder_growth_score = self._score_holder_growth(metrics)
        analysis["holder_growth_score"] = holder_growth_score
        analysis["holder_growth_rate"] = metrics.get_holder_growth_rate()
        
        # Weighted composite score
        total_score = (
            progress_score * self.WEIGHT_PROGRESS +
            holders_score * self.WEIGHT_HOLDERS +
            volume_score * self.WEIGHT_VOLUME +
            momentum_score * self.WEIGHT_MOMENTUM +
            holder_growth_score * self.WEIGHT_HOLDER_GROWTH
        )
        
        # Runner threshold: all major metrics must pass
        is_runner = (
            progress_score > 0.5 and
            holders_score > 0.5 and
            volume_score > 0.5 and
            momentum_score > 0.5 and
            total_score > 0.70  # 70% overall threshold
        )
        
        analysis["total_score"] = total_score
        analysis["is_runner"] = is_runner
        
        return is_runner, total_score, analysis
    
    def _score_progress(self, progress: float) -> float:
        """Score progress (higher in sweet spot 85-98%)."""
        if progress < self.MIN_PROGRESS or progress > self.MAX_PROGRESS:
            return 0.0
        
        # Peak score at 90%
        optimal = 0.90
        distance = abs(progress - optimal)
        return max(0.0, 1.0 - (distance / 0.15))
    
    def _score_holders(self, holders: int) -> float:
        """Score holder count (sigmoid, peaks at 100+)."""
        if holders < self.MIN_HOLDERS:
            return 0.0
        
        # Score increases with holders, peaks at 500+
        return min(1.0, (holders - self.MIN_HOLDERS) / 450)
    
    def _score_volume(self, volumes: List[float]) -> float:
        """Score volume surge."""
        if len(volumes) < 2:
            return 0.0
        
        recent_avg = sum(volumes[-5:]) / min(5, len(volumes))
        older_avg = sum(volumes[-20:-5]) / min(15, len(volumes) - 5) if len(volumes) > 5 else recent_avg
        
        if older_avg == 0:
            return 0.5 if recent_avg > 0 else 0.0
        
        surge = recent_avg / older_avg
        
        # Score: 0 at 1x, peaks at 3x, caps at 1.0
        if surge < 1.0:
            return 0.0
        return min(1.0, (surge - 1.0) / 2.0)
    
    def _score_momentum(self, metrics: TokenMetrics) -> float:
        """Score momentum velocity."""
        velocity = metrics.get_momentum_velocity()
        
        if velocity < self.MIN_MOMENTUM_VELOCITY:
            return 0.0
        
        # Score increases with velocity, peaks at 3% per minute
        return min(1.0, velocity / 0.03)
    
    def _score_holder_growth(self, metrics: TokenMetrics) -> float:
        """Score holder growth rate."""
        growth_rate = metrics.get_holder_growth_rate()
        
        if growth_rate <= 0:
            return 0.0
        
        # Score peaks at 5+ new holders per minute
        return min(1.0, growth_rate / 5.0)
    
    @staticmethod
    def _strength_from_score(score: float) -> SignalStrength:
        """Convert score to signal strength."""
        if score >= 0.85:
            return SignalStrength.VERY_STRONG
        elif score >= 0.75:
            return SignalStrength.STRONG
        elif score >= 0.65:
            return SignalStrength.MODERATE
        else:
            return SignalStrength.WEAK
