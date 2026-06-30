import sqlite3
import json
from pathlib import Path

# Source: where the registration script put them
SOURCE_DB = Path("data/aide_memory.db")
# Destination: where the actual DeviceRegistry looks
DEST_DB = Path.home() / ".aide" / "data" / "device_registry.db"

def migrate():
    print(f"Migrating spokes from {SOURCE_DB} to {DEST_DB}...")
    
    if not SOURCE_DB.exists():
        print("Source DB not found!")
        return

    src_conn = sqlite3.connect(SOURCE_DB)
    src_conn.row_factory = sqlite3.Row
    
    dest_conn = sqlite3.connect(DEST_DB)
    
    try:
        spokes = src_conn.execute("SELECT * FROM spoke_registry").fetchall()
        print(f"Found {len(spokes)} spokes to migrate.")
        
        for s in spokes:
            did = s['device_id']
            name = s['device_name']
            host = s['mesh_host']
            port = s['mesh_port']
            caps = json.loads(s['capabilities'])
            
            # 1. Identity
            dest_conn.execute("""
                INSERT INTO device_identities (device_id, device_name, device_type, created_at)
                VALUES (?, ?, 'spoke', ?)
                ON CONFLICT(device_id) DO UPDATE SET device_name = excluded.device_name
            """, (did, name, "2026-04-22T00:00:00Z"))
            
            # 2. Trust
            dest_conn.execute("""
                INSERT INTO device_trust (device_id, relationship_type, trust_level, created_at)
                VALUES (?, 'owner_device', 'trusted', ?)
                ON CONFLICT(device_id) DO UPDATE SET trust_level = 'trusted'
            """, (did, "2026-04-22T00:00:00Z"))
            
            # 3. Transport
            dest_conn.execute("""
                INSERT INTO device_transports (device_id, transport_type, mesh_host, mesh_port, bound_at)
                VALUES (?, 'mesh', ?, ?, ?)
                ON CONFLICT(device_id, transport_type) DO UPDATE SET
                    mesh_host = excluded.mesh_host,
                    mesh_port = excluded.mesh_port
            """, (did, host, port, "2026-04-22T00:00:00Z"))
            
            print(f"✓ Migrated {name}")
            
        dest_conn.commit()
        print("Migration successful.")
    except Exception as e:
        print(f"Error during migration: {e}")
    finally:
        src_conn.close()
        dest_conn.close()

if __name__ == "__main__":
    migrate()
