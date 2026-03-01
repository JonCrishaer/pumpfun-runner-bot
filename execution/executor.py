"""
Main Trade Execution Orchestrator for Pump.fun Trading System

Coordinates execution across multiple DEXs:
- Jupiter (primary routing)
- Pump.fun (bonding curve)
- Raydium (post-graduation)

Handles:
- Pre-execution checks
- Venue selection
- Retry logic with exponential backoff
- Transaction confirmation
- Error recovery
- Paper trading mode
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime
import time

from models import (
    Order, OrderSide, OrderType, OrderStatus,
    ExecutionResult, ExecutionVenue, RetryConfig,
    ExecutionConfig, WalletState, Position,
    TokenInfo, SlippageConfig, LiquidityInfo
)

from jupiter_client import JupiterClient, get_jupiter_client
from pumpfun_client import PumpFunClient, get_pumpfun_client
from raydium_client import RaydiumClient, get_raydium_client
from paper_trading import PaperTradingEngine, create_paper_trading_engine

# Setup logging
logger = logging.getLogger(__name__)

# Token constants
SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = "So11111111111111111111111111111111111111112"


class ExecutionError(Exception):
    """Execution orchestrator error"""
    pass


class InsufficientBalanceError(ExecutionError):
    """Insufficient balance for trade"""
    pass


class PreExecutionCheckError(ExecutionError):
    """Pre-execution check failed"""
    pass


class TradeExecutor:
    """
    Main Trade Execution Orchestrator
    
    Coordinates trade execution across multiple venues with:
    - Intelligent venue selection
    - Retry logic with exponential backoff
    - MEV protection
    - Slippage management
    - Transaction confirmation
    """
    
    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        wallet_keypair: Optional[Any] = None,
    ):
        self.config = config or ExecutionConfig()
        self.wallet_keypair = wallet_keypair
        
        # Initialize clients
        self.jupiter: Optional[JupiterClient] = None
        self.pumpfun: Optional[PumpFunClient] = None
        self.raydium: Optional[RaydiumClient] = None
        self.paper_engine: Optional[PaperTradingEngine] = None
        
        # State
        self._initialized = False
        self._pending_orders: Dict[str, Order] = {}
        self._execution_history: List[ExecutionResult] = []
        
        # Callbacks
        self._pre_execution_callbacks: List[Callable] = []
        self._post_execution_callbacks: List[Callable] = []
        
        logger.info("TradeExecutor initialized")
    
    async def initialize(self):
        """Initialize all DEX clients"""
        if self._initialized:
            return
        
        logger.info("Initializing TradeExecutor clients...")
        
        # Initialize Jupiter
        self.jupiter = JupiterClient(
            api_url=self.config.jupiter_api_url,
            rpc_url=self.config.rpc_url,
            use_mev_protection=self.config.use_mev_protection,
        )
        
        # Initialize Pump.fun
        self.pumpfun = PumpFunClient(
            rpc_url=self.config.rpc_url,
        )
        
        # Initialize Raydium
        self.raydium = RaydiumClient(
            rpc_url=self.config.rpc_url,
        )
        
        # Initialize paper trading if enabled
        if self.config.paper_trading:
            self.paper_engine = create_paper_trading_engine(
                initial_sol=self.config.paper_initial_sol,
            )
            logger.info(f"Paper trading mode enabled with {self.config.paper_initial_sol} SOL")
        
        self._initialized = True
        logger.info("TradeExecutor initialization complete")
    
    async def close(self):
        """Close all clients"""
        if self.jupiter:
            await self.jupiter.close()
        if self.pumpfun:
            await self.pumpfun.close()
        if self.raydium:
            await self.raydium.close()
        
        self._initialized = False
        logger.info("TradeExecutor closed")
    
    async def __aenter__(self):
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    # ==================== Pre-Execution Checks ====================
    
    async def _check_wallet_balance(
        self,
        order: Order,
    ) -> bool:
        """
        Check if wallet has sufficient balance
        
        Args:
            order: Order to check
        
        Returns:
            True if sufficient balance
        """
        from solana.rpc.async_api import AsyncClient
        
        if not self.wallet_keypair:
            logger.warning("No wallet keypair configured")
            return True  # Allow in paper mode
        
        if self.config.paper_trading:
            return True  # Paper trading always has balance
        
        try:
            rpc_client = AsyncClient(self.config.rpc_url)
            
            try:
                wallet_pubkey = self.wallet_keypair.pubkey()
                
                if order.side == OrderSide.BUY:
                    # Check SOL balance
                    response = await rpc_client.get_balance(wallet_pubkey)
                    balance_lamports = response.value
                    balance_sol = Decimal(balance_lamports) / Decimal("1000000000")
                    
                    required = order.amount_in + Decimal("0.01")  # Add buffer for fees
                    
                    if balance_sol < required:
                        logger.error(f"Insufficient SOL: {balance_sol} < {required}")
                        return False
                    
                    logger.debug(f"SOL balance check: {balance_sol} >= {required}")
                    return True
                    
                else:
                    # Check token balance
                    from spl.token.instructions import get_associated_token_address
                    
                    token_account = get_associated_token_address(
                        wallet_pubkey,
                        __import__('solders.pubkey').Pubkey.from_string(order.token_in)
                    )
                    
                    response = await rpc_client.get_token_account_balance(token_account)
                    
                    if response.value:
                        balance = Decimal(response.value.amount)
                        
                        if balance < order.amount_in:
                            logger.error(f"Insufficient token balance: {balance} < {order.amount_in}")
                            return False
                        
                        logger.debug(f"Token balance check: {balance} >= {order.amount_in}")
                        return True
                    else:
                        logger.error(f"Token account not found: {order.token_in}")
                        return False
            
            finally:
                await rpc_client.close()
        
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return False
    
    async def _ensure_token_account(
        self,
        token_address: str,
    ) -> bool:
        """
        Ensure token account exists (create if needed)
        
        Args:
            token_address: Token mint address
        
        Returns:
            True if account exists or was created
        """
        from solana.rpc.async_api import AsyncClient
        from solana.transaction import Transaction
        from solders.pubkey import Pubkey
        from spl.token.instructions import get_associated_token_address, create_associated_token_account
        
        if not self.wallet_keypair or self.config.paper_trading:
            return True
        
        try:
            rpc_client = AsyncClient(self.config.rpc_url)
            
            try:
                wallet_pubkey = self.wallet_keypair.pubkey()
                token_pubkey = Pubkey.from_string(token_address)
                
                token_account = get_associated_token_address(wallet_pubkey, token_pubkey)
                
                # Check if account exists
                account_info = await rpc_client.get_account_info(token_account)
                
                if account_info.value is not None:
                    logger.debug(f"Token account exists: {token_account}")
                    return True
                
                # Create token account
                logger.info(f"Creating token account for {token_address}")
                
                transaction = Transaction()
                create_ix = create_associated_token_account(
                    payer=wallet_pubkey,
                    owner=wallet_pubkey,
                    mint=token_pubkey,
                )
                transaction.add(create_ix)
                
                # Sign and send
                blockhash_resp = await rpc_client.get_latest_blockhash()
                transaction.recent_blockhash = blockhash_resp.value.blockhash
                transaction.fee_payer = wallet_pubkey
                transaction.sign(self.wallet_keypair)
                
                response = await rpc_client.send_raw_transaction(transaction.serialize())
                signature = response.value
                
                logger.info(f"Token account creation sent: {signature}")
                
                # Wait for confirmation
                await asyncio.sleep(2)
                
                return True
                
            finally:
                await rpc_client.close()
        
        except Exception as e:
            logger.error(f"Token account creation error: {e}")
            return False
    
    async def _validate_slippage(
        self,
        order: Order,
        token_address: str,
    ) -> bool:
        """
        Validate slippage tolerance
        
        Args:
            order: Order to validate
            token_address: Token address for progress check
        
        Returns:
            True if slippage is acceptable
        """
        # Get recommended slippage based on bonding curve progress
        if self.pumpfun:
            try:
                slippage_config = await self.pumpfun.get_slippage_config(token_address)
                
                if slippage_config.should_avoid:
                    logger.warning(f"Token at high progress - trading not recommended")
                    return False
                
                if order.slippage_bps > slippage_config.max_slippage_bps:
                    logger.warning(
                        f"Slippage {order.slippage_bps} bps exceeds recommended "
                        f"{slippage_config.max_slippage_bps} bps"
                    )
                    return False
                
            except Exception as e:
                logger.warning(f"Could not get slippage config: {e}")
        
        return True
    
    async def _run_pre_execution_checks(
        self,
        order: Order,
    ) -> Optional[str]:
        """
        Run all pre-execution checks
        
        Args:
            order: Order to check
        
        Returns:
            Error message if check fails, None if all pass
        """
        logger.info(f"Running pre-execution checks for order {order.id[:8]}...")
        
        # Check 1: Wallet balance
        if not await self._check_wallet_balance(order):
            return "Insufficient balance"
        
        # Check 2: Token account
        token_to_check = order.token_out if order.side == OrderSide.BUY else order.token_in
        if not await self._ensure_token_account(token_to_check):
            return "Could not ensure token account"
        
        # Check 3: Slippage validation
        progress_token = order.token_out if order.side == OrderSide.BUY else order.token_in
        if not await self._validate_slippage(order, progress_token):
            return "Slippage validation failed"
        
        # Run custom callbacks
        for callback in self._pre_execution_callbacks:
            try:
                result = await callback(order)
                if result is not True:
                    return f"Pre-execution callback failed: {result}"
            except Exception as e:
                logger.error(f"Pre-execution callback error: {e}")
                return f"Callback error: {e}"
        
        logger.info("Pre-execution checks passed")
        return None
    
    # ==================== Venue Selection ====================
    
    async def _select_venue(
        self,
        order: Order,
    ) -> ExecutionVenue:
        """
        Select best execution venue
        
        Args:
            order: Order to execute
        
        Returns:
            Selected venue
        """
        # Check if token is on Pump.fun bonding curve
        if self.pumpfun and order.side == OrderSide.BUY:
            try:
                curve_info = await self.pumpfun.get_bonding_curve_info(order.token_out)
                
                if curve_info and not curve_info.get("is_graduated", True):
                    progress = curve_info.get("progress", 0)
                    
                    if progress < 1.0:
                        logger.info(f"Token on Pump.fun bonding curve (progress: {progress:.2%})")
                        return ExecutionVenue.PUMPFUN
                    
            except Exception as e:
                logger.debug(f"Could not check Pump.fun status: {e}")
        
        # Check if token has Raydium pool
        if self.raydium:
            try:
                pool_info = await self.raydium.get_pool_info(
                    order.token_out if order.side == OrderSide.BUY else order.token_in,
                )
                
                if pool_info:
                    logger.info("Token has Raydium pool")
                    return ExecutionVenue.RAYDIUM
                    
            except Exception as e:
                logger.debug(f"Could not check Raydium pool: {e}")
        
        # Default to Jupiter for best routing
        logger.info("Using Jupiter for execution")
        return ExecutionVenue.JUPITER
    
    # ==================== Execution ====================
    
    async def _execute_with_retry(
        self,
        order: Order,
        venue: ExecutionVenue,
        retry_config: RetryConfig,
    ) -> ExecutionResult:
        """
        Execute order with retry logic
        
        Args:
            order: Order to execute
            venue: Execution venue
            retry_config: Retry configuration
        
        Returns:
            ExecutionResult
        """
        last_error = None
        
        for attempt in range(retry_config.max_retries + 1):
            try:
                logger.info(f"Execution attempt {attempt + 1}/{retry_config.max_retries + 1}")
                
                # Execute based on venue
                if self.config.paper_trading and self.paper_engine:
                    result = await self._execute_paper(order, venue)
                else:
                    result = await self._execute_live(order, venue)
                
                # Update retry count
                result.retry_count = attempt
                
                if result.success:
                    logger.info(f"Execution successful on attempt {attempt + 1}")
                    return result
                
                # Check if we should retry
                if attempt < retry_config.max_retries:
                    if self._should_retry(result):
                        delay = retry_config.get_delay(attempt)
                        logger.warning(f"Execution failed, retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error("Execution failed, not retryable")
                        return result
                else:
                    logger.error("Max retries exceeded")
                    return result
                    
            except Exception as e:
                last_error = e
                logger.exception(f"Execution error on attempt {attempt + 1}")
                
                if attempt < retry_config.max_retries:
                    delay = retry_config.get_delay(attempt)
                    logger.warning(f"Error, retrying in {delay}s...")
                    await asyncio.sleep(delay)
                
                continue
        
        # All retries failed
        return ExecutionResult(
            order_id=order.id,
            success=False,
            venue=venue,
            error_message=f"Max retries exceeded. Last error: {last_error}",
            retry_count=retry_config.max_retries,
        )
    
    def _should_retry(self, result: ExecutionResult) -> bool:
        """Determine if execution should be retried"""
        if result.success:
            return False
        
        # Don't retry certain errors
        non_retryable = [
            "insufficient",
            "Insufficient",
            "invalid",
            "Invalid",
            "unauthorized",
            "not found",
        ]
        
        if result.error_message:
            for msg in non_retryable:
                if msg in result.error_message:
                    return False
        
        return True
    
    async def _execute_live(
        self,
        order: Order,
        venue: ExecutionVenue,
    ) -> ExecutionResult:
        """Execute live order"""
        if not self.wallet_keypair:
            raise ExecutionError("No wallet keypair configured for live trading")
        
        # Get priority fee level
        priority_fee = "medium"
        if self.pumpfun:
            slippage_config = await self.pumpfun.get_slippage_config(
                order.token_out if order.side == OrderSide.BUY else order.token_in
            )
            priority_fee = slippage_config.priority_fee_level
        
        # Execute on appropriate venue
        if venue == ExecutionVenue.JUPITER:
            if not self.jupiter:
                raise ExecutionError("Jupiter client not initialized")
            return await self.jupiter.execute_swap(order, self.wallet_keypair, priority_fee)
        
        elif venue == ExecutionVenue.PUMPFUN:
            if not self.pumpfun:
                raise ExecutionError("Pump.fun client not initialized")
            if order.side == OrderSide.BUY:
                return await self.pumpfun.execute_buy(order, self.wallet_keypair, priority_fee)
            else:
                return await self.pumpfun.execute_sell(order, self.wallet_keypair, priority_fee)
        
        elif venue == ExecutionVenue.RAYDIUM:
            if not self.raydium:
                raise ExecutionError("Raydium client not initialized")
            return await self.raydium.execute_swap(order, self.wallet_keypair, priority_fee)
        
        else:
            raise ExecutionError(f"Unknown venue: {venue}")
    
    async def _execute_paper(
        self,
        order: Order,
        venue: ExecutionVenue,
    ) -> ExecutionResult:
        """Execute paper trade"""
        if not self.paper_engine:
            raise ExecutionError("Paper trading engine not initialized")
        
        # Get current price for simulation
        token_address = order.token_out if order.side == OrderSide.BUY else order.token_in
        
        # Try to get price from Jupiter
        price = None
        if self.jupiter:
            try:
                price = await self.jupiter.get_token_price(token_address)
            except Exception as e:
                logger.debug(f"Could not get Jupiter price: {e}")
        
        # Fallback to Raydium
        if price is None and self.raydium:
            try:
                price = await self.raydium.get_token_price(token_address)
            except Exception as e:
                logger.debug(f"Could not get Raydium price: {e}")
        
        # Execute paper trade
        return await self.paper_engine.execute_order(order, price)
    
    # ==================== Public API ====================
    
    async def execute_order(
        self,
        order: Order,
        force_venue: Optional[ExecutionVenue] = None,
    ) -> ExecutionResult:
        """
        Execute a trading order
        
        Args:
            order: Order to execute
            force_venue: Force specific venue (optional)
        
        Returns:
            ExecutionResult
        """
        if not self._initialized:
            await self.initialize()
        
        logger.info(f"Executing order {order.id[:8]}: {order.side.value} "
                   f"{order.amount_in} {order.token_in} -> {order.token_out}")
        
        # Pre-execution checks
        check_error = await self._run_pre_execution_checks(order)
        if check_error:
            logger.error(f"Pre-execution check failed: {check_error}")
            return ExecutionResult(
                order_id=order.id,
                success=False,
                error_message=f"Pre-execution check failed: {check_error}",
            )
        
        # Select venue
        venue = force_venue or await self._select_venue(order)
        
        # Get retry config
        retry_config = self.config.entry_retry if order.side == OrderSide.BUY else self.config.exit_retry
        
        # Execute with retry
        result = await self._execute_with_retry(order, venue, retry_config)
        
        # Store result
        self._execution_history.append(result)
        
        # Post-execution callbacks
        for callback in self._post_execution_callbacks:
            try:
                await callback(order, result)
            except Exception as e:
                logger.error(f"Post-execution callback error: {e}")
        
        # Log result
        if result.success:
            logger.info(f"Order {order.id[:8]} executed successfully: {result.signature}")
        else:
            logger.error(f"Order {order.id[:8]} failed: {result.error_message}")
        
        return result
    
    async def execute_buy(
        self,
        token_address: str,
        amount_sol: Decimal,
        slippage_bps: int = 100,
        priority_fee: Optional[int] = None,
    ) -> ExecutionResult:
        """
        Execute a buy order
        
        Args:
            token_address: Token to buy
            amount_sol: Amount of SOL to spend
            slippage_bps: Slippage tolerance in basis points
            priority_fee: Priority fee in microlamports
        
        Returns:
            ExecutionResult
        """
        order = Order(
            side=OrderSide.BUY,
            token_in=SOL_MINT,
            token_out=token_address,
            amount_in=amount_sol,
            slippage_bps=slippage_bps,
            priority_fee=priority_fee,
        )
        
        return await self.execute_order(order)
    
    async def execute_sell(
        self,
        token_address: str,
        amount_tokens: Decimal,
        slippage_bps: int = 100,
        priority_fee: Optional[int] = None,
    ) -> ExecutionResult:
        """
        Execute a sell order
        
        Args:
            token_address: Token to sell
            amount_tokens: Amount of tokens to sell
            slippage_bps: Slippage tolerance in basis points
            priority_fee: Priority fee in microlamports
        
        Returns:
            ExecutionResult
        """
        order = Order(
            side=OrderSide.SELL,
            token_in=token_address,
            token_out=SOL_MINT,
            amount_in=amount_tokens,
            slippage_bps=slippage_bps,
            priority_fee=priority_fee,
        )
        
        return await self.execute_order(order)
    
    async def get_token_price(
        self,
        token_address: str,
        vs_token: str = SOL_MINT,
    ) -> Optional[Decimal]:
        """
        Get token price across all venues
        
        Args:
            token_address: Token mint address
            vs_token: Quote token
        
        Returns:
            Best price or None
        """
        if not self._initialized:
            await self.initialize()
        
        prices = []
        
        # Try Jupiter
        if self.jupiter:
            try:
                price = await self.jupiter.get_token_price(token_address, vs_token)
                if price:
                    prices.append((price, "jupiter"))
            except Exception as e:
                logger.debug(f"Jupiter price error: {e}")
        
        # Try Raydium
        if self.raydium:
            try:
                price = await self.raydium.get_token_price(token_address, vs_token)
                if price:
                    prices.append((price, "raydium"))
            except Exception as e:
                logger.debug(f"Raydium price error: {e}")
        
        if not prices:
            return None
        
        # Return best price (highest for sells, lowest for buys)
        # Default to Jupiter price if available
        jupiter_prices = [p for p in prices if p[1] == "jupiter"]
        if jupiter_prices:
            return jupiter_prices[0][0]
        
        return prices[0][0]
    
    async def get_liquidity_info(
        self,
        token_address: str,
    ) -> Optional[LiquidityInfo]:
        """
        Get liquidity information for a token
        
        Args:
            token_address: Token mint address
        
        Returns:
            LiquidityInfo or None
        """
        if not self._initialized:
            await self.initialize()
        
        # Try Raydium first for graduated tokens
        if self.raydium:
            try:
                info = await self.raydium.get_liquidity_info(token_address)
                if info:
                    return info
            except Exception as e:
                logger.debug(f"Raydium liquidity error: {e}")
        
        # Try Jupiter
        if self.jupiter:
            try:
                info = await self.jupiter.get_liquidity_info(token_address)
                if info:
                    return info
            except Exception as e:
                logger.debug(f"Jupiter liquidity error: {e}")
        
        return None
    
    def add_pre_execution_callback(self, callback: Callable):
        """Add a pre-execution callback"""
        self._pre_execution_callbacks.append(callback)
    
    def add_post_execution_callback(self, callback: Callable):
        """Add a post-execution callback"""
        self._post_execution_callbacks.append(callback)
    
    def get_execution_history(self) -> List[ExecutionResult]:
        """Get execution history"""
        return self._execution_history.copy()
    
    def get_paper_portfolio(self) -> Optional[Dict[str, Any]]:
        """Get paper trading portfolio"""
        if self.paper_engine:
            return self.paper_engine.get_portfolio_value()
        return None


# Convenience functions
async def create_executor(
    config: Optional[ExecutionConfig] = None,
    wallet_keypair: Optional[Any] = None,
) -> TradeExecutor:
    """Create and initialize a trade executor"""
    executor = TradeExecutor(config, wallet_keypair)
    await executor.initialize()
    return executor


async def execute_quick_buy(
    token_address: str,
    amount_sol: Decimal,
    wallet_keypair: Any,
    slippage_bps: int = 100,
    paper_mode: bool = False,
) -> ExecutionResult:
    """
    Quick buy execution helper
    
    Args:
        token_address: Token to buy
        amount_sol: Amount of SOL to spend
        wallet_keypair: Wallet keypair
        slippage_bps: Slippage tolerance
        paper_mode: Use paper trading
    
    Returns:
        ExecutionResult
    """
    config = ExecutionConfig(paper_trading=paper_mode)
    
    async with TradeExecutor(config, wallet_keypair) as executor:
        return await executor.execute_buy(token_address, amount_sol, slippage_bps)


async def execute_quick_sell(
    token_address: str,
    amount_tokens: Decimal,
    wallet_keypair: Any,
    slippage_bps: int = 100,
    paper_mode: bool = False,
) -> ExecutionResult:
    """
    Quick sell execution helper
    
    Args:
        token_address: Token to sell
        amount_tokens: Amount of tokens to sell
        wallet_keypair: Wallet keypair
        slippage_bps: Slippage tolerance
        paper_mode: Use paper trading
    
    Returns:
        ExecutionResult
    """
    config = ExecutionConfig(paper_trading=paper_mode)
    
    async with TradeExecutor(config, wallet_keypair) as executor:
        return await executor.execute_sell(token_address, amount_tokens, slippage_bps)
