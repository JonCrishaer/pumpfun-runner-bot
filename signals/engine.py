"""
Main signal engine for Pump.fun trading system.

This module provides the SignalEngine class that combines all signal
factors (progress, volume, dev activity, alpha wallets, holder growth)
to generate actionable trading signals.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
import pandas as pd
import numpy as np

from .bonding_curve import analyze_progress, get_zone, calculate_progress
from .volume_analyzer import analyze_volume, Trade, VolumeAnalysis
from .scoring import calculate_signal_score, ScoreResult, quick_score


# =============================================================================
# ENUMS AND CONSTANTS
# =============================================================================

class SignalAction(Enum):
    """Possible trading actions."""
    NO_ACTION = "no_action"
    ENTER = "enter"
    INCREASE = "increase"
    HOLD = "hold"
    DECREASE = "decrease"
    EXIT = "exit"


class SignalConfidence(Enum):
    """Confidence levels for signals."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class TokenData:
    """Input data for token evaluation."""
    address: str
    symbol: str
    name: str
    bonding_curve_balance: int
    trades: List[Trade]
    dev_sell_ratio: float
    dev_is_active: bool
    alpha_wallets: int
    holder_count: int
    holder_growth_rate: float
    metadata: Optional[Dict] = None


@dataclass
class Signal:
    """Output signal from engine evaluation."""
    token_address: str
    timestamp: datetime
    action: SignalAction
    confidence: SignalConfidence
    score: float
    position_size: float  # % of portfolio
    urgency: int  # 0-3
    reasons: List[str]
    component_scores: Dict[str, float]
    raw_data: Dict[str, Any]


@dataclass
class SignalThresholds:
    """Configurable thresholds for signal generation."""
    min_progress: float = 75.0
    min_volume_acceleration: float = 1.5
    max_dev_sell: float = 0.05
    min_alpha_wallets: int = 2
    min_holder_growth: float = 0.03
    min_score_for_entry: float = 40.0
    min_score_for_increase: float = 60.0


# =============================================================================
# SIGNAL ENGINE CLASS
# =============================================================================

class SignalEngine:
    """
    Main signal engine for Pump.fun trading.
    
    Combines multiple factors to generate trading signals:
    - Bonding curve progress
    - Volume patterns and acceleration
    - Developer activity
    - Alpha wallet presence
    - Holder growth rate
    
    Example:
        >>> engine = SignalEngine()
        >>> token_data = TokenData(...)
        >>> signal = engine.evaluate_token(token_data)
        >>> print(f"Action: {signal.action.value}, Score: {signal.score}")
    """
    
    def __init__(self, thresholds: Optional[SignalThresholds] = None):
        """
        Initialize the signal engine.
        
        Args:
            thresholds: Optional custom thresholds
        """
        self.thresholds = thresholds or SignalThresholds()
        self._history: List[Signal] = []
        self._callbacks: List[Callable[[Signal], None]] = []
    
    def evaluate_token(self, token_data: TokenData) -> Signal:
        """
        Evaluate a token and generate a trading signal.
        
        Args:
            token_data: Token data for evaluation
            
        Returns:
            Signal with action recommendation
        """
        # Analyze bonding curve progress
        progress_analysis = analyze_progress(token_data.bonding_curve_balance)
        
        # Analyze volume patterns
        volume_analysis = analyze_volume(token_data.trades)
        
        # Calculate component scores
        score_result = calculate_signal_score(
            progress=progress_analysis.progress,
            volume_data={
                'volume_acceleration': volume_analysis.volume_acceleration,
                'buy_sell_ratio': volume_analysis.buy_sell_ratio,
                'is_healthy': volume_analysis.is_healthy
            },
            dev_data={
                'sell_ratio': token_data.dev_sell_ratio,
                'is_active': token_data.dev_is_active
            },
            alpha_data={
                'alpha_wallets': token_data.alpha_wallets
            },
            holder_data={
                'growth_rate': token_data.holder_growth_rate,
                'total_holders': token_data.holder_count
            }
        )
        
        # Determine action and confidence
        action, confidence, reasons = self._determine_action(
            token_data, progress_analysis, volume_analysis, score_result
        )
        
        # Create signal
        signal = Signal(
            token_address=token_data.address,
            timestamp=datetime.now(),
            action=action,
            confidence=confidence,
            score=score_result.total_score,
            position_size=score_result.recommended_position,
            urgency=progress_analysis.urgency_level,
            reasons=reasons,
            component_scores=score_result.component_scores,
            raw_data={
                'progress_analysis': progress_analysis,
                'volume_analysis': volume_analysis,
                'score_result': score_result
            }
        )
        
        # Store in history
        self._history.append(signal)
        
        # Trigger callbacks
        for callback in self._callbacks:
            callback(signal)
        
        return signal
    
    def _determine_action(
        self,
        token_data: TokenData,
        progress_analysis: Any,
        volume_analysis: VolumeAnalysis,
        score_result: ScoreResult
    ) -> tuple:
        """
        Determine the trading action based on all factors.
        
        Returns:
            Tuple of (action, confidence, reasons)
        """
        reasons = []
        score = score_result.total_score
        progress = progress_analysis.progress
        
        # Check minimum requirements
        if progress < self.thresholds.min_progress:
            reasons.append(f"Progress {progress:.1f}% below threshold {self.thresholds.min_progress}%")
            return SignalAction.NO_ACTION, SignalConfidence.LOW, reasons
        
        if not volume_analysis.is_healthy:
            reasons.append("Volume patterns unhealthy")
        
        if token_data.dev_sell_ratio > self.thresholds.max_dev_sell:
            reasons.append(f"Dev sell ratio {token_data.dev_sell_ratio:.1%} exceeds limit")
        
        # Determine action based on score
        if score >= 80:
            action = SignalAction.ENTER
            confidence = SignalConfidence.VERY_HIGH
            reasons.append(f"Excellent score: {score:.0f}/100")
        elif score >= 60:
            action = SignalAction.ENTER
            confidence = SignalConfidence.HIGH
            reasons.append(f"Strong score: {score:.0f}/100")
        elif score >= 40:
            action = SignalAction.ENTER
            confidence = SignalConfidence.MEDIUM
            reasons.append(f"Moderate score: {score:.0f}/100")
        elif score >= 20:
            action = SignalAction.NO_ACTION
            confidence = SignalConfidence.LOW
            reasons.append(f"Weak score: {score:.0f}/100")
        else:
            action = SignalAction.NO_ACTION
            confidence = SignalConfidence.LOW
            reasons.append(f"Poor score: {score:.0f}/100")
        
        # Adjust for urgency
        if progress_analysis.urgency_level >= 3 and score >= 50:
            action = SignalAction.ENTER
            reasons.append("Critical urgency - approaching Raydium")
        
        return action, confidence, reasons
    
    def evaluate_multiple(self, tokens: List[TokenData]) -> List[Signal]:
        """
        Evaluate multiple tokens and return sorted signals.
        
        Args:
            tokens: List of TokenData objects
            
        Returns:
            List of signals sorted by score (highest first)
        """
        signals = [self.evaluate_token(token) for token in tokens]
        return sorted(signals, key=lambda s: s.score, reverse=True)
    
    def get_top_signals(
        self,
        tokens: List[TokenData],
        min_score: float = 40.0,
        limit: int = 10
    ) -> List[Signal]:
        """
        Get top signals above minimum score.
        
        Args:
            tokens: List of TokenData objects
            min_score: Minimum score threshold
            limit: Maximum number of results
            
        Returns:
            Filtered and sorted signals
        """
        signals = self.evaluate_multiple(tokens)
        filtered = [s for s in signals if s.score >= min_score]
        return filtered[:limit]
    
    def add_callback(self, callback: Callable[[Signal], None]) -> None:
        """
        Add a callback to be triggered on each signal.
        
        Args:
            callback: Function that takes a Signal argument
        """
        self._callbacks.append(callback)
    
    def get_history(self) -> List[Signal]:
        """Get signal history."""
        return self._history.copy()
    
    def clear_history(self) -> None:
        """Clear signal history."""
        self._history.clear()
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get engine statistics.
        
        Returns:
            Dictionary with statistics
        """
        if not self._history:
            return {
                'total_signals': 0,
                'avg_score': 0.0,
                'action_counts': {},
                'confidence_counts': {}
            }
        
        scores = [s.score for s in self._history]
        actions = [s.action.value for s in self._history]
        confidences = [s.confidence.value for s in self._history]
        
        return {
            'total_signals': len(self._history),
            'avg_score': round(np.mean(scores), 2),
            'max_score': max(scores),
            'min_score': min(scores),
            'action_counts': {a: actions.count(a) for a in set(actions)},
            'confidence_counts': {c: confidences.count(c) for c in set(confidences)}
        }


# =============================================================================
# QUICK EVALUATION FUNCTIONS
# =============================================================================

def quick_evaluate(
    balance: int,
    volume_accel: float,
    dev_sell_ratio: float,
    alpha_count: int,
    holder_growth: float
) -> Dict[str, Any]:
    """
    Quick token evaluation without full data structures.
    
    Args:
        balance: Bonding curve balance in lamports
        volume_accel: Volume acceleration ratio
        dev_sell_ratio: Developer sell ratio (0-1)
        alpha_count: Number of alpha wallets
        holder_growth: Holder growth rate per hour
        
    Returns:
        Dictionary with evaluation results
    """
    progress = calculate_progress(balance)
    score = quick_score(
        progress=progress,
        volume_accel=volume_accel,
        dev_sell_ratio=dev_sell_ratio,
        alpha_count=alpha_count,
        holder_growth=holder_growth
    )
    
    zone = get_zone(progress)
    
    # Determine action
    if score >= 80:
        action = "ENTER_MAX"
        position = 4.5
    elif score >= 60:
        action = "ENTER_STRONG"
        position = 3.0
    elif score >= 40:
        action = "ENTER_MODERATE"
        position = 1.5
    else:
        action = "NO_ACTION"
        position = 0.0
    
    return {
        'progress': round(progress, 2),
        'zone': zone,
        'score': round(score, 2),
        'action': action,
        'position_size': position,
        'is_entry_zone': progress >= 75
    }


def batch_evaluate(tokens_data: List[Dict]) -> List[Dict]:
    """
    Evaluate multiple tokens from dictionary data.
    
    Args:
        tokens_data: List of token dictionaries with required fields
        
    Returns:
        List of evaluation results
    """
    results = []
    for token in tokens_data:
        result = quick_evaluate(
            balance=token.get('balance', 0),
            volume_accel=token.get('volume_accel', 1.0),
            dev_sell_ratio=token.get('dev_sell_ratio', 0.0),
            alpha_count=token.get('alpha_count', 0),
            holder_growth=token.get('holder_growth', 0.0)
        )
        result['token'] = token.get('symbol', 'UNKNOWN')
        result['address'] = token.get('address', '')
        results.append(result)
    
    # Sort by score
    results.sort(key=lambda x: x['score'], reverse=True)
    return results


# =============================================================================
# SIGNAL FORMATTING
# =============================================================================

def format_signal(signal: Signal) -> str:
    """
    Format a signal for display.
    
    Args:
        signal: Signal to format
        
    Returns:
        Formatted string
    """
    lines = [
        f"📊 Signal for {signal.token_address[:8]}...",
        f"   Action: {signal.action.value.upper()}",
        f"   Confidence: {signal.confidence.value}",
        f"   Score: {signal.score:.1f}/100",
        f"   Position: {signal.position_size}%",
        f"   Urgency: {'🔴' * (signal.urgency + 1)}",
        f"   Reasons: {', '.join(signal.reasons[:3])}"
    ]
    return '\n'.join(lines)


def signal_to_dict(signal: Signal) -> Dict[str, Any]:
    """
    Convert signal to dictionary.
    
    Args:
        signal: Signal to convert
        
    Returns:
        Dictionary representation
    """
    return {
        'token_address': signal.token_address,
        'timestamp': signal.timestamp.isoformat(),
        'action': signal.action.value,
        'confidence': signal.confidence.value,
        'score': signal.score,
        'position_size': signal.position_size,
        'urgency': signal.urgency,
        'reasons': signal.reasons,
        'component_scores': signal.component_scores
    }
