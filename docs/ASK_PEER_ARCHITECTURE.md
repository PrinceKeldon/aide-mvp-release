# ASK_PEER Architecture

Last updated: April 13, 2026

## 1. Purpose

`ASK_PEER` is the owner-mesh primitive that allows one trusted AIDE node to ask another trusted AIDE node a real question and receive a real answer from that peer's own agent context.

This is not a ping.

This is not delegated execution.

This is not another person's sovereign peer relationship.

It is the first direct device-to-device conversational layer inside the owner mesh.

That matters because it changes the owner mesh from:

- "a set of reachable devices"

into:

- "a set of reachable agent minds with their own local context and memory"

That shift is part of what makes AIDE distinct.

## 2. Product Definition

`ASK_PEER` means:

- the owner or an owner device addresses a specific trusted peer device
- the system routes the request over live mesh transport
- the target device runs the prompt through its own local AIDE agent
- the reply comes back to the caller as the target device's answer

The important product property is this:

- the reply is generated on the target node, not simulated by the sender

If `Vera Desk` asks `VERA` on Android a question, Android answers from Android's own local state, tools, recent context, and memory.

## 3. Why This Is Distinctive

Most "multi-agent" systems either:

- fake delegation by keeping all cognition on one orchestrator
- send only tasks, not real conversational questions
- treat remote devices as execution shells rather than local agents

AIDE's `ASK_PEER` direction is different:

- each trusted owner device can become a contextual thinking node
- the mesh can ask a device what it knows, remembers, or is currently holding
- the owner can begin to treat devices as specialized cognitive surfaces, not only hardware surfaces

Examples:

- "Ask Android what we were doing with the inbox follow-up workflow."
- "Ask Midas what it remembers about the research brief."
- "Ask Vera Desk what was last decided on the client call preparation."

This is the start of a system where:

- the phone is not just a phone
- the Mac is not just a control terminal
- each node can hold ongoing local conversational state and participate in coordination

## 4. What Exists Today

The current implementation is real, but intentionally narrow.

It supports:

- chat-side direct ask routing such as `Ask Android ...`
- `/mesh` dashboard ask via `Ask Device`
- remote reply generation on the target node
- mesh transport preference for conversational coordination messages

It does not yet support:

- a persistent unified per-device conversation thread in the dashboard
- cross-surface thread reconciliation between `/mesh` and Telegram
- long-running multi-turn peer conversations with visible stored thread history

## 5. Current User Experience

There are two implemented entry points.

### 5.1 Chat Fast Path

In owner chat, the operator can use an explicit target phrasing such as:

- `Ask Android about the inbox workflow follow-up`
- `Tell VERA what I said about the client`
- `Hi VERA`

When the target resolver successfully resolves the device, the local agent bypasses the normal LLM flow and directly calls the routing tool with:

- `message_type = "ASK_PEER"`
- `payload = {"prompt": "..."}`

This makes peer ask a first-class routing action rather than an LLM guess.

### 5.2 `/mesh` Ask Device

The owner dashboard now exposes `Ask Device` for trusted mesh-bound peers.

The UI:

- opens a small modal
- accepts a free-text question
- sends it to the selected peer over the mesh
- renders the peer's reply in place

This gives the owner an operator surface for talking to devices directly without leaving `/mesh`.

## 6. End-to-End Flow

The current `ASK_PEER` flow is:

1. The user targets a peer from chat or the `/mesh` dashboard.
2. The sender resolves the peer by `device_id`.
3. The message router decides that this message must go over mesh, not Telegram.
4. The mesh node sends an intent with `message_type = "ASK_PEER"`.
5. The target node's mesh coordinator receives the intent.
6. The target node authorizes the sender against trust and context policy.
7. The target node runs the prompt through its own local agent.
8. The target node returns `{"status": "completed", "response": ...}`.
9. The sender surfaces that reply back to the operator.

The central design rule is:

- routing happens by `device_id`
- cognition happens on the target node

## 7. Core Files

The current implementation is centered in these files:

- [core/agent.py](/Users/frankkoine/aide/core/agent.py)
- [tools/device_router_tool.py](/Users/frankkoine/aide/tools/device_router_tool.py)
- [mesh/message_router.py](/Users/frankkoine/aide/mesh/message_router.py)
- [mesh/coordinator.py](/Users/frankkoine/aide/mesh/coordinator.py)
- [interface/web.py](/Users/frankkoine/aide/interface/web.py)

## 8. Component Responsibilities

### 8.1 Agent Fast Path

File:

- [core/agent.py](/Users/frankkoine/aide/core/agent.py)

Responsibility:

- detect explicit conversational requests to a resolved peer
- bypass normal local response generation
- route directly to `route_to_device`

Key behavior:

- `_extract_ask_peer_request(...)` recognizes target-first or ask-style phrasing
- once resolved, the agent calls the device router tool with `ASK_PEER`
- the local LLM is skipped for that turn

This is important because it makes peer chat deterministic and operator-controlled.

### 8.2 Device Router Tool

File:

- [tools/device_router_tool.py](/Users/frankkoine/aide/tools/device_router_tool.py)

Responsibility:

- provide the tool surface that the agent uses for peer routing
- unwrap the returned mesh response for human-readable output

Key behavior:

- supports `ASK_PEER` in the tool description
- uses `send_with_response(...)`
- if the peer completes the request, returns the peer's reply text directly

This keeps the peer answer clean in chat instead of surfacing raw routing metadata.

### 8.3 Message Router

File:

- [mesh/message_router.py](/Users/frankkoine/aide/mesh/message_router.py)

Responsibility:

- decide how a message reaches a device
- enforce transport and capability rules

Key behavior:

- `ASK_PEER` is treated as an execution-style coordination message
- execution-style coordination messages are not allowed to fall back to Telegram terminals
- when a peer has both Telegram and mesh bindings, `ASK_PEER` prefers mesh

This router decision is critical.

Without it, dual-bound devices can appear reachable but still fail real coordination because the system chooses the wrong transport.

### 8.4 Mesh Coordinator

File:

- [mesh/coordinator.py](/Users/frankkoine/aide/mesh/coordinator.py)

Responsibility:

- receive mesh intents on the target node
- authorize the sender
- run the local agent
- return the reply

Key behavior:

- `_handle_ask_peer(...)` checks trust and `can_receive_context`
- it extracts the prompt from the intent payload
- it calls the target node's local agent
- it returns the final response

This is the point where the remote node actually thinks.

### 8.5 Dashboard Surface

File:

- [interface/web.py](/Users/frankkoine/aide/interface/web.py)

Responsibility:

- expose an operator-facing peer ask API
- provide the `/mesh` `Ask Device` UI

Key behavior:

- `POST /api/mesh/peers/{device_id}/ask`
- validates trust and live mesh binding
- sends the question through the router
- returns the peer response to the modal

## 9. Transport Boundary

This needs to stay explicit.

`ASK_PEER` is currently mesh-native.

That means:

- it requires a trusted peer
- it requires a live mesh route
- it does not treat Telegram as the authoritative conversational transport for inter-device agent chat

This is not a limitation of the concept.

It is a deliberate product boundary in the current tranche.

Reason:

- Telegram is a human chat terminal surface
- mesh is the live agent-to-agent coordination surface
- if those are conflated too early, the system becomes ambiguous and brittle

## 10. Memory Semantics

Current memory behavior is asymmetric by design.

What is true today:

- the target node answers from its own local agent state and memory
- the answer is therefore genuinely shaped by that peer's context

What is not true yet:

- there is no dedicated persistent owner-visible thread for each peer ask conversation
- `/mesh` does not yet expose a durable thread history for peer asks
- Telegram and `/mesh` are not yet reconciled into one shared thread

So the correct current statement is:

- `ASK_PEER` already uses the peer's own memory
- `ASK_PEER` does not yet provide a full shared cross-surface conversation log

## 11. Relationship to Other AIDE Primitives

`ASK_PEER` should be understood as one primitive among several.

### 11.1 Compared with `PING`

`PING` asks:

- "are you reachable?"

`ASK_PEER` asks:

- "what do you know or think right now?"

### 11.2 Compared with `DELEGATE_TASK`

`DELEGATE_TASK` asks:

- "execute bounded work and return a result"

`ASK_PEER` asks:

- "answer this conversational question from your own local context"

### 11.3 Compared with M-Peer

Owner mesh `ASK_PEER` is:

- inside one owner's trusted device mesh

M-Peer is:

- another person's sovereign AIDE with separate trust scope rules

That distinction must remain sharp.

## 12. Why This Matters for the Product Vision

If AIDE succeeds, the owner mesh should not feel like:

- one smart node and several dumb endpoints

It should feel like:

- one owner operating environment
- composed of multiple trusted local agents
- each with its own perspective, availability, and state

`ASK_PEER` is the first user-visible move toward that model.

It creates the possibility of:

- conversational coordination between devices
- specialization by device context
- memory-bearing peer nodes
- operator workflows that involve asking before delegating

That is a stronger and more original model than simple remote execution.

## 13. Current Constraints

The current implementation is intentionally constrained in these ways:

- explicit target resolution is required
- trust and context policy still gate access
- mesh transport must be live
- no canonical per-peer thread store exists yet
- no cross-posting or thread reconciliation exists yet

These are not flaws in the direction.

They are tranche boundaries.

## 14. Next Correct Tranche

The next build layer should not try to jump straight to Telegram reconciliation.

The correct next tranche is:

1. canonical per-peer conversation log
2. correlation IDs for peer ask turns
3. `/mesh` thread history per device
4. optional read-only Telegram mirroring
5. only after that, full reply reconciliation across surfaces

Reason:

- one real thread must exist before multiple surfaces can safely reflect it

## 15. Operator Language

The intended operator mental model should be simple:

- ping checks whether a device is reachable
- ask checks what a device knows
- delegate tells a device to do work

That is a strong product grammar.

It is also easy to teach.

## 16. Summary

`ASK_PEER` is the owner-mesh conversational primitive that lets one AIDE node ask another AIDE node a real question and get a reply produced on that target device.

It is important because it begins to make devices in the mesh feel like distinct agent minds rather than just remote execution slots.

Today it is implemented as:

- explicit routing from chat or `/mesh`
- mesh-native delivery
- trust-aware remote execution on the peer
- direct response return to the operator

The next tranche should build durable peer conversation threads on top of this foundation rather than skipping ahead to full cross-surface reconciliation.
