"""
AIDE — Spoke Registration Script
Run once on Mac hub to register Midas and Kira in the spoke_registry.

Usage:
    cd ~/aide
    python3 scripts/register_spokes.py
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/aide_memory.db")
VAULT_BASE = Path("~/Obsidian/AIDEMemory").expanduser()


def main():
    print("AIDE Spoke Registration")
    print("=" * 40)

    # ── Create spoke_registry table ──────────────────────────────
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS spoke_registry (
            device_id     TEXT PRIMARY KEY,
            device_name   TEXT NOT NULL,
            mesh_host     TEXT,
            mesh_port     INTEGER DEFAULT 7432,
            capabilities  TEXT,
            llm_type      TEXT,
            platform      TEXT,
            last_seen     TEXT,
            vault_path    TEXT,
            registered_at TEXT
        )
    """)
    db.commit()
    print("✓ spoke_registry table ready")

    # ── Spoke definitions ────────────────────────────────────────
    spokes = [
        {
            "device_id": "device_677739740d05",
            "device_name": "midas",
            "mesh_host": "192.168.6.61",
            "mesh_port": 7432,
            "capabilities": json.dumps(
                ["telegram", "email_approval", "mobile_presence"]
            ),
            "llm_type": "none",
            "platform": "android",
            "vault_path": "spokes/midas",
        },
        {
            "device_id": "b4495ef1f6a8490f73e655da24eb72b6066661e77e2b28812c0b6c54c5597de7",
            "device_name": "kira",
            "mesh_host": "192.168.6.29",
            "mesh_port": 7432,
            "capabilities": json.dumps(
                ["cloud_llm", "telegram", "autonomous", "search", "web_browse"]
            ),
            "llm_type": "cloud",
            "platform": "android",
            "vault_path": "spokes/kira",
        },
    ]

    now = datetime.now(timezone.utc).isoformat()

    for s in spokes:
        db.execute(
            """
            INSERT OR REPLACE INTO spoke_registry
                (device_id, device_name, mesh_host, mesh_port,
                 capabilities, llm_type, platform, vault_path, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                s["device_id"],
                s["device_name"],
                s["mesh_host"],
                s["mesh_port"],
                s["capabilities"],
                s["llm_type"],
                s["platform"],
                s["vault_path"],
                now,
            ),
        )
        print(f"✓ Registered spoke: {s['device_name']} ({s['mesh_host']})")

    db.commit()

    # ── Also ensure trusted_peers in facts table ─────────────────
    db.execute(
        "INSERT OR IGNORE INTO facts (key, value) VALUES (?, ?)",
        ("trusted_peer_device_677739740d05", "device_677739740d05"),
    )
    db.execute(
        "INSERT OR IGNORE INTO facts (key, value) VALUES (?, ?)",
        (
            "trusted_peer_b4495ef1f6a8490f73e655da24eb72b6066661e77e2b28812c0b6c54c5597de7",
            "b4495ef1f6a8490f73e655da24eb72b6066661e77e2b28812c0b6c54c5597de7",
        ),
    )
    db.commit()
    print("✓ Trust entries confirmed in facts table")
    db.close()

    # ── Create Obsidian vault structure ──────────────────────────
    print("\nCreating Obsidian vault structure...")

    folders = [
        VAULT_BASE / "hub",
        VAULT_BASE / "hub" / "Episodic",
        VAULT_BASE / "hub" / "Strategic",
        VAULT_BASE / "hub" / "Tactical",
        VAULT_BASE / "spokes" / "midas",
        VAULT_BASE / "spokes" / "kira",
        VAULT_BASE / "shared",
    ]

    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {folder.relative_to(VAULT_BASE.parent)}")

    # ── Write identity.md for each spoke ────────────────────────
    for s in spokes:
        caps = json.loads(s["capabilities"])
        identity_md = f"""# {s['device_name'].capitalize()} — Spoke Identity

**Device ID:** `{s['device_id']}`
**Platform:** {s['platform']}
**LLM Type:** {s['llm_type']}
**Mesh Host:** {s['mesh_host']}:{s['mesh_port']}
**Registered:** {now[:10]}

## Capabilities
{chr(10).join(f'- {c}' for c in caps)}

## Delegatable Task Types
- search
- web_browse
- task_execution

## Notes
_Hub notes about this device go here._
"""
        path = VAULT_BASE / "spokes" / s["device_name"] / "identity.md"
        path.write_text(identity_md)
        print(f"  ✓ Written: spokes/{s['device_name']}/identity.md")

    # ── Write hub identity.md ────────────────────────────────────
    hub_identity = f"""# Vera Desk — Hub Identity

**Device ID:** `9fbef5c5471a008c468e53620f1f42de98428d4fff1b20162053602b2cac497d`
**Platform:** macOS
**LLM Type:** local (Ollama/llama3.1 offline, Ollama/gemma4:31b online)
**Mesh Host:** 192.168.6.96:7432
**Role:** Hub coordinator

## Registered Spokes
- midas (192.168.6.61) — delegated tasks, email approval
- kira (192.168.6.29) — autonomous, cloud LLM

## Capabilities
- full_tool_set
- local_llm
- cloud_llm_fallback
- obsidian_vault_authority
- mesh_coordinator
"""
    (VAULT_BASE / "hub" / "identity.md").write_text(hub_identity)
    print("  ✓ Written: hub/identity.md")

    # ── Write shared/owner_profile.md ────────────────────────────
    owner_profile = """# Owner Profile — Frank Koine / TopD

_This file is maintained by Hub VERA. All spokes receive a read-only copy._

## Identity
- Name: Frank Koine / TopD
- Location: Berlin (Zeuthen), Germany
- Languages: English

## Active Projects
- RIGGED (memoir, Kindle launch)
- UNRIGGED (live experience)
- SANTOORI (music platform)
- AIDE / VERA (this system)
- KARAVA (B2B export intelligence)

## Preferences
_Updated automatically by MemoryExtractor_

## Mesh Devices
| Device | Role | IP |
|--------|------|----|
| Vera Desk | Hub | 192.168.6.96 |
| Midas | Spoke — delegated | 192.168.6.61 |
| Kira | Spoke — autonomous | 192.168.6.29 |
"""
    (VAULT_BASE / "shared" / "owner_profile.md").write_text(owner_profile)
    print("  ✓ Written: shared/owner_profile.md")

    # ── Write shared/active_tasks.md ─────────────────────────────
    active_tasks = """# Active Tasks

_Hub VERA updates this file automatically. You can edit it in Obsidian to cancel or reprioritise._

| Task | Assigned To | Status | Started |
|------|-------------|--------|---------|
| — | — | — | — |

## How to use this file
- Change Status to `cancelled` to cancel a task
- Add a note in the Notes column — VERA will read it within 30 seconds
- Do not delete the table header row
"""
    (VAULT_BASE / "shared" / "active_tasks.md").write_text(active_tasks)
    print("  ✓ Written: shared/active_tasks.md")

    # ── Write shared/mesh_log.md ─────────────────────────────────
    mesh_log = f"""# Mesh Coordination Log

_Appended automatically by Hub VERA on every coordination event._

---

## {now[:10]}
- Spoke registration script run
- Midas registered: 192.168.6.61
- Kira registered: 192.168.6.29
- Vault structure initialised
"""
    (VAULT_BASE / "shared" / "mesh_log.md").write_text(mesh_log)
    print("  ✓ Written: shared/mesh_log.md")

    print("\n" + "=" * 40)
    print("✅ Registration complete.")
    print(f"   Vault: {VAULT_BASE}")
    print("   Next: run python3 scripts/fix_mesh_trust.py")
    print("         to patch _handle_connection lazy trust loading")


if __name__ == "__main__":
    main()
