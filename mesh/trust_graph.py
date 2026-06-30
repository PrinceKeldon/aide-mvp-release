"""
AIDE Mesh - Trust Graph
Stores trusted peers in SQLite.
"""
import sqlite3
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import List, Optional


class TrustLevel(Enum):
    TRUSTED = "trusted"
    LIMITED = "limited"
    PENDING = "pending"
    REVOKED = "revoked"


class TrustedPeer:
    """Represents a peer in the trust graph"""
    
    def __init__(
        self,
        peer_id: str,
        public_key: bytes,
        device_name: str,
        device_type: str,
        trust_level: TrustLevel,
        paired_at: datetime,
        last_seen: datetime = None,
        capabilities: List[str] = None
    ):
        self.peer_id = peer_id
        self.public_key = public_key
        self.device_name = device_name
        self.device_type = device_type
        self.trust_level = trust_level
        self.paired_at = paired_at
        self.last_seen = last_seen
        self.capabilities = capabilities or []


class TrustGraph:
    """
    Manages trust relationships with other devices
    """
    
    def __init__(self, db_path: Path = None):
        if db_path is None:
            db_path = Path.home() / ".aide" / "data" / "trust_graph.db"
        
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self._init_db()
    
    def _init_db(self):
        """Create trust graph table"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trust_graph (
                peer_id TEXT PRIMARY KEY,
                public_key BLOB NOT NULL,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                paired_at INTEGER NOT NULL,
                last_seen INTEGER,
                capabilities TEXT,
                notes TEXT
            )
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trust_level 
            ON trust_graph(trust_level)
        """)
        
        self.conn.commit()
    
    def add_peer(
        self,
        peer_id: str,
        public_key: bytes,
        device_name: str,
        device_type: str,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        capabilities: List[str] = None
    ):
        """Add or update peer in trust graph"""
        
        self.conn.execute("""
            INSERT OR REPLACE INTO trust_graph
            (peer_id, public_key, device_name, device_type, trust_level, 
             paired_at, last_seen, capabilities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            peer_id,
            public_key,
            device_name,
            device_type,
            trust_level.value,
            int(datetime.now().timestamp()),
            int(datetime.now().timestamp()),
            ','.join(capabilities) if capabilities else ''
        ))
        
        self.conn.commit()
        print(f"✓ Added peer: {device_name} ({peer_id[:16]}...)")
    
    def get_peer(self, peer_id: str) -> Optional[TrustedPeer]:
        """Get peer by ID"""
        cursor = self.conn.execute("""
            SELECT peer_id, public_key, device_name, device_type, 
                   trust_level, paired_at, last_seen, capabilities
            FROM trust_graph
            WHERE peer_id = ?
        """, (peer_id,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        return TrustedPeer(
            peer_id=row[0],
            public_key=row[1],
            device_name=row[2],
            device_type=row[3],
            trust_level=TrustLevel(row[4]),
            paired_at=datetime.fromtimestamp(row[5]),
            last_seen=datetime.fromtimestamp(row[6]) if row[6] else None,
            capabilities=row[7].split(',') if row[7] else []
        )
    
    def is_trusted(self, peer_id: str) -> bool:
        """Check if peer is trusted"""
        peer = self.get_peer(peer_id)
        return peer and peer.trust_level == TrustLevel.TRUSTED
    
    def list_trusted_peers(self) -> List[TrustedPeer]:
        """Get all trusted peers"""
        cursor = self.conn.execute("""
            SELECT peer_id, public_key, device_name, device_type, 
                   trust_level, paired_at, last_seen, capabilities
            FROM trust_graph
            WHERE trust_level = ?
            ORDER BY device_name
        """, (TrustLevel.TRUSTED.value,))
        
        peers = []
        for row in cursor.fetchall():
            peers.append(TrustedPeer(
                peer_id=row[0],
                public_key=row[1],
                device_name=row[2],
                device_type=row[3],
                trust_level=TrustLevel(row[4]),
                paired_at=datetime.fromtimestamp(row[5]),
                last_seen=datetime.fromtimestamp(row[6]) if row[6] else None,
                capabilities=row[7].split(',') if row[7] else []
            ))
        
        return peers
    
    def revoke_peer(self, peer_id: str):
        """Revoke trust for a peer"""
        self.conn.execute("""
            UPDATE trust_graph
            SET trust_level = ?
            WHERE peer_id = ?
        """, (TrustLevel.REVOKED.value, peer_id))
        
        self.conn.commit()
        print(f"✓ Revoked peer: {peer_id[:16]}...")
    
    def update_last_seen(self, peer_id: str):
        """Update last seen timestamp"""
        self.conn.execute("""
            UPDATE trust_graph
            SET last_seen = ?
            WHERE peer_id = ?
        """, (int(datetime.now().timestamp()), peer_id))
        
        self.conn.commit()
