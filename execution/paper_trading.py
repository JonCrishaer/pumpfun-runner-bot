"""
Paper Trading Module for Pump.fun Trading System

Simulates trade execution at current market prices without real transactions.
Tracks virtual P&L and portfolio state for strategy testing.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from models import (
    Order, OrderSide, OrderType, OrderStatus,
    ExecutionResult, ExecutionVenue, Position,
    WalletState, TokenInfo, RouteInfo
)

# Setup logging
logger = logging.getLogger(__name__)

# Default simulation parameters
DEFAULT_INITIAL_SOL = Decimal("10")  # 10 SOL starting balance
SOL_PRICE_USD = Decimal("100")  # $100 per SOL for P&L calculation
DEFAULT_SLIPPAGE_SIM = Decimal("0.005")  # 0.5% simulated slippage


@dataclass
class PaperTrade:
    """Paper trade record"""
    id: str
    timestamp: datetime
    side: OrderSide
    token_in: str
    token_out: str
    amount_in: Decimal
    amount_out: Decimal
    price: Decimal
    simulated_slippage: Decimal
    venue: ExecutionVenue
    
    def __post_init__(self):
        for field_name in ['amount_in', 'amount_out', 'price', 'simulated_slippage']:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))


@dataclass
class PortfolioSnapshot:
    """Portfolio state snapshot"""
    timestamp: datetime
    sol_balance: Decimal
    token_balances: Dict[str, Decimal]
    positions_value_sol: Decimal
    total_value_sol: Decimal
    total_pnl_sol: Decimal
    total_pnl_pct: float
    
    def __post_init__(self):
        for field_name in ['sol_balance', 'positions_value_sol', 'total_value_sol', 'total_pnl_sol']:
            value = getattr(self, field_name)
            if isinstance(value, (int, float, str)):
                setattr(self, field_name, Decimal(str(value)))
        
        self.token_balances = {
            k: Decimal(str(v)) if isinstance(v, (int, float, str)) else v
            for k, v in self.token_balances.items()
        }


class PaperTradingEngine:
    """
    Paper Trading Engine
    
    Simulates:
    - Trade execution at market prices
    - Portfolio tracking
    - P&L calculation
    - Position management
    - Fee simulation
    """
    
    def __init__(
        self,
        initial_sol: Decimal = DEFAULT_INITIAL_SOL,
        sol_price_usd: Decimal = SOL_PRICE_USD,
        simulate_slippage: bool = True,
        default_slippage: Decimal = DEFAULT_SLIPPAGE_SIM,
        simulate_fees: bool = True,
        trading_fee_bps: int = 30,  # 0.3% default
    ):
        self.initial_sol = initial_sol
        self.sol_price_usd = sol_price_usd
        self.simulate_slippage = simulate_slippage
        self.default_slippage = default_slippage
        self.simulate_fees = simulate_fees
        self.trading_fee_bps = trading_fee_bps
        
        # Portfolio state
        self.sol_balance = initial_sol
        self.token_balances: Dict[str, Decimal] = {}
        self.positions: Dict[str, Position] = {}
        
        # Trade history
        self.trades: List[PaperTrade] = []
        self.portfolio_history: List[PortfolioSnapshot] = []
        
        # Price cache
        self._price_cache: Dict[str, Decimal] = {}
        
        logger.info(f"PaperTradingEngine initialized with {initial_sol} SOL")
    
    def reset(self):
        """Reset the paper trading state"""
        self.sol_balance = self.initial_sol
        self.token_balances.clear()
        self.positions.clear()
        self.trades.clear()
        self.portfolio_history.clear()
        self._price_cache.clear()
        
        logger.info("Paper trading state reset")
    
    def get_wallet_state(self) -> WalletState:
        """Get current wallet state"""
        return WalletState(
            address="PAPER_TRADING_WALLET",
            sol_balance=self.sol_balance,
            token_accounts=self.token_balances.copy(),
        )
    
    def set_token_price(
        self,
        token_address: str,
        price_in_sol: Decimal,
    ):
        """Set token price for simulation"""
        self._price_cache[token_address] = price_in_sol
    
    def get_token_price(self, token_address: str) -> Optional[Decimal]:
        """Get cached token price"""
        return self._price_cache.get(token_address)
    
    def calculate_simulated_slippage(
        self,
        amount: Decimal,
        token_address: str,
        side: OrderSide,
    ) -> Decimal:
        """
        Calculate simulated slippage based on trade size
        
        Args:
            amount: Trade amount
            token_address: Token being traded
            side: Buy or sell
        
        Returns:
            Simulated slippage as decimal
        """
        if not self.simulate_slippage:
            return Decimal("0")
        
        # Base slippage
        slippage = self.default_slippage
        
        # Increase slippage for larger trades (simplified model)
        # Assume $10K pool depth
        pool_depth_sol = Decimal("100")  # 100 SOL equivalent
        
        if pool_depth_sol > 0:
            trade_ratio = amount / pool_depth_sol
            size_slippage = trade_ratio * Decimal("0.02")  # 2% max for full pool
            slippage += size_slippage
        
        # Cap at reasonable limit
        return min(slippage, Decimal("0.15"))  # Max 15%
    
    async def execute_buy(
        self,
        order: Order,
        token_price_sol: Optional[Decimal] = None,
    ) -> ExecutionResult:
        """
        Simulate a buy order
        
        Args:
            order: Buy order
            token_price_sol: Token price in SOL (uses cached if not provided)
        
        Returns:
            ExecutionResult
        """
        result = ExecutionResult(
            order_id=order.id,
            success=False,
            venue=ExecutionVenue.PAPER,
            amount_in=order.amount_in,
        )
        
        # Get token price
        price = token_price_sol or self.get_token_price(order.token_out)
        
        if price is None or price <= 0:
            result.error_message = "No price available for token"
            result.logs.append("Price unavailable")
            logger.warning(f"No price for {order.token_out}")
            return result
        
        # Check SOL balance
        if self.sol_balance < order.amount_in:
            result.error_message = f"Insufficient SOL balance: {self.sol_balance} < {order.amount_in}"
            result.logs.append("Insufficient balance")
            logger.warning(f"Insufficient SOL: {self.sol_balance} < {order.amount_in}")
            return result
        
        try:
            # Calculate expected tokens
            expected_tokens = order.amount_in / price
            
            # Apply simulated slippage
            slippage = self.calculate_simulated_slippage(
                order.amount_in, order.token_out, OrderSide.BUY
            )
            
            actual_tokens = expected_tokens * (Decimal("1") - slippage)
            
            # Apply trading fee
            if self.simulate_fees:
                fee = actual_tokens * Decimal(self.trading_fee_bps) / Decimal("10000")
                actual_tokens -= fee
            
            # Update state
            self.sol_balance -= order.amount_in
            
            current_balance = self.token_balances.get(order.token_out, Decimal("0"))
            self.token_balances[order.token_out] = current_balance + actual_tokens
            
            # Update or create position
            if order.token_out in self.positions:
                position = self.positions[order.token_out]
                # Average down/up
                total_tokens = position.entry_amount + actual_tokens
                total_cost = (position.entry_price * position.entry_amount) + \
                            (price * actual_tokens)
                position.entry_price = total_cost / total_tokens
                position.entry_amount = total_tokens
                position.entry_value_sol += order.amount_in
                position.current_amount = total_tokens
            else:
                self.positions[order.token_out] = Position(
                    token_address=order.token_out,
                    token_symbol="UNKNOWN",  # Would be set from token info
                    entry_price=price,
                    entry_amount=actual_tokens,
                    entry_value_sol=order.amount_in,
                    current_amount=actual_tokens,
                )
            
            # Record trade
            trade = PaperTrade(
                id=order.id,
                timestamp=datetime.utcnow(),
                side=OrderSide.BUY,
                token_in=order.token_in,
                token_out=order.token_out,
                amount_in=order.amount_in,
                amount_out=actual_tokens,
                price=price,
                simulated_slippage=slippage,
                venue=ExecutionVenue.PAPER,
            )
            self.trades.append(trade)
            
            # Set result
            result.success = True
            result.status = OrderStatus.CONFIRMED
            result.amount_out = actual_tokens
            result.expected_amount_out = expected_tokens
            result.price = price
            result.slippage_bps = int(slippage * 10000)
            result.signature = f"PAPER_{order.id}"
            result.logs.append(f"Paper buy: {order.amount_in} SOL -> {actual_tokens} tokens @ {price}")
            
            logger.info(f"Paper buy executed: {order.amount_in} SOL -> {actual_tokens} tokens")
            
        except Exception as e:
            result.error_message = f"Paper buy error: {e}"
            result.logs.append(f"Error: {e}")
            logger.exception("Paper buy error")
        
        return result
    
    async def execute_sell(
        self,
        order: Order,
        token_price_sol: Optional[Decimal] = None,
    ) -> ExecutionResult:
        """
        Simulate a sell order
        
        Args:
            order: Sell order
            token_price_sol: Token price in SOL (uses cached if not provided)
        
        Returns:
            ExecutionResult
        """
        result = ExecutionResult(
            order_id=order.id,
            success=False,
            venue=ExecutionVenue.PAPER,
            amount_in=order.amount_in,
        )
        
        # Get token price
        price = token_price_sol or self.get_token_price(order.token_in)
        
        if price is None or price <= 0:
            result.error_message = "No price available for token"
            result.logs.append("Price unavailable")
            return result
        
        # Check token balance
        current_balance = self.token_balances.get(order.token_in, Decimal("0"))
        
        if current_balance < order.amount_in:
            result.error_message = f"Insufficient token balance: {current_balance} < {order.amount_in}"
            result.logs.append("Insufficient balance")
            logger.warning(f"Insufficient tokens: {current_balance} < {order.amount_in}")
            return result
        
        try:
            # Calculate expected SOL
            expected_sol = order.amount_in * price
            
            # Apply simulated slippage
            slippage = self.calculate_simulated_slippage(
                order.amount_in, order.token_in, OrderSide.SELL
            )
            
            actual_sol = expected_sol * (Decimal("1") - slippage)
            
            # Apply trading fee
            if self.simulate_fees:
                fee = actual_sol * Decimal(self.trading_fee_bps) / Decimal("10000")
                actual_sol -= fee
            
            # Update state
            self.token_balances[order.token_in] = current_balance - order.amount_in
            self.sol_balance += actual_sol
            
            # Update position
            if order.token_in in self.positions:
                position = self.positions[order.token_in]
                position.record_partial_exit(order.amount_in, price, actual_sol)
                
                # Remove position if fully closed
                if position.current_amount <= 0:
                    del self.positions[order.token_in]
            
            # Record trade
            trade = PaperTrade(
                id=order.id,
                timestamp=datetime.utcnow(),
                side=OrderSide.SELL,
                token_in=order.token_in,
                token_out=order.token_out,
                amount_in=order.amount_in,
                amount_out=actual_sol,
                price=price,
                simulated_slippage=slippage,
                venue=ExecutionVenue.PAPER,
            )
            self.trades.append(trade)
            
            # Set result
            result.success = True
            result.status = OrderStatus.CONFIRMED
            result.amount_out = actual_sol
            result.expected_amount_out = expected_sol
            result.price = price
            result.slippage_bps = int(slippage * 10000)
            result.signature = f"PAPER_{order.id}"
            result.logs.append(f"Paper sell: {order.amount_in} tokens -> {actual_sol} SOL @ {price}")
            
            logger.info(f"Paper sell executed: {order.amount_in} tokens -> {actual_sol} SOL")
            
        except Exception as e:
            result.error_message = f"Paper sell error: {e}"
            result.logs.append(f"Error: {e}")
            logger.exception("Paper sell error")
        
        return result
    
    async def execute_order(
        self,
        order: Order,
        token_price_sol: Optional[Decimal] = None,
    ) -> ExecutionResult:
        """
        Execute an order (buy or sell)
        
        Args:
            order: Order to execute
            token_price_sol: Token price in SOL
        
        Returns:
            ExecutionResult
        """
        if order.side == OrderSide.BUY:
            return await self.execute_buy(order, token_price_sol)
        else:
            return await self.execute_sell(order, token_price_sol)
    
    def get_portfolio_value(self) -> Dict[str, Any]:
        """
        Calculate current portfolio value
        
        Returns:
            Portfolio value breakdown
        """
        # SOL value
        sol_value = self.sol_balance
        
        # Token values
        token_values = {}
        total_token_value = Decimal("0")
        
        for token, balance in self.token_balances.items():
            price = self.get_token_price(token)
            if price:
                value = balance * price
                token_values[token] = {
                    "balance": balance,
                    "price_sol": price,
                    "value_sol": value,
                }
                total_token_value += value
        
        # Total value
        total_value = sol_value + total_token_value
        
        # P&L
        total_pnl = total_value - self.initial_sol
        pnl_pct = float(total_pnl / self.initial_sol * 100) if self.initial_sol > 0 else 0
        
        return {
            "sol_balance": self.sol_balance,
            "sol_value_sol": sol_value,
            "token_values": token_values,
            "tokens_value_sol": total_token_value,
            "total_value_sol": total_value,
            "total_value_usd": total_value * self.sol_price_usd,
            "total_pnl_sol": total_pnl,
            "total_pnl_usd": total_pnl * self.sol_price_usd,
            "total_pnl_pct": pnl_pct,
            "initial_sol": self.initial_sol,
        }
    
    def record_portfolio_snapshot(self) -> PortfolioSnapshot:
        """Record current portfolio state"""
        portfolio = self.get_portfolio_value()
        
        snapshot = PortfolioSnapshot(
            timestamp=datetime.utcnow(),
            sol_balance=portfolio["sol_balance"],
            token_balances=self.token_balances.copy(),
            positions_value_sol=portfolio["tokens_value_sol"],
            total_value_sol=portfolio["total_value_sol"],
            total_pnl_sol=portfolio["total_pnl_sol"],
            total_pnl_pct=portfolio["total_pnl_pct"],
        )
        
        self.portfolio_history.append(snapshot)
        return snapshot
    
    def get_position_pnl(self, token_address: str) -> Dict[str, Any]:
        """
        Get P&L for a specific position
        
        Args:
            token_address: Token address
        
        Returns:
            Position P&L data
        """
        if token_address not in self.positions:
            return {"error": "No position found"}
        
        position = self.positions[token_address]
        current_price = self.get_token_price(token_address)
        
        if not current_price:
            return {"error": "No price available"}
        
        # Unrealized P&L
        current_value = position.current_amount * current_price
        cost_basis = position.entry_amount * position.entry_price
        unrealized_pnl = current_value - cost_basis
        unrealized_pct = float(unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0
        
        # Realized P&L
        realized_pnl = position.realized_pnl_sol
        
        return {
            "token": token_address,
            "entry_price": position.entry_price,
            "current_price": current_price,
            "position_size": position.current_amount,
            "cost_basis_sol": cost_basis,
            "current_value_sol": current_value,
            "unrealized_pnl_sol": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pct,
            "realized_pnl_sol": realized_pnl,
            "total_pnl_sol": unrealized_pnl + realized_pnl,
        }
    
    def get_trade_history(self) -> List[Dict[str, Any]]:
        """Get trade history"""
        return [
            {
                "id": trade.id,
                "timestamp": trade.timestamp.isoformat(),
                "side": trade.side.value,
                "token_in": trade.token_in,
                "token_out": trade.token_out,
                "amount_in": str(trade.amount_in),
                "amount_out": str(trade.amount_out),
                "price": str(trade.price),
                "slippage": str(trade.simulated_slippage),
            }
            for trade in self.trades
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get trading statistics"""
        if not self.trades:
            return {"message": "No trades yet"}
        
        buys = [t for t in self.trades if t.side == OrderSide.BUY]
        sells = [t for t in self.trades if t.side == OrderSide.SELL]
        
        total_buy_volume = sum(t.amount_in for t in buys)
        total_sell_volume = sum(t.amount_out for t in sells)
        
        # Win rate (based on sells with profit)
        profitable_sells = 0
        for sell in sells:
            # Find corresponding buy
            buy_price = None
            for buy in buys:
                if buy.token_out == sell.token_in:
                    buy_price = buy.price
                    break
            
            if buy_price and sell.price > buy_price:
                profitable_sells += 1
        
        win_rate = profitable_sells / len(sells) * 100 if sells else 0
        
        portfolio = self.get_portfolio_value()
        
        return {
            "total_trades": len(self.trades),
            "buys": len(buys),
            "sells": len(sells),
            "total_buy_volume_sol": str(total_buy_volume),
            "total_sell_volume_sol": str(total_sell_volume),
            "win_rate_pct": win_rate,
            "current_pnl_sol": str(portfolio["total_pnl_sol"]),
            "current_pnl_usd": str(portfolio["total_pnl_usd"]),
            "current_pnl_pct": portfolio["total_pnl_pct"],
        }
    
    def export_results(self) -> Dict[str, Any]:
        """Export all paper trading results"""
        return {
            "initial_balance_sol": str(self.initial_sol),
            "final_portfolio": self.get_portfolio_value(),
            "statistics": self.get_statistics(),
            "trades": self.get_trade_history(),
            "positions": [
                {
                    "token": pos.token_address,
                    "entry_price": str(pos.entry_price),
                    "entry_amount": str(pos.entry_amount),
                    "current_amount": str(pos.current_amount),
                    "realized_pnl": str(pos.realized_pnl_sol),
                }
                for pos in self.positions.values()
            ],
        }


class PaperTradingBacktest:
    """
    Backtesting engine using paper trading
    """
    
    def __init__(
        self,
        engine: PaperTradingEngine,
        price_data: Dict[str, List[Dict[str, Any]]],
    ):
        self.engine = engine
        self.price_data = price_data
        self.results: List[Dict[str, Any]] = []
    
    async def run_backtest(
        self,
        signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Run backtest with trading signals
        
        Args:
            signals: List of trading signals with timestamp, token, action, etc.
        
        Returns:
            Backtest results
        """
        self.engine.reset()
        
        for signal in signals:
            timestamp = signal["timestamp"]
            token = signal["token"]
            action = signal["action"]  # "buy" or "sell"
            amount = Decimal(str(signal["amount"]))
            
            # Get price at timestamp
            price = self._get_price_at_time(token, timestamp)
            
            if price:
                self.engine.set_token_price(token, price)
                
                # Create order
                order = Order(
                    side=OrderSide.BUY if action == "buy" else OrderSide.SELL,
                    token_in="SOL" if action == "buy" else token,
                    token_out=token if action == "buy" else "SOL",
                    amount_in=amount,
                )
                
                # Execute
                result = await self.engine.execute_order(order, price)
                
                self.results.append({
                    "timestamp": timestamp.isoformat(),
                    "signal": signal,
                    "result": result.to_dict(),
                })
        
        return self.engine.export_results()
    
    def _get_price_at_time(
        self,
        token: str,
        timestamp: datetime,
    ) -> Optional[Decimal]:
        """Get token price at specific time"""
        if token not in self.price_data:
            return None
        
        # Find closest price point
        prices = self.price_data[token]
        closest = None
        closest_diff = None
        
        for p in prices:
            p_time = p["timestamp"]
            if isinstance(p_time, str):
                p_time = datetime.fromisoformat(p_time)
            
            diff = abs((p_time - timestamp).total_seconds())
            
            if closest_diff is None or diff < closest_diff:
                closest = p
                closest_diff = diff
        
        if closest:
            return Decimal(str(closest["price"]))
        
        return None


# Convenience functions
def create_paper_trading_engine(
    initial_sol: Decimal = DEFAULT_INITIAL_SOL,
    simulate_slippage: bool = True,
    simulate_fees: bool = True,
) -> PaperTradingEngine:
    """Create a paper trading engine"""
    return PaperTradingEngine(
        initial_sol=initial_sol,
        simulate_slippage=simulate_slippage,
        simulate_fees=simulate_fees,
    )
