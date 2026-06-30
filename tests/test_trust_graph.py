from mesh.identity import DeviceIdentity
from mesh.trust_graph import TrustGraph, TrustLevel


def test_trust_graph_add_list_and_revoke(tmp_path):
    graph = TrustGraph(db_path=tmp_path / "trust_graph.db")
    peer = DeviceIdentity.generate("Test Android")

    graph.add_peer(
        peer_id=peer.device_id,
        public_key=peer.public_key,
        device_name=peer.device_name,
        device_type=peer.device_type.value,
        trust_level=TrustLevel.TRUSTED,
        capabilities=["notifications", "camera"],
    )

    assert graph.is_trusted(peer.device_id)
    peers = graph.list_trusted_peers()
    assert len(peers) == 1

    graph.revoke_peer(peer.device_id)
    assert not graph.is_trusted(peer.device_id)
