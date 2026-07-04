import json
import os
from pathlib import Path

from dotenv import load_dotenv

"""
config.py

This module combines two configuration sources:

config.json
-----------
Contains non-sensitive application settings, such as:
- Discord channel IDs
- Command prefix
- Timers
- Feature flags

.env
----
Contains sensitive credentials, such as:
- Discord bot token
- Outwar username
- Outwar password

The rest of the application only calls load_config() and doesn't need
to know where individual settings come from.
"""

# ------------------------------------------------------------------
# File locations
# ------------------------------------------------------------------

# Root directory of the project (where config.py is located)
BASE_DIR = Path(__file__).resolve().parent

# Non-sensitive configuration
CONFIG_PATH = BASE_DIR / "config.json"

# Sensitive credentials
ENV_PATH = BASE_DIR / ".env"


# ------------------------------------------------------------------
# Load environment variables from the local .env file.
#
# override=True ensures that values from the local .env file take
# precedence over any existing environment variables on the machine.
# ------------------------------------------------------------------

load_dotenv(dotenv_path=ENV_PATH, override=True)


# ------------------------------------------------------------------
# Helper function
#
# Reads a required environment variable.
# If the variable is missing, the application stops immediately with
# a clear error message instead of failing later with obscure errors.
# ------------------------------------------------------------------

def require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


# ------------------------------------------------------------------
# Load the complete application configuration.
#
# Steps:
#
# 1. Read all non-sensitive settings from config.json.
# 2. Inject sensitive credentials from .env.
#
# This keeps secrets out of the repository while allowing the rest
# of the application to continue using a single configuration object.
# ------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    config_path = Path(path) if path else CONFIG_PATH

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config["token"] = require_env("DISCORD_BOT_TOKEN")
    config["username"] = require_env("OUTWAR_USERNAME")
    config["password"] = require_env("OUTWAR_PASSWORD")

    return config