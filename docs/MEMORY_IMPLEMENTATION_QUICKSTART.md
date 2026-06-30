# AIDE Memory Architecture - Implementation Quickstart

This document provides a compressed implementation guide referencing the full architecture plan at `docs/LONG_TERM_MEMORY_ARCHITECTURE.md`.

## Vision: Quick Summary

Transition AIDE from **reactive session-based tool** to **proactive long-term cognitive partner** via:
- **Tiered memory**: Episodic (hot) → Tactical (warm) → Strategic (warm) → Structural (cold)
- **Autonomous persistence**: AI self-determines what deserves to be saved and how long
- **Hybrid retrieval**: Semantic + relational graph + temporal + predictive modes
- **Self-evolving schemas**: Memory structure adapts to actual usage patterns
- **Proactive injection**: AI anticipates user needs before they ask

## Core Insight: The Importance Scoring Engine

The entire system hinges on one central concept: **autonomous importance scoring**.

After every exchange, the system evaluates: *"Is this worth remembering? For how long? At what layer?"*

```python
# Simplified pseudocode
importance = (
    0.30 * relevance_to_context +      # How applicable is this?
    0.25 * user_decision_signal +      # Did user invest effort/decision weight?
    0.15 * cross_cutting_impact +      # Mentioned in multiple contexts?
    0.15 * pattern_frequency +         # Recurring theme?
    0.15 * temporal_proximity +        # Recent + forward-looking?
)

layer = {
    importance > 0.8: STRATEGIC,       # Goals, commitments, patterns → months
    0.6 < importance <= 0.8: TACTICAL, # Project decisions, dependencies → weeks
    0.4 < importance <= 0.6: EPISODIC, # Conversational context → hours
    importance <= 0.4: FORGET,         # Don't store
}
```

## Four Memory Layers

| Layer | Purpose | Duration | Query Mode | Examples |
|-------|---------|----------|-----------|----------|
| **Episodic** | Current session context | 48 hours | Semantic + recency | "What did I say about...?" |
| **Tactical** | Active project state | 2-4 weeks | Project-scoped, relational | "What are our blockers on Project X?" |
| **Strategic** | Long-term patterns & goals | 3-12 months | Goal-aligned, pattern-based | "What are my top priorities?" |
| **Structural** | Identity & relationships | Indefinite | Graph traversal | "Who works on what? What entities exist?" |

## Four Retrieval Modes

User asks a question → System selects optimal retrieval mode:

```
"Tell me about the Q3 roadmap"      → STRUCTURAL (graph traversal)
"What was I working on in May?"     → TEMPORAL (time-aware decay)
"What should I know before X?"      → PREDICTIVE (anticipatory)
"Similar to last time..."           → SEMANTIC (conceptual similarity)
```

## Implementation Phases (10 weeks)

### Phase 1-2: Data Layer (Weeks 1-4)
- Create `tactical_memories` + `strategic_memories` tables
- Add relationship tracking, importance scores, lifecycle metadata
- Migrate existing facts to new schema
- **Outcome**: Enhanced DB supporting stratified storage

### Phase 3: Autonomy Engine (Weeks 3-4)
- `MemoryAutonomyEngine` class
- LLM-based importance scoring
- Layer selection + TTL computation
- Relationship discovery
- **Outcome**: Autonomous memory persistence decisions

### Phase 4: Retrieval System (Weeks 5-6)
- Semantic search (ChromaDB)
- Graph queries (SQLite relations)
- Temporal queries with decay
- Predictive surfacing
- Unified `UnifiedMemoryRecall` interface
- **Outcome**: 4-mode hybrid retrieval

### Phase 5: Schema Evolution (Week 7)
- Monitor category effectiveness
- Discover emergent categories
- Recommend schema updates
- **Outcome**: Self-adapting memory structure

### Phase 6: Proactive Injection (Week 8)
- Predict user's next likely task
- Inject relevant memories into system prompt
- **Outcome**: AI surfaces context before being asked

### Phases 7-10: Polish & Integration (Weeks 9-10)
- End-to-end testing
- Performance optimization
- UI/monitoring tools
- Documentation

## Starting Implementation: The Importance Scoring Engine

This is the critical first step. Once this works, everything else builds naturally.

### Step 1: Add Metadata to Existing System
```python
# In memory/extractor.py, enhance the fact extraction

async def _extract_with_importance(self, user_msg: str, assistant_reply: str) -> dict:
    """
    Enhanced extraction that includes importance scoring.
    """
    # Existing extraction
    facts = await self._extract(user_msg, assistant_reply)
    
    # NEW: Score each fact
    scored_facts = {}
    for category, items in facts.items():
        scored_items = []
        for item in items:
            importance = await self._score_importance(item, user_msg, assistant_reply)
            layer = self._select_layer(importance, category)
            ttl = self._compute_ttl(layer, importance)
            
            scored_items.append({
                'content': item,
                'importance': importance,
                'layer': layer,
                'ttl_hours': ttl.total_seconds() / 3600,
            })
        scored_facts[category] = scored_items
    
    return scored_facts

async def _score_importance(self, fact: str, context: str, response: str) -> float:
    """
    Use LLM to score importance on 0-1 scale.
    """
    prompt = f"""
    Evaluate this fact's importance on a 0-1 scale.
    
    Fact: {fact}
    Context: {context[:200]}
    
    Consider:
    - Is user making an explicit decision?
    - Is this a recurring pattern?
    - Does this block other work?
    - Is this about long-term goals?
    - How emotionally invested is the user?
    
    Return JSON: {{"importance": 0.75}}
    """
    
    result = await self._llm.chat(
        messages=[{"role": "user", "content": prompt}],
        task_type=TaskType.PRIVATE,
        max_tokens=100,
        temperature=0.0,
    )
    
    try:
        return json.loads(result['content'])['importance']
    except:
        return 0.5  # Default
```

### Step 2: Create Tactical Memory Table
```python
# In memory/manager.py, add migration

def _init_sqlite_v2(self) -> None:
    """Enhanced schema with tactical/strategic tiers."""
    with self._get_db() as conn:
        conn.executescript("""
            -- Existing tables preserved...
            
            -- NEW: Tactical memories (project-scoped, 2-4 weeks)
            CREATE TABLE IF NOT EXISTS tactical_memories (
                id INTEGER PRIMARY KEY,
                namespace TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                importance_score REAL DEFAULT 0.5,
                status TEXT DEFAULT 'active',
                expires_at DATETIME,
                metadata JSON DEFAULT '{}'
            );
            
            CREATE INDEX IF NOT EXISTS idx_tactical_namespace 
            ON tactical_memories(namespace);
            
            -- NEW: Strategic memories (3-12 months)
            CREATE TABLE IF NOT EXISTS strategic_memories (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                importance_score REAL DEFAULT 0.7,
                validation_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            
            -- Migration: Move high-importance facts to tactical
            INSERT INTO tactical_memories (namespace, category, content, importance_score)
            SELECT 'general', 
                   SUBSTR(key, 1, INSTR(key, ':')-1),
                   value,
                   0.7
            FROM facts
            WHERE value NOT LIKE '%onboarding%'
            AND LENGTH(value) > 20;
        """)
```

### Step 3: Hook Autonomy Engine into Agent
```python
# In core/agent.py, integrate importance scoring

class VeraAgent:
    def __init__(self, ...):
        # Existing code...
        from memory.extractor import MemoryExtractor
        from memory.autonomy import MemoryAutonomyEngine
        
        self._extractor = MemoryExtractor(self._memory, self._llm)
        self._autonomy = MemoryAutonomyEngine(self._memory, self._llm)
    
    async def _run_react_loop(self, user_message: str):
        # ... existing ReAct loop ...
        
        # Before returning final reply:
        final_reply = ...
        
        # NEW: Extract facts with importance scoring
        await self._extractor.extract_and_store(user_message, final_reply)
        
        # NEW: Let autonomy engine decide persistence layer + TTL
        decisions = await self._autonomy.evaluate_extraction(
            facts=extracted_facts,
            context={
                'conversation': self._history,
                'active_goals': self._get_active_goals(),
                'time': datetime.now(),
            }
        )
        
        # NEW: Persist according to decisions
        await self._memory.apply_autonomy_decisions(decisions)
        
        return final_reply
```

## Key Files to Create/Modify

### New Files
1. `memory/autonomy.py` - `MemoryAutonomyEngine` class
2. `memory/retrieval.py` - `UnifiedMemoryRecall` class with 4 modes
3. `memory/schema_evolution.py` - `SchemaEvolutionEngine` class
4. `memory/proactive.py` - `ProactiveContextSystem` class
5. `docs/LONG_TERM_MEMORY_ARCHITECTURE.md` - Full spec (already created)

### Modified Files
1. `memory/manager.py` - Add schema v2, new query methods
2. `memory/extractor.py` - Integrate importance scoring
3. `core/agent.py` - Hook autonomy + retrieval + proactive systems
4. `core/settings.py` - Add memory config options

## Testing Strategy

### Unit Tests
- Importance scoring: verify edge cases, boundaries
- Layer selection: check correct routing
- TTL computation: validate decay functions

### Integration Tests
- Full extraction → autonomy → storage pipeline
- Retrieval: confirm correct results from each mode
- Cross-layer consistency: facts properly promoted/demoted

### User Testing
- Proactive injection: does AI surface right context?
- Schema evolution: do new categories actually help?
- Autonomy quality: do importance scores align with user expectations?

## Configuration Template

```yaml
# Add to .env

# Memory configuration
MEMORY_EPISODIC_TTL_HOURS=48
MEMORY_TACTICAL_TTL_DAYS=21
MEMORY_STRATEGIC_TTL_DAYS=180

MEMORY_IMPORTANCE_THRESHOLD_TACTICAL=0.6
MEMORY_IMPORTANCE_THRESHOLD_STRATEGIC=0.8

MEMORY_AUTONOMY_ENABLED=true
MEMORY_PROACTIVE_ENABLED=true
MEMORY_SCHEMA_EVOLUTION_ENABLED=true

MEMORY_SCHEMA_EVOLUTION_INTERVAL_DAYS=7

MEMORY_RETRIEVAL_SEMANTIC_ENABLED=true
MEMORY_RETRIEVAL_STRUCTURAL_ENABLED=true
MEMORY_RETRIEVAL_TEMPORAL_ENABLED=true
MEMORY_RETRIEVAL_PREDICTIVE_ENABLED=true
```

## Metrics Dashboard (Future)

Track these to validate the architecture's success:

```python
class MemoryMetrics:
    """Monitoring dashboard for memory system health."""
    
    async def get_metrics(self) -> dict:
        return {
            # Volume metrics
            'episodic_count': count_episodic_memories(),
            'tactical_count': count_tactical_memories(),
            'strategic_count': count_strategic_memories(),
            
            # Quality metrics
            'avg_importance_score': avg_importance(),
            'prediction_accuracy': % of predictive recalls that proved useful,
            'autonomy_agreement': % of autonomy decisions user agrees with,
            
            # Usage metrics
            'recall_latency_ms': avg time to retrieve memories,
            'proactive_acceptance_rate': % of injected context user finds relevant,
            'schema_evolution_rate': emergent categories per month,
            
            # Lifecycle metrics
            'promotion_rate': % of episodic → tactical,
            'demotion_rate': % of tactical → episodic,
            'expiration_rate': % of facts that complete TTL without promotion,
        }
```

## FAQ

**Q: Won't this create information overload?**
A: The autonomy engine aggressively filters (importance > 0.4 just to store episodically). Most conversational noise never persists.

**Q: How do I prevent memory staleness?**
A: Strategic memories have explicit validation counts. Outdated facts get deprecated when contradicted. Users can manually refresh.

**Q: What if the AI mis-scores importance?**
A: User feedback loop: "That wasn't important" → lower score for similar facts. Schema evolution monitors accuracy.

**Q: How does this handle privacy?**
A: All memory is local. User controls proactivity level. Transparent logging of what's surfaced. Easy audit trail.

**Q: When should I start using this?**
A: Begin with Phase 1-2 (data layer + importance scoring). That's the foundation everything else builds on.

---

## Next Steps

1. **Review** the full architecture plan: `docs/LONG_TERM_MEMORY_ARCHITECTURE.md`
2. **Sketch** database migrations for your current schema
3. **Prototype** the importance scoring engine
4. **Test** with a small subset of your conversation history
5. **Iterate** based on early results

Good luck building VERA's long-term memory! 🧠
