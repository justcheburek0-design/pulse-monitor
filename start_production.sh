#!/bin/bash
# Pulse Monitor — production startup script
# Monitors: Mission Control (kanban), GitHub API, Hermes API, Caddy

set -e

PULSE_DIR="/root/.hermes/workspace/night_projects/projects/2026-05-31-pulse"
cd "$PULSE_DIR"

# Generate secret key if not set
if [ -z "$PULSE_SECRET_KEY" ]; then
    export PULSE_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

export PULSE_PORT=8090
export PULSE_DATABASE_URL="sqlite+aiosqlite:///./pulse_production.db"
export PULSE_LOG_LEVEL=INFO

echo "🚀 Starting Pulse Monitor on port $PULSE_PORT"
echo "   Database: $PULSE_DATABASE_URL"

# Initialize DB
python3 -c "
import asyncio
from src.config.database import init_db
asyncio.run(init_db())
print('✅ Database initialized')
"

# Start server
exec uvicorn src.main:app \
    --host 0.0.0.0 \
    --port "$PULSE_PORT" \
    --workers 1 \
    --log-level info
