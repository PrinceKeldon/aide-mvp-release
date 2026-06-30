# AIDE Product Document

Last updated: April 6, 2026

## 1. Executive Summary

AIDE is a personal, owner-controlled agent system designed to operate across the owner's devices, data sources, and approval surfaces as one continuous intelligence layer.

It is not just a chatbot, not just a browser agent, and not just a productivity assistant. AIDE is best understood as an owner mesh: a private operational layer that can remember, plan, route work, request approvals, and act across trusted devices while preserving continuity across model providers.

Today, AIDE already includes:

- Central memory shared across model backends
- Multi-model routing across local and cloud LLMs
- Owner mesh trust, pairing, discovery, and device governance
- Interactive daily briefing through `Your Day`
- Email reading, drafting, replying, sending, and approval flows
- Calendar ingestion, conflict detection, and schedule suggestions
- Telegram and web surfaces for action and oversight

The product direction is to become the operating system for a person’s workday: inbox, calendar, approvals, trusted devices, and delegated execution, all mediated through one persistent agent.

The next layer beyond that is M-Peer:

- not more owner devices
- but sovereign agent-to-agent collaboration across different owners

## 1.1 Layer Model

AIDE now has a two-layer product model:

- Layer I: Owner Mesh
  One owner, many trusted devices, one trust domain
- Layer II: M-Peer
  Many owners, sovereign agent collaboration, scoped trust across trust domains

This distinction matters because another person’s AIDE must never be treated like just another one of my devices.

## 2. Product Thesis

Most AI agents today are either:

- session-based assistants
- app-building agents
- browser-automation agents
- memory-first developer tools
- single-surface productivity copilots

AIDE takes a different position:

It treats the owner, not the app, as the primary unit of coordination.

That leads to a different architecture:

- one owner identity
- many trusted devices
- one shared operational memory
- one approval and routing fabric
- many interchangeable model providers
- one daily operating surface

This is what makes AIDE different. It is not primarily "an agent that can do tasks." It is a persistent personal operating layer that can survive model switching, device switching, and surface switching without losing the thread.

## 2.1 M-Peer In Plain Language

The simplest explanation of M-Peer is:

**My AIDE can ask your AIDE for help, but neither of us gives up control.**

That is the next strategic extension of AIDE beyond Owner Mesh.

## 3. Problem Statement

Knowledge workers increasingly use fragmented AI:

- one model for chat
- another for coding
- separate tools for email
- separate tools for calendars
- separate mobile and desktop experiences
- no consistent memory across systems
- no trust model across devices
- no unified approval workflow

As soon as the model changes, the thread breaks. As soon as the surface changes, the context collapses. As soon as an action becomes sensitive, the system becomes brittle or unsafe.

AIDE is built to solve that fragmentation by making memory, routing, trust, and approval first-class system primitives.

## 4. What AIDE Is Today

### Core capabilities

- Central conversation continuity backed by SQLite memory
- Semantic memory via Chroma-backed recall
- Local-first and cloud-capable model routing
- Ollama support for local and cloud-hosted model endpoints
- OpenAI, Gemini, and Groq routing support
- Telegram bot interface
- Web chat interface
- Owner-facing `Your Day` cockpit
- Owner mesh trust dashboard

### Productivity capabilities

- Multi-account email read, search, draft, reply, and send
- Approval-gated email execution
- Daily inbox digest with summaries
- Draft replies generated from real email context
- Thread-aware email actions from the web panel
- Calendar ingestion from Google Calendar and `.ics`
- Schedule agenda generation
- Calendar conflict detection
- Schedule suggestions
- Pending approvals embedded into the daily brief

### Owner mesh capabilities

- Canonical device registry
- QR/payload pairing
- Trust and revoke/restore lifecycle
- Discovery of peers on LAN
- Mesh routing
- Delegated task execution
- Approval escalation across trusted devices
- Peer admin dashboard with trust and capability controls

### Interaction surfaces

- Telegram for messaging and approvals
- Web for chat, `Your Day`, and owner mesh administration

## 5. Product Definition

### Product category

AIDE is a personal agent operating system for the owner’s workday.

### Primary job to be done

"Help me run my day across inbox, calendar, devices, and approvals without losing context."

### Core product promise

No matter which model is active, which device is in use, or which surface is handling the interaction, AIDE should preserve:

- continuity
- trust
- memory
- actionability

## 6. Technology Architecture

### 6.1 Control Plane

AIDE’s control plane is made of:

- `VeraAgent` for dialogue and tool orchestration
- `ModelRouter` for provider/model selection
- `SafetyGate` for approval policy
- `OwnerMeshOrchestrator` for delegated execution and approval routing
- `DeviceRegistry` as the canonical trust and capability source

### 6.2 Memory

AIDE uses multiple memory layers:

- SQLite for canonical conversation persistence
- semantic recall via Chroma
- explicit extracted facts and summaries
- daily brief storage for operator-facing state

This matters because AIDE is designed to preserve continuity across provider changes. The memory system belongs to the product, not to any single model session.

### 6.3 Model Layer

AIDE routes across:

- Ollama
- Gemini
- Groq
- OpenAI

It can operate local-first, cloud-first, or hybrid. This is strategically important because it prevents lock-in to one model vendor and allows privacy, latency, and cost tradeoffs to be made at runtime.

### 6.4 Trust and Device Layer

AIDE has a real device trust model:

- device identities
- public keys
- trusted peer records
- mesh transport binding
- per-device capabilities
- revoke and restore controls

This is significantly more advanced than treating "phone vs desktop" as just UI clients.

### 6.5 Workflow Layer

The workflow layer currently includes:

- `Your Day`
- approval requests
- email drafting and send flows
- delegated task routing
- schedule and conflict handling

This is where AIDE starts to become an operational system instead of a conversational assistant.

## 7. Why AIDE Is Different

The clearest way to explain AIDE is by contrast.

### Against OpenAI Agent Mode / Operator

OpenAI’s Operator introduced a browser-acting agent that can click, type, and scroll on the user’s behalf, and was later folded into ChatGPT agent mode. That product is strongest as a cloud-first browser task performer.

AIDE is different in three ways:

- AIDE is owner-centric rather than browser-centric
- AIDE treats memory continuity across models and surfaces as a product primitive
- AIDE has a trusted device mesh and approval fabric, not just a single agent surface

OpenAI source:

- https://openai.com/index/introducing-operator/

### Against Anthropic Computer Use

Anthropic’s computer use is an important action primitive: screenshot, mouse, keyboard, desktop control.

AIDE is not competing at the primitive level. It is building a persistent operating layer above primitives like that. Computer use is a capability. AIDE is a personal system.

Anthropic source:

- https://docs.anthropic.com/en/docs/build-with-claude/computer-use

### Against Replit Agent

Replit Agent is an excellent software-creation agent. Its center of gravity is turning ideas into deployable apps.

AIDE’s center of gravity is different:

- not app creation
- not developer productivity alone
- but owner productivity across communication, schedule, approvals, and devices

Replit source:

- https://docs.replit.com/replitai/agent
- https://replit.com/products/agent

### Against LangGraph

LangGraph is a powerful orchestration framework for building long-running, stateful agents. It provides developer infrastructure for durable execution, human-in-the-loop, and memory.

AIDE is not a framework. It is a concrete opinionated product built around a specific user problem: the owner’s operational day.

LangGraph source:

- https://docs.langchain.com/oss/python/langgraph/overview

### Against Letta

Letta is the strongest conceptual peer on persistent memory and stateful agents. Its core message is that agents should remember, learn, and improve over time.

AIDE is differentiated by:

- owner-device trust mesh
- approval routing
- productivity cockpit design
- cross-device execution governance
- integrated inbox/calendar/approval operating model

Letta source:

- https://www.letta.com/
- https://docs.letta.com/guides/agents/memory

### Against Superhuman

Superhuman is a best-in-class AI-native email product and increasingly spans email, calendar, and web queries.

AIDE differs because it is not just a premium communications UI. It is a persistent, multi-device, model-portable agent system with approvals, trust, delegation, and owner mesh.

Superhuman source:

- https://superhuman.com/products/mail
- https://help.superhuman.com/hc/en-us/articles/38458628979091-Ask-AI

### Against rabbit teach mode

rabbit’s teach mode is notable because it lets users create task-specific agents by demonstration. That is a compelling path to personal agent training.

AIDE’s differentiation is that it is not built around replaying learned website actions. It is built around persistent owner context, trusted device coordination, and operational workflows.

rabbit source:

- https://www.rabbit.tech/teachmode
- https://www.rabbit.tech/support/article/how-to-use-teach-mode

## 8. The Moat

AIDE’s moat is not one individual model, tool, or UI trick. It is the system-level combination of five things that reinforce each other.

### 8.1 Owner mesh trust graph

Most agent systems assume one user and one surface. AIDE introduces a trust-governed device mesh with:

- explicit pairing
- peer trust state
- capability gating
- revoke and restore
- delegated execution
- approval routing

That is hard to bolt on later because it changes the product’s data model and control plane.

### 8.2 Model-portable memory continuity

Most systems lose coherence when the model provider changes.

AIDE’s memory belongs to AIDE, not to Gemini, Ollama, OpenAI, or Groq. That gives the product provider portability and makes continuity a property of the system rather than a side effect of a single model session.

### 8.3 Personal operations data model

AIDE is converging on a structured operational model of the owner’s day:

- email items
- drafts
- schedule items
- suggestions
- approvals
- device trust state

That creates leverage. Once those entities exist with state and workflow, the product can do more than summarize them. It can coordinate them.

### 8.4 Human approval as a native primitive

Approvals in AIDE are not an afterthought. They are routed, persisted, synced, escalated, and exposed across surfaces.

That matters because useful personal agents eventually touch sensitive actions. Products that treat approval as a modal pop-up will struggle to become reliable daily operating systems.

### 8.5 Local-first plus cloud-optional execution

AIDE can use local or cloud models without changing its identity, memory, or workflow model.

That matters for:

- privacy
- cost control
- latency tuning
- resilience
- vendor leverage

This is a strategic moat because it reduces dependence on any one model vendor while preserving user continuity.

## 9. What Makes AIDE Unusually Strong

The most distinctive thing about AIDE is not any one feature. It is the product shape.

Many agent systems do one of the following well:

- memory
- code generation
- browser action
- inbox productivity
- scheduling
- human approval

AIDE is unusual because it is already combining:

- memory
- inbox
- calendar
- approvals
- trusted devices
- delegated execution
- web and Telegram surfaces
- model portability

inside one coherent owner-centric control plane.

That combination is much rarer than any individual feature.

## 10. Current Product Gaps

To describe AIDE honestly, the document should also record what is not complete yet.

### Product gaps

- Apple Calendar is currently snapshot-based through exported `.ics`, not true live API integration
- Some memory and semantic subsystems need graceful degradation under low disk conditions
- Search routing still benefits from ongoing intent-tuning so local organizer questions stay local
- More diagnostics and recovery tooling are needed for model/provider failures
- The `Your Day` panel can still expand further into richer workflow and agentic follow-through

### UX gaps

- onboarding and setup still require technical steps
- cloud/local model switching could be made safer with more configuration guidance
- device proof and live federation demos should be smoother for non-technical users

## 11. MVP Status

At this point, AIDE has crossed from prototype into meaningful MVP territory.

The MVP is credible because the system already demonstrates:

- persistent memory across model backends
- actionable daily workflow on top of email and calendar
- approval-aware execution
- multi-device owner mesh administration
- trust-governed delegation and routing

That is enough to make AIDE legible as a real product, not just a technical experiment.

## 12. Strategic Positioning

The strongest positioning statement for AIDE is:

**AIDE is the personal operating layer for your workday: one agent, one memory, many trusted devices, and one continuous thread across inbox, calendar, approvals, and action.**

Shorter versions:

- The owner mesh for your digital life
- A persistent personal agent operating system
- The operating layer between your models, your devices, and your day

## 13. Why This Can Become Defensible

Defensibility will come from accumulated operating data and coordination structure, not from a single prompt or model choice.

If AIDE keeps deepening:

- owner-specific memory
- trust and delegation history
- approval policy
- email and calendar workflow state
- cross-device action history

then the product becomes harder to replace with a generic frontier model plus a few tools.

That is the real moat: system memory, workflow state, and trusted operational position in the owner’s day.

## 14. Recommended Next Narrative

The next phase of product storytelling should emphasize:

1. AIDE is not just a chat assistant.
2. AIDE is not just a browser agent.
3. AIDE is the owner’s persistent operating layer.
4. `Your Day` is the visible surface of a much deeper coordination system.
5. Owner mesh is the trust and execution fabric that makes personal agency safe enough to matter.

## 15. Closing

AIDE is differentiated not because it has one flashy feature that nobody else has, but because it is assembling a rare combination into one coherent product:

- persistent memory
- trusted device federation
- approval-aware execution
- productivity workflows
- model portability
- owner-first control

That combination gives AIDE a legitimate path to becoming more than an assistant. It can become the operational layer through which the owner runs the day.
