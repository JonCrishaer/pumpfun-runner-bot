# Integration Points Reference

Quick lookup for how components connect.

## Event Flow

### Bonding Curve → Runner Detector

**Event**: `BondingCurveUpdateEvent`
```python
event_bus.on(BondingCurveUpdateEvent)(detector._handle_curve_update)
```

**Data Flow**:
```
BondingCurveMonitor.emit_curve_update()
    ↓
BondingCurveUpdateEvent(token_address, progress_pct, holders, ...)
    ↓
event_bus.emit(event, priority=HIGH)
    ↓
RunnerDetector._handle_curve_update()
    ↓ (if runner detected)
SignalEvent(signal_type="runner", score=0.78, ...)
```

### Runner Detector → Signal Engine

**Event**: `SignalEvent`
```python
await event_bus.emit(signal_event, priority=EventPriority.HIGH)
```

**Signal Engine** subscribes:
```python
@event_bus.on(SignalEvent)
async def process_signal(event):
    # Filters, validation, risk checks
    # Output: Trade signal
```

### Signal Engine → Execution Engine

**Signal**: `TradeSignal(buy/sell, token, amount, ...)`

**Execution Engine** routes to executor:
```python
if paper_trading:
    await paper_executor.execute(signal)
else:
    await solana_executor.execute(signal)
```

### Executor → Position Model

**Event**: `TradeExecutedEvent`
```python
TradeExecutedEvent(
    token_address="...",
    direction="buy",
    amount_tokens=1000,
    amount_sol=0.5,
    tx_signature="...",
    timestamp=now,
)
```

**Position Model** updates:
```python
position = Position(
    token_address=event.token_address,
    entry_price=event.price,
    amount=event.amount_tokens,
    status=PositionStatus.FILLED,
)
global_state.positions[token] = position
```

### Position Model → Risk Manager

**Trigger**: Continuous monitoring
```python
for pos in global_state.positions.values():
    if pos.current_price < pos.stop_loss:
        # Trigger exit
        await executor.sell(pos.token_address, pos.amount)
```

### Position Model → Monitoring

**Trigger**: Periodic health checks
```python
monitoring.track_position(position)
    ↓
Check alerts (stop loss, take profit, drawdown)
    ↓
Emit alert events → Discord/Telegram
```

---

## Configuration Integration

### Bonding Curve Uses

```python
from core.config import get_settings

settings = get_settings()

# Thresholds
if event.progress_pct > settings.bonding_curve.entry_zone_end:
    # Entry signal
    pass

# API keys
monitor = BondingCurveMonitor(
    api_key=settings.bitquery.api_key,
    ws_url=settings.bitquery.ws_url,
)
```

### Runner Detector Uses

```python
settings = get_settings()

# Thresholds
MIN_PROGRESS = settings.runner.min_progress  # 0.85
MAX_PROGRESS = settings.runner.max_progress  # 0.98
MIN_HOLDERS = settings.runner.min_holders    # 50
SCORE_THRESHOLD = settings.runner.score_threshold  # 0.70
```

### Solana Executor Uses

```python
settings = get_settings()

# Wallet
self.wallet_address = settings.wallet.wallet_address
self.private_key = settings.wallet.private_key

# RPC
self.rpc_url = settings.solana.rpc_url

# Slippage
slippage = settings.trading.default_slippage_bps

# Risk
max_position = settings.risk.max_position_size_pct
```

---

## State Manager Integration

### Global Position Tracking

```python
from core.state import global_state

# Add position
global_state.add_position(position)

# Get positions
for token_addr, position in global_state.positions.items():
    print(f"{token_addr}: {position.status}")

# Update position
global_state.update_position(position)

# Remove position (when closed)
global_state.remove_position(token_address)
```

### State Persistence

Positions are persisted in:
```
core/state.py → PositionManager → JSON file
```

Auto-saved after each trade.

---

## Event Bus Integration

### Subscribing to Events

```python
from core.events import event_bus, BondingCurveUpdateEvent

# Decorator syntax
@event_bus.on(BondingCurveUpdateEvent)
async def handle_update(event):
    print(f"Curve: {event.progress_pct:.0%}")

# Manual subscription
async def handler(event):
    pass

event_bus.on(BondingCurveUpdateEvent)(handler)
```

### Emitting Events

```python
from core.events import event_bus, SignalEvent, EventPriority

event = SignalEvent(...)
await event_bus.emit(event, priority=EventPriority.HIGH)
```

### Event Priorities

- `CRITICAL` (0) - System critical
- `HIGH` (1) - Trade signals
- `NORMAL` (2) - Standard operations
- `LOW` (3) - Informational
- `BACKGROUND` (4) - Logging

---

## Executor Integration

### Paper Executor

```python
from execution.paper_trading import PaperExecutor

executor = PaperExecutor()
result = await executor.execute(signal)
# Simulates fills, updates positions
```

### Solana Executor

```python
from execution.solana_executor import SolanaExecutor

async with SolanaExecutor() as executor:
    result = await executor.buy(
        token_mint="...",
        amount_sol=0.5,
        fak=True,
    )
    if result.success:
        # Emit TradeExecutedEvent
        pass
```

### Executor Selection

```python
if settings.trading.paper_trading:
    executor = PaperExecutor()
else:
    executor = SolanaExecutor()
```

---

## Monitoring Integration

### Health Checks

```python
from monitoring.health import HealthMonitor

monitor = HealthMonitor()
health = await monitor.check_health()

if not health.rpc_ok:
    await alerts.send(f"RPC unreachable: {health.rpc_error}")
```

### Alerts

```python
from monitoring.alerts import AlertDispatcher

alerts = AlertDispatcher()
await alerts.send(
    title="Runner detected!",
    message=f"Token: {token}, Score: {score}",
    severity="HIGH",
)
```

### Metrics

```python
from monitoring.metrics import MetricsCollector

metrics = MetricsCollector()
metrics.record("trade_count", 1)
metrics.record("total_pnl_sol", 0.5)
```

---

## Risk Management Integration

### Position Sizing

```python
from risk.position_sizing import PositionSizer

sizer = PositionSizer(settings=settings)
size = sizer.calculate_position_size(
    portfolio_value=100,  # SOL
    signal_strength=0.8,
    volatility=0.5,
)
# Output: ~0.5 SOL position
```

### Stop Loss

```python
from risk.exit_strategy import ExitStrategy

exit_mgr = ExitStrategy(settings=settings)
sl_price = exit_mgr.calculate_stop_loss(
    entry_price=0.001,
    stop_loss_pct=0.15,
)
# Output: 0.00085 SOL
```

### Drawdown Tracking

```python
from risk.drawdown import DrawdownTracker

tracker = DrawdownTracker(settings=settings)
tracker.add_trade(pnl=0.05)  # +0.05 SOL

if tracker.daily_loss_pct > settings.risk.max_daily_loss_pct:
    # Stop trading for the day
    pass
```

---

## Logging Integration

### Configure Logging

```python
from monitoring.logger import setup_logger

logger = setup_logger(
    name="runner_detector",
    level="DEBUG",
    log_file="logs/runner_detector.log",
)

logger.info("Runner detected!")
logger.error("Buy failed: {error}")
```

### Log Patterns

- `✅` Success
- `❌` Error
- `⚠️` Warning
- `🏃` Runner detected
- `📤` Order submitted
- `📊` Metrics update

---

## Testing Integration

### Paper Trade Test

```python
# Set env vars
os.environ["TRADING_PAPER_TRADING"] = "true"
os.environ["RUNNER_SCORE_THRESHOLD"] = "0.60"  # Lower for testing

# Run
from main import TradingApplication
app = TradingApplication()
await app.run()
```

### Unit Test Example

```python
from signals.runner_detector import RunnerDetector, TokenMetrics

detector = RunnerDetector()
metrics = TokenMetrics(token_address="test", first_seen=now)
metrics.add_sample(0.85, 100, 1.0, now)

is_runner, score, analysis = detector._score_token("test", metrics)
assert is_runner == False  # Need more samples
```

---

## Common Tasks

### Check Runner Detector Status

```python
from signals.runner_detector import RunnerDetector
detector = RunnerDetector()
print(f"Tracked tokens: {len(detector.tokens)}")
for addr, metrics in detector.tokens.items():
    print(f"  {addr}: {len(metrics.progress_samples)} samples")
```

### Check Position Status

```python
from core.state import global_state
for addr, pos in global_state.positions.items():
    print(f"{addr}: {pos.status} @ {pos.current_price}")
```

### Monitor Event Bus

```python
from core.events import event_bus
print(f"Active subscriptions: {len(event_bus._subscribers)}")
for event_type, handlers in event_bus._subscribers.items():
    print(f"  {event_type.__name__}: {len(handlers)} handlers")
```

### Check RPC Connection

```python
import aiohttp
async with aiohttp.ClientSession() as session:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []}
    async with session.post(RPC_URL, json=payload) as resp:
        data = await resp.json()
        if "result" in data:
            print(f"✅ RPC OK (slot {data['result']})")
        else:
            print(f"❌ RPC error: {data['error']}")
```

---

## Troubleshooting Integration

### No bonding curve updates
1. Check Bitquery connection: `BITQUERY_API_KEY`, `BITQUERY_ENABLED`
2. Verify WebSocket: `wss://streaming.bitquery.io/graphql`
3. Check logs for GraphQL errors
4. Test manually: `python -c "from signals.bonding_curve import BondingCurveMonitor; ..."`

### Runner detected but no signals
1. Check signal engine is running
2. Verify event_bus.on() callbacks are registered
3. Lower `RUNNER_SCORE_THRESHOLD` temporarily
4. Check logs for "Runner detected" message

### Buy order fails
1. Check wallet setup: `WALLET_ADDRESS`, `WALLET_PRIVATE_KEY`
2. Verify RPC: `SOLANA_RPC_URL`
3. Check wallet has SOL for fees
4. Review transaction signature in logs

### Positions not tracked
1. Verify `TradeExecutedEvent` is emitted
2. Check Position model receives event
3. Check state manager updates
4. Review logs for "Position opened" message

---

**Reference complete.** See README.md for full documentation.
