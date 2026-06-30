# Owner Mesh Productivity Implementation Spec

Last updated: April 8, 2026

## 1. Objective

Turn the existing Owner Mesh infrastructure into a usable daily productivity system without introducing a new architecture.

The system should support:

- one clear operator surface on Mac
- one or more trusted executor devices
- explicit delegation of bounded work
- visible task lifecycle
- approval-aware execution
- result replay into `Your Day`

This spec is intentionally constrained.

It does **not** try to build:

- open-ended multi-agent conversation between owner devices
- a generic workflow engine
- full automation across every tool
- M-Peer extensions in the same tranche

It focuses on the shortest path from "mesh works" to "AIDE helps run the day."

## 2. Product Model

### 2.1 Core role split

For the near-term product, AIDE should treat the owner mesh as role-specialized:

- `Vera Desk` / Mac: orchestrator, planner, primary cockpit
- Android full node: mobile executor, mobile approval surface, remote context node
- other owner devices: optional approval or visibility nodes

The primary interaction model is:

1. owner asks for work on Mac
2. Mac decides local vs delegated execution
3. remote node executes when appropriate
4. result returns to Mac
5. `Your Day` and `/mesh` surface state and outcomes

### 2.2 First-principles rule

The user should not need to think in terms of:

- intent types
- transport
- device IDs
- mesh routing internals

The user should think in terms of:

- run this here
- run this on Android
- wait for approval
- done

## 3. Current State

### 3.1 Already working

- trusted owner-mesh device records
- mesh ping both directions
- delegated task execution via `DELEGATE_TASK`
- task result return via `TASK_RESULT`
- manual mesh bind fallback
- `Your Day` interactive brief
- approval infrastructure

### 3.2 Current gap

The infrastructure works, but the productivity UX is too thin.

Today the user mostly has:

- `Ping Peer`
- `Test Task`
- basic delegated execution under the hood

What is missing is an operator-grade delegation surface:

- task composer
- task timeline
- packaged workflows
- result replay into the owner cockpit

## 4. Target Workflow

### 4.1 Primary workflow

The primary owner-mesh workflow should be:

1. owner opens `Your Day` or `/mesh`
2. owner chooses a task or creates one
3. owner chooses a target device or lets AIDE choose
4. AIDE creates a persistent mesh task
5. task is routed to executor
6. executor runs it
7. result is returned
8. result appears in `/mesh` and `Your Day`
9. if approval is needed, approval is routed before action continues

### 4.2 Supported task categories in this tranche

Only four packaged workflows should ship first:

1. `Research Brief`
2. `Email Triage`
3. `Follow-up Draft`
4. `Daily Preparation`

Everything else should stay as:

- generic delegated task text

This keeps the build focused and avoids workflow sprawl.

## 5. UX Build

## 5.1 `/mesh` becomes the execution console

The `/mesh` page should evolve from diagnostics/admin into an execution console.

Add:

- `Delegate Work` composer
- per-peer `Run On This Device` action
- `Owner Mesh Tasks` timeline
- result cards

Keep:

- trust controls
- manual bind
- ping

Move diagnostics lower in visual priority.

### 5.1.1 Delegate Work composer

New section near the top of `/mesh`:

- title: `Delegate Work`
- fields:
  - `Task`
  - `Workflow Type`
  - `Target Device`
  - `Needs Approval` toggle
  - `Context Notes` optional field
- actions:
  - `Run Now`
  - `Save as Draft`

`Workflow Type` options:

- `Generic Task`
- `Research Brief`
- `Email Triage`
- `Follow-up Draft`
- `Daily Preparation`

`Target Device` options:

- explicit device
- `Best available executor`

### 5.1.2 Per-peer action row

Each trusted mesh-capable executor row should expose:

- `Ping`
- `Run Test Task`
- `Run On This Device`
- `Pause / Resume`
- `Rebind`

`Run On This Device` should prefill the composer with that device selected.

### 5.1.3 Owner Mesh Tasks timeline

Add a task list in `/mesh` with newest first.

Each task row should show:

- title / summary
- workflow type
- origin device
- assigned device
- current state
- approval state
- created time
- last update time
- result preview

States:

- `planned`
- `routed`
- `waiting_approval`
- `executing`
- `completed`
- `failed`

Actions per task:

- `View Details`
- `Retry`
- `Cancel`
- `Replay Result to Your Day`

## 5.2 `Your Day` becomes the operator cockpit

`Your Day` should remain the owner-facing daily surface.

Add a new section:

- `Delegated Work`

This section should show:

- tasks in progress
- completed delegated results
- approvals blocking delegated tasks

Example cards:

- `VERA prepared a research brief`
- `VERA drafted 3 email replies`
- `Approval needed before sending follow-ups`

### 5.2.1 Result replay

Delegated results should be replayed into `Your Day` as structured items, not raw logs.

Mapping examples:

- `Research Brief` -> `prepared_task`
- `Email Triage` -> `email_digest` + `draft_message`
- `Follow-up Draft` -> `draft_message`
- `Daily Preparation` -> `prepared_task` + schedule items where relevant

## 6. Workflow Packaging

## 6.1 Workflow shape

Each workflow should define:

- `workflow_type`
- `display_name`
- `prompt template`
- `required context`
- `approval policy`
- `expected result schema`
- `target item type for Your Day replay`

These should be lightweight definitions, not a full plugin system yet.

Recommended file:

- [mesh/workflows.py](/Users/frankkoine/aide/mesh/workflows.py)

### 6.1.1 `Research Brief`

Purpose:

- answer a bounded question with a concise result

Inputs:

- task prompt
- optional notes

Expected result:

- summary
- 3-7 bullets
- optional sources if web tools were used

Approval policy:

- none by default

### 6.1.2 `Email Triage`

Purpose:

- review a scoped set of inbox items and return prioritization

Inputs:

- account scope
- email IDs or time window

Expected result:

- urgent
- waiting
- draft candidates

Approval policy:

- sending disabled
- drafting allowed

### 6.1.3 `Follow-up Draft`

Purpose:

- draft follow-up communication for specified thread or contact

Inputs:

- thread context
- desired tone

Expected result:

- subject
- body
- rationale

Approval policy:

- send always requires approval

### 6.1.4 `Daily Preparation`

Purpose:

- prepare a daily readiness packet

Inputs:

- today’s calendar
- inbox summary
- reminders/tasks

Expected result:

- top priorities
- schedule risks
- recommended actions

Approval policy:

- none

## 7. API Additions

Build on existing `/api/mesh/peers/{device_id}/test-task`.

### 7.1 New endpoints

Add:

- `POST /api/mesh/tasks`
  - create and delegate a task
- `GET /api/mesh/tasks`
  - list recent owner-mesh tasks
- `GET /api/mesh/tasks/{task_id}`
  - task detail
- `POST /api/mesh/tasks/{task_id}/retry`
  - retry failed task
- `POST /api/mesh/tasks/{task_id}/cancel`
  - cancel queued or executing task where possible
- `POST /api/mesh/tasks/{task_id}/replay-to-your-day`
  - convert completed result into `Your Day` item(s)

### 7.2 Request shape

`POST /api/mesh/tasks`

```json
{
  "task": "Summarize unread priority emails from this morning",
  "workflow_type": "email_triage",
  "target_device_id": "optional-device-id",
  "requires_approval": false,
  "context_notes": "Focus on anything client-facing and urgent",
  "payload": {}
}
```

### 7.3 Response shape

```json
{
  "ok": true,
  "task": {
    "task_id": "mesh-task-123",
    "workflow_type": "email_triage",
    "state": "executing",
    "assigned_device_id": "device-id",
    "requires_approval": false
  }
}
```

## 8. Data Model

## 8.1 Keep the current task engine

Do not introduce a second task system.

Use [mesh/task_engine.py](/Users/frankkoine/aide/mesh/task_engine.py) as the canonical owner-mesh task lifecycle store.

Extend task payload with:

- `workflow_type`
- `requires_approval`
- `context_notes`
- `result_preview`
- `replayed_to_your_day`

### 8.1.1 Required state invariants

Every delegated task must have:

- `task_id`
- `origin_device_id`
- `assigned_device_id`
- `description`
- `state`
- `payload`
- `result`
- `timestamps`

## 8.2 Do not add a second approval system

Use the existing `SafetyGate`.

Delegated workflows that require approval should:

- create approval request through existing safety path
- update task state to `waiting_approval`
- resume through existing approval resolution path

## 9. Code Changes

## 9.1 `/mesh` web surface

Primary file:

- [interface/web.py](/Users/frankkoine/aide/interface/web.py)

Add:

- delegate work composer markup
- owner-mesh task timeline markup
- frontend handlers for create/retry/cancel/replay

## 9.2 Orchestration layer

Primary file:

- [mesh/orchestrator.py](/Users/frankkoine/aide/mesh/orchestrator.py)

Extend:

- accept `workflow_type`
- attach structured result metadata
- surface richer task state for UI

## 9.3 Coordinator layer

Primary file:

- [mesh/coordinator.py](/Users/frankkoine/aide/mesh/coordinator.py)

Extend:

- execution path to respect workflow package definitions
- produce structured results where workflow type is known

## 9.4 Workflow definitions

New file:

- [mesh/workflows.py](/Users/frankkoine/aide/mesh/workflows.py)

Add:

- lightweight workflow registry
- prompt shaping
- result formatting helpers

## 9.5 `Your Day` integration

Primary files:

- [interface/web.py](/Users/frankkoine/aide/interface/web.py)
- [daily_brief/schema.py](/Users/frankkoine/aide/daily_brief/schema.py)
- [daily_brief/storage.py](/Users/frankkoine/aide/daily_brief/storage.py)

Add:

- delegated-work section in payload
- replay of completed mesh tasks into current brief

## 10. Build Order

This should be implemented in four surgical tranches.

### Tranche 1: Delegation UX

Deliver:

- `/mesh` delegate work composer
- `POST /api/mesh/tasks`
- `GET /api/mesh/tasks`
- task timeline list

Acceptance:

- owner can delegate arbitrary text task to Android from `/mesh`
- task appears in timeline
- state updates to `executing` or `completed`

### Tranche 2: Workflow packages

Deliver:

- `mesh/workflows.py`
- 4 workflow definitions
- workflow type selector in composer
- structured result formatting

Acceptance:

- each workflow produces recognizably different output shape
- no generic unstructured blob for packaged workflows

### Tranche 3: `Your Day` replay

Deliver:

- replay completed delegated result into `Your Day`
- delegated work section
- task-to-brief item mapping

Acceptance:

- completed delegated tasks appear in `Your Day` as actionable items

### Tranche 4: Retry / cancel / approval visibility

Deliver:

- retry and cancel controls
- approval state in task list
- blocked task visibility

Acceptance:

- failed task can be retried
- approval-gated task is visibly blocked and resumable

## 11. Non-Goals

Do not build in this phase:

- free-form agent-to-agent owner chat
- device-specific memory replication system
- generalized plugin marketplace
- M-Peer workflow reuse
- complicated automation rules editor

These would slow the path to usefulness.

## 12. Success Criteria

This build is successful when:

1. The owner can delegate a real task from Mac to Android in one clear UI flow.
2. The owner can see task lifecycle without reading logs.
3. Results come back in structured form.
4. Delegated outputs appear in `Your Day`.
5. Approval-gated delegated work is visible and resumable.
6. The user no longer thinks in terms of ping/test-task as the main mesh experience.

## 13. Recommended Immediate Next Step

Start with `Tranche 1: Delegation UX`.

Reason:

- transport is now working
- delegated execution already exists
- this tranche turns infrastructure into product value fastest

The next coding step should be:

1. add `/api/mesh/tasks`
2. add `/api/mesh/tasks` list view
3. add `/mesh` delegate work composer
4. render recent mesh tasks in `/mesh`

That is the shortest path to a useful owner-mesh productivity loop.
