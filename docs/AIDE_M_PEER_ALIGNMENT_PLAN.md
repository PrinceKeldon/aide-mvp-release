# AIDE M-Peer Alignment and Execution Plan

Last updated: April 6, 2026

Source vision studied:

- [AIDE M-Peer Layer - Build Bible](/Users/frankkoine/Desktop/Kira/AIDE%20M%20.docx)
- [AIDE Product Document](/Users/frankkoine/aide/docs/AIDE_PRODUCT_DOCUMENT.md)
- [AIDE M-Peer Phase 0 and Phase 1 Tranche Plan](/Users/frankkoine/aide/docs/AIDE_M_PEER_PHASE0_PHASE1_TRANCHES.md)

## 1. Executive Readout

The attached M-Peer build bible is directionally correct and should now be treated as the next major product layer after Owner Mesh.

It does not replace AIDE’s current vision. It extends it.

The correct framing is:

- Layer I: Owner Mesh
  One owner, many trusted devices, one trust domain
- Layer II: M-Peer
  Many owners, many sovereign agents, scoped collaboration across trust domains

This is a strong move because it preserves AIDE’s first-principles foundation:

- owner sovereignty
- explicit trust
- persistent memory
- approval-aware action
- portable model runtime

But it extends the system from "my agent across my devices" to "my agent collaborating with your agent without either of us losing control."

That is strategically significant.

## 2. What the Build Bible Gets Right

The build bible is strong on the most important architectural insight:

**M-Peer must not be treated like just another device inside Owner Mesh.**

That distinction is correct.

Owner Mesh and M-Peer have different trust assumptions:

- Owner Mesh assumes same human owner, same operational domain
- M-Peer assumes different humans, separate sovereignty, scoped exchange only

The spec also gets several key design choices right:

- peer trust must be explicit and revocable
- context sharing must be scoped, not implied
- task classes matter
- sandboxing is mandatory
- approvals must exist on both sides when needed
- lifecycle logging matters
- protocol state should be durable

This is the right direction.

## 3. Where the Vision Needs Clarification

The build bible is technically strong, but it needs one product correction:

**M-Peer cannot be built as a protocol-only feature.**

If we stop at:

- schemas
- peer tables
- task logs
- message protocol
- trust scope enums

then we will produce a technically elegant but socially inaccessible system.

For the other 5.2 billion people to onboard, M-Peer must be designed as:

- a human-readable trust relationship
- a guided peer invitation flow
- a safe default scope system
- a simple explanation of what another person’s AIDE can and cannot ask

In other words:

The protocol is necessary, but the onboarding model is the product.

## 4. Alignment with AIDE’s Current Product Vision

The current product document positions AIDE as:

- a personal operating layer
- a workday OS
- an owner mesh
- a memory-preserving agent system

That still holds.

But after reading the M-Peer build bible, the more complete product stack is:

### Layer I: Personal Agent OS

This is what AIDE already is becoming:

- one owner
- one memory
- many trusted owner devices
- one action and approval fabric
- `Your Day` as the operating cockpit

### Layer II: Sovereign Agent Network

This is what M-Peer adds:

- one owner’s AIDE can collaborate with another owner’s AIDE
- each side remains sovereign
- no implicit memory access
- no hidden trust escalation
- only explicit scope-governed work exchange

This means AIDE’s long-term product is not just:

"an assistant for me"

It becomes:

"my sovereign personal agent that can safely collaborate with other sovereign personal agents."

That is a stronger and rarer product category.

## 5. The Correct Product Narrative

The narrative should now be:

### Today

AIDE helps one person run their day across devices, inbox, calendar, approvals, and memory.

### Next

AIDE will allow one person’s agent to work with another person’s agent safely, with explicit permission, scoped context, revocable trust, and full sovereignty on both sides.

### Plain-language version

"My AIDE can ask your AIDE for help, but neither of us gives up control."

This sentence should become a core product message.

## 6. First-Principles Product Rules for M-Peer

To stay aligned with the original vision and make onboarding accessible, M-Peer should obey these rules:

### Rule 1: Sovereignty first

No peer agent should ever be treated like an owner device.

### Rule 2: Trust must be visible

The user must always be able to answer:

- who is this peer
- what can they ask for
- what can they receive
- what happens if I revoke them

### Rule 3: Context must be explicit by default

No hidden memory sharing.

Default M-Peer mode should be:

- explicit payload only
- no memory
- no contextual expansion

### Rule 4: Safe templates beat custom policy for onboarding

Most users should not start with raw trust-scope editing.

They should start with plain templates like:

- Research partner
- Calendar coordination partner
- Project collaboration partner
- Assistant-to-assistant introductions

### Rule 5: Human meaning before protocol purity

Users should never need to understand:

- JSON-RPC
- capability cards
- scope blobs
- sandbox mode enums

Those belong under the hood.

### Rule 6: Approval is product trust

The more sensitive the collaboration, the more visible approvals must become.

### Rule 7: The UI must feel like inviting a person, not configuring a server

If M-Peer feels like enterprise middleware, onboarding will fail.

## 7. Mapping the Build Bible to the Current Codebase

### What already exists and can be reused

From Layer I, AIDE already has:

- canonical trust in `DeviceRegistry`
- pairing and trust lifecycle
- mesh discovery and routing
- peer admin page
- approval routing and escalation
- task lifecycle engine
- device capabilities
- daily brief and operator surface

These are not throwaway systems. They are the substrate M-Peer should build on.

### What must not be reused without adaptation

Owner Mesh assumptions that do **not** carry over directly:

- same-owner memory sharing
- same-owner context defaults
- device-oriented trust semantics
- executor capability meaning identical trust

### What M-Peer should add

- peer-agent identity model distinct from device identity
- trust scopes as first-class policy objects
- peer task logs
- scoped context classes
- strict sandbox execution mode
- bilateral approval states
- peer-facing capability discovery

## 8. Recommended Architecture Decision

### Decision

Implement M-Peer as a new sovereign peer layer above the canonical trust registry, not as a fork of Owner Mesh and not as a parallel one-off subsystem.

### Why

Because AIDE already has a strong canonical runtime for:

- trust
- routing
- approvals
- peer administration
- task execution

The right move is to extend the current trust model, not split the product in two.

### Practical implication

Add a new peer relationship type and peer scope system that plugs into the same governance and messaging fabric, while keeping owner devices and sovereign peers clearly separated in policy and UX.

## 9. Protocol Guidance

The build bible’s protocol thinking is strong, and market context now supports it.

The official A2A protocol is useful validation that the industry is moving toward:

- agent cards
- task lifecycles
- opaque agents
- scoped collaboration
- independent systems negotiating work

Official source:

- https://google-a2a.github.io/A2A/specification/
- https://a2a-protocol.org/dev/

Important product conclusion:

AIDE should take inspiration from A2A concepts, but it should not expose A2A complexity directly to mainstream users.

That means:

- internally: align with A2A-like concepts where helpful
- externally: present M-Peer as simple peer invitation and collaboration

## 10. What the UX Must Look Like for Everyone Else

If we want billions of people to understand AIDE, M-Peer onboarding should feel like:

### Step 1

"Invite another AIDE"

### Step 2

"Choose what they can help with"

Options:

- Research only
- Calendar coordination
- Project collaboration
- Custom

### Step 3

"Approve the relationship"

Show:

- peer name
- owner name
- what they can request
- what they can receive
- whether they can ask for approvals

### Step 4

"Done"

The peer relationship becomes visible in a dashboard with:

- status
- scope
- last activity
- revoke button

That is the consumer onboarding path.

Underneath it, the system can still store:

- trust scope objects
- task logs
- sandbox modes
- signatures

## 11. Updated Execution Plan

### Phase 0: Reframe the product model

Deliverable:

- AIDE product documentation updated to reflect:
  - Layer I Owner Mesh
  - Layer II M-Peer
  - personal OS + sovereign collaboration narrative

Purpose:

Avoid building M-Peer as a technical appendage instead of a product layer.

### Phase 1: Introduce canonical sovereign peer records

Deliverable:

- `peer_agents`
- `trust_scopes`
- `peer_task_log`
- safe scope templates

Notes:

- this should live alongside, but not collapse into, owner-device records
- registry remains canonical runtime authority

### Phase 2: Scope and sandbox enforcement

Deliverable:

- strict validation of:
  - task classes
  - context classes
  - approval requirements
  - sandbox mode

Default:

- explicit-only context
- no memory
- strict sandbox

### Phase 3: Human onboarding and peer invitation flow

Deliverable:

- peer invite page
- QR / payload flow
- plain-language trust templates
- revoke / pause / edit permissions

This is the most important onboarding layer.

### Phase 4: M-Peer message lifecycle

Deliverable:

- request
- accept
- reject
- update
- approval request
- completion
- audit trail

### Phase 5: Bilateral approvals and visible sovereignty

Deliverable:

- both sides can require approval
- both sides can revoke
- both sides can see what was shared and why

### Phase 6: Demo scenarios

Deliverable:

- research delegation
- calendar coordination between two owners
- project collaboration
- explicit reject and revoke path

## 12. MVP Definition for M-Peer

M-Peer MVP is complete when:

1. one AIDE can invite another AIDE
2. trust scope is visible in plain language
3. tasks can be delegated under a safe template
4. only explicit allowed context crosses the boundary
5. approvals work when required
6. both parties can revoke the relationship
7. activity is visible and understandable in the dashboard

If those are true, the product is understandable and demonstrable.

## 13. What Not to Do

### Do not:

- expose raw scope JSON as the primary setup method
- treat peers like just another device
- default to contextual or memory sharing
- let protocol elegance outrun user comprehension
- build M-Peer without a visible revoke and audit story

### Do instead:

- make the defaults safe
- make the UI human-readable
- let advanced users go deeper later
- keep protocol complexity behind the product layer

## 14. Final Alignment Judgment

The M-Peer build bible is aligned with AIDE’s first principles.

It is the correct next layer.

But to succeed, it must be implemented as:

- a sovereignty product
- a trust product
- an onboarding product

not just a message protocol.

The build sequence should therefore be:

1. align the narrative
2. define peer trust objects and safe templates
3. enforce scoped exchange
4. build plain-language onboarding
5. add bilateral workflow and audit

That is the path that stays true to the original vision and keeps the system understandable for the other 5.2 billion people.

## 15. Recommended Immediate Next Move

The best next move is:

**Start Phase 0 and Phase 1 together.**

That means:

- update product framing to include M-Peer
- define canonical sovereign peer records and trust scopes
- keep strict defaults
- design the UI around invitation and safe templates from the beginning

This prevents the architecture from drifting away from the user experience.

## 16. Build Contract

The execution contract for the first implementation pass is now captured in:

- [AIDE M-Peer Phase 0 and Phase 1 Tranche Plan](/Users/frankkoine/aide/docs/AIDE_M_PEER_PHASE0_PHASE1_TRANCHES.md)

That document should be treated as the build-facing breakdown of this alignment memo.
