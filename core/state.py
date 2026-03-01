"""
Global State Management for Pump.fun Trading System

This module provides centralized state management for positions, signals,
portfolio, and market data. It uses an async-safe design with proper
locking mechanisms and supports state persistence.

Example Usage:
    >>> from core.state import global_state, Position, PositionStatus
    >>> 
    >>> # Create a new position
    >>> position = Position(
    >>>     token_address="abc123",
    >>>     entry_price=0.001,
    >>>     size_sol=1.0
    >>> )
    >>> await global_state.add_position(position)
    >>> 
    >>> # Query positions
    >>> open_positions = await global_state.get_open_positions()
    >>> 
    >>> # Update portfolio
    >>> await global_state.update_portfolio_value(100.0)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set
from collections import defaultdict
import copy

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================

class PositionStatus(Enum):
    """Status of a trading position."""
    PENDING = auto()      # Order submitted, not yet filled
    OPEN = auto()         # Position is active
    CLOSING = auto()      # Close order submitted
    CLOSED = auto()       # Position closed
    CANCELLED = auto()    # Position cancelled before fill


class SignalStrength(Enum):
    """Strength of a trading signal."""
    WEAK = 1
    MODERATE = 2
    STRONG = 3
    VERY_STRONG = 4


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class TokenInfo:
    """Information about a tracked token.
    
    Attributes:
        address: Token mint address
        symbol: Token symbol
        name: Token name
        decimals: Token decimals
        created_at: Token creation timestamp
        bonding_curve_progress: Current bonding curve progress (0.0 - 1.0)
        holders: Number of token holders
        volume_24h: 24-hour trading volume
        liquidity_sol: Liquidity in SOL
        last_updated: Last update timestamp
        metadata: Additional token metadata
    """
    address: str
    symbol: str = ""
    name: str = ""
    decimals: int = 9
    created_at: datetime = field(default_factory=datetime.utcnow)
    bonding_curve_progress: float = 0.0
    holders: int = 0
    volume_24h: float = 0.0
    liquidity_sol: float = 0.0
    last_updated: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "decimals": self.decimals,
            "created_at": self.created_at.isoformat(),
            "bonding_curve_progress": self.bonding_curve_progress,
            "holders": self.holders,
            "volume_24h": self.volume_24h,
            "liquidity_sol": self.liquidity_sol,
            "last_updated": self.last_updated.isoformat() if hasattr(self.last_updated, 'isoformat') else str(self.last_updated),
            "metadata": self.metadata,
        }


@dataclass
class Position:
    """Trading position data.
    
    Attributes:
        id: Unique position identifier
        token_address: Token mint address
        entry_price: Entry price in SOL per token
        current_price: Current price in SOL per token
        size_sol: Position size in SOL
        size_tokens: Position size in tokens
        stop_loss: Stop loss price
        take_profits: List of take-profit levels with sizes
        status: Current position status
        created_at: Position creation timestamp
        updated_at: Last update timestamp
        closed_at: Position close timestamp (if closed)
        exit_price: Exit price (if closed)
        pnl_sol: Realized PnL in SOL (if closed)
        pnl_pct: Realized PnL percentage (if closed)
        exit_reason: Reason for closing (if closed)
        highest_pnl_pct: Highest PnL reached (for trailing stops)
        partial_exits: List of partial exit records
        metadata: Additional position metadata
    """
    id: str
    token_address: str
    entry_price: float
    size_sol: float
    size_tokens: float
    current_price: float = 0.0
    stop_loss: float = 0.0
    take_profits: List[Dict[str, float]] = field(default_factory=list)
    status: PositionStatus = PositionStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl_sol: Optional[float] = None
    pnl_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    highest_pnl_pct: float = 0.0
    partial_exits: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def unrealized_pnl_sol(self) -> float:
        """Calculate unrealized PnL in SOL."""
        if self.current_price <= 0 or self.status not in (PositionStatus.OPEN, PositionStatus.CLOSING):
            return 0.0
        return (self.current_price - self.entry_price) * self.size_tokens
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """Calculate unrealized PnL percentage."""
        if self.entry_price <= 0 or self.status not in (PositionStatus.OPEN, PositionStatus.CLOSING):
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price
    
    @property
    def remaining_size_tokens(self) -> float:
        """Calculate remaining position size after partial exits."""
        sold = sum(exit.get("tokens", 0) for exit in self.partial_exits)
        return self.size_tokens - sold
    
    @property
    def remaining_size_sol(self) -> float:
        """Calculate remaining position value in SOL."""
        return self.remaining_size_tokens * self.current_price
    
    @property
    def duration_seconds(self) -> float:
        """Calculate position duration in seconds."""
        end_time = self.closed_at or datetime.utcnow()
        return (end_time - self.created_at).total_seconds()
    
    def update_price(self, new_price: float) -> None:
        """Update current price and track highest PnL."""
        self.current_price = new_price
        self.updated_at = datetime.utcnow()
        
        pnl_pct = self.unrealized_pnl_pct
        if pnl_pct > self.highest_pnl_pct:
            self.highest_pnl_pct = pnl_pct
    
    def add_partial_exit(self, tokens: float, price: float, pnl_sol: float) -> None:
        """Record a partial exit."""
        self.partial_exits.append({
            "tokens": tokens,
            "price": price,
            "pnl_sol": pnl_sol,
            "timestamp": datetime.utcnow().isoformat(),
        })
        self.updated_at = datetime.utcnow()
    
    def close(self, exit_price: float, reason: str) -> None:
        """Close the position."""
        self.exit_price = exit_price
        self.exit_reason = reason
        self.status = PositionStatus.CLOSED
        self.closed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        
        # Calculate realized PnL
        self.pnl_sol = (exit_price - self.entry_price) * self.size_tokens
        self.pnl_pct = (exit_price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "token_address": self.token_address,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "size_sol": self.size_sol,
            "size_tokens": self.size_tokens,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "status": self.status.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "exit_price": self.exit_price,
            "pnl_sol": self.pnl_sol,
            "pnl_pct": self.pnl_pct,
            "exit_reason": self.exit_reason,
            "highest_pnl_pct": self.highest_pnl_pct,
            "partial_exits": self.partial_exits,
            "unrealized_pnl_sol": self.unrealized_pnl_sol,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "metadata": self.metadata,
        }


@dataclass
class Signal:
    """Trading signal data.
    
    Attributes:
        id: Unique signal identifier
        token_address: Token mint address
        signal_type: Type of signal (entry, exit, hold)
        strength: Signal strength
        confidence: Confidence score (0.0 - 1.0)
        signals: Dictionary of individual signal components
        created_at: Signal creation timestamp
        expires_at: Signal expiration timestamp
        executed: Whether the signal was executed
        metadata: Additional signal metadata
    """
    id: str
    token_address: str
    signal_type: str
    strength: SignalStrength
    confidence: float
    signals: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    executed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_expired(self) -> bool:
        """Check if the signal has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "token_address": self.token_address,
            "signal_type": self.signal_type,
            "strength": self.strength.name,
            "confidence": self.confidence,
            "signals": self.signals,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "executed": self.executed,
            "is_expired": self.is_expired,
            "metadata": self.metadata,
        }


@dataclass
class Portfolio:
    """Portfolio state and metrics.
    
    Attributes:
        total_value_sol: Total portfolio value in SOL
        available_sol: Available SOL for trading
        allocated_sol: SOL allocated to open positions
        total_pnl_sol: Total realized PnL in SOL
        total_pnl_pct: Total realized PnL percentage
        daily_pnl_sol: Daily PnL in SOL
        daily_pnl_pct: Daily PnL percentage
        positions_count: Number of positions today
        win_count: Number of winning trades
        loss_count: Number of losing trades
        win_rate: Win rate percentage
        avg_win_pct: Average win percentage
        avg_loss_pct: Average loss percentage
        profit_factor: Profit factor (gross profit / gross loss)
        max_drawdown_pct: Maximum drawdown percentage
        sharpe_ratio: Sharpe ratio (if calculated)
        last_updated: Last update timestamp
    """
    total_value_sol: float = 0.0
    available_sol: float = 0.0
    allocated_sol: float = 0.0
    total_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0
    daily_pnl_sol: float = 0.0
    daily_pnl_pct: float = 0.0
    positions_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    last_updated: datetime = field(default_factory=datetime.utcnow)
    
    def update_metrics(self) -> None:
        """Recalculate derived metrics."""
        total_trades = self.win_count + self.loss_count
        if total_trades > 0:
            self.win_rate = (self.win_count / total_trades) * 100
        self.last_updated = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization - bulletproof against any type."""
        try:
            # Convert last_updated to ISO string safely
            if self.last_updated is None:
                last_updated_str = datetime.utcnow().isoformat()
            elif isinstance(self.last_updated, str):
                last_updated_str = self.last_updated
            elif hasattr(self.last_updated, 'isoformat') and callable(self.last_updated.isoformat):
                last_updated_str = self.last_updated.isoformat()
            else:
                last_updated_str = str(self.last_updated)
            
            return {
                "total_value_sol": float(self.total_value_sol),
                "available_sol": float(self.available_sol),
                "allocated_sol": float(self.allocated_sol),
                "total_pnl_sol": float(self.total_pnl_sol),
                "total_pnl_pct": float(self.total_pnl_pct),
                "daily_pnl_sol": float(self.daily_pnl_sol),
                "daily_pnl_pct": float(self.daily_pnl_pct),
                "positions_count": int(self.positions_count),
                "win_count": int(self.win_count),
                "loss_count": int(self.loss_count),
                "win_rate": float(self.win_rate),
                "avg_win_pct": float(self.avg_win_pct),
                "avg_loss_pct": float(self.avg_loss_pct),
                "profit_factor": float(self.profit_factor),
                "max_drawdown_pct": float(self.max_drawdown_pct),
                "sharpe_ratio": float(self.sharpe_ratio) if self.sharpe_ratio is not None else None,
                "last_updated": last_updated_str,
            }
        except Exception as e:
            logger.error(f"Error in Portfolio.to_dict(): {e}", exc_info=True)
            # Return safe fallback
            return {
                "total_value_sol": 0.0,
                "available_sol": 0.0,
                "allocated_sol": 0.0,
                "total_pnl_sol": 0.0,
                "total_pnl_pct": 0.0,
                "daily_pnl_sol": 0.0,
                "daily_pnl_pct": 0.0,
                "positions_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": None,
                "last_updated": datetime.utcnow().isoformat(),
            }


@dataclass
class MarketData:
    """Market data snapshot.
    
    Attributes:
        token_address: Token mint address
        price_sol: Current price in SOL
        volume_24h: 24-hour volume
        volume_1h: 1-hour volume
        holders: Number of holders
        buys_24h: Number of buys in 24h
        sells_24h: Number of sells in 24h
        liquidity_sol: Liquidity in SOL
        bonding_curve_progress: Bonding curve progress
        timestamp: Data timestamp
    """
    token_address: str
    price_sol: float = 0.0
    volume_24h: float = 0.0
    volume_1h: float = 0.0
    holders: int = 0
    buys_24h: int = 0
    sells_24h: int = 0
    liquidity_sol: float = 0.0
    bonding_curve_progress: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def buy_sell_ratio(self) -> float:
        """Calculate buy/sell ratio."""
        if self.sells_24h == 0:
            return float(self.buys_24h) if self.buys_24h > 0 else 1.0
        return self.buys_24h / self.sells_24h
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "token_address": self.token_address,
            "price_sol": self.price_sol,
            "volume_24h": self.volume_24h,
            "volume_1h": self.volume_1h,
            "holders": self.holders,
            "buys_24h": self.buys_24h,
            "sells_24h": self.sells_24h,
            "liquidity_sol": self.liquidity_sol,
            "bonding_curve_progress": self.bonding_curve_progress,
            "buy_sell_ratio": self.buy_sell_ratio,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Global State Manager
# =============================================================================

class GlobalState:
    """Thread-safe global state manager for the trading system.
    
    This class provides centralized state management with async-safe
    operations, state persistence, and event notifications.
    
    Example:
        >>> state = GlobalState()
        >>> await state.initialize()
        >>> 
        >>> # Add a position
        >>> position = Position(id="pos1", token_address="abc", ...)
        >>> await state.add_position(position)
        >>> 
        >>> # Get open positions
        >>> open_positions = await state.get_open_positions()
        >>> 
        >>> # Update portfolio
        >>> await state.update_portfolio_value(100.0)
    """
    
    def __init__(self, persistence_path: Optional[Path] = None):
        """Initialize the global state manager.
        
        Args:
            persistence_path: Optional path for state persistence
        """
        try:
            self._lock = asyncio.Lock()
        except RuntimeError:
            # If there's no running event loop, just create a dummy lock
            # This can happen during module initialization
            import threading
            self._lock = threading.Lock()
        self._persistence_path = persistence_path
        
        # State containers
        self._positions: Dict[str, Position] = {}
        self._signals: Dict[str, Signal] = {}
        self._tokens: Dict[str, TokenInfo] = {}
        self._market_data: Dict[str, MarketData] = {}
        self._portfolio: Portfolio = Portfolio()
        
        # State change callbacks
        self._callbacks: Dict[str, List[Callable[[str, Any], None]]] = defaultdict(list)
        
        # Historical data
        self._position_history: List[Dict[str, Any]] = []
        self._signal_history: List[Dict[str, Any]] = []
        
        # Running state
        self._initialized = False
        
    async def initialize(self) -> None:
        """Initialize the state manager and load persisted state."""
        async with self._lock:
            if self._initialized:
                return
            
            if self._persistence_path and self._persistence_path.exists():
                await self._load_state()
            
            self._initialized = True
            logger.info("Global state initialized")
    
    async def shutdown(self) -> None:
        """Shutdown the state manager and persist state."""
        async with self._lock:
            if self._persistence_path:
                await self._save_state()
            
            self._initialized = False
            logger.info("Global state shutdown")
    
    # =========================================================================
    # Position Management
    # =========================================================================
    
    async def add_position(self, position: Position) -> None:
        """Add a new position to the state.
        
        Args:
            position: Position to add
        """
        async with self._lock:
            self._positions[position.id] = position
            self._portfolio.allocated_sol += position.size_sol
            self._portfolio.positions_count += 1
            
            logger.info(f"Added position {position.id} for {position.token_address}")
            self._notify("position_added", position)
    
    async def update_position(self, position_id: str, **updates) -> Optional[Position]:
        """Update a position with new values.
        
        Args:
            position_id: Position identifier
            **updates: Key-value pairs to update
            
        Returns:
            Updated position or None if not found
        """
        async with self._lock:
            position = self._positions.get(position_id)
            if not position:
                logger.warning(f"Position {position_id} not found")
                return None
            
            for key, value in updates.items():
                if hasattr(position, key):
                    setattr(position, key, value)
            
            position.updated_at = datetime.utcnow()
            self._notify("position_updated", position)
            return position
    
    async def update_position_price(self, position_id: str, new_price: float) -> Optional[Position]:
        """Update a position's current price.
        
        Args:
            position_id: Position identifier
            new_price: New current price
            
        Returns:
            Updated position or None if not found
        """
        async with self._lock:
            position = self._positions.get(position_id)
            if not position:
                return None
            
            position.update_price(new_price)
            self._notify("position_price_updated", position)
            return position
    
    async def close_position(
        self, 
        position_id: str, 
        exit_price: float, 
        reason: str
    ) -> Optional[Position]:
        """Close a position.
        
        Args:
            position_id: Position identifier
            exit_price: Exit price
            reason: Reason for closing
            
        Returns:
            Closed position or None if not found
        """
        async with self._lock:
            position = self._positions.get(position_id)
            if not position:
                logger.warning(f"Position {position_id} not found for closing")
                return None
            
            position.close(exit_price, reason)
            
            # Update portfolio
            self._portfolio.allocated_sol -= position.size_sol
            self._portfolio.total_pnl_sol += position.pnl_sol or 0
            
            if position.pnl_sol and position.pnl_sol > 0:
                self._portfolio.win_count += 1
            else:
                self._portfolio.loss_count += 1
            
            self._portfolio.update_metrics()
            
            # Move to history
            self._position_history.append(position.to_dict())
            
            logger.info(f"Closed position {position_id}: PnL={position.pnl_sol:.4f} SOL ({position.pnl_pct:.2%})")
            self._notify("position_closed", position)
            return position
    
    async def get_position(self, position_id: str) -> Optional[Position]:
        """Get a position by ID.
        
        Args:
            position_id: Position identifier
            
        Returns:
            Position or None if not found
        """
        async with self._lock:
            return self._positions.get(position_id)
    
    async def get_open_positions(self) -> List[Position]:
        """Get all open positions.
        
        Returns:
            List of open positions
        """
        async with self._lock:
            return [
                p for p in self._positions.values() 
                if p.status in (PositionStatus.OPEN, PositionStatus.PENDING, PositionStatus.CLOSING)
            ]
    
    async def get_positions_by_token(self, token_address: str) -> List[Position]:
        """Get all positions for a token.
        
        Args:
            token_address: Token mint address
            
        Returns:
            List of positions for the token
        """
        async with self._lock:
            return [p for p in self._positions.values() if p.token_address == token_address]
    
    async def has_open_position(self, token_address: str) -> bool:
        """Check if there's an open position for a token.
        
        Args:
            token_address: Token mint address
            
        Returns:
            True if there's an open position
        """
        async with self._lock:
            return any(
                p.token_address == token_address and p.status == PositionStatus.OPEN
                for p in self._positions.values()
            )
    
    # =========================================================================
    # Signal Management
    # =========================================================================
    
    async def add_signal(self, signal: Signal) -> None:
        """Add a new signal to the state.
        
        Args:
            signal: Signal to add
        """
        async with self._lock:
            self._signals[signal.id] = signal
            self._signal_history.append(signal.to_dict())
            logger.info(f"Added {signal.signal_type} signal for {signal.token_address} (confidence: {signal.confidence:.2%})")
            self._notify("signal_added", signal)
    
    async def mark_signal_executed(self, signal_id: str) -> Optional[Signal]:
        """Mark a signal as executed.
        
        Args:
            signal_id: Signal identifier
            
        Returns:
            Updated signal or None if not found
        """
        async with self._lock:
            signal = self._signals.get(signal_id)
            if signal:
                signal.executed = True
                self._notify("signal_executed", signal)
            return signal
    
    async def get_recent_signals(
        self, 
        token_address: Optional[str] = None,
        signal_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Signal]:
        """Get recent signals with optional filtering.
        
        Args:
            token_address: Optional token filter
            signal_type: Optional signal type filter
            limit: Maximum number of signals to return
            
        Returns:
            List of signals
        """
        async with self._lock:
            signals = list(self._signals.values())
            
            if token_address:
                signals = [s for s in signals if s.token_address == token_address]
            if signal_type:
                signals = [s for s in signals if s.signal_type == signal_type]
            
            signals.sort(key=lambda s: s.created_at, reverse=True)
            return signals[:limit]
    
    async def cleanup_expired_signals(self) -> int:
        """Remove expired signals from active state.
        
        Returns:
            Number of signals removed
        """
        async with self._lock:
            expired_ids = [sid for sid, s in self._signals.items() if s.is_expired]
            for sid in expired_ids:
                del self._signals[sid]
            
            if expired_ids:
                logger.info(f"Cleaned up {len(expired_ids)} expired signals")
            
            return len(expired_ids)
    
    # =========================================================================
    # Token Management
    # =========================================================================
    
    async def update_token(self, token_info: TokenInfo) -> None:
        """Update token information.
        
        Args:
            token_info: Token information to update
        """
        async with self._lock:
            token_info.last_updated = datetime.utcnow()
            self._tokens[token_info.address] = token_info
            self._notify("token_updated", token_info)
    
    async def get_token(self, token_address: str) -> Optional[TokenInfo]:
        """Get token information.
        
        Args:
            token_address: Token mint address
            
        Returns:
            Token info or None if not found
        """
        async with self._lock:
            return self._tokens.get(token_address)
    
    async def get_tracked_tokens(self) -> List[TokenInfo]:
        """Get all tracked tokens.
        
        Returns:
            List of tracked tokens
        """
        async with self._lock:
            return list(self._tokens.values())
    
    # =========================================================================
    # Market Data Management
    # =========================================================================
    
    async def update_market_data(self, data: MarketData) -> None:
        """Update market data for a token.
        
        Args:
            data: Market data to update
        """
        async with self._lock:
            self._market_data[data.token_address] = data
    
    async def get_market_data(self, token_address: str) -> Optional[MarketData]:
        """Get market data for a token.
        
        Args:
            token_address: Token mint address
            
        Returns:
            Market data or None if not found
        """
        async with self._lock:
            return self._market_data.get(token_address)
    
    # =========================================================================
    # Portfolio Management
    # =========================================================================
    
    async def update_portfolio_value(self, total_value_sol: float) -> None:
        """Update portfolio total value.
        
        Args:
            total_value_sol: Total portfolio value in SOL
        """
        async with self._lock:
            old_value = self._portfolio.total_value_sol
            self._portfolio.total_value_sol = total_value_sol
            self._portfolio.available_sol = total_value_sol - self._portfolio.allocated_sol
            
            if old_value > 0:
                self._portfolio.total_pnl_pct = (total_value_sol - old_value) / old_value
            
            self._portfolio.last_updated = datetime.utcnow()
            self._notify("portfolio_updated", self._portfolio)
    
    async def get_portfolio(self) -> Portfolio:
        """Get current portfolio state.
        
        Returns:
            Current portfolio
        """
        async with self._lock:
            # Return a copy to prevent external modification
            return copy.deepcopy(self._portfolio)
    
    async def reset_daily_stats(self) -> None:
        """Reset daily statistics."""
        async with self._lock:
            self._portfolio.daily_pnl_sol = 0.0
            self._portfolio.daily_pnl_pct = 0.0
            logger.info("Daily stats reset")
    
    # =========================================================================
    # State Persistence
    # =========================================================================
    
    async def _save_state(self) -> None:
        """Save state to persistence file."""
        if not self._persistence_path:
            return
        
        try:
            state_data = {
                "positions": {pid: p.to_dict() for pid, p in self._positions.items()},
                "portfolio": self._portfolio.to_dict(),
                "tokens": {addr: t.to_dict() for addr, t in self._tokens.items()},
                "position_history": self._position_history[-1000:],  # Keep last 1000
                "signal_history": self._signal_history[-1000:],
                "saved_at": datetime.utcnow().isoformat(),
            }
            
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temp file first, then rename for atomic operation
            temp_path = self._persistence_path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(state_data, f, indent=2)
            temp_path.rename(self._persistence_path)
            
            logger.info(f"State saved to {self._persistence_path}")
        except Exception as e:
            logger.exception(f"Failed to save state: {e}")
    
    async def _load_state(self) -> None:
        """Load state from persistence file."""
        try:
            with open(self._persistence_path, "r") as f:
                state_data = json.load(f)
            
            # Restore portfolio
            portfolio_data = state_data.get("portfolio", {})
            self._portfolio = Portfolio(**portfolio_data)
            
            # Note: Positions are not restored as they may be stale
            # Tokens are not restored as they should be re-fetched
            
            logger.info(f"State loaded from {self._persistence_path}")
        except Exception as e:
            logger.exception(f"Failed to load state: {e}")
    
    # =========================================================================
    # Callbacks
    # =========================================================================
    
    def on(self, event_type: str, callback: Callable[[str, Any], None]) -> None:
        """Register a state change callback.
        
        Args:
            event_type: Type of state change event
            callback: Callback function(event_type, data)
        """
        self._callbacks[event_type].append(callback)
    
    def off(self, event_type: str, callback: Callable[[str, Any], None]) -> None:
        """Unregister a state change callback.
        
        Args:
            event_type: Type of state change event
            callback: Callback function to remove
        """
        if callback in self._callbacks[event_type]:
            self._callbacks[event_type].remove(callback)
    
    def _notify(self, event_type: str, data: Any) -> None:
        """Notify registered callbacks of state change."""
        for callback in self._callbacks.get(event_type, []):
            try:
                callback(event_type, data)
            except Exception as e:
                logger.exception(f"Error in state callback: {e}")
    
    # =========================================================================
    # Statistics
    # =========================================================================
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get state statistics.
        
        Returns:
            Dictionary of state statistics
        """
        async with self._lock:
            open_positions = await self.get_open_positions()
            return {
                "positions": {
                    "total": len(self._positions),
                    "open": len(open_positions),
                    "pending": len([p for p in open_positions if p.status == PositionStatus.PENDING]),
                },
                "signals": {
                    "active": len(self._signals),
                    "history": len(self._signal_history),
                },
                "tokens": {
                    "tracked": len(self._tokens),
                },
                "portfolio": self._portfolio.to_dict(),
            }


# =============================================================================
# Global State Instance
# =============================================================================

# Global state instance for application-wide state management
global_state = GlobalState(persistence_path=Path("data/state.json"))


# =============================================================================
# Export
# =============================================================================

__all__ = [
    # Enums
    "PositionStatus",
    "SignalStrength",
    
    # Data classes
    "TokenInfo",
    "Position",
    "Signal",
    "Portfolio",
    "MarketData",
    
    # State manager
    "GlobalState",
    "global_state",
]