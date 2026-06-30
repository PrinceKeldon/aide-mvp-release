"""
AIDE Daily Brief - Generator with Mesh
Daily brief generation with multi-device sync
"""
from datetime import datetime, date
from typing import List, Optional
import json
import uuid

from daily_brief.context import gather_daily_context, load_user_preferences
from daily_brief.prompts import (
    build_generation_prompt,
    DRAFT_MESSAGES_PROMPT,
    PREPARED_TASKS_PROMPT
)
from daily_brief.schema import (
    DailyBrief,
    DailyBriefSummary,
    DailyBriefItem,
    ItemType,
    Priority
)


class MeshDailyBriefGenerator:
    """
    Daily brief generator with mesh synchronization
    """
    
    def __init__(self, llm_client, mesh_sync=None):
        self.llm = llm_client
        self.mesh_sync = mesh_sync
    
    async def generate(self, target_date: date = None, share_via_mesh: bool = True) -> DailyBrief:
        """Generate daily brief and optionally share via mesh"""
        
        if target_date is None:
            target_date = date.today()
        
        print(f"\n🌅 Generating daily brief for {target_date}...")
        
        # Gather context
        context = gather_daily_context(target_date)
        preferences = load_user_preferences()
        
        # Generate items
        draft_messages = await self._generate_draft_messages(context)
        prepared_tasks = await self._generate_prepared_tasks(context)
        
        # Combine and rank
        all_items = draft_messages + prepared_tasks
        all_items = self._rank_and_limit(all_items, preferences)
        
        # Create summary
        summary = self._create_summary(all_items, preferences)
        
        # Create brief
        brief = DailyBrief(
            date=target_date.isoformat(),
            summary=summary,
            items=all_items,
            generated_at=datetime.now()
        )
        
        print(f"✅ Generated {len(all_items)} items")
        
        # Share via mesh if enabled
        if share_via_mesh and self.mesh_sync:
            await self.mesh_sync.share_brief_with_devices(brief)
        
        return brief
    
    async def _generate_draft_messages(self, context: dict) -> List[DailyBriefItem]:
        """Generate draft messages"""
        
        prompt = build_generation_prompt(DRAFT_MESSAGES_PROMPT, context)
        
        try:
            response = await self.llm.generate(prompt)
            
            # Clean response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
            
            drafts = json.loads(response)
            
            items = []
            for draft in drafts:
                item = DailyBriefItem(
                    id=f"msg_{uuid.uuid4().hex[:8]}",
                    type=ItemType.DRAFT_MESSAGE,
                    priority=Priority.HIGH if "urgent" in draft.get("reason", "").lower() else Priority.MEDIUM,
                    title=f"Reply to {draft['recipient_name']}",
                    reason=draft.get("reason", ""),
                    content=draft,
                    actions=["send", "edit", "dismiss"],
                    created_at=datetime.now()
                )
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"⚠️ Failed to generate draft messages: {e}")
            return []
    
    async def _generate_prepared_tasks(self, context: dict) -> List[DailyBriefItem]:
        """Generate prepared tasks"""
        
        prompt = build_generation_prompt(PREPARED_TASKS_PROMPT, context)
        
        try:
            response = await self.llm.generate(prompt)
            
            # Clean response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
            
            tasks = json.loads(response)
            
            items = []
            for task in tasks:
                item = DailyBriefItem(
                    id=f"task_{uuid.uuid4().hex[:8]}",
                    type=ItemType.PREPARED_TASK,
                    priority=Priority.MEDIUM,
                    title=task.get("title", ""),
                    reason="Prepared to reduce effort later",
                    content=task,
                    actions=["view", "dismiss"],
                    created_at=datetime.now()
                )
                items.append(item)
            
            return items
            
        except Exception as e:
            print(f"⚠️ Failed to generate prepared tasks: {e}")
            return []
    
    def _rank_and_limit(self, items: List[DailyBriefItem], preferences: dict) -> List[DailyBriefItem]:
        """Rank and limit items"""
        
        max_counts = {
            ItemType.DRAFT_MESSAGE: 3,
            ItemType.PREPARED_TASK: 2
        }
        
        # Group by type
        by_type = {}
        for item in items:
            if item.type not in by_type:
                by_type[item.type] = []
            by_type[item.type].append(item)
        
        # Sort each type by priority
        for item_type in by_type:
            by_type[item_type].sort(
                key=lambda x: (x.priority.value, x.created_at)
            )
        
        # Apply limits
        limited = []
        for item_type, max_count in max_counts.items():
            if item_type in by_type:
                limited.extend(by_type[item_type][:max_count])
        
        # Sort: messages first, then tasks
        limited.sort(key=lambda x: (0 if x.type == ItemType.DRAFT_MESSAGE else 1, x.priority.value))
        
        return limited[:6]
    
    def _create_summary(self, items: List[DailyBriefItem], preferences: dict) -> DailyBriefSummary:
        """Create summary"""
        
        counts = {
            "draft_messages": sum(1 for i in items if i.type == ItemType.DRAFT_MESSAGE),
            "schedule_suggestions": 0,
            "prepared_tasks": sum(1 for i in items if i.type == ItemType.PREPARED_TASK),
            "approval_requests": 0
        }
        
        user_name = preferences.get("user_name")
        if user_name:
            headline = f"Good morning, {user_name}. I've prepared your day."
        else:
            headline = "Good morning. I've prepared your day."
        
        return DailyBriefSummary(
            headline=headline,
            counts=counts
        )
