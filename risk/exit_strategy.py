"""
Exit Strategy Module for Pump.fun Trading System

Implements profit-taking tiers, stop-losses, and time-based exits.

Post-Graduation Reality:
- 81-97% of graduated tokens lose >50% value immediately post-listing
- Time decay: 0-4h peak, 4-24h sustained, 24-72h rapid decay
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np

from .models import (
    ExitConstraints,
    ExitTrigger,
    PositionRisk,
    RiskCheckResult,
    RiskLevel,
    DEFAULT_EXIT_CONSTRAINTS
)

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Exit signal with details"""
    should_exit: bool
    trigger: Optional[ExitTrigger] = None
    exit_percentage: float = 0.0  # 0-1, 1 = full exit
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # Higher = more urgent


@dataclass
class TieredExit:
    """Tiered exit configuration"""
    tier: int
    target_multiple: float
    exit_percentage: float  # Percentage of remaining position to exit
    description: str


class ExitStrategy:
    """
    Exit strategy with tiered profit-taking and stop-losses.
    
    Tiered Exit Strategy:
    - Tier 1: 25% at 5x
    - Tier 2: 25% at 10x  
    - Tier 3: 25% at 20x
    - Tier 4: 25% runner (hold with trailing stop)
    
    Stop Loss:
    - Hard stop: -50% from entry
    - Trailing stop: Activate after 3x, 30% trailing distance
    
    Time Decay:
    - 0-4h: Peak momentum (aggressive profit-taking)
    - 4-24h: Sustained elevation for strong runners
    - 24-72h: Rapid decay for majority
    """
    
    def __init__(self, constraints: Optional[ExitConstraints] = None):
        self.constraints = constraints or DEFAULT_EXIT_CONSTRAINTS
        
        # Define tiered exits
        self.tiered_exits = [
            TieredExit(
                tier=1,
                target_multiple=self.constraints.tier_1_target,
                exit_percentage=0.25,
                description=f"Take 25% profit at {self.constraints.tier_1_target}x"
            ),
            TieredExit(
                tier=2,
                target_multiple=self.constraints.tier_2_target,
                exit_percentage=0.25,
                description=f"Take 25% profit at {self.constraints.tier_2_target}x"
            ),
            TieredExit(
                tier=3,
                target_multiple=self.constraints.tier_3_target,
                exit_percentage=0.25,
                description=f"Take 25% profit at {self.constraints.tier_3_target}x"
            ),
            TieredExit(
                tier=4,
                target_multiple=float('inf'),
                exit_percentage=0.0,
                description="Hold remaining 25% as runner with trailing stop"
            )
        ]
        
        self.logger = logging.getLogger(__name__)
    
    def check_exit_signals(
        self,
        token_address: str,
        entry_price: float,
        current_price: float,
        entry_time: datetime,
        is_graduated: bool,
        graduation_time: Optional[datetime],
        tiers_hit: List[int],
        max_profit_seen: float = 0.0
    ) -> ExitSignal:
        """
        Check all exit conditions and return appropriate signal.
        
        Args:
            token_address: Token address
            entry_price: Entry price
            current_price: Current price
            entry_time: When position was entered
            is_graduated: Whether token has graduated
            graduation_time: When token graduated (if applicable)
            tiers_hit: List of profit tiers already hit
            max_profit_seen: Maximum profit multiple seen
            
        Returns:
            ExitSignal with exit decision
        """
        if entry_price <= 0:
            return ExitSignal(should_exit=False, reason="Invalid entry price")
        
        current_multiple = current_price / entry_price
        pnl_pct = current_multiple - 1.0
        
        # Priority 1: Hard stop loss (-50%)
        hard_stop_signal = self._check_hard_stop(pnl_pct)
        if hard_stop_signal.should_exit:
            return hard_stop_signal
        
        # Priority 2: Trailing stop (after 3x)
        if max_profit_seen >= self.constraints.trailing_stop_activation:
            trailing_signal = self._check_trailing_stop(
                current_multiple, max_profit_seen
            )
            if trailing_signal.should_exit:
                return trailing_signal
        
        # Priority 3: Tiered profit targets
        tier_signal = self._check_tiered_exits(current_multiple, tiers_hit)
        if tier_signal.should_exit:
            return tier_signal
        
        # Priority 4: Time-based decay (post-graduation)
        if is_graduated and graduation_time:
            time_signal = self._check_time_decay(graduation_time)
            if time_signal.should_exit:
                return time_signal
        
        # Priority 5: Realistic upper bound (30x)
        if current_multiple >= self.constraints.max_realistic_multiple:
            return ExitSignal(
                should_exit=True,
                trigger=ExitTrigger.PRICE_TARGET,
                exit_percentage=1.0,
                reason=f"Reached realistic upper bound of {self.constraints.max_realistic_multiple}x",
                details={'current_multiple': current_multiple},
                priority=5
            )
        
        return ExitSignal(should_exit=False, reason="No exit conditions met")
    
    def _check_hard_stop(self, pnl_pct: float) -> ExitSignal:
        """Check hard stop loss condition"""
        if pnl_pct <= self.constraints.hard_stop_pct:
            return ExitSignal(
                should_exit=True,
                trigger=ExitTrigger.HARD_STOP,
                exit_percentage=1.0,
                reason=f"Hard stop triggered at {pnl_pct:.2%} (limit: {self.constraints.hard_stop_pct:.2%})",
                details={'pnl_pct': pnl_pct, 'stop_level': self.constraints.hard_stop_pct},
                priority=10  # Highest priority
            )
        return ExitSignal(should_exit=False)
    
    def _check_trailing_stop(
        self,
        current_multiple: float,
        max_profit_seen: float
    ) -> ExitSignal:
        """Check trailing stop condition"""
        # Trailing stop activates after 3x
        if max_profit_seen < self.constraints.trailing_stop_activation:
            return ExitSignal(should_exit=False)
        
        # Calculate trailing stop level
        # If max was 5x and trailing distance is 30%, stop at 5x * 0.7 = 3.5x
        stop_level = max_profit_seen * (1 - self.constraints.trailing_stop_distance)
        
        if current_multiple <= stop_level:
            return ExitSignal(
                should_exit=True,
                trigger=ExitTrigger.TRAILING_STOP,
                exit_percentage=1.0,
                reason=f"Trailing stop triggered at {current_multiple:.2f}x (max: {max_profit_seen:.2f}x, stop: {stop_level:.2f}x)",
                details={
                    'current_multiple': current_multiple,
                    'max_multiple': max_profit_seen,
                    'stop_level': stop_level,
                    'trailing_distance': self.constraints.trailing_stop_distance
                },
                priority=9
            )
        return ExitSignal(should_exit=False)
    
    def _check_tiered_exits(
        self,
        current_multiple: float,
        tiers_hit: List[int]
    ) -> ExitSignal:
        """Check tiered profit-taking targets"""
        for tier in self.tiered_exits:
            if tier.tier in tiers_hit:
                continue  # Already hit this tier
            
            if current_multiple >= tier.target_multiple:
                return ExitSignal(
                    should_exit=True,
                    trigger=ExitTrigger.PRICE_TARGET,
                    exit_percentage=tier.exit_percentage,
                    reason=tier.description,
                    details={
                        'tier': tier.tier,
                        'target_multiple': tier.target_multiple,
                        'current_multiple': current_multiple,
                        'exit_pct': tier.exit_percentage
                    },
                    priority=7
                )
        
        return ExitSignal(should_exit=False)
    
    def _check_time_decay(self, graduation_time: datetime) -> ExitSignal:
        """Check time-based decay conditions"""
        now = datetime.utcnow()
        hours_since_grad = (now - graduation_time).total_seconds() / 3600
        
        # 24-72 hours: Rapid decay period - consider exit
        if hours_since_grad >= self.constraints.decay_hours:
            return ExitSignal(
                should_exit=True,
                trigger=ExitTrigger.TIME_DECAY,
                exit_percentage=1.0,
                reason=f"Time decay: {hours_since_grad:.1f}h since graduation (rapid decay period)",
                details={
                    'hours_since_graduation': hours_since_grad,
                    'decay_threshold': self.constraints.decay_hours
                },
                priority=4
            )
        
        # 4-24 hours: Sustained elevation - monitor closely
        if hours_since_grad >= self.constraints.sustained_hours:
            # This is a warning, not a mandatory exit
            return ExitSignal(
                should_exit=False,
                reason=f"Time warning: {hours_since_grad:.1f}h since graduation (monitor closely)"
            )
        
        return ExitSignal(should_exit=False)
    
    def calculate_exit_size(
        self,
        position_size_sol: float,
        exit_percentage: float,
        tiers_hit: List[int]
    ) -> Tuple[float, float]:
        """
        Calculate exit size based on percentage and tiers already hit.
        
        Args:
            position_size_sol: Current position size
            exit_percentage: Percentage to exit (0-1)
            tiers_hit: Tiers already executed
            
        Returns:
            Tuple of (exit_size, remaining_size)
        """
        # Calculate remaining position after previous tier exits
        remaining_pct = 1.0
        for tier_num in tiers_hit:
            tier = next((t for t in self.tiered_exits if t.tier == tier_num), None)
            if tier:
                remaining_pct *= (1 - tier.exit_percentage)
        
        # Calculate this exit from remaining position
        exit_size = position_size_sol * remaining_pct * exit_percentage
        new_remaining = position_size_sol * remaining_pct * (1 - exit_percentage)
        
        return exit_size, new_remaining
    
    def get_exit_plan(self, entry_price: float) -> Dict[str, Any]:
        """
        Generate a complete exit plan for a position.
        
        Args:
            entry_price: Entry price for the position
            
        Returns:
            Dictionary with exit plan details
        """
        plan = {
            'entry_price': entry_price,
            'hard_stop': {
                'price': entry_price * (1 + self.constraints.hard_stop_pct),
                'loss_pct': self.constraints.hard_stop_pct
            },
            'trailing_stop': {
                'activation_multiple': self.constraints.trailing_stop_activation,
                'activation_price': entry_price * self.constraints.trailing_stop_activation,
                'trailing_distance': self.constraints.trailing_stop_distance
            },
            'tiered_exits': [],
            'time_decay': {
                'peak_momentum_hours': self.constraints.peak_momentum_hours,
                'sustained_hours': self.constraints.sustained_hours,
                'decay_hours': self.constraints.decay_hours
            }
        }
        
        for tier in self.tiered_exits:
            plan['tiered_exits'].append({
                'tier': tier.tier,
                'target_multiple': tier.target_multiple,
                'target_price': entry_price * tier.target_multiple,
                'exit_percentage': tier.exit_percentage,
                'description': tier.description
            })
        
        return plan
    
    def get_time_decay_assessment(
        self,
        graduation_time: Optional[datetime]
    ) -> Dict[str, Any]:
        """
        Get time decay assessment for a graduated token.
        
        Args:
            graduation_time: When token graduated
            
        Returns:
            Dictionary with time decay assessment
        """
        if not graduation_time:
            return {
                'status': 'not_graduated',
                'hours_since_graduation': None,
                'phase': None,
                'recommendation': 'Wait for graduation'
            }
        
        now = datetime.utcnow()
        hours = (now - graduation_time).total_seconds() / 3600
        
        if hours < self.constraints.peak_momentum_hours:
            phase = 'peak_momentum'
            recommendation = 'Aggressive profit-taking window'
            urgency = 'high'
        elif hours < self.constraints.sustained_hours:
            phase = 'sustained_elevation'
            recommendation = 'Monitor for strong runners'
            urgency = 'medium'
        elif hours < self.constraints.decay_hours:
            phase = 'early_decay'
            recommendation = 'Consider exit for non-runners'
            urgency = 'medium-high'
        else:
            phase = 'rapid_decay'
            recommendation = 'Exit majority of positions'
            urgency = 'high'
        
        return {
            'status': 'graduated',
            'hours_since_graduation': hours,
            'phase': phase,
            'recommendation': recommendation,
            'urgency': urgency
        }


class ExitExecutor:
    """
    Executes exit orders based on exit signals.
    
    Tracks exit history and manages partial exits.
    """
    
    def __init__(self, exit_strategy: Optional[ExitStrategy] = None):
        self.strategy = exit_strategy or ExitStrategy()
        self.exit_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.tiers_hit: Dict[str, List[int]] = defaultdict(list)
        self.max_profit_seen: Dict[str, float] = {}
        self.logger = logging.getLogger(__name__)
    
    def update_position_state(
        self,
        token_address: str,
        current_multiple: float
    ) -> None:
        """Update tracked state for a position"""
        # Update max profit seen
        if token_address not in self.max_profit_seen:
            self.max_profit_seen[token_address] = current_multiple
        else:
            self.max_profit_seen[token_address] = max(
                self.max_profit_seen[token_address],
                current_multiple
            )
    
    def record_exit(
        self,
        token_address: str,
        exit_signal: ExitSignal,
        size_exited: float,
        price: float
    ) -> None:
        """Record an exit execution"""
        exit_record = {
            'timestamp': datetime.utcnow(),
            'trigger': exit_signal.trigger.value if exit_signal.trigger else None,
            'reason': exit_signal.reason,
            'size_exited': size_exited,
            'price': price,
            'details': exit_signal.details
        }
        
        self.exit_history[token_address].append(exit_record)
        
        # Record tier hit if applicable
        if exit_signal.trigger == ExitTrigger.PRICE_TARGET:
            tier = exit_signal.details.get('tier')
            if tier:
                self.tiers_hit[token_address].append(tier)
        
        self.logger.info(
            f"Exit recorded for {token_address[:8]}... | "
            f"Size: {size_exited:.4f} SOL | "
            f"Trigger: {exit_signal.trigger.value if exit_signal.trigger else 'unknown'}"
        )
    
    def get_position_exit_summary(self, token_address: str) -> Dict[str, Any]:
        """Get exit summary for a position"""
        return {
            'token_address': token_address,
            'exits_taken': len(self.exit_history[token_address]),
            'exit_history': self.exit_history[token_address],
            'tiers_hit': self.tiers_hit[token_address],
            'max_profit_seen': self.max_profit_seen.get(token_address, 0.0),
            'remaining_position_pct': self._calculate_remaining_pct(token_address)
        }
    
    def _calculate_remaining_pct(self, token_address: str) -> float:
        """Calculate remaining position percentage after exits"""
        remaining = 1.0
        for tier_num in self.tiers_hit[token_address]:
            tier = next(
                (t for t in self.strategy.tiered_exits if t.tier == tier_num),
                None
            )
            if tier:
                remaining *= (1 - tier.exit_percentage)
        return remaining
    
    def clear_position(self, token_address: str) -> None:
        """Clear all tracking for a closed position"""
        if token_address in self.exit_history:
            del self.exit_history[token_address]
        if token_address in self.tiers_hit:
            del self.tiers_hit[token_address]
        if token_address in self.max_profit_seen:
            del self.max_profit_seen[token_address]


# Convenience functions
def check_exit(
    entry_price: float,
    current_price: float,
    entry_time: datetime,
    is_graduated: bool = False,
    graduation_time: Optional[datetime] = None,
    tiers_hit: Optional[List[int]] = None,
    max_profit_seen: float = 0.0
) -> ExitSignal:
    """
    Quick function to check if position should exit.
    
    Args:
        entry_price: Entry price
        current_price: Current price
        entry_time: When position was entered
        is_graduated: Whether token graduated
        graduation_time: When token graduated
        tiers_hit: Profit tiers already hit
        max_profit_seen: Maximum profit multiple seen
        
    Returns:
        ExitSignal with exit decision
    """
    strategy = ExitStrategy()
    return strategy.check_exit_signals(
        token_address="",
        entry_price=entry_price,
        current_price=current_price,
        entry_time=entry_time,
        is_graduated=is_graduated,
        graduation_time=graduation_time,
        tiers_hit=tiers_hit or [],
        max_profit_seen=max_profit_seen
    )
