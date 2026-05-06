"""
AIDE Daily Brief - Action Handlers with Mesh Sync
Handle actions and sync across devices
"""
from datetime import datetime
from pathlib import Path
import json
from typing import Dict, Optional

from daily_brief.actions import ActionHandler


class MeshActionHandler(ActionHandler):
    """
    Action handler with mesh synchronization
    """
    
    def __init__(self, mesh_sync=None):
        super().__init__()
        self.mesh_sync = mesh_sync
    
    async def handle_action(self, item_id: str, action: str, item: Dict, modified_content: Dict = None):
        """Handle action and sync to other devices"""
        
        # Handle action locally
        success = await super().handle_action(item_id, action, item, modified_content)
        
        # Sync to other devices if mesh available
        if success and self.mesh_sync:
            await self.mesh_sync.sync_action_to_devices(item_id, action, item)
        
        return success
