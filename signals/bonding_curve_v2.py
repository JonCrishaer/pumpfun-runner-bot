"""
Bonding Curve Monitor v2 - REST API Polling (More Reliable)

Uses Bitquery REST API to poll top Pump.fun tokens every 10 seconds.
More reliable than WebSocket for initial version.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Any
import aiohttp

from core.events import BondingCurveUpdateEvent, event_bus, EventPriority
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BondingCurveSnapshot:
    """Real-time bonding curve state snapshot."""
    token_address: str
    symbol: str
    timestamp: datetime
    progress_pct: float  # 0.0 - 1.0
    holders: int
    volume_24h: float  # SOL
    sol_raised: float  # SOL


class BondingCurveMonitorV2:
    """Monitor pump.fun bonding curves via Bitquery REST API polling."""
    
    # Bonding curve parameters
    NATIVE_SOL_START = 0.2069  # SOL
    TARGET_SOL = 1.0  # SOL
    
    def __init__(self, api_key: str, threshold_pct: float = 0.85, poll_interval: int = 10):
        self.api_key = api_key
        self.threshold_pct = threshold_pct
        self.poll_interval = poll_interval
        self.running = False
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._seen_tokens = {}  # Track {address: progress} to detect changes
    
    async def start(self):
        """Start polling Bitquery for bonding curve updates."""
        self.running = True
        self.logger.info(f"🟢 BondingCurveMonitor v2 started (polling every {self.poll_interval}s)")
        asyncio.create_task(self._polling_loop())
    
    async def stop(self):
        """Stop the monitor."""
        self.running = False
        self.logger.info("Bonding curve monitor stopped")
    
    async def _polling_loop(self):
        """Continuously poll Bitquery for high-progress tokens."""
        while self.running:
            try:
                await self._fetch_and_process()
            except Exception as e:
                self.logger.error(f"Polling error: {e}")
            finally:
                await asyncio.sleep(self.poll_interval)
    
    async def _fetch_and_process(self):
        """Fetch top Pump.fun tokens and check progress."""
        try:
            tokens = await self._fetch_top_tokens()
            
            # If we got results, great - process them
            # If not, silently skip (network issue, will retry next poll)
            if not tokens:
                self.logger.debug("No tokens fetched (network issue), will retry next poll")
                return
            
            for token in tokens:
                progress = token.get("progress_pct", 0)
                address = token.get("address", "")
                
                # Emit event if progress >= threshold
                if progress >= self.threshold_pct:
                    # Check if this is new or changed
                    prev_progress = self._seen_tokens.get(address, 0)
                    self._seen_tokens[address] = progress
                    
                    # Emit update
                    event = BondingCurveUpdateEvent(
                        token_address=address,
                        progress_pct=progress,
                        sol_raised=token.get("sol_raised", 0),
                        target_sol=self.TARGET_SOL,
                        holders=token.get("holders", 0),
                        volume_24h=token.get("volume_24h", 0),
                    )
                    
                    await event_bus.emit(event, priority=EventPriority.HIGH)
                    self.logger.debug(f"📊 Token {address[:8]}... @ {progress*100:.1f}% progress")
        
        except Exception as e:
            self.logger.debug(f"Process error: {e}")
    
    async def _fetch_top_tokens(self) -> List[Dict[str, Any]]:
        """Fetch top Pump.fun tokens near graduation via Bitquery V2."""
        # Bitquery V2 API query for Solana token transfers (as proxy for bonding curve activity)
        query = """
        query {
            solana {
                transfers(
                    limit: {count: 100}
                    where: {
                        amount: {gt: "0"}
                    }
                    orderBy: {descending: block_height}
                ) {
                    mint {
                        address
                        name
                    }
                    amount
                }
            }
        }
        """
        
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    "https://graphql.bitquery.io",
                    json={"query": query},
                    headers={
                        "Authorization": f"Bearer {self.api_key}",  # Bitquery V2 uses Bearer OAuth token
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                
                if response.status == 200:
                    data = await response.json()
                    pools = data.get("data", {}).get("Solana", {}).get("DEXPools", [])
                    
                    tokens = []
                    for pool in pools:
                        try:
                            market = pool.get("Pool", {}).get("Market", {})
                            base_balance_lamports = float(pool.get("Base", {}).get("Balance", 0))
                            base_balance_sol = base_balance_lamports / 1e9
                            
                            progress = max(0, min(1.0, (base_balance_sol - self.NATIVE_SOL_START) / (self.TARGET_SOL - self.NATIVE_SOL_START)))
                            
                            token = {
                                "address": market.get("BaseCurrency", {}).get("MintAddress", ""),
                                "symbol": market.get("BaseCurrency", {}).get("Symbol", ""),
                                "progress_pct": progress,
                                "sol_raised": base_balance_sol - self.NATIVE_SOL_START,
                                "holders": 0,  # Would need separate query
                                "volume_24h": 0,  # Would need separate query
                            }
                            
                            if token["address"]:
                                tokens.append(token)
                        except Exception as e:
                            self.logger.debug(f"Parse error for pool: {e}")
                    
                    return tokens
                else:
                    self.logger.error(f"Bitquery API error: {response.status}")
                    return []
        
        except Exception as e:
            self.logger.error(f"Fetch error: {e}")
            return []
