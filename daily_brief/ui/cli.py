"""
AIDE Daily Brief - CLI UI
Terminal-based morning screen
"""
from daily_brief.storage import load_daily_brief
from datetime import date


def display_morning_screen(brief_data: dict = None):
    """Display morning screen in terminal"""
    
    if brief_data is None:
        brief_data = load_daily_brief(date.today())
    
    if not brief_data:
        print("\n📭 No daily brief available yet.")
        print("   Generate one with: python generate_daily_brief.py")
        return
    
    # Header
    print("\n" + "="*70)
    print(brief_data["summary"]["headline"])
    print("="*70)
    
    # Summary counts
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
        summary_parts.append(f"{counts['prepared_tasks']} task(s) prepared")
    
    if summary_parts:
        print(f"\n{', '.join(summary_parts)}")
    
    if not brief_data["items"]:
        print("\n✨ Your day is clear. Nothing urgent needs preparation.")
        print("="*70 + "\n")
        return
    
    # Items
    for i, item in enumerate(brief_data["items"], 1):
        print("\n" + "-"*70)
        
        # Priority & type icons
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
        
        # Display content
        if item["type"] == "calendar_agenda":
            for event in item["content"].get("events", []):
                if event.get("all_day"):
                    time_label = "All day"
                else:
                    time_label = f"{event['start_at'][11:16]}-{event['end_at'][11:16]}"
                print(f"\n   {time_label} | {event['title']} [{event['calendar']}]")
                if event.get("location"):
                    print(f"   Location: {event['location']}")

        elif item["type"] == "email_digest":
            content = item["content"]
            print(f"\n   From: {content['from']}")
            print(f"   Subject: {content['subject']}")
            print(f"   Summary: {content['summary']}")

        elif item["type"] == "draft_message":
            content = item["content"]
            print(f"\n   To: {content['recipient_name']}")
            if content.get("subject"):
                print(f"   Subject: {content['subject']}")
            print(f"\n   {content['body']}")

        elif item["type"] == "schedule_suggestion":
            content = item["content"]
            print(f"\n{content.get('description', '')}")

        elif item["type"] == "approval_request":
            content = item["content"]
            print(f"\n   Action: {content['action_description']}")
            print(f"   Impact: {content['consequence']}")
            if content.get("expires_at"):
                print(f"   Expires: {content['expires_at']}")

        elif item["type"] == "prepared_task":
            content = item["content"]
            print(f"\n{content['content']}")
        
        # Actions
        actions_str = " | ".join([f"[{a.upper()}]" for a in item["actions"]])
        print(f"\n   {actions_str}")
    
    print("\n" + "="*70)
    print()


if __name__ == "__main__":
    display_morning_screen()
