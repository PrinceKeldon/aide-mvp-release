#!/usr/bin/env python3
"""
Generate daily brief and share via mesh
Run this on your Mac (full node with Ollama)
"""
import asyncio
from datetime import date

from core.llm import LLMClient
from core.settings import Settings

# Import mesh
from mesh.node import MeshNode
from mesh.identity import get_or_create_identity

# Import daily brief with mesh
from daily_brief.generator_with_mesh import MeshDailyBriefGenerator
from daily_brief.mesh_sync import MeshDailyBriefSync
from daily_brief.storage import save_daily_brief


async def main():
    print("\n" + "="*70)
    print("🌅 AIDE - Daily Brief with Mesh Sync")
    print("="*70)
    
    # Initialize mesh
    print("\n1️⃣  Initializing mesh...")
    identity = get_or_create_identity("Frank's MacBook")
    mesh_node = MeshNode(identity)
    
    # Start mesh discovery
    await mesh_node.start()
    
    # Initialize mesh sync
    mesh_sync = MeshDailyBriefSync(mesh_node)
    
    # Initialize LLM
    print("\n2️⃣  Initializing LLM...")
    settings = Settings()
    llm = LLMClient(settings)
    
    # Create generator with mesh
    generator = MeshDailyBriefGenerator(llm, mesh_sync)
    
    # Generate brief
    print("\n3️⃣  Generating daily brief...")
    brief = await generator.generate(date.today(), share_via_mesh=True)
    
    # Save locally
    save_daily_brief(brief)
    
    print("\n" + "="*70)
    print("✅ Complete!")
    print("="*70)
    print("\nBrief generated and shared with:")
    
    online_devices = mesh_node.get_online_trusted_devices()
    if online_devices:
        for device_id in online_devices:
            peer = mesh_node.trust_graph.get_peer(device_id)
            print(f"   • {peer.device_name if peer else device_id[:16]}")
    else:
        print("   (No other devices online)")
    
    print("\nView on this device: python -m daily_brief.ui.cli")
    print("View on other devices: Open AIDE app\n")


if __name__ == "__main__":
    asyncio.run(main())
