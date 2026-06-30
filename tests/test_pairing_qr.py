from mesh.identity import DeviceIdentity
from mesh.ui.pairing_ui import build_pairing_payload, generate_pairing_qr, parse_pairing_qr


def test_pairing_qr_round_trip():
    identity = DeviceIdentity.generate("Test Device")

    img = generate_pairing_qr(identity)
    assert img is not None

    payload = build_pairing_payload(identity)
    parsed = parse_pairing_qr(payload)

    assert parsed["id"] == identity.device_id
    assert parsed["name"] == identity.device_name
    assert parsed["type"] == identity.device_type.value
