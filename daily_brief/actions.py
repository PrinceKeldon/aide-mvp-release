"""
AIDE Daily Brief - Action Handlers
Handle user actions on brief items (send, edit, dismiss, etc.)
"""
from datetime import datetime
from pathlib import Path
import json
from typing import Dict


class ActionHandler:
    """
    Handles actions on daily brief items
    """
    
    def __init__(self):
        self.actions_log = Path.home() / ".aide" / "actions.log"
    
    async def handle_action(self, item_id: str, action: str, item: Dict, modified_content: Dict = None):
        """
        Handle an action on a brief item
        
        Args:
            item_id: ID of the item
            action: Action to perform (send, edit, dismiss, etc.)
            item: Full item data
            modified_content: Modified content if user edited
        """
        
        print(f"\n🎯 Handling action: {action} on {item_id}")
        
        if action == "send":
            return await self._handle_send(item, modified_content)
        elif action == "edit":
            return await self._handle_edit(item)
        elif action == "dismiss":
            return await self._handle_dismiss(item_id)
        elif action == "approve":
            return await self._handle_approve(item)
        elif action == "deny":
            return await self._handle_deny(item_id)
        elif action == "view":
            return await self._handle_view(item)
        elif action == "accept":
            return await self._handle_accept(item)
        else:
            print(f"⚠️ Unknown action: {action}")
            return False
    
    async def _handle_send(self, item: Dict, modified_content: Dict = None) -> bool:
        """Send a draft message"""
        
        content = modified_content or item["content"]
        
        print(f"\n📤 Sending message...")
        print(f"   To: {content['recipient_name']}")
        print(f"   Channel: {content['channel']}")
        
        if content['channel'] == 'email':
            # TODO: Integrate with your email tool
            print(f"   Subject: {content.get('subject', '(no subject)')}")
            print(f"   Body: {content['body'][:100]}...")
            
            # For now, just log it
            self._log_action(item["id"], "send", {
                "recipient": content['recipient_name'],
                "channel": content['channel'],
                "subject": content.get('subject'),
                "sent_at": datetime.now().isoformat()
            })
            
            print("\n✅ Message sent (simulated - integrate with email tool)")
            return True
        
        elif content['channel'] == 'telegram':
            # TODO: Integrate with Telegram
            print(f"   Message: {content['body']}")
            
            self._log_action(item["id"], "send", {
                "recipient": content['recipient_name'],
                "channel": content['channel'],
                "sent_at": datetime.now().isoformat()
            })
            
            print("\n✅ Message sent via Telegram (simulated)")
            return True
        
        return False
    
    async def _handle_edit(self, item: Dict) -> bool:
        """Allow editing a draft"""
        
        print("\n✏️ Edit mode")
        print("Current content:")
        print(item["content"]["body"])
        print("\nEnter new content (or press Enter to keep):")
        
        new_body = input("> ").strip()
        
        if new_body:
            # Return modified content for re-handling
            modified = item["content"].copy()
            modified["body"] = new_body
            
            print("\n✅ Content updated")
            print("\nSend now? (y/n)")
            if input("> ").lower() == 'y':
                return await self._handle_send(item, modified)
        
        return True
    
    async def _handle_dismiss(self, item_id: str) -> bool:
        """Dismiss an item"""
        
        self._log_action(item_id, "dismiss", {
            "dismissed_at": datetime.now().isoformat()
        })
        
        print("\n✅ Item dismissed")
        return True
    
    async def _handle_approve(self, item: Dict) -> bool:
        """Approve an action"""
        
        content = item["content"]
        
        print(f"\n✅ Approved: {content['action_description']}")
        
        self._log_action(item["id"], "approve", {
            "action": content['action_type'],
            "approved_at": datetime.now().isoformat()
        })
        
        # TODO: Execute the approved action
        print("   (Action execution not yet implemented)")
        
        return True
    
    async def _handle_deny(self, item_id: str) -> bool:
        """Deny an action"""
        
        self._log_action(item_id, "deny", {
            "denied_at": datetime.now().isoformat()
        })
        
        print("\n✅ Action denied")
        return True
    
    async def _handle_view(self, item: Dict) -> bool:
        """View a prepared task"""
        
        content = item["content"]
        
        print("\n" + "="*60)
        print(content["title"])
        print("="*60)
        print(content["content"])
        print("="*60)
        
        return True
    
    async def _handle_accept(self, item: Dict) -> bool:
        """Accept a schedule suggestion"""
        
        content = item["content"]
        
        print(f"\n✅ Accepted: {content['description']}")
        
        self._log_action(item["id"], "accept", {
            "suggestion": content['description'],
            "accepted_at": datetime.now().isoformat()
        })
        
        # TODO: Actually modify calendar
        print("   (Calendar modification not yet implemented)")
        
        return True
    
    def _log_action(self, item_id: str, action: str, details: Dict):
        """Log action for tracking"""
        
        log_entry = {
            "item_id": item_id,
            "action": action,
            "timestamp": datetime.now().isoformat(),
            "details": details
        }
        
        # Append to log file
        with open(self.actions_log, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")
