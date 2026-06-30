# AIDE Memory Implementation Guide

This document describes the current technical implementation of the memory infrastructure in AIDE. While `docs/LONG_TERM_MEMORY_ARCHITECTURE.md` outlines the long-term vision, this guide focuses on the current working implementation.

## Architecture Overview

AIDE uses a **hybrid memory system** combining structured storage, semantic vector search, and a human-readable synchronization layer.

### 1. Storage Components
- **SQLite (`core/settings.py` $\rightarrow$ `memory_db_path`)**: The primary source of truth for structured facts, identity, and stratified memory.
- **ChromaDB (`core/settings.py` $\rightarrow$ `chroma_db_path`)**: A vector database used for semantic recall of past conversations.
- **Obsidian Vault (`core/settings.py` $\rightarrow$ `obsidian_vault_path`)**: A markdown-based mirror of strategic and tactical memories for human exploration and curation.

---

## Technical Implementation

### Memory Manager (`memory/manager.py`)
The `MemoryManager` class coordinates all storage layers. It provides:
- **Fact Storage**: Simple key-value pairs for preferences and identity.
- **Conversation History**: Persistent logs of all user-agent exchanges.
- **Stratified Memory Layers**:
    - **Episodic**: Raw conversation history.
    - **Tactical**: Project-specific content and short-term goals.
    - **Strategic**: Long-term patterns, high-level goals, and identity.

### Semantic Recall
AIDE implements semantic search using ChromaDB. When `recall()` is called, the system:
1. Retrieves known structured facts from SQLite.
2. Performs a vector search in ChromaDB to find the most semantically similar past conversations.
3. Blends these results into a single context window for the LLM.

### Obsidian Integration (`memory/obsidian.py`)
The `ObsidianMemorySync` class provides bidirectional synchronization between the internal database and a local Obsidian vault.

#### Sync to Obsidian ($\text{SQLite} \rightarrow \text{Markdown}$)
- Exports high-importance memories from the **Strategic** and **Tactical** layers.
- Generates markdown files with YAML frontmatter containing `memory_id`, `importance_score`, and `status`.
- Organizes files into a structured hierarchy:
    - `Strategic/Goals/`
    - `Strategic/Patterns/`
    - `Tactical/Projects/[Namespace]/`

#### Sync from Obsidian ($\text{Markdown} \rightarrow \text{SQLite}$)
- Scans the vault for modified `.md` files.
- Parses YAML frontmatter to identify the corresponding database record.
- Updates the SQLite database with user-modified metadata (e.g., updated importance or status).
- Captures bidirectional links (`[[Note]]`) to build relationship graphs.

---

## Automation & Scheduling

The memory system is integrated into the `ProactiveScheduler` (`core/scheduler.py`). 

### Scheduled Synchronization
A background job `_sync_obsidian` is configured to run at an interval defined by `settings.obsidian_sync_interval_seconds` (default: 300s). This ensures that:
1. New memories extracted by the AI are pushed to Obsidian for the user to see.
2. User reflections and links made in Obsidian are pulled back into the AI's memory.

---

## Configuration

Configuration is managed via `core/settings.py` and can be overridden in the `.env` file.

| Setting | Description | Default Value |
| :--- | :--- | :--- |
| `memory_db_path` | Path to the SQLite database | `./data/aide_memory.db` |
| `chroma_db_path` | Path to the ChromaDB storage | `./data/chroma` |
| `obsidian_vault_path` | Path to the local Obsidian vault | `~/Obsidian/AIDEMemory` |
| `obsidian_sync_interval_seconds` | Sync frequency in seconds | `300` |

## Maintenance
To manually trigger a sync or verify memory state, the `MemoryManager.sync_with_obsidian()` method can be invoked programmatically.
