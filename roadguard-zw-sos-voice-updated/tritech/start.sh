#!/bin/sh
cd "$(dirname "$0")" && pip install -q -r requirements.txt && PORT="${PORT:-5002}" python3 server.py
