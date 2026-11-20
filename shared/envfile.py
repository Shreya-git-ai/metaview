"""Minimal .env loader (no external dependency). Existing environment variables win."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | os.PathLike | None) -> None:
    if not path:
        return
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {p} (run: python scripts/setup_dev.py)")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
