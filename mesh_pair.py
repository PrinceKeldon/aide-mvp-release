"""
AIDE Mesh — Mac pairing script
Run separately from main.py to generate QR for Android pairing.
"""
import sys, json, base64, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mesh.identity import get_or_create_identity, DeviceType
from mesh.trust import TrustGraph, RelationshipType, TrustScope
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from datetime import datetime
import qrcode

TRUST_FILE = Path.home() / ".aide" / "data" / "trust_graph.json"


def make_qr_payload(identity) -> str:
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(identity.private_key)
    ts = int(time.time())
    pub_b64 = base64.b64encode(identity.public_key).decode()

    payload = {
        "v":    1,
        "id":   identity.device_id,
        "pk":   pub_b64,
        "name": identity.device_name,
        "type": identity.device_type.value,
        "ts":   ts,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = priv_key.sign(payload_json.encode())
    payload["sig"] = base64.b64encode(sig).decode()
    return json.dumps(payload, separators=(",", ":"))


def show_qr_ascii(payload_str: str, identity):
    try:
        import qrcode
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=1, border=2)
        qr.add_data(payload_str)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass
    print(f"\nDevice: {identity.device_name}")
    print(f"ID:     {identity.device_id[:24]}...")
    print(f"\n--- Raw QR data (paste into Android if scanning fails) ---")
    print(payload_str)
    print("---\n")


def verify_and_store_peer(qr_data_str: str, trust_graph: TrustGraph):
    try:
        data = json.loads(qr_data_str.strip())
        pub_bytes = base64.b64decode(data["pk"])
        sig_bytes = base64.b64decode(data.pop("sig"))
        payload_json = json.dumps(data, separators=(",", ":"), sort_keys=True)

        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        pub_key.verify(sig_bytes, payload_json.encode())

        if abs(time.time() - data["ts"]) > 300:
            print("QR expired. Regenerate on Android.")
            return False

        trust_graph.add_peer(
            peer_id=data["id"],
            public_key=pub_bytes,
            device_name=data["name"],
            device_type=data["type"],
            relationship_type=RelationshipType.OWNER_DEVICE,
            trust_scope=TrustScope.for_owner_device(),
        )
        print(f"\n✅ Paired with: {data['name']}")
        print(f"   ID: {data['id'][:24]}...")
        return True

    except Exception as e:
        print(f"❌ Pairing failed: {e}")
        return False


def main():
    print("\n" + "="*50)
    print("AIDE MESH — Mac Pairing")
    print("="*50)

    identity    = get_or_create_identity("Frank's MacBook", DeviceType.FULL_NODE)
    trust_graph = TrustGraph()
    qr_payload  = make_qr_payload(identity)

    while True:
        print("\n" + "="*50)
        print("  1. Show my QR (for Android to scan)")
        print("  2. Paste Android's QR data")
        print("  3. List trusted peers")
        print("  4. Exit")

        choice = input("\nChoose: ").strip()

        if choice == "1":
            show_qr_ascii(qr_payload, identity)

        elif choice == "2":
            print("\nPaste Android QR data, then Enter:")
            android_data = input("> ").strip()
            if android_data:
                verify_and_store_peer(android_data, trust_graph)

        elif choice == "3":
            peers = trust_graph.list_peers()
            if not peers:
                print("\nNo trusted peers yet.")
            else:
                print(f"\n📱 Trusted peers ({len(peers)}):")
                for p in peers:
                    print(f"  • {p['device_name']} ({p['device_type']}) — {p['relationship_type']}")
                    print(f"    ID: {p['peer_id'][:24]}...")

        elif choice == "4":
            break

if __name__ == "__main__":
    main()
