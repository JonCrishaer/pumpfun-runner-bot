"""
Event Bus System for Pump.fun Trading System

This module provides an asynchronous event bus for decoupled component communication.
It supports typed events, prioritized handlers, and both synchronous and asynchronous
event processing patterns.

Example Usage:
    >>> from core.events import event_bus, BondingCurveUpdateEvent
    >>> 
    >>> # Subscribe to events
    >>> @event_bus.on(BondingCurveUpdateEvent)
    >>> async def handle_update(event):
    >>>     print(f"Bonding curve: {event.progress_pct:.2%}")
    >>>
    >>> # Emit events
    >>> await event_bus.emit(BondingCurveUpdateEvent(
    >>>     token_address="abc123",
    >>>     progress_pct=0.85
    >>> ))
"""

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import (
    Any, Callable, Coroutine, Dict, Generic, List, Optional, 
    Set, Type, TypeVar, Union, get_type_hints
)
from collections import defaultdict
import uuid

# Configure logging
logger = logging.getLogger(__name__)


# Event type variable for generic handlers
T = TypeVar("T", bound="BaseEvent")


class EventPriority(Enum):
    """Priority levels for event handlers.
    
    Higher priority handlers are executed first.
    """
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass(frozen=True)
class BaseEvent(ABC):
    """Base class for all events in the system.
    
    All events must inherit from this class and should be immutable
    (frozen dataclasses) to ensure thread safety.
    
    Attributes:
        event_id: Unique identifier for this event instance
        timestamp: UTC timestamp when the event was created
        source: Component or service that generated the event
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = field(default="unknown")
    
    @property
    @abstractmethod
    def event_type(self) -> str:
        """Return the event type identifier."""
        pass


# =============================================================================
# Trading Events
# =============================================================================

@dataclass(frozen=True)
class BondingCurveUpdateEvent(BaseEvent):
    """Emitted when a token's bonding curve progress changes.
    
    Attributes:
        token_address: Token mint address
        progress_pct: Current bonding curve progress (0.0 - 1.0)
        sol_raised: Amount of SOL raised in bonding curve
        target_sol: Target SOL amount for graduation
        holders: Current number of token holders
        volume_24h: 24-hour trading volume
    """
    token_address: str = ""
    progress_pct: float = 0.0
    sol_raised: float = 0.0
    target_sol: float = 0.0
    holders: int = 0
    volume_24h: float = 0.0
    
    @property
    def event_type(self) -> str:
        return "bonding_curve_update"


@dataclass(frozen=True)
class VolumeSpikeEvent(BaseEvent):
    """Emitted when a token experiences significant volume acceleration.
    
    Attributes:
        token_address: Token mint address
        current_volume: Current volume measurement
        average_volume: Historical average volume
        multiplier: Volume acceleration multiplier
        timeframe_minutes: Measurement timeframe
    """
    token_address: str = ""
    current_volume: float = 0.0
    average_volume: float = 0.0
    multiplier: float = 0.0
    timeframe_minutes: int = 0
    
    @property
    def event_type(self) -> str:
        return "volume_spike"


@dataclass(frozen=True)
class SignalEvent(BaseEvent):
    """Emitted when a trading signal is generated.
    
    Attributes:
        token_address: Token mint address
        signal_type: Type of signal (entry, exit, hold)
        confidence: Signal confidence score (0.0 - 1.0)
        signals: Dictionary of individual signal components
        metadata: Additional signal context
    """
    token_address: str = ""
    signal_type: str = "hold"  # "entry", "exit", "hold"
    confidence: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> str:
        return f"signal_{self.signal_type}"


@dataclass(frozen=True)
class PositionOpenedEvent(BaseEvent):
    """Emitted when a new trading position is opened.
    
    Attributes:
        position_id: Unique position identifier
        token_address: Token mint address
        entry_price: Entry price in SOL
        size_sol: Position size in SOL
        size_tokens: Position size in tokens
        stop_loss: Stop loss price
        take_profits: List of take-profit levels
    """
    position_id: str = ""
    token_address: str = ""
    entry_price: float = 0.0
    size_sol: float = 0.0
    size_tokens: float = 0.0
    stop_loss: float = 0.0
    take_profits: List[Dict[str, float]] = field(default_factory=list)
    
    @property
    def event_type(self) -> str:
        return "position_opened"


@dataclass(frozen=True)
class PositionClosedEvent(BaseEvent):
    """Emitted when a trading position is closed.
    
    Attributes:
        position_id: Unique position identifier
        token_address: Token mint address
        exit_price: Exit price in SOL
        pnl_sol: Profit/loss in SOL
        pnl_pct: Profit/loss percentage
        exit_reason: Reason for position closure
        duration_seconds: Position holding duration
    """
    position_id: str = ""
    token_address: str = ""
    exit_price: float = 0.0
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    duration_seconds: float = 0.0
    
    @property
    def event_type(self) -> str:
        return "position_closed"


@dataclass(frozen=True)
class PositionUpdateEvent(BaseEvent):
    """Emitted when a position's unrealized PnL changes significantly.
    
    Attributes:
        position_id: Unique position identifier
        token_address: Token mint address
        current_price: Current token price
        unrealized_pnl_sol: Unrealized PnL in SOL
        unrealized_pnl_pct: Unrealized PnL percentage
        highest_pnl_pct: Highest PnL reached (for trailing stops)
    """
    position_id: str = ""
    token_address: str = ""
    current_price: float = 0.0
    unrealized_pnl_sol: float = 0.0
    unrealized_pnl_pct: float = 0.0
    highest_pnl_pct: float = 0.0
    
    @property
    def event_type(self) -> str:
        return "position_update"


@dataclass(frozen=True)
class GraduationEvent(BaseEvent):
    """Emitted when a token graduates from Pump.fun.
    
    Attributes:
        token_address: Token mint address
        graduation_price: Price at graduation
        market_cap: Market capitalization at graduation
        liquidity_sol: Liquidity in SOL at graduation
        holders: Number of holders at graduation
    """
    token_address: str = ""
    graduation_price: float = 0.0
    market_cap: float = 0.0
    liquidity_sol: float = 0.0
    holders: int = 0
    
    @property
    def event_type(self) -> str:
        return "graduation"


@dataclass(frozen=True)
class WalletActivityEvent(BaseEvent):
    """Emitted when significant wallet activity is detected.
    
    Attributes:
        token_address: Token mint address
        wallet_address: Wallet that performed the action
        wallet_type: Type of wallet (dev, alpha, whale)
        action: Action performed (buy, sell, transfer)
        amount_sol: Transaction amount in SOL
        amount_tokens: Transaction amount in tokens
    """
    token_address: str = ""
    wallet_address: str = ""
    wallet_type: str = ""
    action: str = ""
    amount_sol: float = 0.0
    amount_tokens: float = 0.0
    
    @property
    def event_type(self) -> str:
        return f"wallet_{self.wallet_type}_{self.action}"


@dataclass(frozen=True)
class SystemHealthEvent(BaseEvent):
    """Emitted for system health status updates.
    
    Attributes:
        component: Component reporting health status
        status: Health status (healthy, degraded, unhealthy)
        message: Health status message
        metrics: Optional health metrics
    """
    component: str = ""
    status: str = "unknown"  # "healthy", "degraded", "unhealthy"
    message: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> str:
        return f"health_{self.component}"


@dataclass(frozen=True)
class ErrorEvent(BaseEvent):
    """Emitted when an error occurs in the system.
    
    Attributes:
        component: Component where the error occurred
        error_type: Type of error
        message: Error message
        exception: Optional exception object
        context: Additional error context
    """
    component: str = ""
    error_type: str = ""
    message: str = ""
    exception: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> str:
        return f"error_{self.component}"


# =============================================================================
# Event Handler Types
# =============================================================================

# Type alias for event handler functions
EventHandler = Callable[[T], Coroutine[Any, Any, None]]
SyncEventHandler = Callable[[T], None]


@dataclass
class HandlerRegistration:
    """Internal class to track handler registrations."""
    handler: Callable[..., Any]
    priority: EventPriority
    event_type: Type[BaseEvent]
    is_async: bool


# =============================================================================
# Event Bus Implementation
# =============================================================================

class EventBus:
    """Asynchronous event bus for decoupled component communication.
    
    The event bus allows components to publish and subscribe to events
    without direct coupling. It supports prioritized handlers, both sync
    and async handlers, and provides error isolation between handlers.
    
    Example:
        >>> bus = EventBus()
        >>>
        >>> @bus.on(BondingCurveUpdateEvent, priority=EventPriority.HIGH)
        >>> async def on_curve_update(event):
        >>>     print(f"Progress: {event.progress_pct:.2%}")
        >>>
        >>> await bus.emit(BondingCurveUpdateEvent(
        >>>     token_address="abc",
        >>>     progress_pct=0.85,
        >>>     sol_raised=50.0,
        >>>     target_sol=60.0,
        >>>     holders=100,
        >>>     volume_24h=10000.0
        >>> ))
    """
    
    def __init__(self):
        """Initialize the event bus."""
        # Map event types to their handler registrations
        self._handlers: Dict[Type[BaseEvent], List[HandlerRegistration]] = defaultdict(list)
        # Global error handler
        self._error_handler: Optional[Callable[[BaseEvent, Exception], None]] = None
        # Event statistics
        self._stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"emitted": 0, "handled": 0, "errors": 0})
        # Running state
        self._running = False
        
    def on(
        self, 
        event_type: Type[T], 
        priority: EventPriority = EventPriority.NORMAL
    ) -> Callable[[EventHandler[T]], EventHandler[T]]:
        """Decorator to register an event handler.
        
        Args:
            event_type: The event type to subscribe to
            priority: Handler execution priority (default: NORMAL)
            
        Returns:
            Decorator function that registers the handler
            
        Example:
            >>> @event_bus.on(BondingCurveUpdateEvent, priority=EventPriority.HIGH)
            >>> async def handle_update(event):
            >>>     print(f"Progress: {event.progress_pct:.2%}")
        """
        def decorator(handler: EventHandler[T]) -> EventHandler[T]:
            self.register(event_type, handler, priority)
            return handler
        return decorator
    
    def register(
        self, 
        event_type: Type[T], 
        handler: Union[EventHandler[T], SyncEventHandler[T]],
        priority: EventPriority = EventPriority.NORMAL
    ) -> None:
        """Register an event handler.
        
        Args:
            event_type: The event type to subscribe to
            handler: Handler function (sync or async)
            priority: Handler execution priority
            
        Example:
            >>> async def handler(event):
            >>>     print(event)
            >>> event_bus.register(BondingCurveUpdateEvent, handler)
        """
        is_async = inspect.iscoroutinefunction(handler)
        registration = HandlerRegistration(
            handler=handler,
            priority=priority,
            event_type=event_type,
            is_async=is_async
        )
        
        # Insert in priority order (lower priority value = higher priority)
        handlers = self._handlers[event_type]
        insert_idx = len(handlers)
        for i, reg in enumerate(handlers):
            if reg.priority.value > priority.value:
                insert_idx = i
                break
        handlers.insert(insert_idx, registration)
        
        logger.debug(f"Registered handler for {event_type.__name__} with priority {priority.name}")
    
    def unregister(
        self, 
        event_type: Type[T], 
        handler: Union[EventHandler[T], SyncEventHandler[T]]
    ) -> bool:
        """Unregister an event handler.
        
        Args:
            event_type: The event type to unsubscribe from
            handler: Handler function to remove
            
        Returns:
            True if handler was found and removed, False otherwise
        """
        handlers = self._handlers.get(event_type, [])
        for i, reg in enumerate(handlers):
            if reg.handler == handler:
                handlers.pop(i)
                logger.debug(f"Unregistered handler for {event_type.__name__}")
                return True
        return False
    
    async def emit(self, event: BaseEvent) -> None:
        """Emit an event to all registered handlers.
        
        Handlers are executed in priority order. Errors in one handler
        do not affect other handlers.
        
        Args:
            event: The event to emit
            
        Example:
            >>> await event_bus.emit(BondingCurveUpdateEvent(...))
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        
        self._stats[event.event_type]["emitted"] += 1
        
        if not handlers:
            logger.debug(f"No handlers registered for {event_type.__name__}")
            return
        
        logger.debug(f"Emitting {event_type.__name__} to {len(handlers)} handlers")
        
        # Execute handlers with error isolation
        for registration in handlers:
            try:
                if registration.is_async:
                    await registration.handler(event)
                else:
                    registration.handler(event)
                self._stats[event.event_type]["handled"] += 1
            except Exception as e:
                self._stats[event.event_type]["errors"] += 1
                logger.exception(f"Error in handler for {event_type.__name__}: {e}")
                
                # Call global error handler if set
                if self._error_handler:
                    try:
                        self._error_handler(event, e)
                    except Exception:
                        logger.exception("Error in error handler")
    
    def set_error_handler(
        self, 
        handler: Callable[[BaseEvent, Exception], None]
    ) -> None:
        """Set a global error handler for handler exceptions.
        
        Args:
            handler: Function to call when a handler raises an exception
        """
        self._error_handler = handler
    
    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Get event emission statistics.
        
        Returns:
            Dictionary with event type statistics
        """
        return dict(self._stats)
    
    def clear_stats(self) -> None:
        """Clear event statistics."""
        self._stats.clear()
    
    def get_handler_count(self, event_type: Optional[Type[T]] = None) -> int:
        """Get the number of registered handlers.
        
        Args:
            event_type: Optional event type to count handlers for
            
        Returns:
            Number of registered handlers
        """
        if event_type:
            return len(self._handlers.get(event_type, []))
        return sum(len(handlers) for handlers in self._handlers.values())
    
    def clear_handlers(self, event_type: Optional[Type[T]] = None) -> None:
        """Clear all handlers (or handlers for a specific event type).
        
        Args:
            event_type: Optional event type to clear handlers for
        """
        if event_type:
            self._handlers[event_type].clear()
            logger.debug(f"Cleared handlers for {event_type.__name__}")
        else:
            self._handlers.clear()
            logger.debug("Cleared all handlers")


# =============================================================================
# Global Event Bus Instance
# =============================================================================

# Global event bus for application-wide event communication
event_bus = EventBus()


# =============================================================================
# Event Builder Utilities
# =============================================================================

class EventBuilder:
    """Utility class for building common events with validation."""
    
    @staticmethod
    def bonding_curve_update(
        token_address: str,
        progress_pct: float,
        sol_raised: float,
        target_sol: float,
        holders: int,
        volume_24h: float,
        source: str = "monitor"
    ) -> BondingCurveUpdateEvent:
        """Build a BondingCurveUpdateEvent with validation.
        
        Args:
            token_address: Token mint address
            progress_pct: Progress percentage (0.0 - 1.0)
            sol_raised: SOL raised in bonding curve
            target_sol: Target SOL for graduation
            holders: Number of holders
            volume_24h: 24h volume
            source: Event source
            
        Returns:
            Validated BondingCurveUpdateEvent
            
        Raises:
            ValueError: If validation fails
        """
        if not (0.0 <= progress_pct <= 1.0):
            raise ValueError(f"progress_pct must be between 0.0 and 1.0, got {progress_pct}")
        if sol_raised < 0 or target_sol <= 0:
            raise ValueError("sol_raised must be >= 0 and target_sol must be > 0")
        
        return BondingCurveUpdateEvent(
            token_address=token_address,
            progress_pct=progress_pct,
            sol_raised=sol_raised,
            target_sol=target_sol,
            holders=holders,
            volume_24h=volume_24h,
            source=source
        )
    
    @staticmethod
    def signal(
        token_address: str,
        signal_type: str,
        confidence: float,
        signals: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "signal_engine"
    ) -> SignalEvent:
        """Build a SignalEvent with validation.
        
        Args:
            token_address: Token mint address
            signal_type: Type of signal (entry, exit, hold)
            confidence: Confidence score (0.0 - 1.0)
            signals: Individual signal components
            metadata: Additional context
            source: Event source
            
        Returns:
            Validated SignalEvent
        """
        if signal_type not in ("entry", "exit", "hold"):
            raise ValueError(f"signal_type must be 'entry', 'exit', or 'hold', got {signal_type}")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence}")
        
        return SignalEvent(
            token_address=token_address,
            signal_type=signal_type,
            confidence=confidence,
            signals=signals or {},
            metadata=metadata or {},
            source=source
        )


# =============================================================================
# Export
# =============================================================================

__all__ = [
    # Event bus
    "EventBus",
    "event_bus",
    "EventPriority",
    "HandlerRegistration",
    
    # Event types
    "BaseEvent",
    "BondingCurveUpdateEvent",
    "VolumeSpikeEvent",
    "SignalEvent",
    "PositionOpenedEvent",
    "PositionClosedEvent",
    "PositionUpdateEvent",
    "GraduationEvent",
    "WalletActivityEvent",
    "SystemHealthEvent",
    "ErrorEvent",
    
    # Utilities
    "EventBuilder",
]