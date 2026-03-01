"""
Trade Execution Models for Pump.fun Trading System

This module defines the core data models for orders, trades, and execution results.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, Dict, List, Any
import uuid


class OrderSide(Enum):
    """Order side - buy or sell"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type classification"""
    MARKET = "market"
    LIMIT = "limit"
    BONDING_CURVE = "bonding_curve"  # Pump.fun specific


class OrderStatus(Enum):
    """Order execution status"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL_FILL = "partial_fill"


class ExecutionVenue(Enum):
    """Where the trade was executed"""
    JUPITER = "jupiter"
    RAYDIUM = "raydium"
    PUMPFUN = "pumpfun"
    PAPER = "paper_trading"
    UNKNOWN = "unknown"


class ProgressRange(Enum):
    """Bonding curve progress ranges for slippage/fee configuration"""
    RANGE_75_90 = "75_90"    # 75-90% progress
    RANGE_90_95 = "90_95"    # 90-95% progress
    RANGE_95_100 = "95_100"  # 95-100% progress


@dataclass
class TokenInfo:
    """Token information"""
    address: str
    symbol: str
    name: str
    decimals: int = 6
    is_graduated: bool = False
    bonding_curve_progress: float = 0.0
    
    def __post_init__(self):
        if isinstance(self.decimals, str):
            self.decimals = int(self.decimals)
        if isinstance(self.bonding_curve_progress, str):
            self.bonding_curve_progress = float(self.bonding_curve_progress)


@dataclass
class LiquidityInfo:
    """Pool liquidity information"""
    pool_address: str
    token_a_amount: Decimal
    token_b_amount: Decimal
    total_liquidity_usd: Decimal
    volume_24h: Decimal = field(default=Decimal("0"))
    
    @property
    def max_exit_size(self) -> Decimal:
        """Calculate maximum safe exit size based on liquidity"""
        if self.total_liquidity_usd < Decimal("10000"):
            return Decimal("500")
        elif self.total_liquidity_usd < Decimal("50000"):
            return Decimal("2500")
        else:
            return self.total_liquidity_usd * Decimal("0.05")  # 5% of pool


@dataclass
class SlippageConfig:
    """Slippage configuration based on progress range"""
    max_slippage_bps: int  # Basis points (100 = 1%)
    priority_fee_level: str  # "standard", "high", "very_high"
    should_avoid: bool = False
    
    @classmethod
    def from_progress(cls, progress: float) -> "SlippageConfig":
        """Get slippage config based on bonding curve progress"""
        if progress < 0.75:
            return cls(max_slippage_bps=500, priority_fee_level="standard")  # 5%
        elif progress < 0.90:
            return cls(max_slippage_bps=1000, priority_fee_level="standard")  # 10%
        elif progress < 0.95:
            return cls(max_slippage_bps=1500, priority_fee_level="high")  # 15%
        else:
            return cls(
                max_slippage_bps=1500, 
                priority_fee_level="very_high",
                should_avoid=True
            )


@dataclass
class Order:
    """Trading order"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    token_in: str = ""  # Token address
    token_out: str = ""  # Token address
    amount_in: Decimal = field(default=Decimal("0"))
    amount_out: Optional[Decimal] = None
    min_amount_out: Optional[Decimal] = None  # For slippage protection
    slippage_bps: int = 100  # Default 1%
    priority_fee: Optional[int] = None  # microlamports
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: OrderStatus = OrderStatus.PENDING
    
    # Execution context
    wallet_address: Optional[str] = None
    mev_protection: bool = True
    
    def __post_init__(self):
        if isinstance(self.amount_in, (int, float, str)):
            self.amount_in = Decimal(str(self.amount_in))
        if isinstance(self.amount_out, (int, float, str)):
            self.amount_out = Decimal(str(self.amount_out))
        if isinstance(self.min_amount_out, (int, float, str)):
            self.min_amount_out = Decimal(str(self.min_amount_out))


@dataclass
class RouteInfo:
    """Jupiter route information"""
    route_id: str
    in_amount: Decimal
    out_amount: Decimal
    price_impact_pct: float
    market_infos: List[Dict[str, Any]] = field(default_factory=list)
    other_amount_threshold: Decimal = field(default=Decimal("0"))
    swap_mode: str = "ExactIn"
    
    def __post_init__(self):
        if isinstance(self.in_amount, (int, float, str)):
            self.in_amount = Decimal(str(self.in_amount))
        if isinstance(self.out_amount, (int, float, str)):
            self.out_amount = Decimal(str(self.out_amount))
        if isinstance(self.other_amount_threshold, (int, float, str)):
            self.other_amount_threshold = Decimal(str(self.other_amount_threshold))


@dataclass
class ExecutionResult:
    """Trade execution result"""
    order_id: str
    success: bool
    
    # Transaction details
    signature: Optional[str] = None
    block_time: Optional[datetime] = None
    
    # Execution details
    venue: ExecutionVenue = ExecutionVenue.UNKNOWN
    route: Optional[RouteInfo] = None
    
    # Amounts
    amount_in: Decimal = field(default=Decimal("0"))
    amount_out: Decimal = field(default=Decimal("0"))
    expected_amount_out: Decimal = field(default=Decimal("0"))
    
    # Costs
    price: Decimal = field(default=Decimal("0"))
    slippage_bps: int = 0
    priority_fee_paid: int = 0
    transaction_fee_lamports: int = 0
    
    # Status
    status: OrderStatus = OrderStatus.PENDING
    error_message: Optional[str] = None
    
    # Metadata
    executed_at: datetime = field(default_factory=datetime.utcnow)
    retry_count: int = 0
    
    # Logs
    logs: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if isinstance(self.amount_in, (int, float, str)):
            self.amount_in = Decimal(str(self.amount_in))
        if isinstance(self.amount_out, (int, float, str)):
            self.amount_out = Decimal(str(self.amount_out))
        if isinstance(self.expected_amount_out, (int, float, str)):
            self.expected_amount_out = Decimal(str(self.expected_amount_out))
        if isinstance(self.price, (int, float, str)):
            self.price = Decimal(str(self.price))
    
    @property
    def actual_slippage_bps(self) -> int:
        """Calculate actual slippage in basis points"""
        if self.expected_amount_out > 0:
            slippage = (self.expected_amount_out - self.amount_out) / self.expected_amount_out
            return int(slippage * 10000)
        return 0
    
    @property
    def total_cost_usd(self) -> Decimal:
        """Total cost including fees (approximate)"""
        # Approximate SOL price for fee calculation
        sol_price = Decimal("100")  # $100 per SOL (conservative)
        fee_sol = Decimal(self.transaction_fee_lamports) / Decimal("1000000000")
        return fee_sol * sol_price
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "order_id": self.order_id,
            "success": self.success,
            "signature": self.signature,
            "block_time": self.block_time.isoformat() if self.block_time else None,
            "venue": self.venue.value,
            "amount_in": str(self.amount_in),
            "amount_out": str(self.amount_out),
            "expected_amount_out": str(self.expected_amount_out),
            "price": str(self.price),
            "slippage_bps": self.slippage_bps,
            "actual_slippage_bps": self.actual_slippage_bps,
            "priority_fee_paid": self.priority_fee_paid,
            "transaction_fee_lamports": self.transaction_fee_lamports,
            "status": self.status.value,
            "error_message": self.error_message,
            "executed_at": self.executed_at.isoformat(),
            "retry_count": self.retry_count,
            "logs": self.logs,
        }


@dataclass
class Position:
    """Trading position"""
    token_address: str
    token_symbol: str
    entry_price: Decimal
    entry_amount: Decimal  # Token amount
    entry_value_sol: Decimal  # SOL value at entry
    entry_timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Current state
    current_amount: Decimal = field(default=Decimal("0"))
    realized_pnl_sol: Decimal = field(default=Decimal("0"))
    
    # Exit tracking
    partial_exits: List[Dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        for field_name in ['entry_price', 'entry_amount', 'entry_value_sol', 
                          'current_amount', 'realized_pnl_sol']:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))
        if self.current_amount == 0:
            self.current_amount = self.entry_amount
    
    @property
    def unrealized_pnl_pct(self, current_price: Decimal = Decimal("0")) -> float:
        """Calculate unrealized P&L percentage"""
        if self.entry_price > 0 and current_price > 0:
            return float((current_price - self.entry_price) / self.entry_price * 100)
        return 0.0
    
    def record_partial_exit(self, amount: Decimal, price: Decimal, value_sol: Decimal):
        """Record a partial exit"""
        self.partial_exits.append({
            "timestamp": datetime.utcnow().isoformat(),
            "amount": str(amount),
            "price": str(price),
            "value_sol": str(value_sol),
        })
        self.current_amount -= amount
        self.realized_pnl_sol += value_sol


@dataclass
class WalletState:
    """Wallet state for trading"""
    address: str
    sol_balance: Decimal = field(default=Decimal("0"))
    token_accounts: Dict[str, Decimal] = field(default_factory=dict)  # token -> balance
    
    def __post_init__(self):
        if isinstance(self.sol_balance, (int, float, str)):
            self.sol_balance = Decimal(str(self.sol_balance))
        self.token_accounts = {
            k: Decimal(str(v)) if isinstance(v, (int, float, str)) else v
            for k, v in self.token_accounts.items()
        }
    
    def has_sufficient_sol(self, amount: Decimal, buffer: Decimal = Decimal("0.01")) -> bool:
        """Check if wallet has sufficient SOL (with buffer for fees)"""
        return self.sol_balance >= (amount + buffer)
    
    def get_token_balance(self, token_address: str) -> Decimal:
        """Get token balance"""
        return self.token_accounts.get(token_address, Decimal("0"))


@dataclass
class RetryConfig:
    """Retry configuration"""
    max_retries: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    
    def get_delay(self, attempt: int) -> float:
        """Get delay for retry attempt"""
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)


@dataclass
class ExecutionConfig:
    """Global execution configuration"""
    # RPC settings
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    commitment: str = "confirmed"
    
    # Jupiter settings
    jupiter_api_url: str = "https://quote-api.jup.ag/v6"
    use_mev_protection: bool = True
    
    # Retry settings
    entry_retry: RetryConfig = field(default_factory=lambda: RetryConfig(max_retries=3))
    exit_retry: RetryConfig = field(default_factory=lambda: RetryConfig(max_retries=5))
    
    # Confirmation settings
    confirmation_timeout: int = 30  # seconds
    max_confirmation_attempts: int = 10
    
    # Paper trading
    paper_trading: bool = False
    paper_initial_sol: Decimal = field(default=Decimal("10"))  # 10 SOL
    
    # Logging
    log_level: str = "INFO"
    log_trades: bool = True


# Type aliases
TokenAddress = str
TxSignature = str
