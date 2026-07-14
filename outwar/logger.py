from datetime import datetime
import sys

DEBUG_ENABLED = True

# ---------------------------------------------------------------------------
# Force stdout/stderr to UTF-8 so log messages containing characters like the
# arrow "→" (used in cap debug lines), emojis, or other non-ASCII text never
# crash the bot. On Windows the default console/pipe encoding is cp1252, which
# can't encode "→" (\u2192) — under pythonw/supervisor that raised
# "'charmap' codec can't encode character '\u2192'" and broke raids. Forcing
# UTF-8 (with errors="replace" as a backstop) makes logging robust everywhere.
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # older streams / already-wrapped; the errors="replace" in _emit covers it


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _emit(line: str):
    """Print a log line, guaranteed not to raise on encoding issues."""
    try:
        print(line)
    except Exception:
        # Absolute backstop: strip to ASCII so a weird char can never crash the bot.
        try:
            print(line.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def debug(component: str, message: str):
    if DEBUG_ENABLED:
        _emit(f"[{_timestamp()}] [DEBUG] [{component}] {message}")


def info(component: str, message: str):
    _emit(f"[{_timestamp()}] [INFO] [{component}] {message}")


def warning(component: str, message: str):
    _emit(f"[{_timestamp()}] [WARNING] [{component}] {message}")


def error(component: str, message: str):
    _emit(f"[{_timestamp()}] [ERROR] [{component}] {message}")


def exception(component: str, message: str):
    _emit(f"[{_timestamp()}] [EXCEPTION] [{component}] {message}")
