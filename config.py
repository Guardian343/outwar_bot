import json
import os

# Load .env into the environment if python-dotenv is installed and a .env exists.
# Optional import so the bot still runs if dotenv isn't installed (falls back to config.json).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Config keys the bot uses -> their environment-variable names (.env).
_ENV_MAP = {
    "prefix":     "BOT_PREFIX",
    "token":      "DISCORD_TOKEN",
    "channel":    "CHANNEL_ID",
    "logchannel": "LOG_CHANNEL_ID",
    "godchannel": "GOD_CHANNEL_ID",
    "username":   "OUTWAR_USERNAME",
    "password":   "OUTWAR_PASSWORD",
}
_INT_KEYS = {"channel", "logchannel", "godchannel"}  # channel IDs are ints


def load_config(path: str = None) -> dict:
    """Load bot config. Prefers environment variables (from a git-ignored .env);
    falls back to config.json for anything not set in the environment. This keeps
    secrets in .env (the dev convention) while staying backward-compatible with an
    existing config.json during/after the transition. Returns the same dict shape
    the bot already expects, so nothing downstream changes."""
    # 1) Start from config.json if present (fallback / backward compatible)
    if path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "config.json")
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    # 2) Override with environment variables where set (.env wins over config.json)
    for key, env_name in _ENV_MAP.items():
        val = os.getenv(env_name)
        if val not in (None, ""):
            cfg[key] = val

    # 3) Coerce channel IDs to int (env values arrive as strings)
    for key in _INT_KEYS:
        if cfg.get(key) is not None:
            try:
                cfg[key] = int(cfg[key])
            except (ValueError, TypeError):
                pass

    cfg.setdefault("prefix", "?")
    return cfg
