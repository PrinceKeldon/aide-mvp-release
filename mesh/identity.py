"""
AIDE Mesh - Device Identity
Generates and manages Ed25519 device identity.
"""
import json
from pathlib import Path
from datetime import datetime
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from enum import Enum


class DeviceType(Enum):
    FULL_NODE = "full_node"
    SOVEREIGN_TERMINAL = "terminal"
    RELAY = "relay"


class DeviceIdentity:
    """
    Device identity = Ed25519 keypair + metadata
    """
    
    def __init__(
        self,
        device_id: str,
        public_key: bytes,
        private_key: bytes,
        device_name: str,
        device_type: DeviceType,
        created_at: datetime = None
    ):
        self.device_id = device_id
        self.public_key = public_key
        self.private_key = private_key
        self.device_name = device_name
        self.device_type = device_type
        self.created_at = created_at or datetime.now()
    
    @classmethod
    def generate(cls, device_name: str, device_type: DeviceType = DeviceType.FULL_NODE):
        """Generate new device identity"""
        
        # Generate Ed25519 keypair
        private_key_obj = ed25519.Ed25519PrivateKey.generate()
        public_key_obj = private_key_obj.public_key()
        
        # Serialize keys
        private_key = private_key_obj.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_key = public_key_obj.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        
        # Device ID = hex of public key
        device_id = public_key.hex()
        
        return cls(
            device_id=device_id,
            public_key=public_key,
            private_key=private_key,
            device_name=device_name,
            device_type=device_type
        )
    
    def to_dict(self):
        """Export identity (without private key)"""
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "device_type": self.device_type.value,
            "created_at": self.created_at.isoformat(),
            "public_key": self.public_key.hex()
        }

    def sign(self, payload: bytes) -> bytes:
        private_key_obj = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key)
        return private_key_obj.sign(payload)

    @staticmethod
    def verify(public_key: bytes | str, payload: bytes, signature: bytes) -> bool:
        try:
            if isinstance(public_key, str):
                public_key_bytes = bytes.fromhex(public_key)
            else:
                public_key_bytes = public_key
            public_key_obj = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key_obj.verify(signature, payload)
            return True
        except Exception:
            return False
    
    def save(self, directory: Path):
        """Save identity to disk"""
        directory.mkdir(parents=True, exist_ok=True)
        
        # Save public info
        public_file = directory / "device_id.json"
        with open(public_file, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        
        # Save private key (should encrypt in production!)
        private_file = directory / "private_key.bin"
        with open(private_file, 'wb') as f:
            f.write(self.private_key)
        
        print(f"✓ Identity saved to {directory}")
    
    @classmethod
    def load(cls, directory: Path):
        """Load identity from disk"""
        public_file = directory / "device_id.json"
        private_file = directory / "private_key.bin"
        
        if not public_file.exists() or not private_file.exists():
            return None
        
        # Load public info
        with open(public_file, 'r') as f:
            data = json.load(f)
        
        # Load private key
        with open(private_file, 'rb') as f:
            private_key = f.read()
        
        return cls(
            device_id=data['device_id'],
            public_key=bytes.fromhex(data['public_key']),
            private_key=private_key,
            device_name=data['device_name'],
            device_type=DeviceType(data['device_type']),
            created_at=datetime.fromisoformat(data['created_at'])
        )


def get_or_create_identity(
    device_name: str,
    device_type: DeviceType = DeviceType.FULL_NODE,
    identity_dir: Path = None
) -> DeviceIdentity:
    """
    Load existing identity or generate new one
    """
    if identity_dir is None:
        identity_dir = Path.home() / ".aide" / "identity"
    
    # Try to load existing
    identity = DeviceIdentity.load(identity_dir)
    
    if identity:
        print(f"✓ Loaded existing identity: {identity.device_name}")
        print(f"  Device ID: {identity.device_id[:16]}...")
        return identity
    
    # Generate new
    print(f"Generating new identity for: {device_name}")
    identity = DeviceIdentity.generate(device_name, device_type)
    identity.save(identity_dir)
    
    print(f"✓ New identity created")
    print(f"  Device ID: {identity.device_id[:16]}...")
    
    return identity
