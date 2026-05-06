"""
AIDE Daily Brief - Context Gathering
Collects calendar, notes, memory, and user preferences
"""
from datetime import datetime, date, timedelta
from typing import Dict, List
import sys
from pathlib import Path

from daily_brief.calendar import load_calendar_context
from tools.email_tool import fetch_recent_unread_briefing_emails

# Add project root to path if needed
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def _get_user_timezone() -> str:
    try:
        from memory.manager import MemoryManager
        return MemoryManager().get_fact("user_timezone") or "UTC"
    except Exception:
        return "UTC"


def gather_daily_context(target_date: date = None) -> Dict:
    """
    Gather all context needed for daily brief generation
    Integrates with existing AIDE memory system
    """
    if target_date is None:
        target_date = date.today()
    
    context = {
        "date": target_date.isoformat(),
        "timezone": _get_user_timezone(),
        "calendar_events": [],
        "calendar_conflicts": [],
        "calendar_suggestions": [],
        "unread_emails": [],
        "pending_approvals": [],
        "notes": [],
        "preferences": {
            "tone": "warm, concise, professional",
            "focus_hours": ["09:00-12:00"],
            "max_daily_outputs": 6
        },
        "important_contacts": []
    }
    
    # Gather from memory
    try:
        context["notes"] = _gather_from_memory(target_date)
    except Exception as e:
        print(f"⚠️ Could not load memory context: {e}")

    try:
        calendar_context = load_calendar_context(target_date, timezone_name=context["timezone"])
        context.update(calendar_context)
    except Exception as e:
        print(f"⚠️ Could not load calendar context: {e}")
    
    # Gather from recent conversations
    try:
        context["recent_context"] = _gather_recent_context()
    except Exception as e:
        print(f"⚠️ Could not load recent context: {e}")

    try:
        context["unread_emails"] = fetch_recent_unread_briefing_emails(hours=48)
    except Exception as e:
        print(f"⚠️ Could not load unread emails: {e}")

    try:
        context["pending_approvals"] = _gather_pending_approvals()
    except Exception as e:
        print(f"⚠️ Could not load pending approvals: {e}")
    
    return context


def _gather_from_memory(target_date: date) -> List[str]:
    """
    Query AIDE memory for relevant context
    """
    try:
        from memory.manager import MemoryManager
        
        memory = MemoryManager()
        
        # Query for recent important items
        queries = [
            "important tasks today",
            "meetings scheduled",
            "follow-ups needed",
            "pending replies",
            "commitments made"
        ]
        
        notes = []
        for query in queries:
            results = memory.search(query, n=3)
            for result in results:
                # Extract meaningful text from memory
                if hasattr(result, 'content'):
                    notes.append(result.content)
                elif isinstance(result, dict) and 'content' in result:
                    notes.append(result['content'])
        
        return notes[:10]  # Max 10 notes
        
    except ImportError:
        print("⚠️ Memory module not available")
        return []
    except Exception as e:
        print(f"⚠️ Memory query failed: {e}")
        return []


def _gather_recent_context() -> List[str]:
    """
    Get recent conversation context from last 24 hours
    """
    try:
        from memory.manager import MemoryManager
        
        memory = MemoryManager()
        
        recent = memory.get_recent(n=5)

        context = []
        for item in recent:
            if isinstance(item, dict):
                user_message = item.get("user", "").strip()
                assistant_reply = item.get("assistant", "").strip()
                if user_message:
                    context.append(f"User: {user_message[:160]}")
                if assistant_reply:
                    context.append(f"AIDE: {assistant_reply[:160]}")
        
        return context
        
    except Exception as e:
        print(f"⚠️ Recent context query failed: {e}")
        return []


def _gather_pending_approvals() -> List[Dict]:
    try:
        from memory.manager import MemoryManager

        memory = MemoryManager()
        if hasattr(memory, "get_pending_approvals"):
            return memory.get_pending_approvals()
        if hasattr(memory, "load_pending_approvals"):
            return memory.load_pending_approvals()
        return []
    except Exception as e:
        print(f"⚠️ Pending approvals query failed: {e}")
        return []


def load_user_preferences() -> Dict:
    """Load user preferences from onboarding"""
    from onboarding.storage import get_onboarding_data
    
    onboarding = get_onboarding_data()
    
    return {
        "user_name": onboarding.user_display_name,
        "operator_name": onboarding.operator_name,
        "tone": "warm, concise, professional",
        "max_daily_outputs": 6
    }


def add_manual_notes(notes: List[str]):
    """
    Add manual notes for the day (until calendar integration)
    Stores in a simple JSON file
    """
    from pathlib import Path
    import json
    from datetime import date
    
    notes_dir = Path.home() / ".aide" / "daily_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    
    notes_file = notes_dir / f"{date.today().isoformat()}.json"
    
    with open(notes_file, 'w') as f:
        json.dump({"notes": notes, "date": date.today().isoformat()}, f, indent=2)
    
    print(f"✅ Added {len(notes)} notes for today")


def load_manual_notes(target_date: date = None) -> List[str]:
    """Load manually added notes"""
    from pathlib import Path
    import json
    
    if target_date is None:
        target_date = date.today()
    
    notes_file = Path.home() / ".aide" / "daily_notes" / f"{target_date.isoformat()}.json"
    
    if not notes_file.exists():
        return []
    
    with open(notes_file, 'r') as f:
        data = json.load(f)
    
    return data.get("notes", [])
