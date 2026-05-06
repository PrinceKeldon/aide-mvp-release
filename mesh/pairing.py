"""
AIDE Mesh - Pairing
Complete pairing flow between two devices.
"""
import base64

from mesh.identity import DeviceIdentity
from mesh.peer_invitation import parse_peer_invitation_payload
from mesh.trust_graph import TrustGraph, TrustLevel
from mesh.ui.pairing_ui import generate_pairing_qr, parse_pairing_qr


class PairingManager:
    """
    Manages device pairing process
    """
    
    def __init__(
        self,
        identity: DeviceIdentity,
        trust_graph: TrustGraph = None,
        device_registry=None,
        alias_registry=None,
    ):
        self.identity = identity
        self.trust_graph = trust_graph
        self.device_registry = device_registry
        self.alias_registry = alias_registry
    
    def initiate_pairing(self):
        """
        Start pairing - generate QR code
        Returns QR code image
        """
        print(f"\n🔗 Initiating pairing for: {self.identity.device_name}")
        print("=" * 50)
        
        qr_img = generate_pairing_qr(self.identity)
        
        print("\n1. Show this QR code to the other device")
        print("2. Scan their QR code")
        print("3. Both devices will be paired")
        
        return qr_img
    
    def complete_pairing(self, peer_qr_data: str):
        """
        Complete pairing by scanning peer's QR code
        """
        try:
            # Parse and verify peer's QR
            peer_data = parse_pairing_qr(peer_qr_data)
            
            print(f"\n✓ Valid QR code from: {peer_data['name']}")
            print(f"  Device ID: {peer_data['id'][:16]}...")
            print(f"  Device Type: {peer_data['type']}")
            
            public_key = base64.b64decode(peer_data["pk"])
            device_id = peer_data["id"]

            if self.device_registry is not None:
                self.device_registry.create_device(
                    device_id=device_id,
                    device_name=peer_data["name"],
                    device_type=peer_data["type"],
                    public_key=public_key.hex(),
                )

                capability_updates = {}
                if peer_data["type"] == "full_node":
                    capability_updates["can_execute"] = 1
                    capability_updates["can_receive_memory"] = 1

                if capability_updates:
                    self.device_registry.update_capabilities(device_id, **capability_updates)

                self.device_registry.mark_paired(
                    device_id,
                    trust_source="qr_pairing",
                    note=f"Paired with {peer_data['name']} via QR pairing.",
                )

                if self.alias_registry is not None:
                    self.alias_registry.register(
                        device_id=device_id,
                        canonical_name=peer_data["name"],
                        aliases=[peer_data["name"].lower()],
                        is_default=False,
                    )

            if self.trust_graph is not None and self.device_registry is None:
                self.trust_graph.add_peer(
                    peer_id=device_id,
                    public_key=public_key,
                    device_name=peer_data["name"],
                    device_type=peer_data["type"],
                    trust_level=TrustLevel.TRUSTED,
                )

            print(f"\n✅ Pairing complete!")
            print(f"   {self.identity.device_name} ↔ {peer_data['name']}")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Pairing failed: {e}")
            return False


class PeerInvitationManager:
    """
    Manages sovereign peer invitation flow.
    This is intentionally separate from owner-device pairing.
    """

    def __init__(
        self,
        identity: DeviceIdentity,
        device_registry=None,
    ):
        self.identity = identity
        self.device_registry = device_registry

    def complete_invitation(
        self,
        invitation_payload: str,
        *,
        trust_scope_id: str = "assistant_introduction",
        paired_by: str | None = None,
    ) -> bool:
        try:
            peer_data = parse_peer_invitation_payload(invitation_payload)
            if self.device_registry is None:
                raise ValueError("Device registry required for peer invitations")

            public_key = base64.b64decode(peer_data["pk"]).hex()
            self.device_registry.create_peer_agent(
                peer_agent_id=peer_data["peer_agent_id"],
                public_key=public_key,
                display_name=peer_data["display_name"],
                owner_name=peer_data.get("owner_name") or None,
                primary_device_id=peer_data.get("primary_device_id") or None,
                trust_scope_id=trust_scope_id,
                paired_by=paired_by,
                agent_version=peer_data.get("agent_version") or None,
                capabilities=peer_data.get("capabilities") or [],
                metadata={"invitation_kind": peer_data.get("kind", "")},
            )
            return True
        except Exception as e:
            print(f"\n❌ Peer invitation failed: {e}")
            return False
    
    def list_paired_devices(self):
        """Show all paired devices"""
        if self.device_registry is not None:
            peers = self.device_registry.list_trusted_peers()
        elif self.trust_graph is not None:
            peers = self.trust_graph.list_trusted_peers()
        else:
            peers = []

        if not peers:
            print("\nNo paired devices yet.")
            return
        
        print(f"\n📱 Paired Devices ({len(peers)}):")
        print("=" * 50)
        
        for peer in peers:
            if isinstance(peer, dict):
                print(f"\n• {peer['device_name']}")
                print(f"  Device ID: {peer['device_id'][:16]}...")
                print(f"  Type: {peer['device_type']}")
                if peer.get("paired_at"):
                    print(f"  Paired: {peer['paired_at'][:16].replace('T', ' ')}")
                if peer.get("last_seen_at"):
                    print(f"  Last Seen: {peer['last_seen_at'][:16].replace('T', ' ')}")
                continue

            print(f"\n• {peer.device_name}")
            print(f"  Device ID: {peer.peer_id[:16]}...")
            print(f"  Type: {peer.device_type}")
            print(f"  Paired: {peer.paired_at.strftime('%Y-%m-%d %H:%M')}")
            if peer.last_seen:
                print(f"  Last Seen: {peer.last_seen.strftime('%Y-%m-%d %H:%M')}")
