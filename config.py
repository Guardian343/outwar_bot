import json
import os

def load_config(path: str = None) -> dict:
    """Load config from JSON file. Defaults to config.json in the bot's directory."""
    if path is None:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "config.json")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
