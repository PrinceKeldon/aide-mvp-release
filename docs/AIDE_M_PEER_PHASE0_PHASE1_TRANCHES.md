# AIDE M-Peer Phase 0 and Phase 1 Tranche Plan

Last updated: April 6, 2026

Related documents:

- [AIDE Product Document](/Users/frankkoine/aide/docs/AIDE_PRODUCT_DOCUMENT.md)
- [AIDE M-Peer Alignment and Execution Plan](/Users/frankkoine/aide/docs/AIDE_M_PEER_ALIGNMENT_PLAN.md)
- [AIDE M-Peer Build Bible](/Users/frankkoine/Desktop/Kira/AIDE%20M%20.docx)

## Purpose

This document turns the M-Peer alignment plan into the actual implementation tranche list for:

- Phase 0: Product framing and UX contract
- Phase 1: Canonical sovereign peer records and trust scopes

The goal is to keep the build aligned with first principles:

- sovereignty first
- explicit trust
- explicit context by default
- safe templates before raw policy
- onboarding simple enough for mainstream users

## Delivery Principle

Phase 0 and Phase 1 should be built together.

Why:

- if we build schema and runtime first without the product framing, M-Peer will drift into protocol complexity
- if we build UI language first without canonical runtime objects, the onboarding story will be fake

So the correct order is:

1. establish the product language and user-visible concepts
2. define the canonical data structures to support them
3. expose only safe, understandable controls in the first UI surface

## Phase 0

### Objective

Define M-Peer as a product layer, not just a protocol extension.

### Output

- clear language for what M-Peer is
- clear distinction between Owner Mesh and M-Peer
- safe default collaboration templates
- first-pass UI and API contract for peer invitation and scope explanation

### Files to create or update

#### 1. [docs/AIDE_PRODUCT_DOCUMENT.md](/Users/frankkoine/aide/docs/AIDE_PRODUCT_DOCUMENT.md)

Status:
- update

Tasks:
- add explicit two-layer architecture:
  - `Layer I: Owner Mesh`
  - `Layer II: M-Peer`
- update the product category from only personal agent OS to:
  - personal agent OS plus sovereign agent collaboration layer
- add a short section called `M-Peer in Plain Language`
- add a short section called `Why M-Peer matters`
- make sure the document explains M-Peer without protocol jargon

Acceptance criteria:
- a non-technical reader can understand the difference between:
  - my devices
  - another person’s agent

#### 2. [docs/AIDE_M_PEER_ALIGNMENT_PLAN.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_ALIGNMENT_PLAN.md)

Status:
- update

Tasks:
- add explicit tranche references to this Phase 0 / Phase 1 build document
- tighten the language around:
  - invitation
  - trust templates
  - revoke model
  - default no-memory cross-peer sharing
- convert high-level recommendations into build assumptions

Acceptance criteria:
- the alignment plan reads like an architecture and product contract, not just a strategy memo

#### 3. New file: [docs/AIDE_M_PEER_USER_MODEL.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_USER_MODEL.md)

Status:
- create

Purpose:
- define the human mental model for M-Peer

Required sections:
- What is another AIDE?
- What can another AIDE ask mine to do?
- What can another AIDE see?
- What is a trust scope?
- What does revoke do?
- What happens when approval is needed?

Tone requirement:
- plain language only
- no schema or enum language in the core explanation

Acceptance criteria:
- this file can be used as the copy source for onboarding UI

#### 4. New file: [docs/AIDE_M_PEER_SCOPE_TEMPLATES.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_SCOPE_TEMPLATES.md)

Status:
- create

Purpose:
- define safe starter templates before code

Required templates:
- `Research Partner`
- `Calendar Coordination`
- `Project Collaboration`
- `Assistant Introduction`

For each template define:
- plain-language description
- allowed task classes
- allowed context classes
- whether approvals are required
- default sandbox mode
- expected risk level

Acceptance criteria:
- each template can be explained in one sentence to a mainstream user

#### 5. [interface/web.py](/Users/frankkoine/aide/interface/web.py)

Status:
- prepare, but do not overbuild in Phase 0

Tasks:
- add placeholder navigation concept for future `/m-peer`
- add copy-safe helper text strings or temporary stub sections if helpful
- do not yet build the full invite flow in Phase 0
- keep UI language aligned with:
  - peer
  - invitation
  - scope
  - revoke

Acceptance criteria:
- current `/mesh` terminology does not mislead users into thinking sovereign peers are just devices

#### 6. New file: [tests/test_m_peer_docs_or_contracts.py](/Users/frankkoine/aide/tests/test_m_peer_docs_or_contracts.py)

Status:
- optional but recommended

Purpose:
- low-cost test to lock in initial template IDs and public user-facing contract strings if they become code constants

Acceptance criteria:
- prevents accidental renaming of initial safe scope identifiers once implementation begins

## Phase 1

### Objective

Create canonical sovereign peer records and trust scopes as runtime primitives without collapsing them into Owner Mesh device records.

### Output

- peer agents stored canonically
- trust scopes stored canonically
- safe scope templates available
- peer lifecycle data model ready
- no live memory sharing by default

### Architecture rule

Phase 1 must preserve this distinction:

- `device_identities` are owner-side devices
- `peer_agents` are sovereign external agents

These may interact, but they are not the same entity class.

### Files to create or update

#### 1. [mesh/device_registry.py](/Users/frankkoine/aide/mesh/device_registry.py)

Status:
- update heavily

Tasks:
- keep current owner-device registry intact
- add new canonical tables:
  - `peer_agents`
  - `trust_scopes`
  - `peer_task_log`
  - optionally `trust_scope_templates`
- add migration-safe table initialization
- add peer-specific CRUD methods:
  - `create_peer_agent(...)`
  - `get_peer_agent(...)`
  - `list_peer_agents()`
  - `peer_agent_exists(...)`
  - `revoke_peer_agent(...)`
  - `restore_peer_agent(...)`
  - `rename_peer_agent(...)`
- add trust-scope methods:
  - `create_trust_scope(...)`
  - `get_trust_scope(...)`
  - `list_trust_scopes()`
  - `create_scope_template(...)`
  - `list_scope_templates()`
- add peer task log methods:
  - `create_peer_task_log(...)`
  - `update_peer_task_log_state(...)`
  - `get_peer_task_log(...)`
  - `list_peer_task_logs(...)`

Design constraints:
- do not overload existing owner-device methods with peer semantics
- peer APIs should be explicit
- owner-device trust and peer-agent trust must remain distinguishable in code

Acceptance criteria:
- device registry becomes the canonical runtime persistence layer for sovereign peers and scopes
- existing owner mesh tests still pass

#### 2. New file: [mesh/peer_trust_scope.py](/Users/frankkoine/aide/mesh/peer_trust_scope.py)

Status:
- create

Purpose:
- hold the typed scope model from the build bible

Required contents:
- `SandboxMode`
- `ContextPolicy`
- `PeerTrustScope` dataclass
- validation helpers:
  - `validate_task_type(...)`
  - `validate_context_type(...)`
- serializer/deserializer helpers to and from DB JSON

Acceptance criteria:
- a trust scope can be loaded from DB and validated without importing UI or routing code

#### 3. New file: [mesh/peer_templates.py](/Users/frankkoine/aide/mesh/peer_templates.py)

Status:
- create

Purpose:
- define code-level safe starter templates

Tasks:
- encode the initial templates from the docs as stable constants
- expose helpers:
  - `default_scope_templates()`
  - `get_template(template_id)`
- keep template IDs stable and human-readable

Acceptance criteria:
- templates can be pre-seeded into storage on startup or migration

#### 4. New file: [mesh/peer_registry.py](/Users/frankkoine/aide/mesh/peer_registry.py)

Status:
- create if separation improves clarity

Purpose:
- thin service layer above `DeviceRegistry` for peer-agent operations

Tasks:
- encapsulate sovereign-peer operations so higher layers do not talk raw SQL-like registry methods directly
- expose:
  - peer invitation record creation
  - trust scope assignment
  - revoke/restore
  - task-log creation

Acceptance criteria:
- routing and UI code can consume peer operations without directly embedding schema details

Note:
- if this becomes unnecessary duplication, peer-specific helpers may stay inside `DeviceRegistry`
- but the separation is recommended for clarity

#### 5. [mesh/pairing.py](/Users/frankkoine/aide/mesh/pairing.py)

Status:
- extend carefully

Tasks:
- keep current device pairing intact for Owner Mesh
- add explicit distinction between:
  - owner-device pairing
  - sovereign-peer invitation
- do not reuse `complete_pairing(...)` blindly for M-Peer
- add a new path or manager method for peer invitation payloads, for example:
  - `complete_peer_invitation(...)`
  - or a separate manager class if cleaner

Required behavior:
- peer invitation creates a `peer_agent` record, not a device identity
- stores public key and display name
- assigns an initial trust scope template
- records lifecycle metadata

Acceptance criteria:
- no sovereign peer can accidentally land in the owner-device table path

#### 6. New file: [mesh/peer_invitation.py](/Users/frankkoine/aide/mesh/peer_invitation.py)

Status:
- create

Purpose:
- hold M-Peer invite payload logic independent of device pairing logic

Tasks:
- define invitation payload shape
- sign and verify payload
- keep payload human-sharable:
  - QR-ready
  - text payload ready

Acceptance criteria:
- future UI can use this without confusing device pairing and peer invitation

#### 7. New file: [mesh/messages/peer_task.py](/Users/frankkoine/aide/mesh/messages/peer_task.py)

Status:
- create

Purpose:
- define typed message payloads for peer-to-peer task exchange

Tasks:
- define initial payload models for:
  - request
  - accept
  - reject
  - update
  - result
  - approval request
  - approval response

Note:
- Phase 1 only needs the message definitions and serialization contract
- live routing enforcement can come in later phases

Acceptance criteria:
- there is one clear typed place for M-Peer task message shapes

#### 8. [mesh/coordinator.py](/Users/frankkoine/aide/mesh/coordinator.py)

Status:
- prepare, limited Phase 1 work only

Tasks:
- introduce a clear branch for future sovereign-peer messages
- do not yet implement full peer execution in Phase 1
- add stubs or registration points for:
  - `PEER_TASK_REQUEST`
  - `PEER_TASK_UPDATE`
  - `PEER_TASK_RESULT`

Acceptance criteria:
- Phase 2 can plug into the coordinator without a major refactor

#### 9. [mesh/message_router.py](/Users/frankkoine/aide/mesh/message_router.py)

Status:
- prepare, limited Phase 1 work only

Tasks:
- add explicit message typing or namespace support so owner-device mesh messages and peer-agent messages do not blur together
- do not yet route live M-Peer traffic if policy is not ready

Acceptance criteria:
- code structure makes the future separation obvious

#### 10. [mesh/orchestrator.py](/Users/frankkoine/aide/mesh/orchestrator.py)

Status:
- no major live behavior change yet

Tasks:
- identify what is owner-only orchestration versus future sovereign peer orchestration
- add TODO-safe seams or internal method separation

Acceptance criteria:
- later peer collaboration logic does not need to hack through owner-device assumptions

#### 11. [interface/web.py](/Users/frankkoine/aide/interface/web.py)

Status:
- extend with first-pass M-Peer admin page scaffolding

Tasks:
- add `/m-peer` page shell
- add `/api/m-peer/...` placeholder endpoints backed by real Phase 1 data
- show:
  - known peer agents
  - trust scope name
  - paired/invited state
  - revoked state
  - last seen
- include plain-language explanations of:
  - what a peer is
  - what a scope means

Do not yet build:
- full bilateral task routing UI
- advanced live message controls

Acceptance criteria:
- a user can see that peers are different from devices
- a user can understand the relationship in plain language

#### 12. [main.py](/Users/frankkoine/aide/main.py)

Status:
- update lightly

Tasks:
- seed initial M-Peer trust scope templates on startup if missing
- wire peer runtime service into web runtime if needed
- do not auto-bootstrap M-Peer relationships the way owner-device discovery does

Acceptance criteria:
- startup creates safe defaults without silently creating trust relationships

#### 13. New file: [tests/test_m_peer_registry.py](/Users/frankkoine/aide/tests/test_m_peer_registry.py)

Status:
- create

Coverage:
- peer agent create/get/list
- revoke and restore
- trust scope create/get/list
- template seeding
- peer task log create/update
- ensure peer agents do not appear as owner devices

Acceptance criteria:
- canonical storage semantics are locked in

#### 14. New file: [tests/test_m_peer_invitation.py](/Users/frankkoine/aide/tests/test_m_peer_invitation.py)

Status:
- create

Coverage:
- invitation payload create/verify
- invitation creates peer agent record
- owner-device pairing and peer invitation remain separate flows

Acceptance criteria:
- invitation semantics are explicit and safe

#### 15. New file: [tests/test_m_peer_web.py](/Users/frankkoine/aide/tests/test_m_peer_web.py)

Status:
- create

Coverage:
- `/m-peer` page renders
- peer list endpoint returns canonical peer data
- scope labels are human-readable
- revoked peers display clearly

Acceptance criteria:
- the first M-Peer UI surface is test-backed

## Phase 0 and Phase 1 Combined Acceptance Criteria

These phases are complete when:

1. AIDE documentation clearly distinguishes Owner Mesh from M-Peer.
2. The product has safe, human-readable peer scope templates.
3. Sovereign peer records exist as canonical runtime data.
4. Trust scopes are stored canonically and validated as typed objects.
5. Peer invitation is modeled separately from owner-device pairing.
6. A first `/m-peer` admin surface exists and is understandable in plain language.
7. No cross-peer memory sharing occurs by default.
8. Existing Owner Mesh flows continue to work.

## Recommended Execution Order

### Tranche 0A: Product framing

Files:
- [docs/AIDE_PRODUCT_DOCUMENT.md](/Users/frankkoine/aide/docs/AIDE_PRODUCT_DOCUMENT.md)
- [docs/AIDE_M_PEER_ALIGNMENT_PLAN.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_ALIGNMENT_PLAN.md)
- new [docs/AIDE_M_PEER_USER_MODEL.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_USER_MODEL.md)
- new [docs/AIDE_M_PEER_SCOPE_TEMPLATES.md](/Users/frankkoine/aide/docs/AIDE_M_PEER_SCOPE_TEMPLATES.md)

Goal:
- lock the product vocabulary before runtime implementation expands

### Tranche 1A: Canonical peer storage

Files:
- [mesh/device_registry.py](/Users/frankkoine/aide/mesh/device_registry.py)
- new [mesh/peer_trust_scope.py](/Users/frankkoine/aide/mesh/peer_trust_scope.py)
- new [mesh/peer_templates.py](/Users/frankkoine/aide/mesh/peer_templates.py)
- optionally new [mesh/peer_registry.py](/Users/frankkoine/aide/mesh/peer_registry.py)

Goal:
- create canonical peer and scope persistence

### Tranche 1B: Invitation model separation

Files:
- [mesh/pairing.py](/Users/frankkoine/aide/mesh/pairing.py)
- new [mesh/peer_invitation.py](/Users/frankkoine/aide/mesh/peer_invitation.py)
- new [mesh/messages/peer_task.py](/Users/frankkoine/aide/mesh/messages/peer_task.py)

Goal:
- prevent M-Peer from being mis-modeled as device pairing

### Tranche 1C: First M-Peer UI surface

Files:
- [interface/web.py](/Users/frankkoine/aide/interface/web.py)
- [main.py](/Users/frankkoine/aide/main.py)

Goal:
- give the product a visible and comprehensible peer layer early

### Tranche 1D: Test contract

Files:
- new [tests/test_m_peer_registry.py](/Users/frankkoine/aide/tests/test_m_peer_registry.py)
- new [tests/test_m_peer_invitation.py](/Users/frankkoine/aide/tests/test_m_peer_invitation.py)
- new [tests/test_m_peer_web.py](/Users/frankkoine/aide/tests/test_m_peer_web.py)

Goal:
- lock in the separation between devices and sovereign peers

## Final Note

If we follow this tranche order, M-Peer will launch as:

- understandable
- safe by default
- architecturally clean
- consistent with AIDE’s first principles

That is the right foundation if the ambition is to make AIDE usable not only by technical operators, but by the other 5.2 billion people as well.
