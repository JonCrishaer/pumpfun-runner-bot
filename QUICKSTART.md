# Quick Start Guide - Pump.fun Runner Bot

Get the bot running in 5 minutes.

## Step 1: Setup (2 minutes)

```bash
cd ~/.openclaw/workspace/trading/pumpfun_runner_bot_merged
cp .env.example .env
pip install -r requirements.txt
```

## Step 2: Minimal Configuration (1 minute)

Edit `.env`:

```bash
# Paper trading (NO real money)
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
TRADING_PAPER_TRADING=true
RUNNER_ENABLED=true
```

Save and close.

## Step 3: Run! (2 minutes)

```bash
python main.py --paper-trading
```

You should see:
```
2026-02-27 02:30:15 | INFO | Starting trading application...
2026-02-27 02:30:16 | INFO | ✅ Bonding curve monitor started
2026-02-27 02:30:16 | INFO | ✅ Runner detector started
```

Let it run for a few minutes. When it detects a runner:
```
2026-02-27 02:30:45 | INFO | 🏃 Runner detected: abc123... (score=0.78)
2026-02-27 02:30:46 | INFO | 📤 Buy order: 0.5 SOL for abc123...
2026-02-27 02:30:47 | INFO | ✅ Buy executed: 1000 tokens for 0.5 SOL
```

## ✅ Done!

You're now running the bot in **paper trading mode** (no real transactions).

---

## Next Steps

### To go LIVE (real money):

1. **Get a Helius RPC key** (free): https://helius.dev
   ```
   SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
   ```

2. **Fund Phantom wallet** with SOL (e.g., 1 SOL for testing)

3. **Export private key from Phantom**, then add to `.env`:
   ```
   WALLET_ADDRESS=your_wallet_address
   WALLET_PRIVATE_KEY=your_base58_private_key
   TRADING_PAPER_TRADING=false
   ```

4. **Start with SMALL position**:
   ```bash
   RISK_MAX_POSITION_SIZE_PCT=0.001  # ~0.1 SOL
   python main.py
   ```

5. **Watch logs** for 1-2 hours before increasing position size.

### To enable Bitquery real-time monitoring:

1. Get API key from https://bitquery.io (free tier)
2. Add to `.env`:
   ```
   BITQUERY_API_KEY=your_bitquery_key
   BITQUERY_ENABLED=true
   ```

---

## 🛑 Emergency Stop

```bash
Ctrl+C
```

The bot gracefully shuts down. Any open positions are tracked in the state file.

---

## Troubleshooting

### "No runner detected after 10 minutes"
- Lower detection threshold temporarily:
  ```bash
  RUNNER_SCORE_THRESHOLD=0.60 python main.py --paper-trading
  ```

### "Python not found"
```bash
# Use Python 3.10+
python3 main.py --paper-trading
```

### "aiohttp ImportError"
```bash
pip install -r requirements.txt --upgrade
```

---

## Config Cheat Sheet

| Setting | Default | Paper | Small Live | Production |
|---------|---------|-------|-----------|------------|
| TRADING_PAPER_TRADING | true | true | false | false |
| RISK_MAX_POSITION_SIZE_PCT | 0.05 | 0.05 | 0.001 | 0.005 |
| RUNNER_SCORE_THRESHOLD | 0.70 | 0.70 | 0.75 | 0.80 |
| TRADING_MAX_CONCURRENT_POSITIONS | 5 | 5 | 2 | 5 |

---

## Real-time Monitoring

In another terminal, watch the logs:
```bash
tail -f logs/pumpfun_trader.log
```

Or check position state:
```bash
python -c "
from core.state import global_state

for pos in global_state.positions.values():
    if pos.status == 'FILLED':
        pnl = (pos.current_price - pos.entry_price) * pos.amount
        print(f'{pos.token_address[:8]}: {pos.amount} tokens @ {pos.entry_price:.6f} | P&L: {pnl:.4f} SOL')
"
```

---

**That's it!** You're ready. Start with paper trading, then go live when comfortable. 🚀
