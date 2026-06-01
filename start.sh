#!/bin/bash
cd /root/.hermes/workspace/night_projects/projects/2026-05-31-pulse
export PULSE_SECRET_KEY="prod-secret-key-2026-very-long-and-secure-random-bytes"
export PULSE_DATABASE_URL="sqlite+aiosqlite:///./pulse_production.db"
export PULSE_PORT=8090
exec uvicorn src.main:app --host 0.0.0.0 --port 8090 --workers 1 --log-level info
