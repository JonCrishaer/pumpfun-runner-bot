"""
Mock Bonding Curve Monitor - For testing without external API calls

Generates realistic test data when Bitquery is unavailable.
"""

import asyncio
import logging
import random
from datetime import datetime
from typing import Dict, List, Any

from core.events import BondingCurveUpdateEvent, event_bus, EventPriority

logger = logging.getLogger(__name__)


class BondingCurveMonitorMock:
    """Mock monitor that generates test data."""
    
    def __init__(self, api_key: str = "", threshold_pct: float = 0.85, poll_interval: int = 10):
        self.threshold_pct = threshold_pct
        self.poll_interval = poll_interval
        self.running = False
        self.logger = logger
        self._token_counter = 0
    
    async def start(self):
        """Start generating mock data."""
        self.running = True
        self.logger.info(f"🟡 Mock BondingCurve Monitor started (poll every {self.poll_interval}s)")
        asyncio.create_task(self._mock_polling_loop())
    
    async def stop(self):
        """Stop the monitor."""
        self.running = False
        self.logger.info("Mock bonding curve monitor stopped")
    
    async def _mock_polling_loop(self):
        """Generate mock bonding curve updates."""
        while self.running:
            try:
                await self._generate_mock_updates()
            except Exception as e:
                self.logger.error(f"Mock generation error: {e}")
            finally:
                await asyncio.sleep(self.poll_interval)
    
    async def _generate_mock_updates(self):
        """Generate realistic mock token data."""
        # Simulate 5-10 tokens per poll
        num_tokens = random.randint(5, 10)
        
        for _ in range(num_tokens):
            self._token_counter += 1
            
            # Random token address (fake)
            token_address = f"fake{self._token_counter:05d}{'0' * 32}"[:44]
            
            # 30% chance of high-progress token (>85%)
            if random.random() < 0.3:
                progress = random.uniform(self.threshold_pct, 0.99)
            else:
                progress = random.uniform(0.1, self.threshold_pct - 0.01)
            
            # Mock data
            sol_raised = progress * 0.7931
            holders = random.randint(10, 500)
            volume_24h = random.uniform(0.1, 50.0)
            
            # Emit event for high-progress tokens
            if progress >= self.threshold_pct:
                event = BondingCurveUpdateEvent(
                    token_address=token_address,
                    progress_pct=progress,
                    sol_raised=sol_raised,
                    target_sol=1.0,
                    holders=holders,
                    volume_24h=volume_24h,
                )
                
                try:
                    await event_bus.emit(event)
                    self.logger.info(f"🧪 MOCK: Token {token_address[:8]}... @ {progress*100:.1f}% progress")
                except TypeError:
                    # Fallback if emit doesn't support priority
                    self.logger.info(f"🧪 MOCK: Token {token_address[:8]}... @ {progress*100:.1f}% progress")
