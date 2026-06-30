# AIDE Memory Architecture - Visual Reference & Component Map

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AIDE AGENT LOOP                                   │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────┐
                    │   User Message Received      │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Build Proactive Context     │◄──┐
                    │  (ProactiveContextSystem)    │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Query Relevant Memories      │   │
                    │ (UnifiedMemoryRecall)        │   │
                    │  • Semantic Search           │   │
                    │  • Graph Traversal           │   │
                    │  • Temporal Decay            │   │
                    │  • Predictive Surfacing      │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Inject into System Prompt    │   │
                    │ (memory-enhanced context)    │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │  LLM Inference               │   │
                    │  (Router: Groq/Ollama/etc)   │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Extract Facts                │   │
                    │ (MemoryExtractor)            │   │
                    │ Output: [decisions,          │   │
                    │          preferences, ...]   │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Score Importance             │   │
                    │ (MemoryAutonomyEngine)       │   │
                    │ Output: [(fact, layer,       │   │
                    │           ttl, relationships)]    │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Persist to Memory Layer      │   │
                    │ • Episodic (48h)             │   │
                    │ • Tactical (2-4w)            │   │
                    │ • Strategic (3-12m)          │   │
                    │ • Structural (∞)             │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Update Schema                │   │
                    │ (SchemaEvolutionEngine)      │   │
                    │ Monitors effectiveness,      │   │
                    │ discovers new categories     │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   ▼                   │
                    ┌──────────────────────────────┐   │
                    │ Return Response to User      │   │
                    └──────────────┬───────────────┘   │
                                   │                   │
                                   └───────────────────┘
                                   (feedback loop)
```

## Data Layer: Memory Stratification

```
COLD (Indefinite)                        HOT (Session)
   ▲                                        ▲
   │                                        │
   │ ┌────────────────────────────┐         │
   │ │  STRUCTURAL MEMORY         │         │
   │ │  (Identity, Relationships) │         │
   │ │                            │         │
   │ │ • Entity facts             │         │
   │ │ • Relationship graphs      │         │
   │ │ • User profile             │         │
   │ │ • Persistent entities      │         │
   │ │                            │         │
   │ │ TTL: Indefinite            │         │
   │ │ Access: Graph traversal    │         │
   │ └────────────────────────────┘         │
   │                                        │
   │ ┌────────────────────────────┐         │
   │ │  STRATEGIC MEMORY          │         │
   │ │  (Goals, Patterns)         │         │
   │ │                            │         │
   │ │ • Long-term goals (3-12m)  │         │
   │ │ • Recurring patterns       │         │
   │ │ • Learned behaviors        │         │
   │ │ • Commitments              │         │
   │ │ • Validated decisions      │         │
   │ │                            │         │
   │ │ TTL: 90-180 days           │         │
   │ │ Promoted: from Tactical    │         │
   │ │ Access: Goal-aligned       │         │
   │ └────────────────────────────┘         │
   │                                        │
   │ ┌────────────────────────────┐         │
   │ │  TACTICAL MEMORY           │         │
   │ │  (Active Projects)         │         │
   │ │                            │         │
   │ │ • Project decisions        │         │
   │ │ • Dependencies/blockers    │         │
   │ │ • Progress tracking        │         │
   │ │ • Recent choices           │         │
   │ │ • Active namespace context │         │
   │ │                            │         │
   │ │ TTL: 14-28 days            │         │
   │ │ Promoted: from Episodic    │         │
   │ │ Access: Project-scoped     │         │
   │ └────────────────────────────┘         │
   │                                        │
   │ ┌────────────────────────────┐         │
   │ │  EPISODIC MEMORY           │         │
   │ │  (Session Context)         │         │
   │ │                            │         │
   │ │ • Current conversation     │         │
   │ │ • Recent exchanges         │         │
   │ │ • Immediate context        │         │
   │ │ • Hot facts                │         │
   │ │                            │         │
   │ │ TTL: 48 hours              │         │
   │ │ Storage: In-memory + DB    │         │
   │ │ Access: Semantic search    │         │
   │ └────────────────────────────┘         │
   │                                        │
```

## Component Interaction Map

```
┌──────────────────────────────────────────────────────────────┐
│                     MEMORY MANAGER                            │
│  (Central hub - SQLite persistence)                          │
│                                                              │
│  • store_fact(key, value)                                    │
│  • store_episodic(message, response)                         │
│  • store_tactical(namespace, category, content)              │
│  • store_strategic(title, description, goal_ids)             │
│  • apply_autonomy_decisions(decisions)                       │
│  • load_recent_conversations(n)                              │
└──────────────────────────────────────────────────────────────┘
       ▲           ▲                    ▲              ▲
       │           │                    │              │
       │ writes    │ writes             │ reads        │ reads
       │ reads     │ queries            │ decisions    │ context
       │           │                    │              │
       ▼           ▼                    ▼              ▼

    ┌──────────┐  ┌──────────┐  ┌────────────────┐  ┌──────────────┐
    │  Agent   │  │Extractor │  │  Autonomy      │  │Retrieval     │
    │  Loop    │  │  Engine  │  │  Engine        │  │System        │
    │          │  │          │  │                │  │              │
    │ Loads    │  │ Extracts │  │ Scores         │  │ 4 modes:     │
    │ context  │  │ facts    │  │ importance     │  │ • Semantic   │
    │ on init  │  │ after    │  │ Selects layer  │  │ • Structural │
    │          │  │ each     │  │ Computes TTL   │  │ • Temporal   │
    │ Injects  │  │ response │  │ Finds rels     │  │ • Predictive │
    │ to       │  │          │  │                │  │              │
    │ prompt   │  │ LLM-     │  │ Returns        │  │ Queries DB   │
    │          │  │ based    │  │ decisions      │  │ Semantic idx │
    │ Calls    │  │ JSON out │  │                │  │ Graph index  │
    │ retrieval│  │          │  │ Async:         │  │              │
    │ before   │  │ Async:   │  │ never blocks    │  │ Returns      │
    │ inference│  │ non-blk  │  │                │  │ ranked       │
    │          │  │          │  │ Task type:     │  │ results      │
    │          │  │ Groq/    │  │ PRIVATE        │  │              │
    │          │  │ Ollama   │  │                │  │              │
    └──────────┘  └──────────┘  └────────────────┘  └──────────────┘


    ┌──────────────────┐         ┌──────────────────┐
    │Schema Evolution  │         │Proactive Context │
    │Engine            │         │System            │
    │                  │         │                  │
    │ Monitors:        │         │ Predicts:        │
    │ • Access stats   │         │ • User intent    │
    │ • Effectiveness  │         │ • Next task      │
    │ • Retention      │         │ • Timeline       │
    │                  │         │                  │
    │ Discovers:       │         │ Surfaces:        │
    │ • New categories │         │ • Goal-aligned   │
    │ • Unused types   │         │   memories       │
    │                  │         │ • Relevant       │
    │ Recommends:      │         │   patterns       │
    │ • Deprecations   │         │ • Blockers       │
    │ • Additions      │         │ • Deadlines      │
    │ • Modifications  │         │                  │
    │                  │         │ Injects into:    │
    │ Runs: Weekly     │         │ • System prompt  │
    │ Task: Periodic   │         │ • Context window │
    └──────────────────┘         │                  │
            ▲                     │ Timing: Pre-    │
            │                     │ inference       │
            └────────────────────►└──────────────────┘
            (recommends updates)  (queries current state)
```

## Importance Scoring Formula

```
┌─────────────────────────────────────────────────────────────────┐
│  IMPORTANCE_SCORE = weighted composite of 5 factors            │
│                                                                 │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ 1. RELEVANCE (0.3 weight)                             │    │
│  │    • How applicable to current/future context?        │    │
│  │    • Connected to active goals?                       │    │
│  │    • Addresses blockers?                              │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ 2. USER DECISION SIGNAL (0.25 weight)                 │    │
│  │    • Time spent elaborating                           │    │
│  │    • Revision frequency                               │    │
│  │    • Explicit priority statements                     │    │
│  │    • Emotional intensity                              │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ 3. CROSS-CUTTING IMPACT (0.15 weight)                 │    │
│  │    • Mentioned in multiple namespaces?                │    │
│  │    • Related to multiple goals?                       │    │
│  │    • Referenced in dependencies?                      │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ 4. PATTERN FREQUENCY (0.15 weight)                    │    │
│  │    • Recurrence count                                 │    │
│  │    • Consistency across contexts                      │    │
│  │    • Explanatory power                                │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ 5. TEMPORAL PROXIMITY (0.15 weight)                   │    │
│  │    • Recent? (bias toward now)                        │    │
│  │    • Forward-looking? (mentions future)               │    │
│  │    • Time-sensitive?                                  │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                 │
│  RESULT: score ∈ [0, 1.0]                                     │
│                                                                 │
│  score ∈ [0, 0.4]     → FORGET (episodic decay)               │
│  score ∈ (0.4, 0.6]   → EPISODIC (hot, 48h)                  │
│  score ∈ (0.6, 0.8]   → TACTICAL (warm, 2-4w)                │
│  score ∈ (0.8, 1.0]   → STRATEGIC (cold, 3-12m)              │
│  + always possible to promote to STRUCTURAL (persistent)      │
└─────────────────────────────────────────────────────────────────┘
```

## Retrieval Mode Decision Tree

```
                           User Query
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
            Contains temporal  Contains     Is asking
            reference?         entity ref?  for prediction?
               YES │            YES │         YES │
                   ▼            ▼            ▼
            ┌─────────────┐  ┌──────────┐  ┌────────────┐
            │  TEMPORAL   │  │STRUCTURAL│  │PREDICTIVE  │
            │  RETRIEVAL  │  │RETRIEVAL │  │RETRIEVAL   │
            │             │  │          │  │            │
            │ "What was   │  │"Tell me  │  │"What should│
            │  I doing in │  │ about X  │  │ I know     │
            │  May?"      │  │ project" │  │ before...?"│
            │             │  │          │  │            │
            │ SQL:        │  │ SQL:     │  │ LLM +      │
            │ Time range  │  │ Graph    │  │ Multiple   │
            │ + decay fn  │  │ traversal│  │ modes      │
            │             │  │          │  │            │
            │ Output:     │  │ Output:  │  │ Output:    │
            │ Historical  │  │ Related  │  │ Relevant   │
            │ facts       │  │ entities │  │ + useful   │
            └─────────────┘  └──────────┘  └────────────┘
                   │              │              │
                   └──────────────┼──────────────┘
                                  │
                          Otherwise: DEFAULT
                                  │
                                  ▼
                          ┌───────────────┐
                          │  SEMANTIC     │
                          │  RETRIEVAL    │
                          │               │
                          │"Similar to..." │
                          │"Tell me about"│
                          │"What about..."│
                          │               │
                          │ChromaDB       │
                          │semantic search│
                          │               │
                          │Output:        │
                          │Conceptually   │
                          │similar facts  │
                          └───────────────┘
```

## Database Schema Hierarchy

```
ROOT: AIDE MEMORY DATABASE (aide_memory.db)
│
├── EPISODIC LAYER (Hot, Session-scoped)
│   ├── episodic_memories
│   │   ├── id (PK)
│   │   ├── session_id (FK)
│   │   ├── user_message TEXT
│   │   ├── agent_response TEXT
│   │   ├── importance_score REAL
│   │   ├── expires_at DATETIME
│   │   └── metadata JSON
│   │
│   └── [ChromaDB collection: "episodic"]
│       └── vectors + metadata for semantic search
│
├── TACTICAL LAYER (Warm, Project-scoped)
│   ├── tactical_memories
│   │   ├── id (PK)
│   │   ├── namespace TEXT (project/goal grouping)
│   │   ├── category TEXT (DECISION, DEPENDENCY, etc)
│   │   ├── content TEXT
│   │   ├── importance_score REAL
│   │   ├── status TEXT (active/archived/resolved)
│   │   └── metadata JSON
│   │
│   └── tactical_relationships
│       ├── source_id (FK → tactical_memories)
│       ├── target_id (FK → tactical_memories)
│       └── relationship_type TEXT (DEPENDS_ON, etc)
│
├── STRATEGIC LAYER (Warm, Long-term)
│   ├── strategic_memories
│   │   ├── id (PK)
│   │   ├── category TEXT (GOAL, PATTERN, COMMITMENT, etc)
│   │   ├── title TEXT
│   │   ├── description TEXT
│   │   ├── importance_score REAL
│   │   ├── validation_count INTEGER
│   │   ├── horizon_days INTEGER (expected relevance window)
│   │   ├── status TEXT (active/deprecated/obsolete)
│   │   └── metadata JSON
│   │
│   └── strategic_insights
│       ├── memory_id (FK → strategic_memories)
│       ├── insight_type TEXT (CORRELATION, PREDICTION, etc)
│       └── confidence REAL
│
├── STRUCTURAL LAYER (Cold, Persistent)
│   ├── identity_facts
│   │   ├── entity_type TEXT (USER, PROJECT, PERSON, etc)
│   │   ├── entity_id TEXT
│   │   ├── property TEXT
│   │   └── value TEXT
│   │
│   └── identity_relationships
│       ├── source_entity_type TEXT
│       ├── source_entity_id TEXT
│       ├── relationship_type TEXT (OWNS, COLLABORATES_WITH, etc)
│       ├── target_entity_type TEXT
│       ├── target_entity_id TEXT
│       └── properties JSON
│
├── MANAGEMENT & MONITORING
│   ├── memory_lifecycle (audit trail)
│   │   ├── memory_type TEXT
│   │   ├── memory_id INTEGER
│   │   ├── action TEXT (created, promoted, expired, etc)
│   │   └── timestamp DATETIME
│   │
│   └── schema_evolution_log
│       ├── recommendation_id TEXT
│       ├── action TEXT (add, deprecate, modify)
│       ├── category TEXT
│       ├── status TEXT (proposed, approved, implemented)
│       └── metadata JSON
│
└── LEGACY/EXISTING TABLES (for compatibility)
    ├── facts (migrated → tactical/strategic)
    ├── conversations (kept for historical reference)
    ├── audit_log (enhanced with tier info)
    ├── preferences
    └── pending_approvals
```

## Decay Functions by Layer

```
EPISODIC: Exponential Decay (aggressive)
│
│  importance
│    1.0 ├─ ○ ← Fresh (just created)
│        │  \
│    0.8 │   \
│        │    ╲
│    0.6 │     ╲
│        │      ╲
│    0.4 │       ╲__
│        │          ╲___
│    0.2 │              ╲____
│        │
│    0.0 ├─────────────────────► time
│         0h    12h    24h    48h
│
│ Lifetime: 48 hours (2 days)
│ Half-life: ~12 hours
│ Purpose: Recent context loses relevance fast

TACTICAL: Linear Decay (moderate)
│
│  importance
│    1.0 ├─ ○ ← Fresh
│        │  │
│    0.8 │  │╲
│        │  │ ╲
│    0.6 │  │  ╲
│        │  │   ╲
│    0.4 │  │    ╲
│        │  │     ╲
│    0.2 │  │      ╲___
│        │  │          ╲
│    0.0 ├──┴─────────────────► time
│         0d    7d    14d    21d
│
│ Lifetime: 14-28 days
│ Decay rate: ~5% per day
│ Purpose: Project context remains relevant

STRATEGIC: Very Slow Decay (minimal)
│
│  importance
│    1.0 ├─ ○ ← Fresh
│        │  │
│    0.9 │  ├─ Validated facts stay hot
│        │  │  (access = renewal)
│    0.8 │  │
│        │  │
│    0.7 │  ├─ Unvalidated slowly fade
│        │  │
│    0.6 │  │╲___
│        │  │     ╲___
│    0.5 │  │         ╲______
│        │  │                ╲___
│    0.0 ├──┴──────────────────────► time
│         0d    30d   60d   90d  180d
│
│ Lifetime: 90-180 days (3-6 months)
│ Decay rate: ~0.5% per day
│ BUT: Each validation/access resets timer
│ Purpose: Long-term patterns, validated through use

STRUCTURAL: No Decay (indefinite)
│
│  importance
│    1.0 ├─ ○ ← Created
│        │  │
│        │  │
│        │  │ (stays constant)
│        │  │
│        │  │
│        │  │
│        │  │
│        │  │
│    0.0 ├──┴────────────────────► time
│         0d    100d   1000d
│
│ Lifetime: Indefinite
│ Decay: None
│ Access pattern: Graph queries, not time-based
│ Purpose: Immutable identity/relationships
```

## State Transitions & Promotion Logic

```
                    Promotion Path →
                    
    EPISODIC ──(importance > 0.6)──> TACTICAL ──(validated 2+)──> STRATEGIC
      48h           + active project       14-28d      times              90-180d
                    + decision signal      + status                       + goal-linked
                                          tracking
    
    All Layers ──(promoted to)──> STRUCTURAL (if entity-based)
                (indefinite)

    Demotion Path ←

    STRATEGIC ──(contradicted/obsolete)──> DEPRECATED (still queryable)
    TACTICAL ──(project resolved)──> ARCHIVED (queryable, marked inactive)
    EPISODIC ──(expires)──> PURGED (deleted after TTL)

    Schema Evolution ←
    
    Unused Category ──(after 30 days no access)──> DEPRECATED
    Emergent Category ──(after 10+ examples, 0.7+ confidence)──> PROPOSED
    PROPOSED ──(user approves OR semantic validation)──> ACTIVE
```

## Example: Multi-Layer Memory for a Project

```
USER: "We're building a real-time dashboard for Q3 deliverable.
       It needs WebSocket support and has to scale to 10k concurrent users.
       We're blocked waiting for backend API design from James' team."

EXTRACTION OUTPUT:
{
  "decisions": [
    "Real-time dashboard is Q3 deliverable",
    "Must support WebSocket",
    "Scale target: 10k concurrent users"
  ],
  "commitments": [
    "Waiting for backend API design from James' team"
  ],
  "blockers": [
    "Blocked: Backend API design dependency"
  ]
}

AUTONOMY ENGINE SCORING:
┌──────────────────────────────────────┐
│ Fact: "Real-time dashboard Q3"       │
│ Relevance: 0.95 (active project)     │
│ User Signal: 0.9 (explicit delivery) │
│ Cross-cut: 0.8 (affects timeline)    │
│ Frequency: new                        │
│ Temporal: 0.9 (forward-looking)      │
│                                      │
│ → Combined: 0.92                     │
│ → Layer: STRATEGIC                   │
│ → TTL: 90 days                       │
│ → Namespace: "Q3_Deliverables"       │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│ Fact: "Blocked on James API"         │
│ Relevance: 0.85 (active blocker)     │
│ User Signal: 0.6 (mentioned issue)   │
│ Cross-cut: 0.5 (affects plan)        │
│ Frequency: new                        │
│ Temporal: 0.95 (immediate concern)   │
│                                      │
│ → Combined: 0.75                     │
│ → Layer: TACTICAL                    │
│ → TTL: 14 days                       │
│ → Namespace: "Dashboard_Project"     │
│ → Status: "blocker"                  │
└──────────────────────────────────────┘

PERSISTENT STORAGE:

STRATEGIC_MEMORIES:
├─ ID: 42
├─ Category: GOAL
├─ Title: "Q3 Real-time Dashboard"
├─ Description: "Build real-time dashboard for Q3 deliverable..."
├─ Namespace: "Q3_Deliverables"
├─ Importance: 0.92
├─ Created: 2026-04-15
├─ Status: active
└─ Metadata: {requirements: [websocket, 10k_users]}

TACTICAL_MEMORIES:
├─ ID: 487
├─ Category: BLOCKER
├─ Namespace: "Dashboard_Project"
├─ Content: "Blocked waiting for backend API design from James' team"
├─ Importance: 0.75
├─ Status: active
├─ Parent_id: 42 (linked to strategic goal)
└─ Related: [{489: "WebSocket requirement"}]

RELATIONSHIPS:
├─ source: 487 (blocker)
├─ target: 42 (strategic goal)
├─ type: BLOCKS
└─ strength: 0.9

CHROMADB SEMANTIC INDEX:
├─ "Q3 dashboard real-time WebSocket 10k users" → doc_id: 42_semantic
├─ "Blocked James team backend API" → doc_id: 487_semantic
└─ (Can recall these via semantic similarity for future queries)

FUTURE RETRIEVAL:
When user asks: "How's the dashboard coming?"
→ Predictive mode surfaces both STRATEGIC + TACTICAL
→ Agent knows: goal exists, has specific requirements, is blocked
→ Agent can proactively offer: "Let me check on James' API status"
```

---

## Key Concepts Reference

| Concept | Definition | Example |
|---------|-----------|---------|
| **Importance Score** | 0-1 value indicating worthiness of persistence | 0.92 = definitely save, 0.35 = probably forget |
| **Layer** | Memory tier (Episodic/Tactical/Strategic/Structural) | Strategic for goals; Tactical for blockers |
| **TTL (Time-to-Live)** | How long memory persists before expiration | 48h episodic, 21d tactical, 180d strategic |
| **Namespace** | Logical grouping (usually project/goal) | "Q3_Deliverables", "Dashboard_Project" |
| **Promotion** | Moving fact up to higher layer (colder) | Episodic → Tactical when importance > 0.6 |
| **Demotion** | Moving fact down to lower layer (hotter) | Tactical → Episodic when project concludes |
| **Decay** | Time-based reduction in relevance | Exponential (episodic) vs linear (tactical) |
| **Relationship** | Link between two memories with type | "Dashboard DEPENDS_ON API_design" |
| **Validation** | User confirms/uses a memory | Accessing strategic fact = validation |
| **Access Boost** | Renewing TTL when memory is accessed | Reading fact resets its expiration timer |
| **Supersession** | New fact replaces old contradictory one | "New deadline is May 1" supersedes "April 30" |
| **Confidence** | AI's certainty about this fact's validity | 0.95 = very confident, 0.6 = less certain |

This reference should help you visualize how the pieces fit together. Refer to the full architecture document (`LONG_TERM_MEMORY_ARCHITECTURE.md`) for implementation details.
