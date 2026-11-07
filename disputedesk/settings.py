"""Load .env into os.environ (no extra dependency). Owner: lead.

Existing environment variables win over .env, so CI/shell overrides keep working.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    env_file = path or ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not os.environ.get(key):  # shell overrides win; empty values get filled
            os.environ[key] = value


def require(name: str) -> str:
    """Return a required setting or fail with a message that names it (never its value)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting {name}. Add it to .env (see .env.example).")
    return value
