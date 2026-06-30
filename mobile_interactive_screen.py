#!/usr/bin/env python3
"""
Interactive mobile morning screen with mesh-synced actions
"""
import asyncio
from datetime import date

from mesh.node import MeshNode
from mesh.identity import get_or_create_identity

from daily_brief.mesh_sync import MeshDailyBriefSync
from daily_brief.actions_with_mesh import MeshActionHandler
from daily_brief.storage import load_daily_brief


async def main():
    print("\n" + "="*70)
    print("📱 AIDE Mobile - Interactive Morning Screen")
    print("="*70)
    
    # Initialize mesh
    identity = get_or_create_identity("Frank's Android")
    mesh_node = MeshNode(identity)
    await mesh_node.start()
    
    # Initialize mesh sync
    mesh_sync = MeshDailyBriefSync(mesh_node)
    
    # Get brief (request if needed)
    brief_data = load_daily_brief(date.today())
    
    if not brief_data:
        # Request from Mac
        online_devices = mesh_node.get_online_trusted_devices()
        if online_devices:
            await mesh_sync.request_brief_from_device(online_devices[0], date.today())
            await asyncio.sleep(2)
            brief_data = load_daily_brief(date.today())
    
    if not brief_data:
        print("\n❌ No brief available")
        return
    
    # Display and handle actions
    handler = MeshActionHandler(mesh_sync)
    
    while True:
        # Display screen
        _display_screen(brief_data)
        
        items = brief_data["items"]
        
        if not items:
            print("\n✨ All done!")
            break
        
        # Show menu
        print("\n" + "="*70)
        print("Select item:")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item['title']}")
        print("  q. Quit")
        print("="*70)
        
        choice = input("\nSelect: ").strip().lower()
        
        if choice == 'q':
            break
        
        try:
            item_idx = int(choice) - 1
            if 0 <= item_idx < len(items):
                item = items[item_idx]
                await _handle_item(item, handler)
                
                # Remove item
                items.pop(item_idx)
                brief_data["items"] = items
        except ValueError:
            print("Invalid input")


def _display_screen(brief_data: dict):
    """Display brief"""
    print("\n" + "="*70)
    print(brief_data["summary"]["headline"])
    print("="*70)


async def _handle_item(item: dict, handler):
    """Handle actions on item"""
    
    print(f"\n{item['title']}")
    print(f"   {item['reason']}")
    
    if item["type"] == "draft_message":
        print(f"\n   To: {item['content']['recipient_name']}")
        print(f"\n   {item['content']['body']}")
    
    print("\nActions:")
    for i, action in enumerate(item["actions"], 1):
        print(f"  {i}. {action.upper()}")
    
    action_choice = input("\nChoose: ").strip()
    
    try:
        action_idx = int(action_choice) - 1
        if 0 <= action_idx < len(item["actions"]):
            action = item["actions"][action_idx]
            await handler.handle_action(item["id"], action, item)
    except ValueError:
        print("Invalid")


if __name__ == "__main__":
    asyncio.run(main())
