"""
Test configuration module.

Loads configuration from tests/.env file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from tests directory
_tests_dir = Path(__file__).parent
_env_path = _tests_dir / ".env"

if _env_path.exists():
    load_dotenv(_env_path)
else:
    # Try loading from tests/.env.example as fallback
    _example_path = _tests_dir / ".env.example"
    if _example_path.exists():
        load_dotenv(_example_path)
        print(f"Warning: tests/.env not found, using .env.example")

# Configuration values
API_KEY = os.getenv("API_KEY", "sk-test-key")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8002")
MODEL_ID = os.getenv("MODEL_ID", "claude-sonnet-4-5-20250929")

# Aliases for compatibility
PROXY_BASE_URL = BASE_URL
