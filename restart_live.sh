#!/bin/bash

echo "🚀 RESTARTING BOT - LIVE MODE"

# Kill everything
pkill -9 python3 2>/dev/null
sleep 1

# Nuclear cache clean
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -delete 2>/dev/null || true

# Delete all .pyc in site-packages (if any)
python3 -Bc "import py_compile; py_compile.compile('.', doraise=False)" 2>/dev/null || true

echo "✅ Cache cleared"
sleep 1

# Start fresh
echo "🟢 Starting bot in LIVE mode..."
nohup python3 -B main.py > bot.log 2>&1 &

sleep 5
echo "📊 Monitoring logs..."
tail -f bot.log
