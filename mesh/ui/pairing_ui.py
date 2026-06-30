"""
AIDE Mesh - Pairing UI
QR code generation and scanning for device pairing.
"""
import json
import base64
import qrcode
from io import BytesIO
from PIL import Image
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


def build_pairing_payload(identity) -> str:
    """
    Build the signed QR payload for pairing.
    """
    from datetime import datetime

    payload = {
        "v": 1,
        "kind": "owner_device_pairing",
        "id": identity.device_id,
        "pk": base64.b64encode(identity.public_key).decode("utf-8"),
        "name": identity.device_name,
        "type": identity.device_type.value,
        "ts": int(datetime.now().timestamp()),
    }

    payload_json = json.dumps(payload, sort_keys=True)
    private_key_obj = ed25519.Ed25519PrivateKey.from_private_bytes(identity.private_key)
    signature = private_key_obj.sign(payload_json.encode("utf-8"))
    payload["sig"] = base64.b64encode(signature).decode("utf-8")
    return json.dumps(payload)


def generate_pairing_qr(identity) -> Image:
    """
    Generate QR code containing device identity for pairing
    """
    qr_data = build_pairing_payload(identity)
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    return img


def parse_pairing_qr(qr_data: str) -> dict:
    """
    Parse and verify QR code data
    """
    try:
        payload = json.loads(qr_data)

        if payload.get("kind", "owner_device_pairing") != "owner_device_pairing":
            raise ValueError("Payload is not an owner-device pairing code")
        
        # Extract signature
        signature = base64.b64decode(payload.pop("sig"))
        
        # Recreate payload for verification
        payload_json = json.dumps(payload, sort_keys=True)
        
        # Verify self-signature
        public_key_bytes = base64.b64decode(payload["pk"])
        public_key_obj = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
        
        try:
            public_key_obj.verify(signature, payload_json.encode('utf-8'))
        except:
            raise ValueError("Invalid signature - QR code may be tampered")
        
        # Verify timestamp (must be within 5 minutes)
        from datetime import datetime, timedelta
        qr_time = datetime.fromtimestamp(payload["ts"])
        now = datetime.now()
        
        if abs((now - qr_time).total_seconds()) > 300:  # 5 minutes
            raise ValueError("QR code expired (older than 5 minutes)")
        
        return payload
        
    except Exception as e:
        raise ValueError(f"Invalid QR code: {e}")


def display_pairing_qr(identity):
    """Display QR code for pairing"""
    img = generate_pairing_qr(identity)
    img.show()  # Opens in default image viewer
    print(f"\n✓ QR code displayed for: {identity.device_name}")
    print(f"  Device ID: {identity.device_id[:16]}...")
    print("\nScan this QR code from the other device to pair.")
