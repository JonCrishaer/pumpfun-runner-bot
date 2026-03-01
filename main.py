"""
Pump.fun Automated Trading System - Main Application Entry Point

This module provides the main entry point for the Pump.fun trading system.
It handles application lifecycle management, signal handling, and coordinates
all system components.

Usage:
    # Run with default configuration
    python main.py
    
    # Run with specific strategy mode
    python main.py --mode aggressive
    
    # Run in paper trading mode (default)
    python main.py --paper-trading
    
    # Run with live trading (requires wallet configuration)
    python main.py --live

Environment Variables:
    TRADING_STRATEGY_MODE: Strategy mode (conservative/balanced/aggressive)
    TRADING_PAPER_TRADING: Enable paper trading (true/false)
    SOLANA_RPC_URL: Solana RPC endpoint
    WALLET_PRIVATE_KEY: Base58-encoded private key (for live trading)
    MONITOR_LOG_LEVEL: Logging level (DEBUG/INFO/WARNING/ERROR)

Example:
    >>> import asyncio
    >>> from main import TradingApplication
    >>> 
    >>> app = TradingApplication()
    >>> asyncio.run(app.run())
"""

import argparse
import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from core import (
    get_settings,
    reload_settings,
    StrategyMode,
    LogLevel,
    event_bus,
    EventPriority,
    global_state,
    Position,
    PositionStatus,
    BondingCurveUpdateEvent,
    SignalEvent,
    PositionOpenedEvent,
    PositionClosedEvent,
    GraduationEvent,
    SystemHealthEvent,
    ErrorEvent,
)
from core.config import Settings


# =============================================================================
# Logging Configuration
# =============================================================================

def setup_logging(log_level: LogLevel, log_to_file: bool = True, log_file_path: Optional[Path] = None) -> logging.Logger:
    """Configure application logging.
    
    Args:
        log_level: Logging verbosity level
        log_to_file: Whether to log to file
        log_file_path: Path to log file
        
    Returns:
        Configured logger instance
    """
    # Create logs directory if needed
    if log_to_file and log_file_path:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(log_level.value)
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level.value)
    console_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler
    if log_to_file and log_file_path:
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setLevel(log_level.value)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
    
    return logging.getLogger(__name__)


# =============================================================================
# Trading Application
# =============================================================================

class TradingApplication:
    """Main trading application controller.
    
    This class manages the application lifecycle, coordinates all components,
    and handles graceful shutdown. It uses an async/await pattern for
    efficient I/O operations.
    
    Attributes:
        settings: Application configuration
        logger: Application logger
        running: Whether the application is running
        shutdown_event: Event to signal shutdown
        tasks: List of running background tasks
    
    Example:
        >>> app = TradingApplication()
        >>> await app.initialize()
        >>> await app.run()
        >>> await app.shutdown()
    """
    
    def __init__(self, settings: Optional[Settings] = None):
        """Initialize the trading application.
        
        Args:
            settings: Optional settings instance (uses global settings if None)
        """
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(__name__)
        self.running = False
        self.shutdown_event = asyncio.Event()
        self.tasks: List[asyncio.Task] = []
        
        # Component references (will be initialized in initialize())
        self._signal_processor: Optional['SignalProcessor'] = None
        self._position_manager: Optional['PositionManager'] = None
        self._risk_manager: Optional['RiskManager'] = None
        self._monitor: Optional['SystemMonitor'] = None
        
    async def initialize(self) -> None:
        """Initialize all application components.
        
        This method sets up logging, initializes the global state,
        registers event handlers, and starts background tasks.
        """
        self.logger.info(f"Initializing {self.settings.app_name} v{self.settings.app_version}")
        self.logger.info(f"Strategy mode: {self.settings.trading.strategy_mode.value}")
        self.logger.info(f"Paper trading: {self.settings.trading.paper_trading}")
        
        # Initialize global state
        await global_state.initialize()
        
        # Register event handlers
        self._register_event_handlers()
        
        # Initialize components
        self._signal_processor = SignalProcessor(self.settings)
        self._position_manager = PositionManager(self.settings)
        self._risk_manager = RiskManager(self.settings)
        self._monitor = SystemMonitor(self.settings)
        
        await self._signal_processor.initialize()
        await self._position_manager.initialize()
        await self._risk_manager.initialize()
        await self._monitor.initialize()
        
        self.logger.info("Application initialized successfully")
    
    def _register_event_handlers(self) -> None:
        """Register event handlers for system events."""
        # Bonding curve updates
        @event_bus.on(BondingCurveUpdateEvent, priority=EventPriority.HIGH)
        async def on_bonding_curve_update(event: BondingCurveUpdateEvent) -> None:
            self.logger.debug(f"Bonding curve update: {event.token_address} @ {event.progress_pct:.2%}")
            await global_state.update_market_data(
                global_state._market_data.get(event.token_address) or type('obj', (object,), {
                    'token_address': event.token_address,
                    'price_sol': 0.0,
                    'volume_24h': event.volume_24h,
                    'holders': event.holders,
                    'bonding_curve_progress': event.progress_pct,
                })()
            )
        
        # Position events
        @event_bus.on(PositionOpenedEvent, priority=EventPriority.NORMAL)
        async def on_position_opened(event: PositionOpenedEvent) -> None:
            self.logger.info(f"Position opened: {event.position_id} for {event.token_address}")
        
        @event_bus.on(PositionClosedEvent, priority=EventPriority.NORMAL)
        async def on_position_closed(event: PositionClosedEvent) -> None:
            self.logger.info(
                f"Position closed: {event.position_id} | "
                f"PnL: {event.pnl_sol:.4f} SOL ({event.pnl_pct:+.2%}) | "
                f"Reason: {event.exit_reason}"
            )
        
        # Graduation events
        @event_bus.on(GraduationEvent, priority=EventPriority.CRITICAL)
        async def on_graduation(event: GraduationEvent) -> None:
            self.logger.info(
                f"Token graduated: {event.token_address} | "
                f"Price: {event.graduation_price:.6f} SOL | "
                f"Market Cap: {event.market_cap:.2f} SOL"
            )
            # Trigger post-graduation exit logic
            if self._position_manager:
                await self._position_manager.handle_graduation(event.token_address)
        
        # Error events
        @event_bus.on(ErrorEvent, priority=EventPriority.CRITICAL)
        async def on_error(event: ErrorEvent) -> None:
            self.logger.error(f"Error in {event.component}: {event.message}")
        
        # System health events
        @event_bus.on(SystemHealthEvent, priority=EventPriority.LOW)
        async def on_health_update(event: SystemHealthEvent) -> None:
            if event.status != "healthy":
                self.logger.warning(f"Health check: {event.component} is {event.status}: {event.message}")
    
    async def run(self) -> None:
        """Run the main application loop.
        
        This method starts all background tasks and waits for shutdown signal.
        """
        self.running = True
        self.logger.info("Starting trading application...")
        
        # Start background tasks
        self.tasks = [
            asyncio.create_task(self._signal_processor.run(), name="signal_processor"),
            asyncio.create_task(self._position_manager.run(), name="position_manager"),
            asyncio.create_task(self._risk_manager.run(), name="risk_manager"),
            asyncio.create_task(self._monitor.run(), name="system_monitor"),
            asyncio.create_task(self._cleanup_task(), name="cleanup_task"),
        ]
        
        # Wait for shutdown signal
        try:
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            self.logger.info("Main loop cancelled")
        
        self.logger.info("Shutdown signal received")
    
    async def _cleanup_task(self) -> None:
        """Background task for periodic cleanup operations."""
        while self.running:
            try:
                # Clean up expired signals
                await global_state.cleanup_expired_signals()
                
                # Save state periodically
                if global_state._persistence_path:
                    await global_state._save_state()
                
                await asyncio.sleep(60)  # Run every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.exception(f"Error in cleanup task: {e}")
                await asyncio.sleep(60)
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the application.
        
        This method stops all background tasks, closes positions if needed,
        persists state, and releases resources.
        """
        self.logger.info("Shutting down trading application...")
        self.running = False
        
        # Signal shutdown
        self.shutdown_event.set()
        
        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Shutdown components
        if self._position_manager:
            await self._position_manager.shutdown()
        if self._risk_manager:
            await self._risk_manager.shutdown()
        if self._monitor:
            await self._monitor.shutdown()
        
        # Shutdown global state
        await global_state.shutdown()
        
        self.logger.info("Application shutdown complete")
    
    def signal_shutdown(self) -> None:
        """Signal the application to shutdown.
        
        This method can be called from signal handlers or other threads.
        """
        self.logger.info("Shutdown requested")
        self.shutdown_event.set()


# =============================================================================
# Component Stubs (for demonstration)
# =============================================================================

class SignalProcessor:
    """Processes market data and generates trading signals via Bitquery.
    
    Integrates real bonding curve monitoring and runner detection.
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
        self._bonding_curve_monitor = None
    
    async def initialize(self) -> None:
        """Initialize the signal processor with real Bitquery integration."""
        try:
            from signals.bonding_curve_v2 import BondingCurveMonitorV2
            from signals.bonding_curve_mock import BondingCurveMonitorMock
            from signals.runner_detector import RunnerDetector
            
            # DEMO MODE: Use mock monitor for testing (no API key needed)
            # To use real Bitquery API, set DEMO_USE_MOCK=false in .env
            use_mock = self.settings.demo.use_mock
            
            if use_mock:
                self._bonding_curve_monitor = BondingCurveMonitorMock(
                    threshold_pct=self.settings.runner.min_progress,
                    poll_interval=10
                )
                self.logger.info("🧪 DEMO MODE: Using MOCK bonding curve monitor (testing, no API needed)")
            else:
                self._bonding_curve_monitor = BondingCurveMonitorV2(
                    api_key=self.settings.bitquery.api_key,
                    threshold_pct=self.settings.runner.min_progress,
                    poll_interval=10
                )
                self.logger.info("✅ LIVE MODE: Using real Bitquery API for bonding curve monitoring")
            
            # Initialize runner detector
            try:
                self._runner_detector = RunnerDetector(settings=self.settings)
            except:
                # Fallback if RunnerDetector not available
                self._runner_detector = None
            
            # Register event handler for bonding curve updates
            @event_bus.on(BondingCurveUpdateEvent)
            async def on_bonding_curve_update(event: BondingCurveUpdateEvent):
                """Handle bonding curve updates and emit signals."""
                self.logger.info(f"📊 Bonding curve update: {event.token_address[:8]}... @ {event.progress_pct*100:.1f}%")
                
                # For now, emit all high-progress tokens as signals
                # In production, would run through runner detector
                if event.progress_pct >= self.settings.runner.min_progress:
                    self.logger.info(f"🟢 RUNNER DETECTED: {event.token_address} @ {event.progress_pct*100:.1f}%")
                    await event_bus.emit(SignalEvent(
                        token_address=event.token_address,
                        signal_type="entry",
                        confidence=min(0.95, event.progress_pct)  # Higher progress = higher confidence
                    ))
            
            self.logger.info("✅ Signal processor initialized with Bitquery v2 polling")
        except Exception as e:
            self.logger.error(f"Failed to initialize signal processor: {e}", exc_info=True)
            raise
    
    async def run(self) -> None:
        """Run the signal processing loop with real data."""
        self.running = True
        self.logger.info("Signal processor started - monitoring Bitquery for runners")
        
        try:
            if self._bonding_curve_monitor:
                await self._bonding_curve_monitor.start()
            
            while self.running:
                await asyncio.sleep(1)  # Keep task alive
        except asyncio.CancelledError:
            self.logger.info("Signal processor cancelled")
        finally:
            self.running = False
            if self._bonding_curve_monitor:
                await self._bonding_curve_monitor.stop()
    
    async def shutdown(self) -> None:
        """Shutdown the signal processor."""
        self.running = False
        if self._bonding_curve_monitor:
            await self._bonding_curve_monitor.stop()
        self.logger.info("Signal processor shutdown")


class PositionManager:
    """Manages trading positions and execution.
    
    This is a stub implementation. In production, this would:
    - Execute buy/sell orders
    - Track position PnL
    - Handle take-profit and stop-loss
    - Manage post-graduation exits
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
    
    async def initialize(self) -> None:
        """Initialize the position manager."""
        self.logger.info("Position manager initialized")
    
    async def run(self) -> None:
        """Run the position management loop."""
        self.running = True
        self.logger.info("Position manager started")
        
        try:
            while self.running:
                # In production, this would:
                # - Monitor open positions
                # - Check stop-loss and take-profit levels
                # - Execute exit orders
                # - Update position state
                
                await asyncio.sleep(2)  # Check every 2 seconds
        except asyncio.CancelledError:
            self.logger.info("Position manager cancelled")
        finally:
            self.running = False
    
    async def handle_graduation(self, token_address: str) -> None:
        """Handle token graduation event.
        
        Args:
            token_address: Token that graduated
        """
        self.logger.info(f"Handling graduation for {token_address}")
        # In production, trigger rapid exit logic
    
    async def shutdown(self) -> None:
        """Shutdown the position manager."""
        self.running = False
        self.logger.info("Position manager shutdown")


class RiskManager:
    """Manages risk and position sizing.
    
    This is a stub implementation. In production, this would:
    - Calculate position sizes using Kelly criterion
    - Monitor portfolio exposure
    - Enforce daily loss limits
    - Track drawdown
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
    
    async def initialize(self) -> None:
        """Initialize the risk manager."""
        self.logger.info("Risk manager initialized")
    
    async def run(self) -> None:
        """Run the risk management loop."""
        self.running = True
        self.logger.info("Risk manager started")
        
        try:
            while self.running:
                # In production, this would:
                # - Check portfolio exposure
                # - Monitor daily loss limits
                # - Calculate position sizes
                # - Update risk metrics
                
                await asyncio.sleep(10)  # Check every 10 seconds
        except asyncio.CancelledError:
            self.logger.info("Risk manager cancelled")
        finally:
            self.running = False
    
    async def shutdown(self) -> None:
        """Shutdown the risk manager."""
        self.running = False
        self.logger.info("Risk manager shutdown")


class SystemMonitor:
    """Monitors system health and performance.
    
    This is a stub implementation. In production, this would:
    - Monitor RPC connection health
    - Track system performance metrics
    - Report health status
    - Alert on issues
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
    
    async def initialize(self) -> None:
        """Initialize the system monitor."""
        self.logger.info("System monitor initialized")
    
    async def run(self) -> None:
        """Run the monitoring loop."""
        self.running = True
        self.logger.info("System monitor started")
        
        try:
            while self.running:
                # Emit health status
                await event_bus.emit(SystemHealthEvent(
                    component="system",
                    status="healthy",
                    message="System is operating normally",
                    source="monitor"
                ))
                
                await asyncio.sleep(self.settings.monitoring.health_check_interval_sec)
        except asyncio.CancelledError:
            self.logger.info("System monitor cancelled")
        finally:
            self.running = False
    
    async def shutdown(self) -> None:
        """Shutdown the system monitor."""
        self.running = False
        self.logger.info("System monitor shutdown")


# =============================================================================
# Signal Handlers
# =============================================================================

def setup_signal_handlers(app: TradingApplication) -> None:
    """Setup OS signal handlers for graceful shutdown.
    
    Args:
        app: Trading application instance
    """
    def signal_handler(sig, frame):
        app.signal_shutdown()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


# =============================================================================
# CLI Entry Point
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.
    
    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Pump.fun Automated Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default configuration (paper trading, balanced mode)
  python main.py
  
  # Run in aggressive mode
  python main.py --mode aggressive
  
  # Run with live trading (requires wallet configuration)
  python main.py --live
  
  # Run with custom RPC endpoint
  python main.py --rpc-url https://api.mainnet-beta.solana.com
        """
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        choices=["conservative", "balanced", "aggressive"],
        default=None,
        help="Strategy mode (overrides environment variable)"
    )
    
    parser.add_argument(
        "--paper-trading",
        action="store_true",
        dest="paper_trading",
        default=None,
        help="Enable paper trading mode (default)"
    )
    
    parser.add_argument(
        "--live",
        action="store_false",
        dest="paper_trading",
        help="Enable live trading (requires wallet configuration)"
    )
    
    parser.add_argument(
        "--rpc-url",
        type=str,
        default=None,
        help="Solana RPC endpoint URL"
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default=".env",
        help="Path to configuration file (default: .env)"
    )
    
    return parser.parse_args()


def apply_cli_args(args: argparse.Namespace) -> None:
    """Apply command-line arguments to settings.
    
    Args:
        args: Parsed command-line arguments
    """
    # Note: In a real implementation, you might want to modify
    # environment variables or use a different approach to override settings
    import os
    
    if args.mode:
        os.environ["TRADING_STRATEGY_MODE"] = args.mode
    
    if args.paper_trading is not None:
        os.environ["TRADING_PAPER_TRADING"] = str(args.paper_trading).lower()
    
    if args.rpc_url:
        os.environ["SOLANA_RPC_URL"] = args.rpc_url
    
    if args.log_level:
        os.environ["MONITOR_LOG_LEVEL"] = args.log_level


async def main() -> int:
    """Main entry point.
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Parse command-line arguments
    args = parse_args()
    apply_cli_args(args)
    
    # Reload settings with CLI overrides
    settings = reload_settings()
    
    # Setup logging
    logger = setup_logging(
        log_level=settings.monitoring.log_level,
        log_to_file=settings.monitoring.log_to_file,
        log_file_path=settings.monitoring.log_file_path
    )
    
    logger.info("=" * 60)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info("=" * 60)
    
    # Validate configuration
    if not settings.trading.paper_trading and not settings.wallet.private_key:
        logger.error("Live trading requires WALLET_PRIVATE_KEY to be set")
        return 1
    
    # Create and run application
    app = TradingApplication(settings)
    setup_signal_handlers(app)
    
    try:
        await app.initialize()
        await app.run()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1
    finally:
        await app.shutdown()
    
    logger.info("Application exited normally")
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)