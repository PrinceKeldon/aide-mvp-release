"""Runtime path helpers for source checkouts and PyInstaller bundles."""

from __future__ import annotations

from pathlib import Path
import sys


def app_root() -> Path:
    """Return the read-only app/resource root for this runtime."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)
