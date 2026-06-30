"""
AIDE Daily Brief - Interactive CLI
Terminal UI with action handling
"""
import asyncio
from daily_brief.storage import load_daily_brief
from daily_brief.actions import ActionHandler
from datetime import date


async def interactive_morning_screen():
    """Interactive morning screen with actions"""
    
    brief_data = load_daily_brief(date.today())
    
    if not brief_data:
        print("\n📭 No daily brief available.")
        print("   Generate one with: python generate_daily_brief.py")
        return
    
    handler = ActionHandler()
    
    while True:
        # Display screen
        _display_screen(brief_data)
        
        # Get items
        items = brief_data["items"]
        
        if not items:
            print("\n✨ All done! No actions needed.")
            break
        
        # Show menu
        print("\n" + "="*70)
        print("Actions:")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item['title']}")
        print("  q. Quit")
        print("="*70)
        
        choice = input("\nSelect item number (or q to quit): ").strip().lower()
        
        if choice == 'q':
            break
        
        try:
            item_idx = int(choice) - 1
            if 0 <= item_idx < len(items):
                item = items[item_idx]
                await _handle_item_actions(item, handler)
                
                # Remove handled item
                items.pop(item_idx)
                brief_data["items"] = items
            else:
                print("Invalid selection")
        except ValueError:
            print("Invalid input")


def _display_screen(brief_data: dict):
    """Display the morning screen"""
    
    print("\n" + "="*70)
    print(brief_data["summary"]["headline"])
    print("="*70)
    
    counts = brief_data["summary"]["counts"]
    summary_parts = []
    if counts.get("calendar_events", 0) > 0:
        summary_parts.append(f"{counts['calendar_events']} calendar event(s)")
    if counts.get("calendar_conflicts", 0) > 0:
        summary_parts.append(f"{counts['calendar_conflicts']} conflict(s)")
    if counts.get("approval_requests", 0) > 0:
        summary_parts.append(f"{counts['approval_requests']} approval request(s)")
    if counts["draft_messages"] > 0:
        summary_parts.append(f"{counts['draft_messages']} message(s)")
    if counts["prepared_tasks"] > 0:
        summary_parts.append(f"{counts['prepared_tasks']} task(s)")
    
    if summary_parts:
        print(f"\n{', '.join(summary_parts)}")


async def _handle_item_actions(item: dict, handler: ActionHandler):
    """Handle actions for a specific item"""
    
    print("\n" + "="*70)
    
    # Show item details
    priority_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(item["priority"], "")
    type_icon = {
        "calendar_agenda": "🗓",
        "email_digest": "📬",
        "draft_message": "✉️",
        "schedule_suggestion": "📅",
        "prepared_task": "📋",
        "approval_request": "⚠️"
    }.get(item["type"], "")
    
    print(f"\n{priority_icon} {type_icon} {item['title']}")
    print(f"   {item['reason']}")
    
    # Show content
    if item["type"] == "calendar_agenda":
        for event in item["content"].get("events", []):
            if event.get("all_day"):
                time_label = "All day"
            else:
                time_label = f"{event['start_at'][11:16]}-{event['end_at'][11:16]}"
            print(f"\n   {time_label} | {event['title']} [{event['calendar']}]")

    elif item["type"] == "email_digest":
        content = item["content"]
        print(f"\n   From: {content['from']}")
        print(f"   Subject: {content['subject']}")
        print(f"\n   {content['summary']}")

    elif item["type"] == "draft_message":
        content = item["content"]
        print(f"\n   To: {content['recipient_name']}")
        if content.get("subject"):
            print(f"   Subject: {content['subject']}")
        print(f"\n   {content['body']}")

    elif item["type"] == "schedule_suggestion":
        content = item["content"]
        print(f"\n   {content.get('description', '')}")

    elif item["type"] == "prepared_task":
        content = item["content"]
        print(f"\n{content['content']}")
    
    elif item["type"] == "approval_request":
        content = item["content"]
        print(f"\n   Action: {content['action_description']}")
        print(f"   Impact: {content['consequence']}")
    
    # Show available actions
    print("\n" + "-"*70)
    print("Available actions:")
    for i, action in enumerate(item["actions"], 1):
        print(f"  {i}. {action.upper()}")
    print("  0. Skip")
    
    action_choice = input("\nChoose action: ").strip()
    
    try:
        action_idx = int(action_choice)
        if action_idx == 0:
            print("Skipped")
            return
        
        if 1 <= action_idx <= len(item["actions"]):
            action = item["actions"][action_idx - 1]
            await handler.handle_action(item["id"], action, item)
        else:
            print("Invalid choice")
    except ValueError:
        print("Invalid input")


if __name__ == "__main__":
    asyncio.run(interactive_morning_screen())
