from mesh.identity import DeviceIdentity, DeviceType, get_or_create_identity


def test_identity_persists_across_runs(tmp_path):
    identity_dir = tmp_path / "identity"

    identity = get_or_create_identity(
        "Frank's MacBook",
        DeviceType.FULL_NODE,
        identity_dir=identity_dir,
    )
    identity2 = get_or_create_identity(
        "Frank's MacBook",
        identity_dir=identity_dir,
    )

    assert identity.device_id == identity2.device_id
    assert identity2.device_name == "Frank's MacBook"


def test_device_identity_export_contains_public_fields():
    identity = DeviceIdentity.generate("Verifier")
    exported = identity.to_dict()

    assert exported["device_id"] == identity.device_id
    assert exported["device_name"] == "Verifier"
    assert exported["device_type"] == DeviceType.FULL_NODE.value
