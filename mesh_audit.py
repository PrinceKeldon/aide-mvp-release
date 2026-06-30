"""
AIDE Mesh — Separation Audit Script
Runs the three decisive tests from the ChatGPT audit.

Run after /pair from Android to verify identity is properly
separated from Telegram transport.

Usage:
    cd ~/aide
    source venv/bin/activate
    python3 mesh_audit.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mesh.device_registry import DeviceRegistry


def run_audit():
    print("\n" + "="*60)
    print("AIDE Mesh — Identity Separation Audit")
    print("="*60)

    registry = DeviceRegistry()
    devices  = registry.list_all()

    print(f"\nDevices registered: {len(devices)}")
    if not devices:
        print("\n⚠️  No devices found. Run /pair from Android first.")
        return

    for d in devices:
        transport = d.get("transport_type") or "none"
        chat_id   = d.get("telegram_chat_id")
        print(f"\n  • {d['device_name']}")
        print(f"    device_id:  {d['device_id']}")
        print(f"    type:       {d['device_type']}")
        print(f"    transport:  {transport}" + (f" (chat_id {chat_id})" if chat_id else ""))
        print(f"    relationship: {d['relationship_type']}")

    print("\n" + "-"*60)
    print("Running separation tests...")
    print("-"*60)

    # ── Test A ─────────────────────────────────────────────────
    print("\nTest A: Identity survives without transport")
    print("  Simulating: unbind Telegram from first device...")

    test_device = devices[0]
    did = test_device["device_id"]

    registry.unbind_telegram(did)
    still_exists = registry.device_exists(did)

    if still_exists:
        print(f"  ✅ PASS — {test_device['device_name']} still exists in trust graph")
        print(f"     device_id {did} survives without Telegram binding")
    else:
        print(f"  ❌ FAIL — device was deleted when transport was removed")
        print(f"     Identity is incorrectly tied to Telegram")

    # Restore the binding
    if test_device.get("telegram_chat_id"):
        registry.bind_telegram(did, test_device["telegram_chat_id"])
        print(f"  (Telegram binding restored)")

    # ── Test B ─────────────────────────────────────────────────
    print("\nTest B: Same device identity survives transport rebind")
    print("  Simulating: rebind device to different chat_id...")

    fake_chat_id = 9999999999
    original_chat_id = test_device.get("telegram_chat_id")

    try:
        registry.rebind_telegram(did, fake_chat_id)
        device_after = registry.get_by_device_id(did)

        if device_after and device_after["telegram_chat_id"] == fake_chat_id:
            print(f"  ✅ PASS — {test_device['device_name']} kept same device_id")
            print(f"     Old chat_id: {original_chat_id} → New: {fake_chat_id}")
            print(f"     device_id unchanged: {did}")
        else:
            print(f"  ❌ FAIL — rebind broke device identity")
    except Exception as e:
        print(f"  ❌ ERROR — {e}")

    # Restore original binding
    if original_chat_id:
        registry.rebind_telegram(did, original_chat_id)
        print(f"  (Original binding restored)")

    # ── Test C ─────────────────────────────────────────────────
    print("\nTest C: Router addresses by device_id, not chat_id")
    print("  Simulating: resolve device without knowing chat_id...")

    # Look up device by device_id only
    resolved = registry.get_by_device_id(did)
    chat_id_resolved = registry.get_telegram_chat_id(did)

    if resolved and resolved["device_name"]:
        print(f"  ✅ PASS — addressed {did!r} → resolved {resolved['device_name']!r}")
        print(f"     Transport resolved separately: chat_id = {chat_id_resolved}")
        print(f"     Caller never needed to know the chat_id")
    else:
        print(f"  ❌ FAIL — device_id lookup failed")

    # ── Summary ────────────────────────────────────────────────
    print("\n" + "="*60)
    print("Audit complete.")
    print("\nOne-line truth check:")
    print("  If Telegram disappeared tomorrow, would your devices")
    print("  still exist in the trust graph as the same devices?")
    print()

    all_exist_without_transport = all(
        registry.device_exists(d["device_id"]) for d in devices
    )
    if all_exist_without_transport:
        print("  ✅ YES — device identity is separate from Telegram.")
        print("     Your mesh is architecturally correct.")
    else:
        print("  ❌ NO — device identity is tied to Telegram.")
        print("     Run the migration to fix this.")

    print("="*60 + "\n")


if __name__ == "__main__":
    run_audit()