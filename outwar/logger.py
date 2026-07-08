from datetime import datetime

DEBUG_ENABLED = True


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def debug(component: str, message: str):
    if DEBUG_ENABLED:
        print(f"[{_timestamp()}] [DEBUG] [{component}] {message}")


def info(component: str, message: str):
    print(f"[{_timestamp()}] [INFO] [{component}] {message}")


def warning(component: str, message: str):
    print(f"[{_timestamp()}] [WARNING] [{component}] {message}")


def error(component: str, message: str):
    print(f"[{_timestamp()}] [ERROR] [{component}] {message}")

def exception(component: str, message: str):
    print(f"[{_timestamp()}] [EXCEPTION] [{component}] {message}")