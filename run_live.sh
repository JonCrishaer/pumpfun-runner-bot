#!/bin/bash
export DEMO_USE_MOCK=false
export PYTHONDONTWRITEBYTECODE=1
exec python3 -B main.py "$@"
