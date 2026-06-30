from __future__ import annotations

from mesh.peer_trust_scope import ContextPolicy, PeerTrustScope, SandboxMode


def default_scope_templates() -> list[PeerTrustScope]:
    return [
        PeerTrustScope(
            scope_id="research_partner",
            scope_name="Research Partner",
            description="Research and summarization help with explicit facts and public context only.",
            can_request_tasks=True,
            can_receive_results=True,
            can_send_updates=True,
            can_request_approvals=False,
            can_receive_context=ContextPolicy.EXPLICIT_ONLY,
            can_receive_memory=False,
            allowed_task_types=["research", "analysis", "summarization"],
            allowed_context_types=["explicit_facts", "public_context"],
            sandbox_mode=SandboxMode.STRICT,
            requires_local_approval=False,
            risk_level="low",
            is_template=True,
        ),
        PeerTrustScope(
            scope_id="calendar_coordination",
            scope_name="Calendar Coordination",
            description="Coordinate schedules and compare explicit availability windows.",
            can_request_tasks=True,
            can_receive_results=True,
            can_send_updates=True,
            can_request_approvals=True,
            can_receive_context=ContextPolicy.EXPLICIT_ONLY,
            can_receive_memory=False,
            allowed_task_types=["calendar_coordination", "scheduling", "availability_check"],
            allowed_context_types=["explicit_facts", "schedule_windows"],
            sandbox_mode=SandboxMode.STRICT,
            requires_local_approval=True,
            risk_level="medium",
            is_template=True,
        ),
        PeerTrustScope(
            scope_id="project_collaboration",
            scope_name="Project Collaboration",
            description="Scoped collaboration around project tasks and explicit project context.",
            can_request_tasks=True,
            can_receive_results=True,
            can_send_updates=True,
            can_request_approvals=True,
            can_receive_context=ContextPolicy.CONTEXTUAL,
            can_receive_memory=False,
            allowed_task_types=["project_coordination", "summarization", "analysis", "drafting"],
            allowed_context_types=["explicit_facts", "project_context"],
            sandbox_mode=SandboxMode.LIMITED,
            requires_local_approval=True,
            risk_level="medium",
            is_template=True,
        ),
        PeerTrustScope(
            scope_id="assistant_introduction",
            scope_name="Assistant Introduction",
            description="Minimal trust for introduction and capability discovery only.",
            can_request_tasks=True,
            can_receive_results=True,
            can_send_updates=False,
            can_request_approvals=False,
            can_receive_context=ContextPolicy.EXPLICIT_ONLY,
            can_receive_memory=False,
            allowed_task_types=["introduction", "capability_discovery"],
            allowed_context_types=["explicit_facts"],
            sandbox_mode=SandboxMode.STRICT,
            requires_local_approval=False,
            risk_level="low",
            is_template=True,
        ),
    ]


def get_template(template_id: str) -> PeerTrustScope | None:
    for template in default_scope_templates():
        if template.scope_id == template_id:
            return template
    return None
