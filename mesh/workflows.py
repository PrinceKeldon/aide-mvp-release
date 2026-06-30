"""
AIDE Owner Mesh workflow definitions.

This is intentionally small. It shapes delegated prompts and result packaging
for the first owner-mesh productivity workflows without introducing a second
execution system.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MeshWorkflowDefinition:
    workflow_type: str
    display_name: str
    result_kind: str
    approval_hint: str
    output_contract: str


_GENERIC_WORKFLOW = MeshWorkflowDefinition(
    workflow_type="generic_task",
    display_name="Generic Task",
    result_kind="mesh_result",
    approval_hint="No special approval policy.",
    output_contract="Return a direct, useful result.",
)


_WORKFLOWS: dict[str, MeshWorkflowDefinition] = {
    "generic_task": _GENERIC_WORKFLOW,
    "research_brief": MeshWorkflowDefinition(
        workflow_type="research_brief",
        display_name="Research Brief",
        result_kind="research_brief",
        approval_hint="No approval required by default.",
        output_contract=(
            "Return markdown with these exact sections:\n"
            "## Summary\n"
            "## Key Points\n"
            "## Recommended Next Step\n"
            "## Sources Used (only if tools or external sources were actually used)"
        ),
    ),
    "email_triage": MeshWorkflowDefinition(
        workflow_type="email_triage",
        display_name="Email Triage",
        result_kind="email_digest",
        approval_hint="Drafting is allowed. Sending is not allowed.",
        output_contract=(
            "Return markdown with these exact sections:\n"
            "## Urgent\n"
            "## Waiting\n"
            "## Draft Candidates\n"
            "## Notes"
        ),
    ),
    "follow_up_draft": MeshWorkflowDefinition(
        workflow_type="follow_up_draft",
        display_name="Follow-up Draft",
        result_kind="draft_message",
        approval_hint="Sending always requires approval.",
        output_contract=(
            "Return markdown with these exact sections:\n"
            "## Subject\n"
            "## Draft Body\n"
            "## Rationale\n"
            "## Approval Notes"
        ),
    ),
    "daily_preparation": MeshWorkflowDefinition(
        workflow_type="daily_preparation",
        display_name="Daily Preparation",
        result_kind="prepared_task",
        approval_hint="No approval required by default.",
        output_contract=(
            "Return markdown with these exact sections:\n"
            "## Top Priorities\n"
            "## Schedule Risks\n"
            "## Recommended Actions\n"
            "## Preparation Notes"
        ),
    ),
}


def normalize_workflow_type(workflow_type: str | None) -> str:
    key = (workflow_type or "generic_task").strip().lower()
    return key if key in _WORKFLOWS else "generic_task"


def get_workflow_definition(workflow_type: str | None) -> MeshWorkflowDefinition:
    return _WORKFLOWS.get(normalize_workflow_type(workflow_type), _GENERIC_WORKFLOW)


def build_execution_prompt(
    workflow_type: str | None,
    task: str,
    *,
    context_notes: str | None = None,
    requires_approval: bool = False,
) -> str:
    definition = get_workflow_definition(workflow_type)
    task = (task or "").strip()
    context_notes = (context_notes or "").strip()

    if definition.workflow_type == "generic_task":
        if not context_notes:
            return task
        return f"{task}\n\nContext notes:\n{context_notes}"

    sections = [
        f"Workflow: {definition.display_name}",
        f"Task:\n{task}",
    ]
    if context_notes:
        sections.append(f"Context notes:\n{context_notes}")
    sections.append(f"Approval policy:\n{definition.approval_hint}")
    if requires_approval:
        sections.append("Execution note:\nThe owner explicitly requested approval-aware handling.")
    sections.append(f"Output contract:\n{definition.output_contract}")
    sections.append("Return only the requested markdown output.")
    return "\n\n".join(sections)


def format_workflow_result(
    workflow_type: str | None,
    task: str,
    raw_result: str,
    *,
    context_notes: str | None = None,
) -> dict[str, str]:
    definition = get_workflow_definition(workflow_type)
    task = (task or "").strip()
    context_notes = (context_notes or "").strip()
    body = (raw_result or "").strip() or "No result returned."

    if definition.workflow_type == "generic_task":
        preview = _preview_text(body)
        return {
            "result": body,
            "result_preview": preview,
            "result_kind": definition.result_kind,
            "workflow_type": definition.workflow_type,
            "workflow_display_name": definition.display_name,
        }

    lines = [
        f"# {definition.display_name}",
        "",
        "## Request",
        task,
    ]
    if context_notes:
        lines.extend(["", "## Context Notes", context_notes])
    lines.extend(["", body])
    formatted = "\n".join(lines).strip()
    return {
        "result": formatted,
        "result_preview": _preview_text(body),
        "result_kind": definition.result_kind,
        "workflow_type": definition.workflow_type,
        "workflow_display_name": definition.display_name,
    }


def _preview_text(text: str, limit: int = 180) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
