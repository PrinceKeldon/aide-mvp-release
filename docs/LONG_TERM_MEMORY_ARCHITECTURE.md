# AIDE: Long-Term Memory Architecture Plan
## From Reactive Tool to Proactive Cognitive Partner

**Vision**: Transform AIDE from a session-based reactive tool into a sophisticated, self-evolving cognitive system capable of maintaining deep project continuity and nuanced context over months, enabling truly long-term collaboration with autonomous memory management and predictive context injection.

---

## Executive Summary

### Current State
- **Linear Storage**: Flat SQLite facts + flat ChromaDB vectors
- **Passive Retrieval**: Memory only accessed when explicitly queried
- **Categorical Rigidity**: Fixed extraction categories (decisions, preferences, commitments, patterns, relationships)
- **No Temporal Semantics**: Facts treated as static entities without lifecycle management
- **Limited Inference**: No relationship discovery or cross-fact synthesis

### Proposed Evolution
1. **Tiered Memory Architecture** (Operational → Tactical → Strategic → Episodic)
2. **Autonomous Importance Scoring** (AI self-determines what persists)
3. **Temporal Knowledge Graph** (relationships with time, decay, and evolution)
4. **Hybrid Retrieval System** (semantic + structural + temporal + relational)
5. **Self-Adapting Schemas** (memory structure evolves with usage patterns)
6. **Proactive Context Injection** (predictive memory surfacing for upcoming tasks)

---

## Architecture Layers

### Layer 1: Episodic Memory (Per-Session, Hot)
**Purpose**: Current conversation context, immediate decisions
**Retention**: Session lifetime + 48 hours
**Storage**: In-memory + SQLite `conversations` table
**Retrieval**: Chronological, semantic similarity
**Decay**: Exponential (recent is hotter)

**Data Model**:
```sql
CREATE TABLE episodic_memories (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    conversation_turn INTEGER,
    user_message TEXT,
    agent_response TEXT,
    extracted_facts JSON,
    importance_score REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed DATETIME,
    metadata JSON DEFAULT '{}'
);
```

**Lifecycle**:
- Created: Every exchange
- Accessed: Semantic search, recent context building
- Promotion: High-importance facts → Tactical
- Decay: Exponential after 48 hours
- Purge: After 7 days unless promoted

### Layer 2: Tactical Memory (Project-Scoped, Warm)
**Purpose**: Active project context, decisions, dependencies
**Retention**: 2-4 weeks (configurable per project)
**Storage**: SQLite `tactical_memories` + relational graph
**Retrieval**: Project-scoped, importance-weighted
**Decay**: Linear with access frequency boost

**Data Model**:
```sql
CREATE TABLE tactical_memories (
    id INTEGER PRIMARY KEY,
    namespace TEXT NOT NULL,  -- project/goal scope
    category TEXT NOT NULL,  -- DECISION, DEPENDENCY, BLOCKER, PROGRESS
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    promoted_at DATETIME,
    last_accessed DATETIME,
    importance_score REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.7,
    related_facts JSON DEFAULT '[]',  -- [id1, id2, ...]
    status TEXT DEFAULT 'active',  -- active, archived, resolved
    parent_id INTEGER REFERENCES tactical_memories(id),
    metadata JSON DEFAULT '{}'
);

CREATE TABLE tactical_relationships (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES tactical_memories(id),
    target_id INTEGER REFERENCES tactical_memories(id),
    relationship_type TEXT,  -- DEPENDS_ON, BLOCKED_BY, RELATES_TO, SUPERSEDES
    strength REAL DEFAULT 0.5,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);
```

**Lifecycle**:
- Created: Extracted from episodic, explicitly persisted facts
- Accessed: Project context injection, dependency resolution
- Updated: Status changes, relationship discovery
- Promotion: High-importance, stable facts → Strategic
- Decay: Linear, refreshed on access
- Archive: Resolved dependencies, concluded projects

### Layer 3: Strategic Memory (Long-Term, Warm)
**Purpose**: User goals, patterns, commitments, capabilities, learned behavior
**Retention**: 3-12 months (or indefinite for evergreen facts)
**Storage**: SQLite `strategic_memories` + semantic index
**Retrieval**: Goal/capability-aligned, topic clustering
**Decay**: Very slow, access-boosted

**Data Model**:
```sql
CREATE TABLE strategic_memories (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,  -- GOAL, PATTERN, COMMITMENT, CAPABILITY, VALUE, CONSTRAINT
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    promoted_at DATETIME,
    last_validated DATETIME,
    validation_count INTEGER DEFAULT 0,
    importance_score REAL DEFAULT 0.7,
    confidence REAL DEFAULT 0.8,
    horizon_days INTEGER DEFAULT 90,  -- expected relevance window
    related_goals JSON DEFAULT '[]',
    related_patterns JSON DEFAULT '[]',
    status TEXT DEFAULT 'active',  -- active, deprecated, obsolete
    superseded_by INTEGER REFERENCES strategic_memories(id),
    metadata JSON DEFAULT '{}'
);

CREATE TABLE strategic_insights (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER REFERENCES strategic_memories(id),
    insight_type TEXT,  -- CORRELATION, PREDICTION, RECOMMENDATION, ANTI_PATTERN
    description TEXT NOT NULL,
    confidence REAL DEFAULT 0.6,
    discovery_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    validation_count INTEGER DEFAULT 0,
    metadata JSON DEFAULT '{}'
);
```

**Lifecycle**:
- Created: Stable patterns from tactical layer (2+ validations)
- Evolved: Refined through repeated encounters
- Validated: Confirmed during relevant tasks
- Deprecated: Contradicted by new evidence
- Archival: Obsolete patterns, historic interest

### Layer 4: Structural Memory (Persistent, Cold)
**Purpose**: Identity, relationships, facts independent of time
**Retention**: Indefinite
**Storage**: SQLite `identity_facts` + relational graph
**Retrieval**: Relationship traversal, graph queries

**Data Model**:
```sql
CREATE TABLE identity_facts (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,  -- USER, PROJECT, PERSON, SYSTEM, CONCEPT
    entity_id TEXT NOT NULL,
    property TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, property)
);

CREATE TABLE identity_relationships (
    id INTEGER PRIMARY KEY,
    source_entity_type TEXT,
    source_entity_id TEXT,
    relationship_type TEXT,  -- OWNS, WORKS_ON, COLLABORATES_WITH, DEPENDS_ON
    target_entity_type TEXT,
    target_entity_id TEXT,
    properties JSON DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Lifecycle**:
- Created: Persistent user/identity facts
- Updated: Relationship changes
- Never deleted: Full audit trail
- Queryable: Graph traversal for context

---

## Importance Scoring & Autonomy

### Autonomous Importance Engine
**Purpose**: AI self-determines what deserves persistence

**Scoring Factors** (composite score 0-1):
```python
importance_score = (
    0.3 * relevance_to_current_context +
    0.25 * user_decision_signal +      # user spent time/effort on this
    0.15 * cross_cutting_impact +      # mentioned in multiple contexts
    0.15 * pattern_frequency +         # recurring theme
    0.15 * temporal_proximity +        # recent + forward-looking
)
```

**Relevance Signals** (LLM-evaluated):
- Explicit user statements ("I want...", "I've decided...", "This is important...")
- Decision point detection (crossroads, choices, tradeoffs)
- Meta-commentary ("This is a pattern I noticed...", "Historically...")
- Conflict resolution ("But unlike last time...", "Learned from...")
- Temporal markers ("Next week...", "In Q3...", "Long-term goal...")

**User Decision Signal**:
- Time spent elaborating (message length, turn count)
- Revision frequency (user corrects/refines)
- Priority assertions ("This is critical", "Top priority")
- Emotional intensity (all-caps, punctuation density)

**Cross-Cutting Impact**:
- Mentioned in multiple projects/namespaces
- Related to multiple goals
- Referenced in dependencies
- Applies to multiple domains

**Pattern Frequency**:
- Recurrence count within time window
- Consistency across contexts
- Predictive value (helps predict future needs)
- Explanatory power (clarifies other facts)

### Autonomy Implementation
```python
class MemoryAutonomyEngine:
    """
    Runs after every extraction.
    Decides: persist? promote? relate? decay?
    """
    
    async def evaluate_extraction(
        self,
        facts: dict,  # extracted from exchange
        context: dict,  # conversation history, projects, etc.
    ) -> MemoryDecisions:
        """
        Return: {
            persist: [(fact, layer, ttl), ...],
            relate: [(fact_id_1, fact_id_2, rel_type), ...],
            demote: [(memory_id, reason), ...],
            predict_next_contexts: [context_hint, ...]
        }
        """
        decisions = MemoryDecisions()
        
        for fact in facts:
            score = await self._score_importance(fact, context)
            
            # Determine layer
            layer = self._select_layer(score, fact.type)
            ttl = self._compute_ttl(layer, score, context)
            
            decisions.persist.append((fact, layer, ttl))
        
        # Find relationships between facts
        decisions.relate = await self._discover_relationships(facts, context)
        
        # Identify stale strategic memories that need review
        decisions.demote = await self._identify_stale_memories(context)
        
        # Predict what contexts this might be useful for
        decisions.predict_next_contexts = await self._predict_contexts(facts)
        
        return decisions
    
    async def _score_importance(self, fact, context) -> float:
        """LLM scores fact importance based on multifactorial criteria."""
        pass
    
    async def _select_layer(self, score: float, fact_type: str) -> str:
        """Maps (score, type) → (Episodic | Tactical | Strategic | Structural)"""
        if score > 0.8 and fact_type in ['GOAL', 'COMMITMENT', 'PATTERN']:
            return 'STRATEGIC'
        elif score > 0.6 or fact_type in ['DEPENDENCY', 'DECISION']:
            return 'TACTICAL'
        else:
            return 'EPISODIC'
    
    async def _compute_ttl(self, layer: str, score: float, context) -> timedelta:
        """Assigns time-to-live: score×layer_max_ttl + context_boosters"""
        base_ttls = {
            'EPISODIC': timedelta(hours=48),
            'TACTICAL': timedelta(days=21),
            'STRATEGIC': timedelta(days=180),
            'STRUCTURAL': None,  # indefinite
        }
        base = base_ttls[layer]
        if not base:
            return None
        
        # Boost if related to active projects
        boost = self._compute_context_boost(context)
        return base * (1 + boost)
```

---

## Hybrid Retrieval System

### Retrieval Strategy
Combine four retrieval modes based on query context:

#### 1. Semantic Retrieval (Conceptual Similarity)
```python
async def semantic_recall(query: str, layers: list[str]) -> list[Memory]:
    """
    ChromaDB vector search across specified layers.
    Returns: top-k semantically similar memories
    """
    # Embed query
    query_vector = await embedder.embed(query)
    
    # Search each layer independently (different collections)
    results = []
    for layer in layers:
        collection_name = f"memory_{layer}"
        matches = chroma_client.query(
            collection_name=collection_name,
            query_embeddings=[query_vector],
            n_results=k,
            where={'status': 'active'},  # filter metadata
        )
        results.extend(matches)
    
    # Rank by importance_score * similarity_score
    return sorted(results, key=lambda x: x['score'] * x['importance'])
```

#### 2. Structural/Relational Retrieval (Graph Traversal)
```python
async def structural_recall(
    starting_entity: str,
    relationship_types: list[str],
    hops: int = 2,
) -> list[Memory]:
    """
    Graph traversal: find entities via relationships.
    Useful for: "What do we know about people working on X?"
    """
    visited = set()
    to_visit = [starting_entity]
    results = []
    
    for _ in range(hops):
        next_batch = []
        for entity in to_visit:
            if entity in visited:
                continue
            visited.add(entity)
            
            # Find connected entities
            connections = query_graph(
                entity,
                relationship_types=relationship_types
            )
            next_batch.extend(connections)
            results.extend(connections)
        
        to_visit = next_batch
    
    return results
```

#### 3. Temporal Retrieval (Time-Aware)
```python
async def temporal_recall(
    time_reference: str,  # "yesterday", "Q3 2025", "before_event_X"
    include_forward: bool = False,
    decay: str = 'exponential',
) -> list[Memory]:
    """
    Retrieve memories relative to time point.
    Accounts for natural decay + access boost.
    """
    ref_point = parse_temporal_reference(time_reference)
    
    # Find memories within time window
    memories = db.query(
        f"SELECT * FROM all_memories "
        f"WHERE created_at BETWEEN ? AND ? "
        f"AND status = 'active'",
        (ref_point - timedelta(days=30), ref_point + timedelta(days=30))
    )
    
    # Apply decay function
    scored = []
    for mem in memories:
        time_distance = abs((mem.created_at - ref_point).total_seconds())
        if decay == 'exponential':
            decay_factor = math.exp(-time_distance / (24 * 3600))  # 1-day half-life
        else:
            decay_factor = max(0, 1 - time_distance / (30 * 24 * 3600))
        
        score = mem.importance * decay_factor
        scored.append((mem, score))
    
    return sorted(scored, key=lambda x: x[1], reverse=True)
```

#### 4. Predictive/Contextual Retrieval (Anticipatory)
```python
async def predictive_recall(
    current_task: Task,
    forward_horizon_days: int = 7,
) -> list[Memory]:
    """
    Surface memories likely to be relevant for upcoming task.
    Uses: task type prediction + goal alignment + pattern matching
    """
    # Step 1: Identify relevant goal(s)
    goals = find_goals_aligned_with(current_task)
    
    # Step 2: Find tactical memories related to these goals
    tactical = db.query(
        "SELECT * FROM tactical_memories WHERE namespace IN (?, ?, ...)",
        tuple(g.id for g in goals)
    )
    
    # Step 3: Find strategic patterns that apply
    patterns = db.query(
        "SELECT * FROM strategic_memories WHERE category = 'PATTERN' "
        "AND status = 'active'"
    )
    relevant_patterns = [
        p for p in patterns
        if await pattern_applies_to_task(p, current_task)
    ]
    
    # Step 4: Score by time-to-relevance
    results = []
    for mem in tactical + relevant_patterns:
        time_to_relevance = estimate_time_to_relevance(mem, current_task)
        urgency_boost = 1.0 if time_to_relevance <= forward_horizon_days else 0.5
        score = mem.importance * urgency_boost
        results.append((mem, score))
    
    return sorted(results, key=lambda x: x[1], reverse=True)
```

### Unified Recall Interface
```python
class UnifiedMemoryRecall:
    """
    Single entry point for all retrieval modes.
    Automatically selects or blends modes based on query context.
    """
    
    async def recall(
        self,
        query: str,
        context: RecallContext = None,
        mode: Literal['auto', 'semantic', 'structural', 'temporal', 'predictive'] = 'auto',
    ) -> MemoryBlend:
        """
        Returns blended results from multiple retrieval modes.
        """
        if mode == 'auto':
            mode = self._infer_mode(query, context)
        
        if mode == 'semantic':
            return await self.semantic_recall(query, context.layers)
        elif mode == 'structural':
            return await self.structural_recall(context.entity, context.relationships)
        elif mode == 'temporal':
            return await self.temporal_recall(context.time_reference)
        elif mode == 'predictive':
            return await self.predictive_recall(context.current_task)
        else:
            # Blend modes for comprehensive recall
            results = await asyncio.gather(
                self.semantic_recall(query, context.layers),
                self.structural_recall(...),
                self.temporal_recall(...),
            )
            return self._blend_results(results)
    
    def _infer_mode(self, query: str, context) -> str:
        """
        Heuristically choose best retrieval mode.
        "Tell me about the X project" → structural
        "What was I doing in April?" → temporal
        "What should I know before starting X?" → predictive
        "Find memories similar to..." → semantic
        """
        if contains_project_reference(query):
            return 'structural'
        elif contains_temporal_reference(query):
            return 'temporal'
        elif is_task_start_query(query):
            return 'predictive'
        else:
            return 'semantic'
```

---

## Self-Evolving Schema System

### Dynamic Category Discovery
**Problem**: Fixed categories (DECISION, PREFERENCE, etc.) don't adapt to user's actual needs

**Solution**: Track category effectiveness, discover new categories

```python
class SchemaEvolutionEngine:
    """
    Monitors which fact categories are most useful.
    Discovers emergent categories from usage patterns.
    Recommends schema updates.
    """
    
    async def monitor_category_effectiveness(self):
        """
        Runs periodically (weekly).
        Tracks: what categories got most access? which proved predictive?
        """
        stats = {}
        for category in current_schema.categories:
            stats[category] = {
                'access_count': count_accesses(category),
                'avg_importance': avg_importance_score(category),
                'prediction_accuracy': measure_predictive_value(category),
                'retention_rate': measure_retention(category),
            }
        
        return stats
    
    async def discover_emergent_categories(self):
        """
        Cluster extracted facts that don't fit existing categories.
        Propose new categories based on semantic similarity.
        """
        uncategorized = db.query(
            "SELECT * FROM episodic_memories WHERE category IS NULL"
        )
        
        if len(uncategorized) < 10:
            return []
        
        # Cluster by semantic similarity
        embeddings = [await embedder.embed(m.content) for m in uncategorized]
        clusters = cluster(embeddings, n_clusters=3)
        
        proposed_categories = []
        for cluster_id, members in clusters.items():
            # Generate category name from cluster semantics
            category_name = await generate_category_name(members)
            proposed_categories.append({
                'name': category_name,
                'members': len(members),
                'confidence': measure_cluster_coherence(members),
            })
        
        return proposed_categories
    
    async def recommend_schema_updates(self):
        """
        Synthesize monitoring + discovery into recommendations.
        """
        recommendations = []
        
        # Deprecate unused categories
        stats = await self.monitor_category_effectiveness()
        for category, metrics in stats.items():
            if metrics['access_count'] < 5 and metrics['retention_rate'] < 0.2:
                recommendations.append({
                    'action': 'deprecate',
                    'category': category,
                    'reason': 'unused',
                })
        
        # Add emergent categories
        emergent = await self.discover_emergent_categories()
        for cat in emergent:
            if cat['confidence'] > 0.7:
                recommendations.append({
                    'action': 'add',
                    'category': cat['name'],
                    'evidence': len(cat['members']),
                })
        
        return recommendations
```

---

## Proactive Context Injection

### Predictive Memory Surfacing
**Goal**: Agent actively predicts what memory the user needs before they ask

```python
class ProactiveContextSystem:
    """
    Anticipates user needs based on:
    - Conversation patterns
    - Active goals + timeline
    - Temporal patterns (time of day, day of week, season)
    - Task sequences
    """
    
    async def predict_next_context_need(
        self,
        conversation_history: list[Message],
        active_goals: list[Goal],
        current_time: datetime,
    ) -> MemoryBlend:
        """
        Called before every model invocation.
        Injects relevant memories into system context.
        """
        
        # Step 1: Infer likely next task from conversation flow
        likely_tasks = await self._infer_likely_tasks(conversation_history)
        
        # Step 2: Match to active goals
        relevant_goals = [
            g for g in active_goals
            if any(task_aligns_with_goal(t, g) for t in likely_tasks)
        ]
        
        # Step 3: Surface tactical memories from goal namespaces
        tactical_context = await self._gather_tactical_context(relevant_goals)
        
        # Step 4: Surface relevant patterns
        pattern_context = await self._gather_pattern_context(likely_tasks)
        
        # Step 5: Check temporal appropriateness
        temporal_context = await self._gather_temporal_context(current_time)
        
        # Step 6: Blend and rank
        blended = self._blend_contexts(
            tactical=tactical_context,
            patterns=pattern_context,
            temporal=temporal_context,
        )
        
        return blended[:3]  # Top 3 most relevant
    
    async def _infer_likely_tasks(self, history: list[Message]) -> list[str]:
        """
        LLM analyzes conversation flow to predict next steps.
        "User just finished discussing project X and mentioned deadline..."
        → Next task likely: "Check deadline specifics" or "Create timeline"
        """
        prompt = f"""
        Based on this conversation excerpt, what is the user likely to do next?
        Suggest 2-3 probable next tasks/questions.
        
        Conversation:
        {format_recent_history(history)}
        
        Output JSON:
        {{
            "likely_tasks": ["...", "..."],
            "confidence": 0.7
        }}
        """
        result = await llm.chat([{"role": "user", "content": prompt}])
        return json.loads(result)['likely_tasks']
    
    async def _gather_tactical_context(self, goals: list[Goal]) -> dict:
        """Collect active project memories related to goals."""
        context = {}
        for goal in goals:
            tactical = db.query(
                "SELECT * FROM tactical_memories WHERE namespace = ? "
                "AND status = 'active' ORDER BY importance_score DESC LIMIT 5",
                (goal.id,)
            )
            context[goal.id] = tactical
        return context
```

### Memory-Informed System Prompt
```python
async def build_context_aware_system_prompt(
    memory_blend: MemoryBlend,
    user_summary: dict,
    current_goals: list[Goal],
) -> str:
    """
    Dynamically construct system prompt that includes
    most relevant memories + goals + patterns.
    """
    
    sections = []
    
    # Identity section
    sections.append(f"""You are VERA, {user_summary.get('name', 'assistant')}.
{user_summary.get('bio', '')}
Today is {datetime.now().strftime('%A, %B %d, %Y')}.""")
    
    # Active goals section
    if current_goals:
        goals_text = "\n".join([
            f"- {g.title} (deadline: {g.deadline})"
            for g in current_goals[:3]
        ])
        sections.append(f"Current goals:\n{goals_text}")
    
    # Relevant patterns section
    patterns = [m for m in memory_blend if m.type == 'PATTERN']
    if patterns:
        patterns_text = "\n".join([
            f"- {p.title}: {p.description}"
            for p in patterns[:2]
        ])
        sections.append(f"Relevant patterns you've noticed:\n{patterns_text}")
    
    # Recent context section
    recent_decisions = [m for m in memory_blend if m.type == 'DECISION']
    if recent_decisions:
        recent_text = "\n".join([
            f"- {d.content}"
            for d in recent_decisions[:2]
        ])
        sections.append(f"Recent decisions:\n{recent_text}")
    
    # Commitments/dependencies
    commitments = [m for m in memory_blend if m.type == 'COMMITMENT']
    if commitments:
        comm_text = "\n".join([
            f"- {c.content}"
            for c in commitments[:2]
        ])
        sections.append(f"Active commitments:\n{comm_text}")
    
    return "\n\n".join(sections)
```

---

## Implementation Roadmap

### Phase 1: Data Foundation (Weeks 1-2)
- [ ] Design and create `tactical_memories` + `strategic_memories` tables
- [ ] Add relationship tracking tables
- [ ] Implement importance scoring algorithm
- [ ] Create data migration script from existing facts
- [ ] Add metadata enrichment to existing conversations

**Deliverable**: Enhanced SQLite schema supporting tiered storage

### Phase 2: Autonomy Engine (Weeks 3-4)
- [ ] Implement `MemoryAutonomyEngine` class
- [ ] LLM-based importance scoring
- [ ] Layer selection logic
- [ ] TTL computation
- [ ] Relationship discovery algorithm
- [ ] Background task for periodic evaluation

**Deliverable**: Autonomous memory persistence decisions

### Phase 3: Hybrid Retrieval (Weeks 5-6)
- [ ] Semantic retrieval (enhanced ChromaDB integration)
- [ ] Structural/graph retrieval system
- [ ] Temporal retrieval with decay functions
- [ ] Predictive retrieval engine
- [ ] Unified `UnifiedMemoryRecall` interface
- [ ] Integration into agent context building

**Deliverable**: Multi-mode memory recall system

### Phase 4: Schema Evolution (Week 7)
- [ ] `SchemaEvolutionEngine` implementation
- [ ] Category effectiveness monitoring
- [ ] Emergent category discovery
- [ ] Recommendation system
- [ ] User approval workflow

**Deliverable**: Self-adapting memory schema

### Phase 5: Proactive Injection (Week 8)
- [ ] `ProactiveContextSystem` implementation
- [ ] Task prediction from conversation flow
- [ ] Temporal pattern recognition
- [ ] Memory-informed system prompt generation
- [ ] Integration into agent initialization

**Deliverable**: Predictive context injection before every response

### Phase 6: Integration & Polish (Week 9-10)
- [ ] Full end-to-end integration testing
- [ ] Performance optimization
- [ ] Audit logging enhancement
- [ ] UI/visualization for memory inspection
- [ ] User documentation

**Deliverable**: Production-ready long-term memory system

---

## Database Schema Summary

```sql
-- ═══════════════════════════════════════════════════════════════
-- EPISODIC (Session-level, Temporary)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE episodic_memories (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_message TEXT NOT NULL,
    agent_response TEXT NOT NULL,
    extracted_facts JSON,
    importance_score REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    last_accessed DATETIME,
    expires_at DATETIME,
    metadata JSON DEFAULT '{}'
);

-- ═══════════════════════════════════════════════════════════════
-- TACTICAL (Project-scoped, 2-4 weeks)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE tactical_memories (
    id INTEGER PRIMARY KEY,
    namespace TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    promoted_at DATETIME,
    last_accessed DATETIME,
    importance_score REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.7,
    related_facts JSON DEFAULT '[]',
    status TEXT DEFAULT 'active',
    parent_id INTEGER REFERENCES tactical_memories(id),
    metadata JSON DEFAULT '{}'
);

CREATE TABLE tactical_relationships (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES tactical_memories(id),
    target_id INTEGER REFERENCES tactical_memories(id),
    relationship_type TEXT,
    strength REAL DEFAULT 0.5,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- STRATEGIC (Long-term goals, patterns, 3-12 months)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE strategic_memories (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    promoted_at DATETIME,
    last_validated DATETIME,
    validation_count INTEGER DEFAULT 0,
    importance_score REAL DEFAULT 0.7,
    confidence REAL DEFAULT 0.8,
    horizon_days INTEGER DEFAULT 90,
    status TEXT DEFAULT 'active',
    superseded_by INTEGER REFERENCES strategic_memories(id),
    metadata JSON DEFAULT '{}'
);

CREATE TABLE strategic_insights (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER REFERENCES strategic_memories(id),
    insight_type TEXT,
    description TEXT NOT NULL,
    confidence REAL DEFAULT 0.6,
    discovery_date DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- STRUCTURAL (Identity, relationships, persistent)
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE identity_facts (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    property TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, property)
);

CREATE TABLE identity_relationships (
    id INTEGER PRIMARY KEY,
    source_entity_type TEXT,
    source_entity_id TEXT,
    relationship_type TEXT,
    target_entity_type TEXT,
    target_entity_id TEXT,
    properties JSON DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ═══════════════════════════════════════════════════════════════
-- MANAGEMENT & MONITORING
-- ═══════════════════════════════════════════════════════════════
CREATE TABLE memory_lifecycle (
    id INTEGER PRIMARY KEY,
    memory_type TEXT NOT NULL,
    memory_id INTEGER NOT NULL,
    action TEXT NOT NULL,  -- created, promoted, demoted, accessed, expired
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metadata JSON DEFAULT '{}'
);

CREATE TABLE schema_evolution_log (
    id INTEGER PRIMARY KEY,
    recommendation_id TEXT NOT NULL,
    action TEXT NOT NULL,  -- add, deprecate, modify
    category TEXT NOT NULL,
    status TEXT DEFAULT 'proposed',  -- proposed, approved, rejected, implemented
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    decided_at DATETIME,
    metadata JSON DEFAULT '{}'
);
```

---

## Key Success Metrics

1. **Context Continuity**: User reports improved context maintenance across sessions
2. **Prediction Accuracy**: % of predictively surfaced memories that prove relevant
3. **Autonomy Quality**: % of autonomous persistence decisions user agrees with
4. **Schema Adaptability**: New categories discovered per month
5. **Retrieval Efficiency**: Latency of memory recall at decision point
6. **Memory Utilization**: % of stored memories accessed within their horizon
7. **Proactive Value**: User accepts/uses predictively surfaced context ≥ 70% of time

---

## Risk Mitigation

### Challenge: "Over-fitting" to user behavior
**Risk**: Memory system learns user patterns that become stale
**Mitigation**: Explicit deprecation mechanism, pattern lifetime limits, user feedback loop

### Challenge: Memory explosion
**Risk**: Unbounded growth of tactical/strategic layers
**Mitigation**: Aggressive pruning of low-importance items, automatic archival of resolved projects

### Challenge: Privacy & control
**Risk**: Overly intrusive proactive context injection
**Mitigation**: User settings for proactivity level, transparency about what's surfaced, easy override

### Challenge: Cross-layer consistency
**Risk**: Conflicting or contradictory facts in different layers
**Mitigation**: Relationship tracking, supersession logic, conflict detection alerts

---

## Configuration & Extensibility

```yaml
memory_config:
  episodic:
    ttl_hours: 48
    max_count: 500
    decay_function: exponential
  
  tactical:
    ttl_days: 21
    max_namespace_count: 100
    importance_threshold: 0.5
    decay_function: linear
  
  strategic:
    ttl_days: 180
    importance_threshold: 0.7
    confidence_threshold: 0.75
    validation_boost: 0.1
  
  retrieval:
    semantic:
      enabled: true
      top_k: 5
    structural:
      enabled: true
      max_hops: 2
    temporal:
      enabled: true
      decay_half_life_days: 30
    predictive:
      enabled: true
      forward_horizon_days: 7
  
  autonomy:
    importance_weights:
      relevance: 0.3
      user_signal: 0.25
      cross_cutting: 0.15
      frequency: 0.15
      proximity: 0.15
    min_confidence: 0.5
    promote_on_validation: true
    
  schema_evolution:
    enabled: true
    monitor_interval_days: 7
    category_min_samples: 10
    discovery_confidence_threshold: 0.7
```

---

## Integration with Obsidian: Dual-Interface Memory System

### The Hybrid Model: SQLite + Obsidian

While SQLite serves as the programmatic backbone, **Obsidian provides a human-readable, explorable interface** to the memory system. Together they create a dual-interface architecture:

```
┌─────────────────────────────────────────────────────────┐
│            USER COGNITIVE LAYER                         │
│  (Obsidian: Interactive exploration & reflection)       │
│                                                         │
│  • Graph visualization of relationships                 │
│  • Markdown-based narratives & reflection              │
│  • Bidirectional linking (backlinks)                    │
│  • Daily Notes for episodic capture                     │
│  • Manual annotation & curation                         │
│  • Search & discovery interface                         │
└─────────────────┬───────────────────────────────────────┘
                  │
        ┌─────────▼─────────┐
        │  Bidirectional    │
        │  Sync Layer       │
        │  (Periodic)       │
        └────┬──────────┬───┘
             │          │
    ┌────────▼──┐  ┌───▼────────┐
    │ Obsidian  │  │  SQLite    │
    │ Vault     │  │  Database  │
    │ (Markdown)│  │ (Structured)
    │           │  │            │
    │ Strategy: │  │ Strategy:  │
    │ Human     │  │ Machine    │
    │ narrative │  │ reasoning  │
    └───────────┘  └────────────┘
             │          │
        ┌────▼──────────▼───┐
        │   AIDE AGENT      │
        │   (Queries both)  │
        │   (Writes to      │
        │    SQLite primary)│
        └──────────────────┘
```

### Obsidian Integration Points

#### 1. **Strategic Memories as Markdown Files**
Store long-term goals, patterns, and commitments as Obsidian notes:

```
vault/
├── Strategic/
│   ├── Goals/
│   │   ├── Q3_Dashboard_Deliverable.md
│   │   ├── Build_Real-Time_API.md
│   │   └── Improve_Team_Collaboration.md
│   ├── Patterns/
│   │   ├── Always_Document_Blockers.md
│   │   ├── Weekly_Retrospective_Habit.md
│   │   └── Async_Decision_Making.md
│   └── Commitments/
│       ├── Mentoring_Sarah.md
│       └── Q2_Executive_Presentation.md
├── Tactical/
│   ├── Projects/
│   │   ├── Dashboard_Project/
│   │   │   ├── README.md (project overview)
│   │   │   ├── Blockers.md
│   │   │   ├── Dependencies.md
│   │   │   └── Timeline.md
│   │   └── API_Redesign/
│   └── Active_Decisions.md
└── Episodic/
    ├── Daily_Notes/
    │   ├── 2026-04-15.md (today's conversations)
    │   ├── 2026-04-14.md
    │   └── 2026-04-13.md
```

Each markdown file uses Obsidian's front matter for metadata:

```markdown
---
memory_type: strategic
category: goal
importance_score: 0.92
created_at: 2026-04-15
validated_count: 1
horizon_days: 90
status: active
tags: [q3, deliverable, dashboard, websocket]
related_goals: [API_Redesign, Performance_Optimization]
related_patterns: [Always_Document_Blockers]
---

# Q3 Real-Time Dashboard Deliverable

## Objective
Build a real-time dashboard for Q3 delivery supporting WebSocket connections at scale.

## Requirements
- WebSocket support
- 10,000 concurrent user capacity
- Real-time data updates (< 100ms latency)

## Blockers
[[Dashboard_Project/Blockers]]

## Related Work
- [[API_Redesign]]
- [[Performance_Optimization]]

## Timeline
- Start: Week of April 15
- Milestone 1: API spec finalized (April 30)
- Milestone 2: MVP (May 31)
- Delivery: June 30

## Progress
- [x] Requirements gathered
- [ ] API design finalized
- [ ] Core implementation
- [ ] Performance testing
```

#### 2. **Bidirectional Linking as Relationship Graph**
Obsidian's bidirectional links become the primary interface for exploring relationships:

```markdown
# Dashboard_Project/Blockers

## Current Blockers

**Backend API Design** - [[API_Design_From_James]]
- Status: Waiting
- Impact: Critical (blocks UI development)
- Estimated resolution: April 25
- Related: [[Q3_Dashboard_Deliverable]], [[Dashboard_Architecture]]

**Performance Testing Framework**
- Status: In progress
- Impact: High (needed before launch)
- Owner: [[John_Doe]]
- Related: [[Performance_Optimization]]
```

When viewed in Obsidian's graph, you see:
- Dashboard goal → connected to API blocker → connected to James person → connected to his other projects
- Strategic patterns that apply → linked from decisions
- Historical decisions that led to current state

#### 3. **Daily Notes for Episodic Capture**
Obsidian's Daily Notes plugin captures session-level context:

```markdown
---
memory_type: episodic
date: 2026-04-15
session_id: sess_20260415_093045
importance_score: 0.65
---

# April 15, 2026

## Key Decisions
- [[Decided_WebSocket_Over_GraphQL]] (importance: 0.9)
- [[User_Approval_Process_Simplified]] (importance: 0.75)

## Blockers Identified
- [[API_Design_From_James]] (priority: critical)
- [[Performance_Test_Framework_Missing]] (priority: high)

## Patterns Noticed
- [[Team_Waits_For_Spec_Before_Starting]] (3rd time this month)
- [[Preference_For_Async_Decisions]] (confirmed)

## Commitments Made
- Check in with James by EOW
- Submit quarterly OKRs by Friday

## Relevant Conversations
[Extracted from LLM exchanges - automatically linked to related notes]

---
## Related
- [[Q3_Dashboard_Deliverable]]
- [[Dashboard_Project]]
- [[James_Collaborator]]
```

#### 4. **Graph Visualization for Relationship Discovery**
Obsidian's graph view reveals:
- **Connection clusters**: Which projects/goals are interrelated
- **Bottlenecks**: Entities with many incoming "blocked by" links
- **Champions**: People/teams connected to multiple strategic initiatives
- **Pattern cascades**: How one pattern influences others

```
Example: Obsidian graph view shows
  Q3_Dashboard
      ↙    ↓    ↘
  WebSocket  API   James_Collaboration
      ↓      ↓    ↓
  Blocker  Pattern  Trust_Pattern
```

#### 5. **Dataview Queries for Memory Analysis**
Obsidian's Dataview plugin enables SQL-like queries on memory metadata:

```markdown
---
# Dashboard: Active Strategic Goals

## Upcoming Deadlines
```dataview
TABLE deadline, status, importance_score
FROM "Strategic/Goals"
WHERE status = "active" AND deadline <= date(now) + dur(30 days)
SORT deadline ASC
```

## Validation Status
```dataview
TABLE validated_count, last_validated, confidence
FROM "Strategic"
WHERE validated_count < 2 AND status = "active"
SORT validated_count ASC
```

## High-Impact Patterns
```dataview
LIST link(file.name)
FROM "Strategic/Patterns"
WHERE importance_score > 0.8
SORT importance_score DESC
```
---

#### 6. **Sync Mechanism: SQLite ↔ Obsidian**

```python
class ObsidianMemorySync:
    """
    Bidirectional sync between SQLite and Obsidian vault.
    Runs periodically (every 5 minutes) and on manual trigger.
    """
    
    async def sync_to_obsidian(self):
        """
        Write high-importance memories from SQLite → Obsidian markdown.
        Strategic & Tactical layers only (Episodic too volatile).
        """
        
        # Step 1: Fetch updated strategic memories
        strategic = db.query(
            "SELECT * FROM strategic_memories WHERE updated_at > last_obsidian_sync"
        )
        
        for mem in strategic:
            # Step 2: Generate markdown file
            md_content = self._generate_markdown(mem)
            
            # Step 3: Write to vault
            vault_path = f"Strategic/{mem.category}/{mem.title}.md"
            write_to_obsidian(vault_path, md_content)
        
        # Similar for tactical layer
        tactical = db.query(
            "SELECT * FROM tactical_memories WHERE updated_at > last_obsidian_sync"
        )
        
        for mem in tactical:
            project_name = self._extract_namespace(mem.namespace)
            vault_path = f"Tactical/Projects/{project_name}/{mem.category}.md"
            md_content = self._generate_tactical_markdown(mem)
            write_to_obsidian(vault_path, md_content)
    
    async def sync_from_obsidian(self):
        """
        Read user annotations from Obsidian markdown → SQLite.
        Captures user curation, linking, and reflection.
        """
        
        # Step 1: Scan vault for modified markdown files
        modified_files = get_modified_files(vault_root)
        
        for file in modified_files:
            # Step 2: Parse frontmatter and content
            metadata = parse_frontmatter(file)
            content = parse_markdown(file)
            links = extract_links(content)
            
            # Step 3: Update corresponding SQLite record
            if metadata.get('memory_id'):
                mem_id = metadata['memory_id']
                
                # User may have added tags, refined importance, etc.
                db.execute("""
                    UPDATE strategic_memories 
                    SET user_tags = ?, 
                        refined_importance = ?,
                        last_reviewed = NOW()
                    WHERE id = ?
                """, (metadata.get('tags'), metadata.get('importance_score'), mem_id))
                
                # Capture new links user created
                for linked_note in links:
                    target_id = resolve_obsidian_link_to_db_id(linked_note)
                    if target_id:
                        create_relationship(mem_id, target_id, 'USER_LINKED')
        
        # Step 4: Treat Obsidian linking as validation
        # If user actively links strategic memories together, boost their importance
        for relationship in new_user_created_links:
            boost_importance(relationship.source_id, boost=0.1)
    
    def _generate_markdown(self, mem: StrategicMemory) -> str:
        """Generate well-formatted markdown from SQLite record."""
        
        md = f"""---
memory_id: {mem.id}
memory_type: strategic
category: {mem.category}
created_at: {mem.created_at.isoformat()}
last_validated: {mem.last_validated.isoformat()}
validation_count: {mem.validation_count}
importance_score: {mem.importance_score}
confidence: {mem.confidence}
status: {mem.status}
horizon_days: {mem.horizon_days}
tags: {json.dumps(mem.metadata.get('tags', []))}
---

# {mem.title}

{mem.description}

## Validation History
- Created: {mem.created_at.strftime('%Y-%m-%d')}
- Validations: {mem.validation_count}
- Last validated: {mem.last_validated.strftime('%Y-%m-%d') if mem.last_validated else 'Never'}
- Confidence: {mem.confidence*100:.0f}%

## Related Memories
{self._generate_related_links(mem)}

## Timeline
- Relevance horizon: {mem.horizon_days} days
- Expected relevance until: {(mem.created_at + timedelta(days=mem.horizon_days)).strftime('%Y-%m-%d')}

## Notes
Add your reflections, questions, or context here. Links to [[Other Notes]] will be automatically captured.
"""
        return md
```

### Why This Deepens the Experience

| Aspect | SQLite Only | With Obsidian |
|--------|-------------|---------------|
| **Exploration** | Programmatic queries | Visual graph, serendipitous discovery |
| **Understanding** | Machine-optimized | Human-readable narratives |
| **Relationships** | Metadata in JSON | Visible bidirectional links |
| **Reflection** | Cold data | Living knowledge base |
| **Curation** | AI-driven | User-guided + AI collaboration |
| **Insight** | Computed | Emergent through linking |
| **Accessibility** | CLI/API | Visual + searchable interface |
| **Long-term Value** | Fades after use | Grows as interconnections deepen |

### Implementation: Add to Roadmap

**Phase 3.5: Obsidian Integration (Between weeks 6-7)**

- [ ] Design Obsidian vault structure
- [ ] Create sync layer (SQLite ↔ Obsidian)
- [ ] Build markdown generation from memories
- [ ] Implement bidirectional link parsing
- [ ] Set up periodic sync daemon
- [ ] Create Obsidian templates for each memory type
- [ ] Add Dataview dashboard for monitoring

**Deliverable**: Dual-interface memory system with human-readable exploration layer

### Configuration

```yaml
obsidian_integration:
  enabled: true
  vault_path: "~/Obsidian/AIDEMemory"
  sync_interval_seconds: 300
  
  layers_to_sync:
    strategic: true
    tactical: true
    episodic: false  # Too volatile, keep in SQLite only
  
  features:
    bidirectional_links: true
    daily_notes: true
    graph_visualization: true
    dataview_queries: true
  
  sync_direction:
    sqlite_to_obsidian: true  # AI writes memories as markdown
    obsidian_to_sqlite: true  # User annotations sync back
  
  markdown_templates:
    strategic: "Strategic Memory Template"
    tactical: "Tactical Project Template"
    episodic: "Daily Note Template"
```

---

## Conclusion

This architecture transforms AIDE from a reactive tool into a **proactive cognitive partner** by:

1. ✅ **Stratifying memory** into contextually-appropriate tiers
2. ✅ **Autonomizing persistence** through AI-driven importance scoring
3. ✅ **Enabling hybrid retrieval** combining semantic, structural, temporal, and predictive modes
4. ✅ **Self-adapting schemas** that evolve with actual usage patterns
5. ✅ **Injecting proactive context** before user needs to ask
6. ✅ **Maintaining deep continuity** over months through sophisticated lifecycle management
7. ✅ **Providing dual interfaces**: Machine-optimized (SQLite) + Human-explorable (Obsidian)

The result: An AI partner that truly remembers, understands relationships, predicts needs, continuously improves its own cognitive capacity, **and grows more valuable as you reflect on and interconnect your memories**.
