import os
from dotenv import load_dotenv
from pathlib import Path
from backend.logger import logger

# Always load .env from the backend/ directory (same folder as this config.py)
_env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_env_path)

# Helper to strip any quotes that might exist in raw .env strings
def clean_env_var(value: str) -> str:
    if not value:
        return ""
    return value.strip().strip("'\"")

# Robust custom parser for space-filled .env files (e.g. "Client ID = 'value'")
def parse_custom_env(filepath=".env"):
    env_vars = {}
    
    # Try multiple common locations relative to current working directory and config.py location
    config_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(config_dir, ".env"),
        os.path.join(config_dir, "..", ".env"),
        filepath,
        "backend/.env"
    ]
    
    target_path = None
    for p in candidates:
        if os.path.exists(p):
            target_path = p
            break
            
    if not target_path:
        logger.warning("Could not find .env file in candidate paths.")
        return env_vars
            
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    parts = line.split("=", 1)
                    k = parts[0].strip()
                    v = clean_env_var(parts[1])
                    env_vars[k] = v
    except Exception as e:
        logger.error(f"Error parsing custom .env: {e}")
    return env_vars

custom_env = parse_custom_env()

# Spotify OAuth Settings
SPOTIFY_CLIENT_ID = clean_env_var(os.getenv("SPOTIFY_CLIENT_ID", ""))
SPOTIFY_CLIENT_SECRET = clean_env_var(os.getenv("SPOTIFY_CLIENT_SECRET", ""))
SPOTIFY_REDIRECT_URI = clean_env_var(os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/api/callback"))

# Gemini API Settings
GEMINI_API_KEY = clean_env_var(os.getenv("GEMINI_API_KEY", ""))

# SQLite Database Location
DATABASE_NAME = clean_env_var(os.getenv("DATABASE_NAME", "spotify_ai.db"))

# Log variables for diagnostics (without exposing secrets fully)
logger.info("=== CONFIG DIAGNOSTICS ===")
logger.info(f"Spotify Client ID: {SPOTIFY_CLIENT_ID[:5]}...{SPOTIFY_CLIENT_ID[-5:] if SPOTIFY_CLIENT_ID else ''}")
logger.info(f"Spotify Client Secret: {'loaded' if SPOTIFY_CLIENT_SECRET else 'missing'}")
logger.info(f"Gemini API Key: {'loaded' if GEMINI_API_KEY else 'missing'}")
logger.info(f"Spotify Redirect URI: {SPOTIFY_REDIRECT_URI}")
logger.info("==========================")
