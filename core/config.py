"""
Configuration Management for Pump.fun Trading System

This module provides centralized configuration management using Pydantic settings.
It supports multiple strategy modes (conservative/balanced/aggressive) and
environment-based configuration loading.

Example Usage:
    >>> from core.config import get_settings, StrategyMode
    >>> settings = get_settings()
    >>> print(settings.trading.strategy_mode)
    >>> print(settings.risk.max_position_size_pct)
"""

import os
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class StrategyMode(str, Enum):
    """Strategy risk modes for position sizing and entry criteria."""
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class LogLevel(str, Enum):
    """Logging level options."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class TradingConfig(BaseSettings):
    """Trading-specific configuration parameters.
    
    Attributes:
        strategy_mode: Risk mode affecting position sizing and entry criteria
        paper_trading: Enable paper trading mode (no real transactions)
        max_concurrent_positions: Maximum number of simultaneous open positions
        min_liquidity_sol: Minimum liquidity in SOL for token consideration
        default_slippage_bps: Default slippage tolerance in basis points
    """
    model_config = SettingsConfigDict(env_prefix="TRADING_")
    
    strategy_mode: StrategyMode = Field(
        default=StrategyMode.BALANCED,
        description="Strategy risk mode: conservative, balanced, or aggressive"
    )
    paper_trading: bool = Field(
        default=True,
        description="Enable paper trading mode (no real transactions)"
    )
    max_concurrent_positions: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of simultaneous open positions"
    )
    min_liquidity_sol: float = Field(
        default=1.0,
        ge=0.1,
        description="Minimum liquidity in SOL for token consideration"
    )
    default_slippage_bps: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Default slippage tolerance in basis points (1% = 100 bps)"
    )


class BondingCurveConfig(BaseSettings):
    """Bonding curve monitoring configuration.
    
    Attributes:
        entry_zone_start: Start of optimal entry zone (75-90%)
        entry_zone_end: End of optimal entry zone
        execution_zone_start: Start of execution-only zone (90-95%)
        execution_zone_end: End of execution-only zone
        graduation_threshold: Bonding curve completion threshold
        base_graduation_rate: Expected base graduation rate (0.8%)
    """
    model_config = SettingsConfigDict(env_prefix="BONDING_")
    
    entry_zone_start: float = Field(
        default=0.75,
        ge=0.5,
        le=0.95,
        description="Start of optimal entry zone (75%)"
    )
    entry_zone_end: float = Field(
        default=0.90,
        ge=0.6,
        le=0.98,
        description="End of optimal entry zone (90%)"
    )
    execution_zone_start: float = Field(
        default=0.90,
        ge=0.7,
        le=0.95,
        description="Start of execution-only zone (90%)"
    )
    execution_zone_end: float = Field(
        default=0.95,
        ge=0.75,
        le=0.99,
        description="End of execution-only zone (95%)"
    )
    graduation_threshold: float = Field(
        default=1.0,
        ge=0.9,
        le=1.1,
        description="Bonding curve completion threshold (100%)"
    )
    base_graduation_rate: float = Field(
        default=0.008,
        ge=0.001,
        le=0.05,
        description="Expected base graduation rate (0.8%)"
    )
    
    @model_validator(mode="after")
    def validate_zones(self) -> "BondingCurveConfig":
        """Validate that zone boundaries are logically consistent."""
        if self.entry_zone_start >= self.entry_zone_end:
            raise ValueError("entry_zone_start must be less than entry_zone_end")
        if self.execution_zone_start >= self.execution_zone_end:
            raise ValueError("execution_zone_start must be less than execution_zone_end")
        if self.execution_zone_start < self.entry_zone_end:
            raise ValueError("execution_zone must start at or after entry_zone_end")
        return self


class SignalThresholdsConfig(BaseSettings):
    """Signal detection thresholds configuration.
    
    Attributes:
        volume_acceleration_multiplier: Volume acceleration threshold (>2.5x)
        dev_wallet_max_liquidation_pct: Max dev wallet liquidation in single tx
        min_alpha_wallets: Minimum alpha wallets for balanced strategy
        holder_growth_rate_min: Minimum holder growth rate per hour
        buy_sell_ratio_min: Minimum healthy buy/sell ratio
    """
    model_config = SettingsConfigDict(env_prefix="SIGNAL_")
    
    volume_acceleration_multiplier: float = Field(
        default=2.5,
        ge=1.0,
        le=10.0,
        description="Volume acceleration threshold multiplier (>2.5x 24h avg)"
    )
    dev_wallet_max_liquidation_pct: float = Field(
        default=0.25,
        ge=0.05,
        le=0.50,
        description="Max dev wallet liquidation in single transaction (25%)"
    )
    min_alpha_wallets: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Minimum alpha wallets for balanced strategy"
    )
    holder_growth_rate_min: float = Field(
        default=0.05,
        ge=0.01,
        le=0.50,
        description="Minimum holder growth rate per hour (5%)"
    )
    buy_sell_ratio_min: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Minimum healthy buy/sell ratio (2:1)"
    )


class RiskConfig(BaseSettings):
    """Risk management configuration.
    
    Attributes:
        max_position_size_pct: Maximum position size as % of portfolio
        min_position_size_pct: Minimum position size as % of portfolio
        stop_loss_pct: Stop loss percentage from entry
        take_profit_levels: List of take-profit levels with percentages
        kelly_fraction: Fractional Kelly criterion multiplier
        max_daily_loss_pct: Maximum daily loss limit
    """
    model_config = SettingsConfigDict(env_prefix="RISK_")
    
    max_position_size_pct: float = Field(
        default=0.05,
        ge=0.01,
        le=0.25,
        description="Maximum position size as % of portfolio (5%)"
    )
    min_position_size_pct: float = Field(
        default=0.01,
        ge=0.005,
        le=0.10,
        description="Minimum position size as % of portfolio (1%)"
    )
    stop_loss_pct: float = Field(
        default=0.15,
        ge=0.05,
        le=0.50,
        description="Stop loss percentage from entry (15%)"
    )
    take_profit_levels: List[Dict[str, float]] = Field(
        default_factory=lambda: [
            {"level": 0.25, "size_pct": 0.30},  # 25% profit, sell 30%
            {"level": 0.50, "size_pct": 0.30},  # 50% profit, sell 30%
            {"level": 1.00, "size_pct": 0.40},  # 100% profit, sell 40%
        ],
        description="Take-profit levels with position percentages to sell"
    )
    kelly_fraction: float = Field(
        default=0.25,
        ge=0.10,
        le=0.50,
        description="Fractional Kelly criterion multiplier (quarter Kelly)"
    )
    max_daily_loss_pct: float = Field(
        default=0.05,
        ge=0.01,
        le=0.20,
        description="Maximum daily loss limit (5%)"
    )
    
    @field_validator("take_profit_levels")
    @classmethod
    def validate_take_profit_levels(cls, v: List[Dict[str, float]]) -> List[Dict[str, float]]:
        """Validate take-profit levels are properly configured."""
        if not v:
            raise ValueError("take_profit_levels cannot be empty")
        total_size = sum(level.get("size_pct", 0) for level in v)
        if abs(total_size - 1.0) > 0.001:
            raise ValueError(f"take_profit_levels size_pct must sum to 1.0, got {total_size}")
        return v


class ExitConfig(BaseSettings):
    """Exit strategy configuration for post-graduation trading.
    
    Attributes:
        post_graduation_exit_delay_sec: Delay before starting post-graduation exit
        rapid_exit_threshold: Profit threshold for rapid exit
        graduation_loss_rate: Expected loss rate for graduates (81-97%)
        time_based_exit_minutes: Time-based exit after entry
    """
    model_config = SettingsConfigDict(env_prefix="EXIT_")
    
    post_graduation_exit_delay_sec: int = Field(
        default=30,
        ge=0,
        le=300,
        description="Delay before starting post-graduation exit (30s)"
    )
    rapid_exit_threshold: float = Field(
        default=0.20,
        ge=0.05,
        le=0.50,
        description="Profit threshold for rapid exit (20%)"
    )
    graduation_loss_rate: float = Field(
        default=0.89,
        ge=0.50,
        le=0.99,
        description="Expected loss rate for graduates (89% average)"
    )
    time_based_exit_minutes: int = Field(
        default=60,
        ge=10,
        le=240,
        description="Time-based exit after entry (60 minutes)"
    )


class SolanaConfig(BaseSettings):
    """Solana blockchain connection configuration.
    
    Attributes:
        rpc_url: Solana RPC endpoint URL
        ws_url: Solana WebSocket endpoint URL
        commitment: Transaction commitment level
        jito_enabled: Enable Jito MEV protection
        priority_fee_microlamports: Priority fee for transactions
    """
    model_config = SettingsConfigDict(env_prefix="SOLANA_")
    
    rpc_url: str = Field(
        default="https://api.mainnet-beta.solana.com",
        description="Solana RPC endpoint URL"
    )
    ws_url: Optional[str] = Field(
        default=None,
        description="Solana WebSocket endpoint URL"
    )
    commitment: str = Field(
        default="confirmed",
        description="Transaction commitment level: processed, confirmed, or finalized"
    )
    jito_enabled: bool = Field(
        default=False,
        description="Enable Jito MEV protection"
    )
    priority_fee_microlamports: int = Field(
        default=10000,
        ge=0,
        le=1000000,
        description="Priority fee for transactions in microlamports"
    )
    
    @field_validator("commitment")
    @classmethod
    def validate_commitment(cls, v: str) -> str:
        """Validate commitment level."""
        valid_levels = ["processed", "confirmed", "finalized"]
        if v.lower() not in valid_levels:
            raise ValueError(f"commitment must be one of {valid_levels}")
        return v.lower()


class WalletConfig(BaseSettings):
    """Wallet and authentication configuration.
    
    Attributes:
        private_key: Base58-encoded private key (loaded from env)
        wallet_address: Wallet public address
        encryption_key: Key for encrypting sensitive data at rest
    """
    model_config = SettingsConfigDict(env_prefix="WALLET_")
    
    private_key: Optional[str] = Field(
        default=None,
        description="Base58-encoded private key (keep secret!)"
    )
    wallet_address: Optional[str] = Field(
        default=None,
        description="Wallet public address"
    )
    encryption_key: Optional[str] = Field(
        default=None,
        description="Key for encrypting sensitive data at rest"
    )


class BitqueryConfig(BaseSettings):
    """Bitquery API configuration for real-time data.
    
    Attributes:
        api_key: Bitquery API key for WebSocket and REST access
        ws_url: Bitquery WebSocket endpoint
        rest_url: Bitquery REST endpoint
        enabled: Whether to use Bitquery for monitoring
    """
    model_config = SettingsConfigDict(env_prefix="BITQUERY_")
    
    api_key: Optional[str] = Field(
        default=None,
        description="Bitquery API key"
    )
    ws_url: str = Field(
        default="wss://streaming.bitquery.io/graphql",
        description="Bitquery WebSocket URL"
    )
    rest_url: str = Field(
        default="https://graphql.bitquery.io",
        description="Bitquery REST endpoint"
    )
    enabled: bool = Field(
        default=True,
        description="Enable Bitquery integration"
    )


class RunnerDetectorConfig(BaseSettings):
    """Runner detector configuration.
    
    Attributes:
        enabled: Enable runner detection
        min_progress: Minimum bonding curve progress (85%)
        max_progress: Maximum bonding curve progress (98%)
        min_holders: Minimum holder count (50)
        min_momentum_velocity: Minimum progress velocity (% per minute)
        min_volume_acceleration: Minimum volume surge multiplier
        score_threshold: Overall score threshold for runner classification
        cleanup_interval_sec: Cleanup old tokens interval
    """
    model_config = SettingsConfigDict(env_prefix="RUNNER_")
    
    enabled: bool = Field(
        default=True,
        description="Enable runner detection"
    )
    min_progress: float = Field(
        default=0.85,
        ge=0.5,
        le=0.98,
        description="Minimum bonding curve progress (85%)"
    )
    max_progress: float = Field(
        default=0.98,
        ge=0.8,
        le=1.0,
        description="Maximum bonding curve progress (98%)"
    )
    min_holders: int = Field(
        default=50,
        ge=10,
        le=1000,
        description="Minimum holder count"
    )
    min_momentum_velocity: float = Field(
        default=0.01,
        ge=0.001,
        le=0.1,
        description="Minimum progress velocity (% per minute)"
    )
    min_volume_acceleration: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Minimum volume surge multiplier"
    )
    score_threshold: float = Field(
        default=0.70,
        ge=0.5,
        le=1.0,
        description="Overall score threshold for runner classification"
    )
    cleanup_interval_sec: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Cleanup old tokens interval (seconds)"
    )


class DemoConfig(BaseSettings):
    """Demo and testing configuration.
    
    Attributes:
        use_mock: Use mock bonding curve monitor (for testing without API)
    """
    model_config = SettingsConfigDict(env_prefix="DEMO_")
    
    use_mock: bool = Field(
        default=True,
        description="Use mock bonding curve monitor for testing"
    )


class MonitoringConfig(BaseSettings):
    """System monitoring and logging configuration.
    
    Attributes:
        log_level: Logging verbosity level
        log_to_file: Enable file logging
        log_file_path: Path to log file
        metrics_enabled: Enable metrics collection
        health_check_interval_sec: Health check interval in seconds
    """
    model_config = SettingsConfigDict(env_prefix="MONITOR_")
    
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Logging verbosity level"
    )
    log_to_file: bool = Field(
        default=True,
        description="Enable file logging"
    )
    log_file_path: Path = Field(
        default=Path("logs/pumpfun_trader.log"),
        description="Path to log file"
    )
    metrics_enabled: bool = Field(
        default=True,
        description="Enable metrics collection"
    )
    health_check_interval_sec: int = Field(
        default=30,
        ge=5,
        le=300,
        description="Health check interval in seconds"
    )


class Settings(BaseSettings):
    """Main application settings container.
    
    This class aggregates all configuration sections and provides
    centralized access to application settings.
    
    Environment variables are loaded from .env file if present.
    
    Example:
        >>> settings = Settings()
        >>> print(settings.trading.strategy_mode)
        >>> print(settings.risk.max_position_size_pct)
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Sub-configurations
    trading: TradingConfig = Field(default_factory=TradingConfig)
    bonding_curve: BondingCurveConfig = Field(default_factory=BondingCurveConfig)
    signals: SignalThresholdsConfig = Field(default_factory=SignalThresholdsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    exit_strategy: ExitConfig = Field(default_factory=ExitConfig)
    solana: SolanaConfig = Field(default_factory=SolanaConfig)
    wallet: WalletConfig = Field(default_factory=WalletConfig)
    bitquery: BitqueryConfig = Field(default_factory=BitqueryConfig)
    runner: RunnerDetectorConfig = Field(default_factory=RunnerDetectorConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)
    
    # Application metadata
    app_name: str = Field(default="pumpfun-trader")
    app_version: str = Field(default="1.0.0")
    debug: bool = Field(default=False)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance.
    
    This function returns a singleton Settings instance that is cached
    for the lifetime of the application. Use this instead of creating
    Settings directly to avoid repeated environment loading.
    
    Returns:
        Settings: Cached settings instance
        
    Example:
        >>> settings = get_settings()
        >>> print(settings.trading.paper_trading)
    """
    return Settings()


def get_strategy_params(mode: StrategyMode) -> Dict[str, Any]:
    """Get strategy parameters for a specific mode.
    
    Args:
        mode: Strategy mode (conservative, balanced, aggressive)
        
    Returns:
        Dictionary of strategy parameters for the specified mode
        
    Example:
        >>> params = get_strategy_params(StrategyMode.CONSERVATIVE)
        >>> print(params["position_size_multiplier"])
    """
    base_params = {
        StrategyMode.CONSERVATIVE: {
            "position_size_multiplier": 0.5,
            "entry_threshold_multiplier": 1.2,
            "stop_loss_tightening": 0.10,
            "min_volume_acceleration": 3.0,
            "require_alpha_wallets": True,
            "max_positions": 3,
        },
        StrategyMode.BALANCED: {
            "position_size_multiplier": 1.0,
            "entry_threshold_multiplier": 1.0,
            "stop_loss_tightening": 0.15,
            "min_volume_acceleration": 2.5,
            "require_alpha_wallets": True,
            "max_positions": 5,
        },
        StrategyMode.AGGRESSIVE: {
            "position_size_multiplier": 1.5,
            "entry_threshold_multiplier": 0.8,
            "stop_loss_tightening": 0.20,
            "min_volume_acceleration": 2.0,
            "require_alpha_wallets": False,
            "max_positions": 8,
        },
    }
    return base_params.get(mode, base_params[StrategyMode.BALANCED])


def reload_settings() -> Settings:
    """Force reload settings from environment.
    
    This function clears the settings cache and reloads configuration
    from environment variables and .env file.
    
    Returns:
        Settings: Fresh settings instance
        
    Example:
        >>> # After changing environment variables
        >>> settings = reload_settings()
    """
    get_settings.cache_clear()
    return get_settings()


# Export commonly used items
__all__ = [
    "Settings",
    "get_settings",
    "reload_settings",
    "get_strategy_params",
    "StrategyMode",
    "LogLevel",
    "TradingConfig",
    "BondingCurveConfig",
    "SignalThresholdsConfig",
    "RiskConfig",
    "ExitConfig",
    "SolanaConfig",
    "WalletConfig",
    "MonitoringConfig",
]