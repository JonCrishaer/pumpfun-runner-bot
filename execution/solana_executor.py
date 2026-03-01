"""
Solana Executor - Execute buys/sells via Phantom wallet + Helius RPC

This module handles live Solana execution:
- FAK (Fill-or-Kill) market buy orders
- GTC (Good-Till-Canceled) limit sell orders
- Order tracking and fill monitoring
- Integration with Kimi's Position model

Requirements:
    - Phantom wallet with private key in WALLET_PRIVATE_KEY env var
    - Helius RPC endpoint configured
    - solders (Solana SDK) installed

Usage:
    >>> executor = SolanaExecutor()
    >>> result = await executor.buy(
    ...     token_mint="abc123",
    ...     amount_sol=0.5,
    ...     fak=True,  # Fill-or-Kill
    ... )
    >>> if result.success:
    ...     print(f"✅ Bought at {result.price_paid}")
"""

import asyncio
import json
import logging
import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from decimal import Decimal

import aiohttp

from execution.models import Position, PositionStatus
from core.events import (
    TradeExecutedEvent,
    ErrorEvent,
    event_bus,
    EventPriority,
)
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of an execution attempt."""
    success: bool
    tx_signature: Optional[str] = None
    token_mint: Optional[str] = None
    amount_bought: float = 0.0  # Tokens
    amount_spent: float = 0.0  # SOL
    price_paid: float = 0.0  # SOL per token
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


class SolanaExecutor:
    """Execute orders on Solana via Phantom + Helius RPC."""
    
    # Pump.fun program ID
    PUMPFUN_PROGRAM = "6EF8rQvFVaB7wfWzK1eVYVM6SSuZjSTnXmqDq4JADwoj"
    
    # Transaction settings
    DEFAULT_PRIORITY_FEE = 10000  # microlamports
    DEFAULT_SLIPPAGE = 0.01  # 1%
    MAX_RETRIES = 3
    
    def __init__(self):
        """Initialize Solana executor."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.settings = get_settings()
        
        self.wallet_address = self.settings.wallet.wallet_address
        self.private_key = self.settings.wallet.private_key
        self.rpc_url = self.settings.solana.rpc_url
        
        # Validation
        if not self.wallet_address:
            raise ValueError("WALLET_ADDRESS not configured")
        if not self.private_key:
            raise ValueError("WALLET_PRIVATE_KEY not configured")
        if not self.rpc_url:
            raise ValueError("SOLANA_RPC_URL not configured")
        
        self.session = None
        self.pending_orders = {}  # Track in-flight orders
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
    
    async def buy(
        self,
        token_mint: str,
        amount_sol: float,
        fak: bool = True,
        slippage_pct: float = 1.0,
    ) -> ExecutionResult:
        """
        Execute a buy order.
        
        Args:
            token_mint: Token mint address
            amount_sol: Amount of SOL to spend
            fak: If True, Fill-or-Kill; else GTC (Good-Till-Canceled)
            slippage_pct: Slippage tolerance
            
        Returns:
            ExecutionResult with tx signature and fill info
        """
        self.logger.info(f"📤 Buy order: {amount_sol} SOL for {token_mint[:8]}...")
        
        try:
            # Build transaction via Helius API
            tx_result = await self._build_buy_tx(
                token_mint=token_mint,
                amount_sol=amount_sol,
                slippage_pct=slippage_pct,
                fak=fak,
            )
            
            if not tx_result:
                return ExecutionResult(
                    success=False,
                    token_mint=token_mint,
                    error_message="Failed to build transaction",
                )
            
            # Sign and send
            tx_sig = await self._sign_and_send_tx(tx_result["transaction"])
            
            if not tx_sig:
                return ExecutionResult(
                    success=False,
                    token_mint=token_mint,
                    error_message="Failed to sign/send transaction",
                )
            
            # Wait for confirmation
            confirmed = await self._wait_for_confirmation(tx_sig)
            
            if not confirmed:
                return ExecutionResult(
                    success=False,
                    tx_signature=tx_sig,
                    token_mint=token_mint,
                    error_message="Transaction failed/timed out",
                )
            
            # Parse fill details from on-chain state
            fill_info = await self._parse_fill(tx_sig, token_mint, amount_sol)
            
            result = ExecutionResult(
                success=True,
                tx_signature=tx_sig,
                token_mint=token_mint,
                amount_spent=amount_sol,
                amount_bought=fill_info.get("tokens_received", 0.0),
                price_paid=fill_info.get("price", 0.0),
            )
            
            self.logger.info(f"✅ Buy executed: {result.amount_bought} tokens for {amount_sol} SOL")
            
            # Emit event
            await self._emit_trade_event(result)
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Buy failed: {e}")
            return ExecutionResult(
                success=False,
                token_mint=token_mint,
                error_message=str(e),
            )
    
    async def sell(
        self,
        token_mint: str,
        token_amount: float,
        limit_price: Optional[float] = None,
        gtc: bool = True,
    ) -> ExecutionResult:
        """
        Execute a sell order.
        
        Args:
            token_mint: Token mint address
            token_amount: Amount of tokens to sell
            limit_price: Optional limit price (SOL per token); if None, market sell
            gtc: If True, Good-Till-Canceled limit; else FAK
            
        Returns:
            ExecutionResult
        """
        self.logger.info(f"📤 Sell order: {token_amount} tokens of {token_mint[:8]}...")
        
        try:
            # Build transaction
            tx_result = await self._build_sell_tx(
                token_mint=token_mint,
                token_amount=token_amount,
                limit_price=limit_price,
                gtc=gtc,
            )
            
            if not tx_result:
                return ExecutionResult(
                    success=False,
                    token_mint=token_mint,
                    error_message="Failed to build sell transaction",
                )
            
            # Sign and send
            tx_sig = await self._sign_and_send_tx(tx_result["transaction"])
            
            if not tx_sig:
                return ExecutionResult(
                    success=False,
                    token_mint=token_mint,
                    error_message="Failed to sign/send sell transaction",
                )
            
            # Wait for confirmation
            confirmed = await self._wait_for_confirmation(tx_sig)
            
            if not confirmed:
                return ExecutionResult(
                    success=False,
                    tx_signature=tx_sig,
                    token_mint=token_mint,
                    error_message="Sell transaction failed/timed out",
                )
            
            # Parse fill
            fill_info = await self._parse_fill(tx_sig, token_mint, token_amount)
            
            result = ExecutionResult(
                success=True,
                tx_signature=tx_sig,
                token_mint=token_mint,
                amount_bought=token_amount,  # Sold this many tokens
                amount_spent=fill_info.get("sol_received", 0.0),  # Got this much SOL
                price_paid=fill_info.get("price", 0.0),
            )
            
            self.logger.info(f"✅ Sell executed: {token_amount} tokens for {result.amount_spent} SOL")
            
            await self._emit_trade_event(result)
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Sell failed: {e}")
            return ExecutionResult(
                success=False,
                token_mint=token_mint,
                error_message=str(e),
            )
    
    async def _build_buy_tx(
        self,
        token_mint: str,
        amount_sol: float,
        slippage_pct: float,
        fak: bool,
    ) -> Optional[Dict[str, Any]]:
        """Build a buy transaction via Helius/Jupiter."""
        try:
            # Use Phantom's quote API or Jupiter for build
            # This is simplified; real implementation would use their API
            
            payload = {
                "tokenAMint": "So11111111111111111111111111111111111111112",  # WSOL
                "tokenBMint": token_mint,
                "amount": int(amount_sol * 1e9),  # Convert to lamports
                "slippageBps": int(slippage_pct * 100),
                "userPublicKey": self.wallet_address,
            }
            
            # Call Helius RPC or Jupiter API
            # Return transaction base64
            
            return {"transaction": "..."}  # Placeholder
            
        except Exception as e:
            self.logger.error(f"Error building buy tx: {e}")
            return None
    
    async def _build_sell_tx(
        self,
        token_mint: str,
        token_amount: float,
        limit_price: Optional[float],
        gtc: bool,
    ) -> Optional[Dict[str, Any]]:
        """Build a sell transaction."""
        try:
            # Similar to buy but reversed
            payload = {
                "tokenAMint": token_mint,
                "tokenBMint": "So11111111111111111111111111111111111111112",  # WSOL
                "amount": int(token_amount * 1e9),  # Convert to base units
                "userPublicKey": self.wallet_address,
            }
            
            # Call API
            return {"transaction": "..."}  # Placeholder
            
        except Exception as e:
            self.logger.error(f"Error building sell tx: {e}")
            return None
    
    async def _sign_and_send_tx(self, tx_base64: str) -> Optional[str]:
        """
        Sign transaction with private key and send to RPC.
        
        Args:
            tx_base64: Base64-encoded transaction
            
        Returns:
            Transaction signature or None
        """
        try:
            # In production, use solders library to sign
            # from solders.transaction import Transaction
            # from solders.keypair import Keypair
            
            # For now, placeholder
            self.logger.debug("Transaction signed (placeholder)")
            
            # Send via RPC
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "maxRetries": self.MAX_RETRIES,
                    }
                ],
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload) as resp:
                    data = await resp.json()
                    
                    if "result" in data:
                        return data["result"]
                    
                    self.logger.error(f"RPC error: {data.get('error')}")
                    return None
            
        except Exception as e:
            self.logger.error(f"Error signing/sending: {e}")
            return None
    
    async def _wait_for_confirmation(
        self,
        tx_sig: str,
        timeout_sec: int = 30,
    ) -> bool:
        """Wait for transaction confirmation."""
        start = asyncio.get_event_loop().time()
        
        while True:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [tx_sig, "json"],
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.rpc_url, json=payload) as resp:
                        data = await resp.json()
                        
                        if data.get("result"):
                            tx = data["result"]
                            if tx.get("meta", {}).get("err") is None:
                                self.logger.debug(f"✅ Confirmed: {tx_sig[:8]}...")
                                return True
                            else:
                                self.logger.error(f"TX failed: {tx['meta']['err']}")
                                return False
                
                # Check timeout
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed > timeout_sec:
                    self.logger.warning(f"Confirmation timeout for {tx_sig[:8]}...")
                    return False
                
                await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.debug(f"Error checking confirmation: {e}")
                await asyncio.sleep(2)
    
    async def _parse_fill(
        self,
        tx_sig: str,
        token_mint: str,
        amount: float,
    ) -> Dict[str, float]:
        """Parse transaction to extract fill details."""
        try:
            # Query RPC for transaction details
            # Extract token account changes to determine actual fill
            # This is complex; simplified version here
            
            return {
                "tokens_received": amount * 1000,  # Placeholder
                "sol_received": amount,
                "price": 0.001,  # SOL per token
            }
        except Exception as e:
            self.logger.error(f"Error parsing fill: {e}")
            return {}
    
    async def _emit_trade_event(self, result: ExecutionResult):
        """Emit trade execution event."""
        try:
            event = TradeExecutedEvent(
                token_address=result.token_mint or "",
                direction="buy" if result.amount_spent > 0 else "sell",
                amount_tokens=result.amount_bought,
                amount_sol=result.amount_spent,
                price=result.price_paid,
                tx_signature=result.tx_signature or "",
                timestamp=result.timestamp,
                source="solana_executor",
            )
            
            await event_bus.emit(event, priority=EventPriority.HIGH)
        except Exception as e:
            self.logger.error(f"Error emitting trade event: {e}")
