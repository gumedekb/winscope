"""Zero-dependency .env loader (keeps requirements.txt to pandas + requests).

Precedence: real environment variables win over .env, so GitHub Actions secrets
override the local file without any code change.
"""
import os

DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOADED = False


def load_env(path: str | None = None) -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    path = path or os.path.join(DATA_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:      # real env wins
                os.environ[key] = val


def get(key: str, default: str = "") -> str:
    load_env()
    return os.environ.get(key, default) or default


def offline() -> bool:
    """Hard kill-switch for every outbound request. Tests set this."""
    return get("WINSCOPE_OFFLINE", "0").lower() in ("1", "true", "yes")
