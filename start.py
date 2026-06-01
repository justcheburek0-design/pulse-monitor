import os, sys, subprocess

os.chdir("/root/.hermes/workspace/night_projects/projects/2026-05-31-pulse")
os.environ["PULSE_SECRET_KEY"] = "production-secret-key-pulse-monitor-2026!!"
os.environ["PULSE_LOG_LEVEL"] = "DEBUG"

# Remove empty production DB, use default pulse.db
os.remove("pulse_production.db") if os.path.exists("pulse_production.db") else None

# Start uvicorn
subprocess.run([
    sys.executable, "-m", "uvicorn", "src.main:app",
    "--host", "0.0.0.0", "--port", "8090",
    "--workers", "1",
], env=os.environ)
