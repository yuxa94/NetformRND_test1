"""
Environment-aware configuration loader.

Usage:
    FLASK_ENV=production python server.py   # loads .env.production
    python server.py                        # defaults to .env.development
"""
import os
from dotenv import load_dotenv


def load_config():
    """Load the correct .env file based on FLASK_ENV."""
    env = os.environ.get("FLASK_ENV", "development")
    base_dir = os.path.abspath(os.path.dirname(__file__))

    # Try environment-specific file first, fall back to .env
    env_file = os.path.join(base_dir, f".env.{env}")
    if os.path.exists(env_file):
        load_dotenv(env_file, override=True)
        print(f"[config] Loaded .env.{env}")
    else:
        fallback = os.path.join(base_dir, ".env")
        load_dotenv(fallback, override=True)
        print(f"[config] .env.{env} not found, loaded .env")


load_config()
