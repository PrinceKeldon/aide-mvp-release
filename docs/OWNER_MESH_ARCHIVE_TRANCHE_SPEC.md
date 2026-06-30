# Owner Mesh Archive Tranche Spec

Last updated: April 8, 2026

## 1. Objective

Add a simple archive system for `Owner Mesh Tasks` so the live execution view stays operational when task history grows.

This tranche should:

- reduce clutter in the active `/mesh` task view
- archive older finished work automatically
- group archived work into simple drawers
- allow owner-controlled backup-before-delete

This tranche should **not** introduce:

- cross-device backup shipping
- import/restore tooling
- advanced search or tagging
- a second task store

## 2. First-Principles Constraints

- active work must stay easy to scan
- archive behavior must be understandable without documentation
- destructive actions must always be guarded
- backup data should be plain JSON metadata

## 3. Product Model

Split owner-mesh history into two surfaces:

1. `Active Tasks`
   - planned
   - routed
   - waiting approval
   - executing
   - recently completed

2. `Archive`
   - older completed tasks
   - failed tasks
   - denied tasks
   - cancelled tasks

The active surface remains the execution console.
The archive becomes the retrieval and cleanup surface.

## 4. Auto-Archive Rules

Initial rules should stay simple:

- `completed` tasks move to archive after a short retention window
- `failed`, `denied`, and `cancelled` tasks move to archive after a longer retention window
- replaying a task into `Your Day` may make it eligible for earlier archive, but should not be required

Suggested defaults:

- `completed`: archive after 48 hours
- `failed`, `denied`, `cancelled`: archive after 7 days

These values should be constants first, not user settings.

## 5. Archive Drawers

Only two archive groupings should ship first.

### 5.1 By Workflow

- Generic Task
- Research Brief
- Email Triage
- Follow-up Draft
- Daily Preparation

### 5.2 By Device

- tasks delegated to Android / VERA
- tasks delegated to Mac / Vera Desk
- tasks delegated to other owner devices

The UI can present these as collapsible drawers with counts.

Do not add nested taxonomy or freeform labels in this tranche.

## 6. Deletion and Backup

Archive deletion must be owner-only and backup-first.

### 6.1 Backup format

Generate a JSON export containing:

- task metadata
- workflow type
- origin device
- assigned device
- state history where available
- timestamps
- approval state
- replay status
- result preview
- result body

### 6.2 Delete flow

Use a two-step flow:

1. `Backup & Prepare Delete`
2. `Confirm Delete`

When the owner requests deletion:

- generate the JSON backup
- make it downloadable immediately
- only then expose final delete confirmation

Do not perform archive deletion in one blind click.

### 6.3 Deferred scope

Do not automatically send backups to another device in this tranche.

That adds delivery guarantees, storage policy, and ownership questions that are not needed yet.

## 7. Data Model

Keep using [mesh/task_engine.py](/Users/frankkoine/aide/mesh/task_engine.py) as the canonical store.

Add minimal archive-related metadata:

- `archived_at`
- `archive_bucket`
- `archive_reason`
- `backup_exported_at`

Do not build a second archive database.

## 8. API Additions

Proposed endpoints:

- `GET /api/mesh/tasks/archive`
- `POST /api/mesh/tasks/{task_id}/archive`
- `POST /api/mesh/tasks/archive/backup`
- `POST /api/mesh/tasks/archive/delete`

Suggested response shapes:

- archive list grouped by workflow and device
- backup endpoint returns metadata plus file payload/download URL
- delete endpoint returns deleted counts and backup reference

## 9. UI Changes

Primary file:

- [interface/web.py](/Users/frankkoine/aide/interface/web.py)

Add:

- `Archive` panel below active task controls
- drawer toggle for `By Workflow`
- drawer toggle for `By Device`
- archive counts
- backup-and-delete controls

Keep archive collapsed by default.

## 10. Build Order

### Step 1

- add archive fields to task records
- implement auto-archive classification

### Step 2

- add archive API
- render archive drawers in `/mesh`

### Step 3

- add JSON backup export
- add guarded delete flow

## 11. Acceptance Criteria

This tranche is successful when:

1. the active `Owner Mesh Tasks` panel stays readable during normal use
2. completed and stale tasks move into archive automatically
3. archive can be browsed by workflow and by device
4. deleting archive data always produces a downloadable JSON backup first
5. the owner can clear archive history without touching active task state
