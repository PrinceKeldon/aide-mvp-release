import json
from pathlib import Path
from typing import Any, Dict
from loguru import logger
from core.settings import settings
from tools.base import BaseTool, SafetyTier


class ObsidianTool(BaseTool):
    """
    Provides direct filesystem access to the Obsidian vault.
    Allows the agent to list, read, write, edit, and delete notes.
    """

    @property
    def name(self) -> str:
        return "obsidian_vault"

    @property
    def description(self) -> str:
        return (
            "Direct access to the Obsidian memory vault. Input must be JSON: "
            '{"action": "list"|"read"|"write"|"edit"|"delete", "path": "relative/path/to/note.md"}. '
            "For 'write', include 'content'. For 'edit', include 'old_text' and 'new_text'."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    async def execute(self, input_text: str) -> str:
        try:
            data = json.loads(input_text)
        except json.JSONDecodeError:
            return "Error: Input must be a valid JSON string."

        action = data.get("action")
        rel_path = data.get("path", "")
        vault_path = Path(settings.obsidian_vault_path).expanduser()

        if not action:
            return "Error: 'action' is required."

        # Security check: prevent path traversal
        if rel_path:
            full_path = (vault_path / rel_path).resolve()
            if not str(full_path).startswith(str(vault_path.resolve())):
                return "Error: Access denied. Path must be within the vault."
        else:
            full_path = vault_path

        try:
            if action == "list":
                files = [str(p.relative_to(vault_path)) for p in vault_path.rglob("*")]
                return "\n".join(files) if files else "Vault is empty."

            if action == "search":
                query = data.get("query", "").lower()
                if not query:
                    return "Error: 'query' is required for searching."
                results = []
                for md_file in vault_path.rglob("*.md"):
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        if query in content.lower():
                            results.append(str(md_file.relative_to(vault_path)))
                    except Exception:
                        continue
                return (
                    "\n".join(results)
                    if results
                    else f"No files containing '{query}' found."
                )

            if not rel_path:
                return "Error: 'path' is required for this action."

            if action == "read":
                if not full_path.is_file():
                    # List directory contents to help the agent find the correct file
                    try:
                        siblings = [p.name for p in full_path.parent.glob("*")]
                        suggestions = (
                            f" Suggeted files in this folder: {', '.join(siblings)}"
                            if siblings
                            else ""
                        )
                        return f"File {rel_path} not found.{suggestions}"
                    except Exception:
                        return f"File {rel_path} not found."
                return full_path.read_text(encoding="utf-8")

            if action == "write":
                content = data.get("content", "")
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content, encoding="utf-8")
                return f"Successfully wrote to {rel_path}."

            if action == "edit":
                old_text = data.get("old_text")
                new_text = data.get("new_text")
                if not old_text or new_text is None:
                    return "Error: 'old_text' and 'new_text' are required for editing."

                if not full_path.is_file():
                    return f"Error: File {rel_path} not found."

                content = full_path.read_text(encoding="utf-8")
                if old_text not in content:
                    return f"Error: Could not find text '{old_text}' in {rel_path}."

                new_content = content.replace(old_text, new_text)
                full_path.write_text(new_content, encoding="utf-8")
                return f"Successfully edited {rel_path}."

            if action == "delete":
                if not full_path.exists():
                    return f"Error: {rel_path} not found."
                if full_path.is_dir():
                    import shutil

                    shutil.rmtree(full_path)
                else:
                    full_path.unlink()
                return f"Successfully deleted {rel_path}."

            return f"Error: Unknown action '{action}'."

        except Exception as e:
            logger.error(f"ObsidianTool error: {e}")
            return f"Error performing {action} on {rel_path}: {e}"
