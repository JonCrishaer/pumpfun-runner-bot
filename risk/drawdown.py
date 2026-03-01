"""
Drawdown Monitoring and Circuit Breakers for Pump.fun Trading System

Implements drawdown tracking, circuit breakers, and emergency shutdown capabilities.
"""

import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
import numpy as np

from .models import (
    DrawdownConstraints,
    PortfolioRisk,
    RiskCheckResult,
    RiskLevel,
    EmergencyShutdown,
    DEFAULT_DRAWDOWN_CONSTRAINTS
)

logger = logging.getLogger(__name__)


@dataclass
class DrawdownAlert:
    """Drawdown alert with severity and action"""
    timestamp: datetime
    drawdown_pct: float
    severity: RiskLevel
    message: str
    recommended_action: str
    auto_triggered: bool = False


@dataclass
class CircuitBreakerState:
    """Circuit breaker state tracking"""
    triggered: bool = False
    triggered_at: Optional[datetime] = None
    reason: Optional[str] = None
    level: Optional[str] = None  # 'daily_loss', 'drawdown', 'consecutive_losses', 'volatility'
    cooldown_until: Optional[datetime] = None
    reset_after: Optional[datetime] = None


class DrawdownMonitor:
    """
    Monitors portfolio drawdown and triggers alerts.
    
    Tracks:
    - Current drawdown from peak
    - Maximum historical drawdown
    - Daily loss limits
    - Consecutive losses
    """
    
    def __init__(
        self,
        constraints: Optional[DrawdownConstraints] = None,
        alert_callbacks: Optional[List[Callable]] = None
    ):
        self.constraints = constraints or DEFAULT_DRAWDOWN_CONSTRAINTS
        self.alert_callbacks = alert_callbacks or []
        
        # Drawdown tracking
        self.peak_value: float = 0.0
        self.current_drawdown_pct: float = 0.0
        self.max_drawdown_pct: float = 0.0
        self.drawdown_history: deque = deque(maxlen=1000)
        
        # Daily tracking
        self.daily_start_value: float = 0.0
        self.daily_loss_pct: float = 0.0
        self.last_reset_date: Optional[datetime] = None
        
        # Loss tracking
        self.consecutive_losses: int = 0
        self.loss_history: deque = deque(maxlen=100)
        
        # Alert history
        self.alerts: List[DrawdownAlert] = []
        
        self.logger = logging.getLogger(__name__)
    
    def initialize(self, initial_portfolio_value: float) -> None:
        """Initialize monitor with starting portfolio value"""
        self.peak_value = initial_portfolio_value
        self.daily_start_value = initial_portfolio_value
        self.last_reset_date = datetime.utcnow()
        self.logger.info(f"DrawdownMonitor initialized with value: {initial_portfolio_value:.4f}")
    
    def update(self, current_portfolio_value: float) -> Optional[DrawdownAlert]:
        """
        Update drawdown metrics with current portfolio value.
        
        Args:
            current_portfolio_value: Current total portfolio value
            
        Returns:
            DrawdownAlert if threshold breached, None otherwise
        """
        now = datetime.utcnow()
        
        # Reset daily stats if needed
        if self.last_reset_date and now.date() != self.last_reset_date.date():
            self._reset_daily_stats(current_portfolio_value)
        
        # Update peak
        if current_portfolio_value > self.peak_value:
            self.peak_value = current_portfolio_value
            self.current_drawdown_pct = 0.0
        else:
            # Calculate drawdown
            if self.peak_value > 0:
                self.current_drawdown_pct = (
                    self.peak_value - current_portfolio_value
                ) / self.peak_value
        
        # Update max drawdown
        self.max_drawdown_pct = max(self.max_drawdown_pct, self.current_drawdown_pct)
        
        # Update daily loss
        if self.daily_start_value > 0:
            self.daily_loss_pct = (
                self.daily_start_value - current_portfolio_value
            ) / self.daily_start_value
        
        # Record history
        self.drawdown_history.append({
            'timestamp': now,
            'value': current_portfolio_value,
            'drawdown_pct': self.current_drawdown_pct
        })
        
        # Check for alerts
        alert = self._check_alert_conditions()
        if alert:
            self.alerts.append(alert)
            self._notify_alert(alert)
        
        return alert
    
    def _check_alert_conditions(self) -> Optional[DrawdownAlert]:
        """Check if any alert conditions are met"""
        now = datetime.utcnow()
        
        # Check critical drawdown (30%)
        if self.current_drawdown_pct >= self.constraints.critical_drawdown_pct:
            return DrawdownAlert(
                timestamp=now,
                drawdown_pct=self.current_drawdown_pct,
                severity=RiskLevel.CRITICAL,
                message=f"CRITICAL: Drawdown {self.current_drawdown_pct:.2%} exceeds {self.constraints.critical_drawdown_pct:.2%}",
                recommended_action="EMERGENCY SHUTDOWN - Halt all trading immediately",
                auto_triggered=True
            )
        
        # Check portfolio drawdown limit (20%)
        if self.current_drawdown_pct >= self.constraints.max_portfolio_drawdown_pct:
            return DrawdownAlert(
                timestamp=now,
                drawdown_pct=self.current_drawdown_pct,
                severity=RiskLevel.HIGH,
                message=f"HIGH: Drawdown {self.current_drawdown_pct:.2%} exceeds {self.constraints.max_portfolio_drawdown_pct:.2%}",
                recommended_action="Reduce position sizes and halt new entries"
            )
        
        # Check daily loss limit (5%)
        if self.daily_loss_pct >= self.constraints.max_daily_loss_pct:
            return DrawdownAlert(
                timestamp=now,
                drawdown_pct=self.daily_loss_pct,
                severity=RiskLevel.HIGH,
                message=f"HIGH: Daily loss {self.daily_loss_pct:.2%} exceeds {self.constraints.max_daily_loss_pct:.2%}",
                recommended_action="Halt trading for the day"
            )
        
        # Check daily drawdown limit (10%)
        if self.daily_loss_pct >= self.constraints.max_daily_drawdown_pct:
            return DrawdownAlert(
                timestamp=now,
                drawdown_pct=self.daily_loss_pct,
                severity=RiskLevel.MEDIUM,
                message=f"MEDIUM: Daily drawdown {self.daily_loss_pct:.2%}",
                recommended_action="Monitor closely and reduce risk"
            )
        
        # Check consecutive losses
        if self.consecutive_losses >= self.constraints.consecutive_losses_limit:
            return DrawdownAlert(
                timestamp=now,
                drawdown_pct=self.current_drawdown_pct,
                severity=RiskLevel.MEDIUM,
                message=f"MEDIUM: {self.consecutive_losses} consecutive losses",
                recommended_action="Take a break and review strategy"
            )
        
        return None
    
    def record_trade_result(self, pnl_sol: float) -> None:
        """Record a trade result for consecutive loss tracking"""
        if pnl_sol < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        
        self.loss_history.append({
            'timestamp': datetime.utcnow(),
            'pnl_sol': pnl_sol,
            'consecutive_losses': self.consecutive_losses
        })
    
    def _reset_daily_stats(self, current_value: float) -> None:
        """Reset daily statistics"""
        self.daily_start_value = current_value
        self.daily_loss_pct = 0.0
        self.last_reset_date = datetime.utcnow()
        self.logger.info(f"Daily stats reset. Starting value: {current_value:.4f}")
    
    def _notify_alert(self, alert: DrawdownAlert) -> None:
        """Notify all registered callbacks of alert"""
        self.logger.warning(f"DRAWDOWN ALERT: {alert.message}")
        
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                self.logger.error(f"Alert callback failed: {e}")
    
    def get_drawdown_stats(self) -> Dict[str, Any]:
        """Get current drawdown statistics"""
        return {
            'current_drawdown_pct': self.current_drawdown_pct,
            'max_drawdown_pct': self.max_drawdown_pct,
            'peak_value': self.peak_value,
            'daily_loss_pct': self.daily_loss_pct,
            'daily_start_value': self.daily_start_value,
            'consecutive_losses': self.consecutive_losses,
            'alert_count': len(self.alerts),
            'recent_alerts': [
                {
                    'timestamp': a.timestamp.isoformat(),
                    'severity': a.severity.value,
                    'message': a.message
                }
                for a in self.alerts[-5:]  # Last 5 alerts
            ]
        }


class CircuitBreaker:
    """
    Circuit breaker implementation for emergency shutdowns.
    
    Triggers:
    - Daily loss limit exceeded (5%)
    - Portfolio drawdown exceeded (20%)
    - Critical drawdown (30%)
    - Consecutive losses (5)
    - Volatility spike (3x normal)
    
    Actions:
    - Full shutdown: Halt all trading
    - New positions: Only close existing, no new entries
    - Sizing reduction: Reduce position sizes by 50%
    """
    
    def __init__(
        self,
        constraints: Optional[DrawdownConstraints] = None,
        emergency_callback: Optional[Callable] = None
    ):
        self.constraints = constraints or DEFAULT_DRAWDOWN_CONSTRAINTS
        self.emergency_callback = emergency_callback
        
        self.state = CircuitBreakerState()
        self.emergency_shutdown = EmergencyShutdown()
        
        # Cooldown tracking
        self.cooldown_active: bool = False
        self.cooldown_end: Optional[datetime] = None
        
        # Trigger history
        self.trigger_history: List[Dict[str, Any]] = []
        
        self.logger = logging.getLogger(__name__)
    
    def check(
        self,
        portfolio_risk: PortfolioRisk,
        current_volatility: Optional[float] = None,
        normal_volatility: Optional[float] = None
    ) -> RiskCheckResult:
        """
        Check all circuit breaker conditions.
        
        Args:
            portfolio_risk: Current portfolio risk state
            current_volatility: Current portfolio volatility
            normal_volatility: Normal/historical volatility
            
        Returns:
            RiskCheckResult with circuit breaker status
        """
        # Check if already in cooldown
        if self._is_in_cooldown():
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Circuit breaker in cooldown until {self.cooldown_end}",
                details={'cooldown_end': self.cooldown_end.isoformat() if self.cooldown_end else None}
            )
        
        # Check emergency shutdown
        if self.emergency_shutdown.active:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                message=f"Emergency shutdown active: {self.emergency_shutdown.reason}",
                details={
                    'shutdown_type': self.emergency_shutdown.shutdown_type,
                    'triggered_at': self.emergency_shutdown.triggered_at.isoformat() if self.emergency_shutdown.triggered_at else None
                },
                emergency_shutdown=True
            )
        
        # Check critical drawdown (30%)
        if portfolio_risk.current_drawdown_pct >= self.constraints.critical_drawdown_pct:
            self._trigger(
                'critical_drawdown',
                f"Critical drawdown: {portfolio_risk.current_drawdown_pct:.2%}",
                'full'
            )
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                message=f"CRITICAL DRAWDOWN: {portfolio_risk.current_drawdown_pct:.2%}",
                details={'drawdown_pct': portfolio_risk.current_drawdown_pct},
                emergency_shutdown=True
            )
        
        # Check portfolio drawdown limit (20%)
        if portfolio_risk.current_drawdown_pct >= self.constraints.max_portfolio_drawdown_pct:
            self._trigger(
                'portfolio_drawdown',
                f"Portfolio drawdown: {portfolio_risk.current_drawdown_pct:.2%}",
                'new_positions'
            )
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Portfolio drawdown limit: {portfolio_risk.current_drawdown_pct:.2%}",
                details={'drawdown_pct': portfolio_risk.current_drawdown_pct},
                recommended_action="Halt new positions, manage existing"
            )
        
        # Check daily loss limit (5%)
        if portfolio_risk.daily_pnl_pct <= -self.constraints.max_daily_loss_pct:
            self._trigger(
                'daily_loss',
                f"Daily loss: {portfolio_risk.daily_pnl_pct:.2%}",
                'new_positions'
            )
            self._start_cooldown(self.constraints.cooldown_after_loss_seconds)
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                message=f"Daily loss limit: {portfolio_risk.daily_pnl_pct:.2%}",
                details={'daily_loss_pct': portfolio_risk.daily_pnl_pct},
                recommended_action="Halt trading for the day"
            )
        
        # Check consecutive losses
        if portfolio_risk.consecutive_losses >= self.constraints.consecutive_losses_limit:
            self._trigger(
                'consecutive_losses',
                f"{portfolio_risk.consecutive_losses} consecutive losses",
                'sizing_reduction'
            )
            self._start_cooldown(self.constraints.cooldown_after_loss_seconds)
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                message=f"Consecutive losses: {portfolio_risk.consecutive_losses}",
                details={'consecutive_losses': portfolio_risk.consecutive_losses},
                recommended_action="Reduce position sizes by 50%"
            )
        
        # Check volatility spike
        if current_volatility and normal_volatility and normal_volatility > 0:
            vol_ratio = current_volatility / normal_volatility
            if vol_ratio >= self.constraints.volatility_spike_threshold:
                self._trigger(
                    'volatility_spike',
                    f"Volatility spike: {vol_ratio:.2f}x normal",
                    'sizing_reduction'
                )
                return RiskCheckResult(
                    passed=False,
                    risk_level=RiskLevel.MEDIUM,
                    message=f"Volatility spike: {vol_ratio:.2f}x normal",
                    details={
                        'current_volatility': current_volatility,
                        'normal_volatility': normal_volatility,
                        'ratio': vol_ratio
                    },
                    recommended_action="Reduce position sizes and widen stops"
                )
        
        return RiskCheckResult(
            passed=True,
            risk_level=RiskLevel.LOW,
            message="All circuit breaker checks passed"
        )
    
    def _trigger(self, level: str, reason: str, shutdown_type: str) -> None:
        """Trigger circuit breaker"""
        self.state.triggered = True
        self.state.triggered_at = datetime.utcnow()
        self.state.reason = reason
        self.state.level = level
        
        # Set reset time
        if level == 'critical_drawdown':
            reset_seconds = self.constraints.emergency_cooldown_seconds
        elif level in ['portfolio_drawdown', 'daily_loss']:
            reset_seconds = self.constraints.cooldown_after_drawdown_seconds
        else:
            reset_seconds = self.constraints.cooldown_after_loss_seconds
        
        self.state.reset_after = datetime.utcnow() + timedelta(seconds=reset_seconds)
        
        # Trigger emergency shutdown if critical
        if level == 'critical_drawdown':
            self.emergency_shutdown.trigger(reason, shutdown_type)
        
        # Record trigger
        self.trigger_history.append({
            'timestamp': datetime.utcnow(),
            'level': level,
            'reason': reason,
            'shutdown_type': shutdown_type
        })
        
        self.logger.critical(f"CIRCUIT BREAKER TRIGGERED: {reason}")
        
        # Call emergency callback if provided
        if self.emergency_callback:
            try:
                self.emergency_callback(level, reason, shutdown_type)
            except Exception as e:
                self.logger.error(f"Emergency callback failed: {e}")
    
    def reset(self) -> bool:
        """
        Attempt to reset circuit breaker.
        
        Returns:
            True if reset successful, False if still in cooldown
        """
        if self.state.reset_after and datetime.utcnow() < self.state.reset_after:
            self.logger.info(f"Cannot reset: in cooldown until {self.state.reset_after}")
            return False
        
        self.state = CircuitBreakerState()
        self.emergency_shutdown.reset()
        self.cooldown_active = False
        self.cooldown_end = None
        
        self.logger.info("Circuit breaker reset")
        return True
    
    def manual_trigger(
        self,
        reason: str,
        shutdown_type: str = 'full'
    ) -> None:
        """Manually trigger circuit breaker"""
        self._trigger('manual', reason, shutdown_type)
        self.logger.critical(f"MANUAL CIRCUIT BREAKER: {reason}")
    
    def _start_cooldown(self, seconds: int) -> None:
        """Start cooldown period"""
        self.cooldown_active = True
        self.cooldown_end = datetime.utcnow() + timedelta(seconds=seconds)
        self.logger.info(f"Cooldown started until {self.cooldown_end}")
    
    def _is_in_cooldown(self) -> bool:
        """Check if currently in cooldown"""
        if not self.cooldown_active or not self.cooldown_end:
            return False
        
        if datetime.utcnow() >= self.cooldown_end:
            self.cooldown_active = False
            self.cooldown_end = None
            return False
        
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status"""
        return {
            'triggered': self.state.triggered,
            'emergency_shutdown': self.emergency_shutdown.active,
            'in_cooldown': self._is_in_cooldown(),
            'cooldown_end': self.cooldown_end.isoformat() if self.cooldown_end else None,
            'trigger_history': self.trigger_history[-10:],  # Last 10 triggers
            'last_trigger': self.trigger_history[-1] if self.trigger_history else None
        }


class RiskManager:
    """
    Comprehensive risk manager combining drawdown monitoring and circuit breakers.
    """
    
    def __init__(
        self,
        initial_capital: float,
        constraints: Optional[DrawdownConstraints] = None,
        emergency_callback: Optional[Callable] = None
    ):
        self.constraints = constraints or DEFAULT_DRAWDOWN_CONSTRAINTS
        
        self.drawdown_monitor = DrawdownMonitor(constraints)
        self.circuit_breaker = CircuitBreaker(constraints, emergency_callback)
        
        self.initial_capital = initial_capital
        self.drawdown_monitor.initialize(initial_capital)
        
        self.logger = logging.getLogger(__name__)
    
    def update(
        self,
        portfolio_risk: PortfolioRisk,
        current_volatility: Optional[float] = None,
        normal_volatility: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Update all risk monitoring.
        
        Args:
            portfolio_risk: Current portfolio risk state
            current_volatility: Current portfolio volatility
            normal_volatility: Normal/historical volatility
            
        Returns:
            Dictionary with risk status
        """
        # Update drawdown monitor
        drawdown_alert = self.drawdown_monitor.update(portfolio_risk.total_value_sol)
        
        # Check circuit breakers
        circuit_result = self.circuit_breaker.check(
            portfolio_risk,
            current_volatility,
            normal_volatility
        )
        
        return {
            'drawdown_alert': drawdown_alert,
            'circuit_breaker_result': circuit_result,
            'can_trade': circuit_result.passed and not self.circuit_breaker.emergency_shutdown.active,
            'drawdown_stats': self.drawdown_monitor.get_drawdown_stats(),
            'circuit_status': self.circuit_breaker.get_status()
        }
    
    def record_trade(self, pnl_sol: float) -> None:
        """Record a trade result"""
        self.drawdown_monitor.record_trade_result(pnl_sol)
    
    def emergency_shutdown_manual(self, reason: str) -> None:
        """Manually trigger emergency shutdown"""
        self.circuit_breaker.manual_trigger(reason, 'full')
    
    def reset(self) -> bool:
        """Reset all risk monitoring"""
        return self.circuit_breaker.reset()


# Convenience functions
def check_drawdown(
    current_value: float,
    peak_value: float,
    daily_start_value: float
) -> Dict[str, Any]:
    """
    Quick drawdown check.
    
    Args:
        current_value: Current portfolio value
        peak_value: Peak portfolio value
        daily_start_value: Value at start of day
        
    Returns:
        Dictionary with drawdown metrics
    """
    drawdown_pct = (peak_value - current_value) / peak_value if peak_value > 0 else 0
    daily_loss_pct = (daily_start_value - current_value) / daily_start_value if daily_start_value > 0 else 0
    
    constraints = DEFAULT_DRAWDOWN_CONSTRAINTS
    
    alerts = []
    if drawdown_pct >= constraints.critical_drawdown_pct:
        alerts.append(f"CRITICAL: Drawdown {drawdown_pct:.2%}")
    elif drawdown_pct >= constraints.max_portfolio_drawdown_pct:
        alerts.append(f"HIGH: Drawdown {drawdown_pct:.2%}")
    
    if daily_loss_pct >= constraints.max_daily_loss_pct:
        alerts.append(f"HIGH: Daily loss {daily_loss_pct:.2%}")
    
    return {
        'drawdown_pct': drawdown_pct,
        'daily_loss_pct': daily_loss_pct,
        'alerts': alerts,
        'is_safe': len(alerts) == 0
    }
