"""
Bonding Curve Monitor v2 - WORKING Bitquery V2 GraphQL Query

Uses VERIFIED Bitquery V2 GraphQL schema to fetch real Pump.fun tokens.
Query tested and confirmed working Feb 28, 2026.
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

# Pump.fun bonding curve constants
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MIN_BOND_BALANCE_LAMPORTS = 206900000  # SOL lamports at start
MAX_BOND_BALANCE_LAMPORTS = 793100000  # SOL lamports at graduation
BOND_RANGE = MAX_BOND_BALANCE_LAMPORTS - MIN_BOND_BALANCE_LAMPORTS


@dataclass(frozen=True)
class BondingCurveSnapshot:
    """Real-time bonding curve state snapshot."""
    token_address: str
    symbol: str
    timestamp: datetime
    progress_pct: float  # 0.0 - 1.0
    sol_balance: float  # SOL in bonding curve
    sol_raised: float  # SOL raised (in Quote token)


class BondingCurveMonitorV2:
    """Monitor pump.fun bonding curves via VERIFIED Bitquery V2 GraphQL API."""
    
    GRAPHQL_ENDPOINT = "https://streaming.bitquery.io/graphql"
    
    # Working GraphQL query (tested Feb 28, 2026 on streaming.bitquery.io)
    QUERY = """query GetTopPumpFunTokens {
  Solana {
    DEXPools(
      limit: { count: 50 }
      orderBy: { descending: Block_Slot }
      where: {
        Pool: {
          Dex: {
            ProgramAddress: { is: "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" }
          }
        }
      }
    ) {
      Pool {
        Market {
          BaseCurrency {
            MintAddress
            Symbol
          }
        }
        Base {
          PostAmount
        }
        Quote {
          PostAmount
        }
      }
    }
  }
}"""
    
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
        
        # Start background polling task
        asyncio.create_task(self._polling_loop())
    
    async def stop(self):
        """Stop polling."""
        self.running = False
        self.logger.info("Stopping BondingCurveMonitor v2")
    
    async def _polling_loop(self):
        """Main polling loop - fetch every N seconds."""
        while self.running:
            try:
                await self._fetch_and_process()
            except Exception as e:
                self.logger.exception(f"Error in polling loop: {e}")
            
            # Wait before next poll
            await asyncio.sleep(self.poll_interval)
    
    async def _fetch_and_process(self):
        """Fetch latest bonding curves from Bitquery and emit events."""
        try:
            pools = await self._query_bitquery()
            
            if not pools:
                self.logger.debug("No pools returned from Bitquery")
                return
            
            # Process each pool
            for pool_data in pools:
                await self._process_pool(pool_data)
                
        except Exception as e:
            self.logger.exception(f"Error fetching/processing pools: {e}")
    
    async def _query_bitquery(self) -> List[Dict[str, Any]]:
        """Query Bitquery V2 API for Pump.fun pools."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "query": self.QUERY
                }
                
                async with session.post(
                    self.GRAPHQL_ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        self.logger.error(f"Bitquery error: {resp.status}")
                        return []
                    
                    data = await resp.json()
                    
                    # Check for GraphQL errors
                    if "errors" in data:
                        self.logger.error(f"GraphQL error: {data['errors']}")
                        return []
                    
                    # Extract pools
                    pools = data.get("data", {}).get("Solana", {}).get("DEXPools", [])
                    return pools
                    
        except asyncio.TimeoutError:
            self.logger.error("Bitquery query timeout")
            return []
        except Exception as e:
            self.logger.exception(f"Error querying Bitquery: {e}")
            return []
    
    async def _process_pool(self, pool_data: Dict[str, Any]):
        """Process a single pool and emit event if relevant."""
        try:
            # Extract data
            pool = pool_data.get("Pool", {})
            market = pool.get("Market", {})
            base_currency = market.get("BaseCurrency", {})
            base = pool.get("Base", {})
            quote = pool.get("Quote", {})
            
            token_address = base_currency.get("MintAddress", "")
            symbol = base_currency.get("Symbol", "UNKNOWN")
            
            if not token_address:
                return
            
            # Parse amounts (strings from GraphQL)
            try:
                sol_balance = float(base.get("PostAmount", 0)) / 1e9  # Convert lamports to SOL
                sol_raised = float(quote.get("PostAmount", 0))  # Already in SOL
            except (ValueError, TypeError):
                return
            
            # Calculate bonding curve progress (0.0 - 1.0)
            balance_lamports = sol_balance * 1e9
            
            if balance_lamports >= MAX_BOND_BALANCE_LAMPORTS:
                # Token has graduated
                progress_pct = 1.0
            elif balance_lamports <= MIN_BOND_BALANCE_LAMPORTS:
                progress_pct = 0.0
            else:
                progress_pct = (balance_lamports - MIN_BOND_BALANCE_LAMPORTS) / BOND_RANGE
            
            # Only emit if at or above threshold
            if progress_pct >= self.threshold_pct:
                # Emit event with real data
                event = BondingCurveUpdateEvent(
                    token_address=token_address,
                    progress_pct=progress_pct,
                    sol_raised=sol_raised,
                    target_sol=1.0,  # Pump.fun target
                    holders=0,  # Not available from this query
                    timestamp=datetime.utcnow(),
                    source=f"bonding_curve_v2_{symbol}"
                )
                
                await event_bus.emit(event)
                
                # Log for debugging
                self.logger.info(f"📊 Bonding curve update: {symbol} ({token_address[:8]}...) @ {progress_pct*100:.1f}%")
                
                # Update tracking
                self._seen_tokens[token_address] = progress_pct
                
        except Exception as e:
            self.logger.exception(f"Error processing pool: {e}")
