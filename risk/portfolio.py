"""
Portfolio State Tracking and Limit Enforcement for Pump.fun Trading System

Manages portfolio-level risk metrics, exposure tracking, and limit enforcement.
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

from .models import (
    PortfolioRisk,
    PositionRisk,
    PositionConstraints,
    RiskCheckResult,
    RiskLevel,
    EmergencyShutdown,
    DEFAULT_POSITION_CONSTRAINTS
)

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Simplified position representation"""
    token_address: str
    entry_price: float
    current_price: float
    size_sol: float
    size_usd: float
    portfolio_pct: float
    entry_time: datetime
    is_graduated: bool = False
    graduation_time: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class PortfolioManager:
    """
    Manages portfolio state, tracks exposures, and enforces limits.
    
    Key responsibilities:
    - Track total portfolio value and available capital
    - Monitor pre/post-graduation exposure
    - Enforce position count limits
    - Track daily/period P&L
    - Manage emergency shutdowns
    """
    
    def __init__(
        self,
        initial_capital_sol: float,
        initial_capital_usd: float,
        constraints: Optional[PositionConstraints] = None
    ):
        self.constraints = constraints or DEFAULT_POSITION_CONSTRAINTS
        
        # Portfolio state
        self.initial_capital_sol = initial_capital_sol
        self.initial_capital_usd = initial_capital_usd
        self.current_capital_sol = initial_capital_sol
        self.current_capital_usd = initial_capital_usd
        
        # Position tracking
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []
        
        # Risk metrics
        self.risk_state = PortfolioRisk(
            total_value_sol=initial_capital_sol,
            total_value_usd=initial_capital_usd,
            available_sol=initial_capital_sol,
            peak_portfolio_value=initial_capital_sol
        )
        
        # Daily tracking
        self.daily_start_value_sol = initial_capital_sol
        self.daily_pnl_sol = 0.0
        self.daily_pnl_pct = 0.0
        self.last_reset_date = datetime.utcnow().date()
        
        # Emergency shutdown
        self.emergency_shutdown = EmergencyShutdown()
        
        # Trade history
        self.trade_history: List[Dict[str, Any]] = []
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(
            f"PortfolioManager initialized with {initial_capital_sol:.4f} SOL "
            f"(${initial_capital_usd:.2f})"
        )
    
    def update_prices(self, price_updates: Dict[str, float]) -> None:
        """
        Update current prices for all positions.
        
        Args:
            price_updates: Dict mapping token_address to current price
        """
        for token_address, price in price_updates.items():
            if token_address in self.positions:
                position = self.positions[token_address]
                position.current_price = price
        
        self._recalculate_portfolio()
    
    def _recalculate_portfolio(self) -> None:
        """Recalculate all portfolio metrics"""
        total_value_sol = self.available_sol
        total_value_usd = self.available_usd
        
        pre_grad_exposure_sol = 0.0
        post_grad_exposure_sol = 0.0
        pre_grad_count = 0
        post_grad_count = 0
        
        unrealized_pnl_sol = 0.0
        
        for position in self.positions.values():
            # Calculate current position value
            if position.entry_price > 0:
                price_change = (position.current_price - position.entry_price) / position.entry_price
                current_value_sol = position.size_sol * (1 + price_change)
            else:
                current_value_sol = position.size_sol
            
            total_value_sol += current_value_sol
            
            # Track exposure by graduation status
            if position.is_graduated:
                post_grad_exposure_sol += current_value_sol
                post_grad_count += 1
            else:
                pre_grad_exposure_sol += current_value_sol
                pre_grad_count += 1
            
            # Track unrealized P&L
            unrealized_pnl_sol += current_value_sol - position.size_sol
        
        # Update risk state
        self.risk_state.total_value_sol = total_value_sol
        self.risk_state.pre_graduation_exposure_sol = pre_grad_exposure_sol
        self.risk_state.post_graduation_exposure_sol = post_grad_exposure_sol
        self.risk_state.total_positions = len(self.positions)
        self.risk_state.pre_graduation_positions = pre_grad_count
        self.risk_state.post_graduation_positions = post_grad_count
        
        # Calculate percentages
        if total_value_sol > 0:
            self.risk_state.pre_graduation_exposure_pct = (
                pre_grad_exposure_sol / total_value_sol
            )
        
        # Update drawdown
        self.risk_state.update_drawdown()
        
        # Update daily P&L
        self.daily_pnl_sol = total_value_sol - self.daily_start_value_sol
        if self.daily_start_value_sol > 0:
            self.daily_pnl_pct = self.daily_pnl_sol / self.daily_start_value_sol
        
        self.risk_state.daily_pnl_sol = self.daily_pnl_sol
        self.risk_state.daily_pnl_pct = self.daily_pnl_pct
    
    def add_position(
        self,
        token_address: str,
        entry_price: float,
        size_sol: float,
        size_usd: float,
        is_graduated: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> RiskCheckResult:
        """
        Add a new position to the portfolio.
        
        Args:
            token_address: Token mint address
            entry_price: Entry price per token
            size_sol: Position size in SOL
            size_usd: Position size in USD
            is_graduated: Whether token is already graduated
            metadata: Additional position metadata
            
        Returns:
            RiskCheckResult with pass/fail status
        """
        # Check emergency shutdown
        if self.emergency_shutdown.active:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                message=f"Trading halted: {self.emergency_shutdown.reason}",
                details={'shutdown_type': self.emergency_shutdown.shutdown_type},
                emergency_shutdown=True
            )
        
        # Check if position already exists
        if token_address in self.positions:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Position for {token_address} already exists",
                recommended_action="Use update_position for existing positions"
            )
        
        # Calculate portfolio percentage
        portfolio_pct = size_sol / self.risk_state.total_value_sol if self.risk_state.total_value_sol > 0 else 0
        
        # Check single position limit (5%)
        if portfolio_pct > self.constraints.max_single_position_pct:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Position size {portfolio_pct:.2%} exceeds max {self.constraints.max_single_position_pct:.2%}",
                details={'requested_pct': portfolio_pct, 'max_pct': self.constraints.max_single_position_pct},
                recommended_action="Reduce position size"
            )
        
        # Check pre-graduation exposure limit
        if not is_graduated:
            new_exposure = self.risk_state.pre_graduation_exposure_pct + portfolio_pct
            if new_exposure > self.constraints.max_pre_graduation_exposure:
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.HIGH,
                    message=f"Pre-graduation exposure would be {new_exposure:.2%}",
                    details={
                        'current_exposure': self.risk_state.pre_graduation_exposure_pct,
                        'new_exposure': new_exposure,
                        'max_exposure': self.constraints.max_pre_graduation_exposure
                    },
                    recommended_action="Wait for graduations or reduce existing exposure"
                )
        
        # Check position count limit
        if len(self.positions) >= self.constraints.max_simultaneous_positions:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                message=f"Maximum {self.constraints.max_simultaneous_positions} positions reached",
                details={'current_positions': len(self.positions)},
                recommended_action="Close existing positions before adding new ones"
            )
        
        # Check available capital
        if size_sol > self.available_sol:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Insufficient capital: need {size_sol:.4f} SOL, have {self.available_sol:.4f} SOL",
                details={'required': size_sol, 'available': self.available_sol},
                recommended_action="Reduce position size or free up capital"
            )
        
        # Create position
        position = Position(
            token_address=token_address,
            entry_price=entry_price,
            current_price=entry_price,
            size_sol=size_sol,
            size_usd=size_usd,
            portfolio_pct=portfolio_pct,
            entry_time=datetime.utcnow(),
            is_graduated=is_graduated,
            metadata=metadata or {}
        )
        
        # Add to portfolio
        self.positions[token_address] = position
        self._recalculate_portfolio()
        
        # Log trade
        self._log_trade('OPEN', token_address, size_sol, size_usd, entry_price)
        
        self.logger.info(
            f"Added position: {token_address[:8]}... | "
            f"Size: {size_sol:.4f} SOL (${size_usd:.2f}) | "
            f"Portfolio %: {portfolio_pct:.2%}"
        )
        
        return RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="Position added successfully",
            details={
                'token_address': token_address,
                'size_sol': size_sol,
                'portfolio_pct': portfolio_pct,
                'total_positions': len(self.positions)
            }
        )
    
    def close_position(
        self,
        token_address: str,
        exit_price: float,
        exit_reason: str = "manual"
    ) -> RiskCheckResult:
        """
        Close a position and record P&L.
        
        Args:
            token_address: Token to close
            exit_price: Exit price
            exit_reason: Reason for closing
            
        Returns:
            RiskCheckResult with trade details
        """
        if token_address not in self.positions:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Position {token_address} not found"
            )
        
        position = self.positions[token_address]
        
        # Calculate P&L
        if position.entry_price > 0:
            pnl_pct = (exit_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = 0.0
        
        exit_value_sol = position.size_sol * (1 + pnl_pct)
        pnl_sol = exit_value_sol - position.size_sol
        
        # Record trade
        self._log_trade(
            'CLOSE',
            token_address,
            exit_value_sol,
            exit_value_sol * (position.size_usd / position.size_sol) if position.size_sol > 0 else 0,
            exit_price,
            pnl_sol=pnl_sol,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason
        )
        
        # Update risk state
        self.risk_state.total_trades += 1
        if pnl_sol > 0:
            self.risk_state.winning_trades += 1
            self.risk_state.consecutive_losses = 0
        else:
            self.risk_state.consecutive_losses += 1
        
        # Move to closed positions
        position.current_price = exit_price
        self.closed_positions.append(position)
        del self.positions[token_address]
        
        self._recalculate_portfolio()
        
        self.logger.info(
            f"Closed position: {token_address[:8]}... | "
            f"P&L: {pnl_sol:.4f} SOL ({pnl_pct:+.2%}) | "
            f"Reason: {exit_reason}"
        )
        
        return RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="Position closed successfully",
            details={
                'token_address': token_address,
                'pnl_sol': pnl_sol,
                'pnl_pct': pnl_pct,
                'exit_price': exit_price,
                'exit_reason': exit_reason
            }
        )
    
    def mark_graduated(self, token_address: str) -> None:
        """Mark a position as graduated"""
        if token_address in self.positions:
            self.positions[token_address].is_graduated = True
            self.positions[token_address].graduation_time = datetime.utcnow()
            self._recalculate_portfolio()
            self.logger.info(f"Position {token_address[:8]}... marked as graduated")
    
    def _log_trade(
        self,
        trade_type: str,
        token_address: str,
        size_sol: float,
        size_usd: float,
        price: float,
        pnl_sol: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        exit_reason: Optional[str] = None
    ) -> None:
        """Log a trade to history"""
        trade = {
            'timestamp': datetime.utcnow(),
            'type': trade_type,
            'token_address': token_address,
            'size_sol': size_sol,
            'size_usd': size_usd,
            'price': price
        }
        
        if pnl_sol is not None:
            trade['pnl_sol'] = pnl_sol
        if pnl_pct is not None:
            trade['pnl_pct'] = pnl_pct
        if exit_reason:
            trade['exit_reason'] = exit_reason
        
        self.trade_history.append(trade)
    
    def reset_daily_stats(self) -> None:
        """Reset daily statistics"""
        current_date = datetime.utcnow().date()
        if current_date != self.last_reset_date:
            self.daily_start_value_sol = self.risk_state.total_value_sol
            self.daily_pnl_sol = 0.0
            self.daily_pnl_pct = 0.0
            self.last_reset_date = current_date
            self.logger.info(f"Daily stats reset. Starting value: {self.daily_start_value_sol:.4f} SOL")
    
    @property
    def available_sol(self) -> float:
        """Calculate available SOL for new positions"""
        committed = sum(p.size_sol for p in self.positions.values())
        return max(0, self.current_capital_sol - committed)
    
    @property
    def available_usd(self) -> float:
        """Calculate available USD for new positions"""
        sol_price = self.current_capital_usd / self.current_capital_sol if self.current_capital_sol > 0 else 0
        return self.available_sol * sol_price
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get a summary of portfolio state"""
        return {
            'total_value_sol': self.risk_state.total_value_sol,
            'total_value_usd': self.risk_state.total_value_usd,
            'available_sol': self.available_sol,
            'available_usd': self.available_usd,
            'total_positions': len(self.positions),
            'pre_graduation_positions': self.risk_state.pre_graduation_positions,
            'post_graduation_positions': self.risk_state.post_graduation_positions,
            'pre_graduation_exposure_pct': self.risk_state.pre_graduation_exposure_pct,
            'daily_pnl_sol': self.daily_pnl_sol,
            'daily_pnl_pct': self.daily_pnl_pct,
            'total_pnl_pct': (self.risk_state.total_value_sol - self.initial_capital_sol) / self.initial_capital_sol,
            'current_drawdown_pct': self.risk_state.current_drawdown_pct,
            'max_drawdown_pct': self.risk_state.max_drawdown_pct,
            'win_rate': self.risk_state.win_rate,
            'consecutive_losses': self.risk_state.consecutive_losses,
            'emergency_shutdown': self.emergency_shutdown.active,
            'risk_level': self.risk_state.risk_level.value
        }
    
    def get_position_details(self, token_address: Optional[str] = None) -> Dict[str, Any]:
        """Get details for all positions or a specific position"""
        if token_address:
            if token_address in self.positions:
                p = self.positions[token_address]
                return {
                    'token_address': p.token_address,
                    'entry_price': p.entry_price,
                    'current_price': p.current_price,
                    'size_sol': p.size_sol,
                    'size_usd': p.size_usd,
                    'portfolio_pct': p.portfolio_pct,
                    'is_graduated': p.is_graduated,
                    'entry_time': p.entry_time.isoformat(),
                    'unrealized_pnl_pct': (p.current_price - p.entry_price) / p.entry_price if p.entry_price > 0 else 0
                }
            return {}
        
        return {
            addr: {
                'entry_price': p.entry_price,
                'current_price': p.current_price,
                'size_sol': p.size_sol,
                'portfolio_pct': p.portfolio_pct,
                'is_graduated': p.is_graduated,
                'unrealized_pnl_pct': (p.current_price - p.entry_price) / p.entry_price if p.entry_price > 0 else 0
            }
            for addr, p in self.positions.items()
        }
