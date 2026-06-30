import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger
from core.settings import settings
from memory.manager import MemoryManager


class ObsidianMemorySync:
    """
    Bidirectional sync between SQLite and Obsidian vault.
    Allows AIDE to export structured memories to human-readable markdown
    and import user annotations and links back into the database.
    """

    def __init__(self, memory: MemoryManager):
        self.memory = memory
        self.vault_path = Path(settings.obsidian_vault_path).expanduser()
        self._ensure_vault_structure()

    def _ensure_vault_structure(self):
        """Creates the required directory hierarchy in the Obsidian vault."""
        dirs = [
            "Strategic/Goals",
            "Strategic/Patterns",
            "Strategic/Commitments",
            "Tactical/Projects",
            "Episodic/Daily_Notes",
            "FinanceOS/Reports",
            "FinanceOS/Ingestions",
            "FinanceOS/Conversations",
            "FinanceOS/Data",
        ]
        for d in dirs:
            (self.vault_path / d).mkdir(parents=True, exist_ok=True)

    def _generate_markdown(self, mem: Dict[str, Any], layer: str) -> str:
        """Generate well-formatted markdown with YAML frontmatter from a memory record."""
        # Handle different layer fields
        if layer == "strategic":
            title = mem.get("title", "Untitled Strategic Memory")
            description = mem.get("description", "")
            category = mem.get("category", "general")
            importance = mem.get("importance_score", 0.0)
            horizon = mem.get("horizon_days", 90)
            status = mem.get("status", "active")
            metadata = json.loads(mem.get("metadata", "{}"))
        else:  # tactical
            title = mem.get("category", "Tactical Note")
            description = mem.get("content", "")
            category = mem.get("category", "general")
            importance = mem.get("importance_score", 0.0)
            horizon = 28  # Default tactical TTL
            status = mem.get("status", "active")
            metadata = json.loads(mem.get("metadata", "{}"))

        frontmatter = {
            "memory_id": mem.get("id"),
            "memory_type": layer,
            "category": category,
            "importance_score": importance,
            "status": status,
            "horizon_days": horizon,
            "tags": metadata.get("tags", []),
            "updated_at": mem.get("updated_at"),
        }

        md = f"---\n{json.dumps(frontmatter, indent=2)}\n---\n\n"
        md += f"# {title}\n\n"
        md += f"{description}\n\n"
        md += "## Notes\nAdd your reflections, questions, or context here. Links to [[Other Notes]] will be automatically captured.\n"

        return md

    async def sync_to_obsidian(self, last_sync: str = None):
        """Write high-importance memories from SQLite -> Obsidian markdown."""
        logger.debug("Syncing memories to Obsidian vault...")

        # Sync Strategic
        strategic = self.memory.get_strategic_memories(updated_after=last_sync)
        for mem in strategic:
            content = self._generate_markdown(mem, "strategic")
            path = (
                self.vault_path
                / "Strategic"
                / mem.get("category", "general")
                / f"{mem.get('title')}.md"
            )
            try:
                path.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.error(
                    f"Failed to write strategic memory {mem.get('id')} to Obsidian: {e}"
                )

        # Sync Tactical
        tactical = self.memory.get_tactical_memories(updated_after=last_sync)
        for mem in tactical:
            content = self._generate_markdown(mem, "tactical")
            namespace = mem.get("namespace", "General")
            path = (
                self.vault_path
                / "Tactical"
                / "Projects"
                / namespace
                / f"{mem.get('category')}.md"
            )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.error(
                    f"Failed to write tactical memory {mem.get('id')} to Obsidian: {e}"
                )

    async def sync_from_obsidian(self):
        """Read user annotations from Obsidian markdown -> SQLite."""
        logger.debug("Syncing annotations from Obsidian vault...")

        # Simplified scan of the vault for modified files
        for md_file in self.vault_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                # Parse YAML frontmatter
                match = re.search(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
                if not match:
                    continue

                try:
                    frontmatter = json.loads(match.group(1))
                except json.JSONDecodeError:
                    # Fallback for simple key: value pairs if not JSON
                    frontmatter = {}
                    for line in match.group(1).split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            frontmatter[k.strip()] = v.strip()

                mem_id = frontmatter.get("memory_id")
                if not mem_id:
                    continue

                mem_id = int(mem_id)
                mem_type = frontmatter.get("memory_type")

                # Extract links
                links = re.findall(r"\[\[(.*?)\]\]", content)

                # Update database
                updates = {
                    "importance_score": frontmatter.get("importance_score"),
                    "status": frontmatter.get("status"),
                }
                # Clean None values
                updates = {k: v for k, v in updates.items() if v is not None}

                if mem_type == "strategic":
                    self.memory.update_strategic_memory(mem_id, updates)
                elif mem_type == "tactical":
                    self.memory.update_tactical_memory(mem_id, updates)

                # Create relationships from links (simplified)
                # In a real impl, we would resolve the link name to an ID
                # For now, we log them
                for link in links:
                    logger.debug(f"User linked {mem_id} to {link}")

            except Exception as e:
                logger.error(f"Failed to parse Obsidian file {md_file}: {e}")
