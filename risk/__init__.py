"""
Risk Management Module for Pump.fun Trading System

This module provides comprehensive risk management functionality including:
- Position sizing with Kelly Criterion
- Portfolio tracking and limit enforcement
- Exit strategies with tiered profit-taking
- Drawdown monitoring and circuit breakers

Usage:
    from pumpfun_trader.risk import PositionSizer, PortfolioManager, ExitStrategy
    from pumpfun_trader.risk import RiskManager, calculate_position_size, check_exit
"""

# Models and data structures
from .models import (
    # Enums
    RiskLevel,
    PositionStage,
    ExitTrigger,
    
    # Parameters
    KellyParameters,
    PositionConstraints,
    ExitConstraints,
    DrawdownConstraints,
    
    # Risk data
    PositionRisk,
    PortfolioRisk,
    RiskCheckResult,
    EmergencyShutdown,
    
    # Defaults
    DEFAULT_KELLY_PARAMS,
    DEFAULT_POSITION_CONSTRAINTS,
    DEFAULT_EXIT_CONSTRAINTS,
    DEFAULT_DRAWDOWN_CONSTRAINTS,
)

# Position sizing
from .position_sizing import (
    KellyCriterion,
    PositionSizer,
    PositionSizeResult,
    calculate_position_size,
)

# Portfolio management
from .portfolio import (
    PortfolioManager,
    Position,
)

# Exit strategies
from .exit_strategy import (
    ExitStrategy,
    ExitExecutor,
    ExitSignal,
    TieredExit,
    check_exit,
)

# Drawdown and circuit breakers
from .drawdown import (
    DrawdownMonitor,
    CircuitBreaker,
    RiskManager,
    DrawdownAlert,
    CircuitBreakerState,
    check_drawdown,
)

__version__ = "1.0.0"
__all__ = [
    # Enums
    'RiskLevel',
    'PositionStage',
    'ExitTrigger',
    
    # Parameters
    'KellyParameters',
    'PositionConstraints',
    'ExitConstraints',
    'DrawdownConstraints',
    
    # Risk data
    'PositionRisk',
    'PortfolioRisk',
    'RiskCheckResult',
    'EmergencyShutdown',
    
    # Position sizing
    'KellyCriterion',
    'PositionSizer',
    'PositionSizeResult',
    'calculate_position_size',
    
    # Portfolio
    'PortfolioManager',
    'Position',
    
    # Exit strategies
    'ExitStrategy',
    'ExitExecutor',
    'ExitSignal',
    'TieredExit',
    'check_exit',
    
    # Drawdown
    'DrawdownMonitor',
    'CircuitBreaker',
    'RiskManager',
    'DrawdownAlert',
    'CircuitBreakerState',
    'check_drawdown',
    
    # Defaults
    'DEFAULT_KELLY_PARAMS',
    'DEFAULT_POSITION_CONSTRAINTS',
    'DEFAULT_EXIT_CONSTRAINTS',
    'DEFAULT_DRAWDOWN_CONSTRAINTS',
]
