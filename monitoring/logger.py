"""
Structured Logging Configuration for Pump.fun Trading Bot
Provides JSON-formatted logs with different log levels and file separation
"""

import logging
import json
import sys
import os
from datetime import datetime
from typing import Dict, Any, Optional
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pythonjsonlogger import jsonlogger
from enum import Enum
import threading
from dataclasses import dataclass, asdict


class LogLevel(Enum):
    """Log levels matching Python logging"""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class LogCategory(Enum):
    """Log categories for file separation"""
    SIGNALS = "signals"
    EXECUTION = "execution"
    ERRORS = "errors"
    PNL = "pnl"
    SYSTEM = "system"
    PERFORMANCE = "performance"
    SECURITY = "security"


@dataclass
class LogEntry:
    """Structured log entry"""
    timestamp: str
    level: str
    category: str
    message: str
    source: str
    context: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'level': self.level,
            'category': self.category,
            'message': self.message,
            'source': self.source,
            'context': self.context
        }


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields"""
    
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        
        # Add timestamp
        log_record['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        
        # Add log level
        log_record['level'] = record.levelname
        
        # Add logger name
        log_record['logger'] = record.name
        
        # Add source file and line
        log_record['source'] = f"{record.filename}:{record.lineno}"
        
        # Add function name
        log_record['function'] = record.funcName
        
        # Add thread info
        log_record['thread'] = record.thread
        log_record['thread_name'] = record.threadName
        
        # Add process info
        log_record['process'] = record.process
        
        # Add category if present
        if hasattr(record, 'category'):
            log_record['category'] = record.category
        else:
            log_record['category'] = 'general'
        
        # Add extra context if present
        if hasattr(record, 'context'):
            log_record['context'] = record.context
        
        # Remove unnecessary fields
        for key in ['name', 'msg', 'args', 'exc_info', 'exc_text', 'stack_info']:
            log_record.pop(key, None)


class CategoryFilter(logging.Filter):
    """Filter logs by category"""
    
    def __init__(self, category: LogCategory):
        super().__init__()
        self.category = category
    
    def filter(self, record):
        if hasattr(record, 'category'):
            return record.category == self.category.value
        return self.category == LogCategory.SYSTEM


class TradingLogger:
    """
    Main logging class for trading system
    Creates separate log files for different categories
    """
    
    def __init__(self, log_dir: str = "logs", 
                 console_level: int = logging.INFO,
                 file_level: int = logging.DEBUG,
                 max_bytes: int = 10 * 1024 * 1024,  # 10MB
                 backup_count: int = 5):
        """
        Initialize trading logger
        
        Args:
            log_dir: Directory for log files
            console_level: Minimum level for console output
            file_level: Minimum level for file output
            max_bytes: Maximum bytes per log file
            backup_count: Number of backup files to keep
        """
        self.log_dir = log_dir
        self.console_level = console_level
        self.file_level = file_level
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        
        # Create log directory
        os.makedirs(log_dir, exist_ok=True)
        
        # Create subdirectories for categories
        for category in LogCategory:
            os.makedirs(os.path.join(log_dir, category.value), exist_ok=True)
        
        # Initialize loggers
        self.loggers: Dict[str, logging.Logger] = {}
        self._init_loggers()
        
        # Lock for thread safety
        self._lock = threading.Lock()
    
    def _init_loggers(self):
        """Initialize all loggers"""
        # Main logger
        self.main_logger = self._create_logger('pumpfun_trader', 'main.log')
        
        # Category-specific loggers
        self.signal_logger = self._create_category_logger(LogCategory.SIGNALS)
        self.execution_logger = self._create_category_logger(LogCategory.EXECUTION)
        self.error_logger = self._create_category_logger(LogCategory.ERRORS)
        self.pnl_logger = self._create_category_logger(LogCategory.PNL)
        self.system_logger = self._create_category_logger(LogCategory.SYSTEM)
        self.performance_logger = self._create_category_logger(LogCategory.PERFORMANCE)
        self.security_logger = self._create_category_logger(LogCategory.SECURITY)
        
        # Store references
        self.loggers = {
            'main': self.main_logger,
            'signals': self.signal_logger,
            'execution': self.execution_logger,
            'errors': self.error_logger,
            'pnl': self.pnl_logger,
            'system': self.system_logger,
            'performance': self.performance_logger,
            'security': self.security_logger
        }
    
    def _create_logger(self, name: str, filename: str) -> logging.Logger:
        """Create a logger with both console and file handlers"""
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.handlers = []  # Clear existing handlers
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.console_level)
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
        
        # File handler with JSON formatting
        log_path = os.path.join(self.log_dir, filename)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        file_handler.setLevel(self.file_level)
        json_formatter = CustomJsonFormatter()
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)
        
        return logger
    
    def _create_category_logger(self, category: LogCategory) -> logging.Logger:
        """Create a category-specific logger"""
        logger = logging.getLogger(f'pumpfun_trader.{category.value}')
        logger.setLevel(logging.DEBUG)
        logger.handlers = []
        
        # Only add file handler for category logs (no console)
        log_path = os.path.join(self.log_dir, category.value, f'{category.value}.log')
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count
        )
        file_handler.setLevel(self.file_level)
        json_formatter = CustomJsonFormatter()
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)
        
        return logger
    
    def _log(self, logger: logging.Logger, level: int, message: str,
             category: LogCategory = None, context: Dict[str, Any] = None,
             exc_info: bool = False):
        """Internal log method with extra fields"""
        with self._lock:
            extra = {}
            if category:
                extra['category'] = category.value
            if context:
                extra['context'] = context
            
            logger.log(level, message, extra=extra, exc_info=exc_info)
    
    # General logging methods
    def debug(self, message: str, category: LogCategory = None, 
              context: Dict[str, Any] = None):
        """Log debug message"""
        self._log(self.main_logger, logging.DEBUG, message, category, context)
    
    def info(self, message: str, category: LogCategory = None,
             context: Dict[str, Any] = None):
        """Log info message"""
        self._log(self.main_logger, logging.INFO, message, category, context)
    
    def warning(self, message: str, category: LogCategory = None,
                context: Dict[str, Any] = None):
        """Log warning message"""
        self._log(self.main_logger, logging.WARNING, message, category, context)
    
    def error(self, message: str, category: LogCategory = None,
              context: Dict[str, Any] = None, exc_info: bool = True):
        """Log error message"""
        self._log(self.main_logger, logging.ERROR, message, category, context, exc_info)
        # Also log to error category
        self._log(self.error_logger, logging.ERROR, message, LogCategory.ERRORS, context, exc_info)
    
    def critical(self, message: str, category: LogCategory = None,
                 context: Dict[str, Any] = None, exc_info: bool = True):
        """Log critical message"""
        self._log(self.main_logger, logging.CRITICAL, message, category, context, exc_info)
        # Also log to error category
        self._log(self.error_logger, logging.CRITICAL, message, LogCategory.ERRORS, context, exc_info)
    
    # Specialized logging methods
    def log_signal(self, token_symbol: str, signal_type: str, confidence: float,
                   factors: list, details: Dict[str, Any] = None):
        """
        Log trading signal
        
        Args:
            token_symbol: Token symbol
            signal_type: Type of signal (BUY, SELL, etc.)
            confidence: Signal confidence (0-1)
            factors: List of trigger factors
            details: Additional signal details
        """
        context = {
            'token_symbol': token_symbol,
            'signal_type': signal_type,
            'confidence': confidence,
            'factors': factors
        }
        if details:
            context.update(details)
        
        message = f"Signal: {signal_type} {token_symbol} (confidence: {confidence:.2%})"
        self._log(self.signal_logger, logging.INFO, message, LogCategory.SIGNALS, context)
        self._log(self.main_logger, logging.INFO, message, LogCategory.SIGNALS, context)
    
    def log_execution(self, token_symbol: str, action: str, amount: float,
                      price: float, tx_hash: str = None, 
                      status: str = 'success', details: Dict[str, Any] = None):
        """
        Log trade execution
        
        Args:
            token_symbol: Token symbol
            action: Action type (BUY, SELL)
            amount: Trade amount
            price: Execution price
            tx_hash: Transaction hash
            status: Execution status
            details: Additional execution details
        """
        context = {
            'token_symbol': token_symbol,
            'action': action,
            'amount': amount,
            'price': price,
            'tx_hash': tx_hash,
            'status': status
        }
        if details:
            context.update(details)
        
        message = f"Execution: {action} {amount} {token_symbol} @ {price}"
        self._log(self.execution_logger, logging.INFO, message, LogCategory.EXECUTION, context)
        self._log(self.main_logger, logging.INFO, message, LogCategory.EXECUTION, context)
    
    def log_pnl(self, token_symbol: str, entry_price: float, exit_price: float,
                quantity: float, pnl_usd: float, pnl_pct: float,
                holding_time: str = None, details: Dict[str, Any] = None):
        """
        Log P&L update
        
        Args:
            token_symbol: Token symbol
            entry_price: Entry price
            exit_price: Exit price (or current price for unrealized)
            quantity: Position quantity
            pnl_usd: P&L in USD
            pnl_pct: P&L percentage
            holding_time: Position holding time
            details: Additional P&L details
        """
        context = {
            'token_symbol': token_symbol,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'quantity': quantity,
            'pnl_usd': pnl_usd,
            'pnl_pct': pnl_pct,
            'holding_time': holding_time
        }
        if details:
            context.update(details)
        
        message = f"P&L: {token_symbol} {pnl_usd:+.2f} USD ({pnl_pct:+.2f}%)"
        self._log(self.pnl_logger, logging.INFO, message, LogCategory.PNL, context)
        self._log(self.main_logger, logging.INFO, message, LogCategory.PNL, context)
    
    def log_performance(self, operation: str, duration_ms: float,
                        details: Dict[str, Any] = None):
        """
        Log performance metrics
        
        Args:
            operation: Operation name
            duration_ms: Duration in milliseconds
            details: Additional performance details
        """
        context = {
            'operation': operation,
            'duration_ms': duration_ms
        }
        if details:
            context.update(details)
        
        message = f"Performance: {operation} took {duration_ms:.2f}ms"
        self._log(self.performance_logger, logging.INFO, message, LogCategory.PERFORMANCE, context)
    
    def log_security(self, event: str, severity: str, details: Dict[str, Any] = None):
        """
        Log security event
        
        Args:
            event: Security event type
            severity: Event severity
            details: Additional security details
        """
        context = {
            'event': event,
            'severity': severity
        }
        if details:
            context.update(details)
        
        message = f"Security: {event} (severity: {severity})"
        level = logging.WARNING if severity == 'medium' else logging.ERROR if severity == 'high' else logging.CRITICAL
        self._log(self.security_logger, level, message, LogCategory.SECURITY, context)
        self._log(self.main_logger, level, message, LogCategory.SECURITY, context)
    
    def log_system(self, event: str, details: Dict[str, Any] = None):
        """
        Log system event
        
        Args:
            event: System event description
            details: Additional system details
        """
        context = details or {}
        message = f"System: {event}"
        self._log(self.system_logger, logging.INFO, message, LogCategory.SYSTEM, context)
    
    def get_recent_logs(self, category: LogCategory = None, 
                       level: LogLevel = None, 
                       limit: int = 100) -> list:
        """
        Get recent log entries
        
        Args:
            category: Filter by category
            level: Filter by level
            limit: Maximum number of entries
            
        Returns:
            List of log entries
        """
        logs = []
        
        # Determine which log file to read
        if category:
            log_file = os.path.join(self.log_dir, category.value, f'{category.value}.log')
        else:
            log_file = os.path.join(self.log_dir, 'main.log')
        
        if not os.path.exists(log_file):
            return logs
        
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                
            for line in lines[-limit:]:
                try:
                    entry = json.loads(line)
                    if level and entry.get('level') != level.name:
                        continue
                    logs.append(entry)
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            self.error(f"Failed to read logs: {e}")
        
        return logs


# Context manager for performance logging
class PerformanceTimer:
    """Context manager for timing operations"""
    
    def __init__(self, logger: TradingLogger, operation: str, 
                 details: Dict[str, Any] = None):
        self.logger = logger
        self.operation = operation
        self.details = details or {}
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds() * 1000
        
        if exc_type:
            self.details['error'] = str(exc_val)
            self.logger.error(
                f"Performance: {self.operation} failed after {duration:.2f}ms",
                context=self.details
            )
        else:
            self.logger.log_performance(self.operation, duration, self.details)


# Global logger instance
_logger_instance: Optional[TradingLogger] = None


def init_logging(log_dir: str = "logs", console_level: int = logging.INFO,
                 file_level: int = logging.DEBUG) -> TradingLogger:
    """
    Initialize global logging instance
    
    Args:
        log_dir: Directory for log files
        console_level: Console log level
        file_level: File log level
        
    Returns:
        TradingLogger instance
    """
    global _logger_instance
    _logger_instance = TradingLogger(log_dir, console_level, file_level)
    return _logger_instance


def get_logger() -> TradingLogger:
    """Get global logger instance"""
    if _logger_instance is None:
        return init_logging()
    return _logger_instance


# Convenience functions
def debug(message: str, category: LogCategory = None, context: Dict[str, Any] = None):
    """Debug log shortcut"""
    get_logger().debug(message, category, context)


def info(message: str, category: LogCategory = None, context: Dict[str, Any] = None):
    """Info log shortcut"""
    get_logger().info(message, category, context)


def warning(message: str, category: LogCategory = None, context: Dict[str, Any] = None):
    """Warning log shortcut"""
    get_logger().warning(message, category, context)


def error(message: str, category: LogCategory = None, context: Dict[str, Any] = None):
    """Error log shortcut"""
    get_logger().error(message, category, context)


def critical(message: str, category: LogCategory = None, context: Dict[str, Any] = None):
    """Critical log shortcut"""
    get_logger().critical(message, category, context)


def log_signal(token_symbol: str, signal_type: str, confidence: float,
               factors: list, details: Dict[str, Any] = None):
    """Signal log shortcut"""
    get_logger().log_signal(token_symbol, signal_type, confidence, factors, details)


def log_execution(token_symbol: str, action: str, amount: float,
                  price: float, tx_hash: str = None, 
                  status: str = 'success', details: Dict[str, Any] = None):
    """Execution log shortcut"""
    get_logger().log_execution(token_symbol, action, amount, price, tx_hash, status, details)


def log_pnl(token_symbol: str, entry_price: float, exit_price: float,
            quantity: float, pnl_usd: float, pnl_pct: float,
            holding_time: str = None, details: Dict[str, Any] = None):
    """P&L log shortcut"""
    get_logger().log_pnl(token_symbol, entry_price, exit_price, quantity, 
                        pnl_usd, pnl_pct, holding_time, details)


def timer(operation: str, details: Dict[str, Any] = None):
    """Performance timer shortcut"""
    return PerformanceTimer(get_logger(), operation, details)


# Example usage
if __name__ == "__main__":
    # Initialize logging
    logger = init_logging(
        log_dir="./test_logs",
        console_level=logging.DEBUG,
        file_level=logging.DEBUG
    )
    
    # Test different log types
    logger.debug("Debug message for testing", LogCategory.SYSTEM)
    logger.info("System started successfully", LogCategory.SYSTEM)
    
    logger.log_signal(
        token_symbol="PEPE",
        signal_type="BUY",
        confidence=0.87,
        factors=["Volume spike", "Holder growth", "Progress acceleration"],
        details={'entry_price': 0.000123, 'target_price': 0.000234}
    )
    
    logger.log_execution(
        token_symbol="PEPE",
        action="BUY",
        amount=1000,
        price=0.000123,
        tx_hash="0xabc123...",
        status="success",
        details={'slippage': 0.5, 'gas_fee': 0.001}
    )
    
    logger.log_pnl(
        token_symbol="PEPE",
        entry_price=0.000123,
        exit_price=0.000150,
        quantity=1000,
        pnl_usd=27.0,
        pnl_pct=21.95,
        holding_time="2h 15m"
    )
    
    logger.log_performance(
        operation="signal_calculation",
        duration_ms=45.2,
        details={'tokens_analyzed': 42}
    )
    
    # Test performance timer
    with timer("database_query", {'table': 'positions'}):
        import time
        time.sleep(0.1)
    
    logger.info("Test completed")
    
    # Read recent logs
    recent = logger.get_recent_logs(limit=10)
    print(f"\nRecent logs: {len(recent)} entries")
