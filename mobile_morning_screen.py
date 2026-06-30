#!/usr/bin/env python3
"""
Mobile morning screen client
Run this on Android or other devices to fetch and display brief
"""
import asyncio
from datetime import date

# Import mesh
from mesh.node import MeshNode
from mesh.identity import get_or_create_identity

# Import daily brief with mesh
from daily_brief.mesh_sync import MeshDailyBriefSync
from daily_brief.storage import load_daily_brief
from daily_brief.ui.cli import display_morning_screen


async def main():
    print("\n" + "="*70)
    print("📱 AIDE Mobile - Morning Screen")
    print("="*70)
    
    # Initialize mesh
    print("\n1️⃣  Connecting to mesh...")
    identity = get_or_create_identity("Frank's Android")
    mesh_node = MeshNode(identity)
    
    await mesh_node.start()
    
    # Initialize mesh sync
    mesh_sync = MeshDailyBriefSync(mesh_node)
    
    # Check if brief exists locally
    print("\n2️⃣  Loading today's brief...")
    brief_data = load_daily_brief(date.today())
    
    if not brief_data:
        print("   Brief not found locally")
        
        # Find Mac device
        online_devices = mesh_node.get_online_trusted_devices()
        
        if not online_devices:
            print("   ⚠️  No devices online to request brief from")
            print("   Generate brief on Mac first")
            return
        
        # Request from first online device (usually Mac)
        mac_device = online_devices[0]
        print(f"   Requesting brief from {mesh_node.trust_graph.get_peer(mac_device).device_name}...")
        
        await mesh_sync.request_brief_from_device(mac_device, date.today())
        
        # Wait a bit for response
        await asyncio.sleep(2)
        
        # Try loading again
        brief_data = load_daily_brief(date.today())
    
    if brief_data:
        print("\n3️⃣  Displaying morning screen...")
        display_morning_screen(brief_data)
    else:
        print("\n   ⚠️  Could not load brief")


if __name__ == "__main__":
    asyncio.run(main())
