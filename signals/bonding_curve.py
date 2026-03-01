"""
Bonding Curve Monitor - Real-time Bitquery WebSocket Integration

Monitors pump.fun bonding curve progress in real-time using Bitquery GraphQL WebSocket.
Detects tokens at >85% progress and emits signals for runner detection.

Real-time progress calculation:
    progress = 100 - (((balance - 206900000) × 100) / 793100000)
    - Native SOL balance start: 206,900,000 lamports (~0.2 SOL)
    - Target: 1,000,000,000 lamports (1 SOL)
    - Bonding curve range: 793,100,000 lamports

Usage:
    >>> monitor = BondingCurveMonitor(api_key="your_bitquery_key")
    >>> await monitor.start()
    >>> # Emits BondingCurveUpdateEvent when progress > threshold
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable, Dict, Any
import aiohttp
import websockets

from core.events import (
    BaseEvent,
    BondingCurveUpdateEvent,
    event_bus,
    EventPriority,
)
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BondingCurveSnapshot:
    """Real-time bonding curve state snapshot."""
    token_address: str
    timestamp: datetime
    native_balance: float  # SOL
    progress_pct: float  # 0.0 - 1.0
    holders: int
    volume_24h: float  # SOL
    volume_1h: float  # SOL
    sol_raised: float  # SOL
    target_sol: float  # 1.0


class BondingCurveMonitor:
    """Monitor pump.fun bonding curve progress via Bitquery WebSocket."""
    
    # Bonding curve parameters (from pump.fun spec)
    NATIVE_SOL_START = 0.2069  # SOL (starting balance)
    TARGET_SOL = 1.0  # SOL (graduation threshold)
    BONDING_RANGE_SOL = TARGET_SOL - NATIVE_SOL_START  # ~0.7931 SOL
    
    # Bitquery GraphQL subscription
    BITQUERY_WS_URL = "wss://streaming.bitquery.io/graphql"
    
    SUBSCRIPTION_QUERY = """
    subscription {
        Solana {
            Instructions(
                where: {
                    Instruction: {
                        Program: {
                            Address: {
                                is: "6EF8rQvFVaB7wfWzK1eVYVM6SSuZjSTnXmqDq4JADwoj"  # pump.fun program
                            }
                        }
                    }
                }
                limit: {count: 100}
            ) {
                Instruction {
                    Data
                }
                Transaction {
                    Signature
                }
            }
        }
    }
    """
    
    def __init__(self, api_key: str, on_update: Optional[Callable] = None):
        """
        Initialize bonding curve monitor.
        
        Args:
            api_key: Bitquery API key
            on_update: Optional callback for curve updates
        """
        self.api_key = api_key
        self.on_update = on_update
        self.running = False
        self.ws = None
        self.tokens_seen = {}  # Track tokens: {address: snapshot}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    async def start(self):
        """Start WebSocket listener for bonding curve updates."""
        self.running = True
        await self._connect_and_listen()
    
    async def stop(self):
        """Stop the monitor."""
        self.running = False
        if self.ws:
            await self.ws.close()
    
    async def _connect_and_listen(self):
        """Connect to Bitquery WebSocket and listen for updates."""
        while self.running:
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                
                async with websockets.connect(
                    self.BITQUERY_WS_URL,
                    extra_headers=headers,
                    close_timeout=30,
                ) as self.ws:
                    self.logger.info("✅ Connected to Bitquery WebSocket")
                    
                    # Send subscription
                    await self.ws.send(json.dumps({
                        "id": "1",
                        "type": "start",
                        "payload": {
                            "query": self.SUBSCRIPTION_QUERY,
                        },
                    }))
                    
                    # Listen for updates
                    async for message in self.ws:
                        if not self.running:
                            break
                        
                        try:
                            data = json.loads(message)
                            await self._process_update(data)
                        except Exception as e:
                            self.logger.error(f"Error processing message: {e}")
                            
            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
                if self.running:
                    await asyncio.sleep(5)  # Backoff before retry
    
    async def _process_update(self, data: Dict[str, Any]):
        """Process Bitquery update message."""
        try:
            # Parse Bitquery response structure
            if "type" == "data":
                payload = data.get("payload", {})
                solana = payload.get("data", {}).get("Solana", {})
                instructions = solana.get("Instructions", [])
                
                for instr in instructions:
                    await self._decode_and_emit(instr)
                    
        except Exception as e:
            self.logger.debug(f"Could not parse update: {e}")
    
    async def _decode_and_emit(self, instruction: Dict[str, Any]):
        """
        Decode pump.fun instruction and emit bonding curve event if relevant.
        
        This is a simplified version. Real implementation would:
        1. Decode the instruction data
        2. Extract token address and current native balance
        3. Calculate bonding curve progress
        4. Emit signal if >85%
        """
        try:
            # In production, you'd decode the instruction data here
            # This is a placeholder that would emit events
            pass
        except Exception as e:
            self.logger.debug(f"Could not decode instruction: {e}")
    
    async def fetch_token_state(self, token_address: str) -> Optional[BondingCurveSnapshot]:
        """
        Fetch current bonding curve state for a token via REST API.
        
        Args:
            token_address: Pump.fun token mint address
            
        Returns:
            BondingCurveSnapshot or None if not found
        """
        try:
            # Query Birdeye or Dune for pump.fun bonding curve state
            # This is a placeholder; real implementation would call the API
            
            query = f"""
            {{
                Solana {{
                    Tokens(
                        where: {{
                            Token: {{
                                MintAddress: {{
                                    is: "{token_address}"
                                }}
                            }}
                        }}
                    ) {{
                        Token {{
                            MintAddress
                            Symbol
                        }}
                        Holders
                    }}
                }}
            }}
            """
            
            # Call Bitquery or alternative API
            # Return structured snapshot
            pass
            
        except Exception as e:
            self.logger.error(f"Error fetching token state: {e}")
            return None
    
    @staticmethod
    def calculate_progress(native_balance_sol: float) -> float:
        """
        Calculate bonding curve progress percentage.
        
        Args:
            native_balance_sol: Current native SOL balance
            
        Returns:
            Progress as 0.0 to 1.0
        """
        if native_balance_sol < BondingCurveMonitor.NATIVE_SOL_START:
            return 0.0
        
        if native_balance_sol >= BondingCurveMonitor.TARGET_SOL:
            return 1.0
        
        raised = native_balance_sol - BondingCurveMonitor.NATIVE_SOL_START
        progress = raised / BondingCurveMonitor.BONDING_RANGE_SOL
        return min(1.0, max(0.0, progress))
    
    async def emit_curve_update(
        self,
        token_address: str,
        progress_pct: float,
        sol_raised: float,
        holders: int,
        volume_24h: float,
    ):
        """Emit a bonding curve update event."""
        event = BondingCurveUpdateEvent(
            token_address=token_address,
            progress_pct=progress_pct,
            sol_raised=sol_raised,
            target_sol=self.TARGET_SOL,
            holders=holders,
            source="bonding_curve_monitor",
        )
        
        await event_bus.emit(event, priority=EventPriority.HIGH)
        
        if self.on_update:
            await self.on_update(event)
