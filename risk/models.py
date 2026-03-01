"""
Risk Models and Constraints for Pump.fun Trading System

This module defines data structures, enums, and constraints for risk management.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime
import numpy as np


class RiskLevel(Enum):
    """Risk classification levels"""
    CRITICAL = "critical"      # Immediate action required
    HIGH = "high"              # Significant concern
    MEDIUM = "medium"          # Monitor closely
    LOW = "low"                # Normal operation
    MINIMAL = "minimal"        # Well within limits


class PositionStage(Enum):
    """Sequential entry stages for position scaling"""
    INITIAL = "initial"        # 25% at 75% progress
    SCALED = "scaled"          # 50% at 85% progress
    FULL = "full"              # 100% at 90% progress


class ExitTrigger(Enum):
    """Exit trigger types"""
    PRICE_TARGET = "price_target"
    TRAILING_STOP = "trailing_stop"
    HARD_STOP = "hard_stop"
    TIME_DECAY = "time_decay"
    MANUAL = "manual"
    CIRCUIT_BREAKER = "circuit_breaker"


@dataclass
class KellyParameters:
    """Parameters for Kelly Criterion calculation"""
    base_graduation_rate: float = 0.008  # 0.8% base rate
    estimated_edge: float = 0.15         # 15% with strong signals (conservative estimate)
    win_return_multiple: float = 10.0    # 10x average win (upper range for positive EV)
    loss_percentage: float = 0.90        # -90% on failure
    fractional_kelly: float = 0.35       # 0.25-0.5 range, conservative 0.35
    
    @property
    def win_probability(self) -> float:
        """Calculated win probability based on edge"""
        return min(self.estimated_edge, 0.5)  # Cap at 50%
    
    @property
    def loss_probability(self) -> float:
        """Loss probability"""
        return 1.0 - self.win_probability


@dataclass
class PositionConstraints:
    """Position sizing constraints"""
    max_single_position_pct: float = 0.05      # 5% max per token
    min_position_pct: float = 0.005            # 0.5% minimum
    max_pre_graduation_exposure: float = 0.30  # 30% total pre-graduation
    min_pre_graduation_exposure: float = 0.20  # 20% minimum deployment
    max_simultaneous_positions: int = 20       # Max 20 tokens
    min_simultaneous_positions: int = 10       # Min 10 for diversification
    
    # Sequential entry scaling percentages
    initial_entry_pct: float = 0.25            # 25% at first trigger
    scaled_entry_pct: float = 0.50             # 50% at second trigger
    full_entry_pct: float = 1.00               # 100% at final trigger
    
    # Progress thresholds for entry
    initial_progress_threshold: float = 0.75   # 75% progress
    scaled_progress_threshold: float = 0.85    # 85% progress
    full_progress_threshold: float = 0.90      # 90% progress


@dataclass
class ExitConstraints:
    """Exit strategy constraints"""
    # Hard stop loss
    hard_stop_pct: float = -0.50               # -50% from entry
    
    # Trailing stop
    trailing_stop_activation: float = 3.0      # Activate after 3x
    trailing_stop_distance: float = 0.30       # 30% trailing distance
    
    # Tiered profit-taking
    tier_1_target: float = 5.0                 # 5x first target
    tier_2_target: float = 10.0                # 10x second target
    tier_3_target: float = 20.0                # 20x third target
    tier_4_runner: bool = True                 # Hold 25% as runner
    
    # Time-based decay
    peak_momentum_hours: float = 4.0           # 0-4 hours aggressive
    sustained_hours: float = 24.0              # 4-24 hours for runners
    decay_hours: float = 72.0                  # 24-72 hours rapid decay
    
    # Realistic upper bounds
    max_realistic_multiple: float = 30.0       # 30x upper bound for 2025
    min_realistic_multiple: float = 10.0       # 10x lower bound


@dataclass
class DrawdownConstraints:
    """Drawdown and circuit breaker constraints"""
    # Daily limits
    max_daily_loss_pct: float = 0.05           # 5% daily loss limit
    max_daily_drawdown_pct: float = 0.10       # 10% daily drawdown
    
    # Position limits
    max_position_drawdown_pct: float = 0.50    # 50% per position
    
    # Portfolio limits
    max_portfolio_drawdown_pct: float = 0.20   # 20% portfolio DD
    critical_drawdown_pct: float = 0.30        # 30% critical - halt trading
    
    # Circuit breakers
    consecutive_losses_limit: int = 5          # 5 consecutive losses
    volatility_spike_threshold: float = 3.0    # 3x normal volatility
    
    # Cooldown periods (seconds)
    cooldown_after_loss_seconds: int = 300     # 5 min after loss
    cooldown_after_drawdown_seconds: int = 900 # 15 min after DD
    emergency_cooldown_seconds: int = 3600     # 1 hour emergency


@dataclass
class PositionRisk:
    """Risk metrics for a single position"""
    token_address: str
    entry_price: float
    current_price: float
    position_size_sol: float
    position_size_usd: float
    portfolio_pct: float
    entry_time: datetime
    stage: PositionStage = PositionStage.INITIAL
    
    # Risk metrics
    unrealized_pnl_pct: float = 0.0
    unrealized_pnl_sol: float = 0.0
    max_profit_seen: float = 0.0
    max_drawdown_seen: float = 0.0
    
    # Exit tracking
    exit_triggers_hit: List[ExitTrigger] = field(default_factory=list)
    partial_exits_taken: int = 0
    remaining_size_pct: float = 1.0
    
    @property
    def current_multiple(self) -> float:
        """Current price multiple from entry"""
        if self.entry_price <= 0:
            return 0.0
        return self.current_price / self.entry_price
    
    @property
    def is_in_profit(self) -> bool:
        """Check if position is profitable"""
        return self.unrealized_pnl_pct > 0
    
    @property
    def risk_level(self) -> RiskLevel:
        """Determine current risk level"""
        if self.unrealized_pnl_pct <= -0.50:
            return RiskLevel.CRITICAL
        elif self.unrealized_pnl_pct <= -0.30:
            return RiskLevel.HIGH
        elif self.unrealized_pnl_pct <= -0.15:
            return RiskLevel.MEDIUM
        elif self.unrealized_pnl_pct >= 3.0:
            return RiskLevel.MINIMAL
        else:
            return RiskLevel.LOW


@dataclass
class PortfolioRisk:
    """Portfolio-level risk metrics"""
    total_value_sol: float = 0.0
    total_value_usd: float = 0.0
    available_sol: float = 0.0
    
    # Exposure tracking
    pre_graduation_exposure_sol: float = 0.0
    pre_graduation_exposure_pct: float = 0.0
    post_graduation_exposure_sol: float = 0.0
    
    # Position counts
    total_positions: int = 0
    pre_graduation_positions: int = 0
    post_graduation_positions: int = 0
    
    # Performance metrics
    daily_pnl_pct: float = 0.0
    daily_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0
    total_pnl_sol: float = 0.0
    
    # Drawdown tracking
    peak_portfolio_value: float = 0.0
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    
    # Consecutive losses
    consecutive_losses: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    
    # Risk flags
    circuit_breaker_active: bool = False
    circuit_breaker_reason: Optional[str] = None
    trading_halted: bool = False
    halt_reason: Optional[str] = None
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate"""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades
    
    @property
    def risk_level(self) -> RiskLevel:
        """Determine portfolio risk level"""
        if self.current_drawdown_pct >= 0.30 or self.circuit_breaker_active:
            return RiskLevel.CRITICAL
        elif self.current_drawdown_pct >= 0.20:
            return RiskLevel.HIGH
        elif self.current_drawdown_pct >= 0.10:
            return RiskLevel.MEDIUM
        elif self.consecutive_losses >= 3:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def update_drawdown(self) -> None:
        """Update drawdown metrics"""
        if self.total_value_sol > self.peak_portfolio_value:
            self.peak_portfolio_value = self.total_value_sol
        
        if self.peak_portfolio_value > 0:
            self.current_drawdown_pct = (
                self.peak_portfolio_value - self.total_value_sol
            ) / self.peak_portfolio_value
        
        self.max_drawdown_pct = max(self.max_drawdown_pct, self.current_drawdown_pct)


@dataclass
class RiskCheckResult:
    """Result of a risk check"""
    passed: bool
    risk_level: RiskLevel
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    recommended_action: Optional[str] = None
    emergency_shutdown: bool = False


@dataclass
class EmergencyShutdown:
    """Emergency shutdown state"""
    active: bool = False
    triggered_at: Optional[datetime] = None
    reason: Optional[str] = None
    shutdown_type: Optional[str] = None  # 'full', 'new_positions', 'sizing_reduction'
    
    # Auto-recovery
    auto_recovery_enabled: bool = False
    recovery_conditions: Dict[str, Any] = field(default_factory=dict)
    
    def trigger(self, reason: str, shutdown_type: str = "full") -> None:
        """Trigger emergency shutdown"""
        self.active = True
        self.triggered_at = datetime.utcnow()
        self.reason = reason
        self.shutdown_type = shutdown_type
    
    def reset(self) -> None:
        """Reset emergency shutdown"""
        self.active = False
        self.triggered_at = None
        self.reason = None
        self.shutdown_type = None


# Default constraint instances
DEFAULT_KELLY_PARAMS = KellyParameters()
DEFAULT_POSITION_CONSTRAINTS = PositionConstraints()
DEFAULT_EXIT_CONSTRAINTS = ExitConstraints()
DEFAULT_DRAWDOWN_CONSTRAINTS = DrawdownConstraints()
