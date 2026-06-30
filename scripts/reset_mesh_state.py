#!/usr/bin/env python3
"""Move local mesh identity/trust state aside for a fresh pairing run."""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = PROJECT_ROOT / "data"


def mesh_targets() -> list[Path]:
    home_aide = Path.home() / ".aide"
    return [
        home_aide / "identity" / "device_id.json",
        home_aide / "identity" / "private_key.bin",
        home_aide / "data" / "device_registry.db",
        home_aide / "data" / "trust_graph.db",
        home_aide / "alias_registry.json",
        PROJECT_ROOT / "data" / "device_registry.db",
        PROJECT_ROOT / "data" / "keys" / "agent_public.pem",
        PROJECT_ROOT / "data" / "keys" / "agent_private.pem",
    ]


def reset_mesh_state(*, dry_run: bool = False) -> int:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"mesh_reset_backup_{timestamp}"
    existing = [path for path in mesh_targets() if path.exists()]

    if not existing:
        print("No mesh state files found to reset.")
        return 0

    print("Mesh files selected for reset:")
    for path in existing:
        print(f"  - {path}")

    if dry_run:
        print(f"Dry run only. Backup target would be: {backup_dir}")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=False)

    for source in existing:
        if source.is_relative_to(PROJECT_ROOT):
            relative = source.relative_to(PROJECT_ROOT)
        else:
            relative = Path("home") / source.relative_to(Path.home())
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        print(f"Moved {source} -> {destination}")

    print(f"Fresh mesh state is ready. Backup: {backup_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would move.")
    args = parser.parse_args()
    return reset_mesh_state(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
