from __future__ import annotations

from typing import Any

from app.models.consultant import ConsultantProfile


MARKETING_READY_STATUSES = {"marketing_ready", "actively_marketing", "offer", "placed", "post_placement"}
ACTIVE_SUBMISSION_STATUSES = {"submitted", "client_review", "interview", "offer"}


def marketing_ready_context(consultant: ConsultantProfile) -> dict[str, Any]:
    gates = [
        {
            "key": "staff_owner",
            "label": "Assigned staff owner",
            "done": bool((getattr(consultant, "staff_owner", "") or "").strip()),
            "detail": "Every consultant must have one staff owner before active marketing.",
        },
        {
            "key": "resume_ready",
            "label": "Resume ready",
            "done": bool(getattr(consultant, "resume_tailoring_complete", False)),
            "detail": "Role-targeted resume is reviewed and ready for submissions.",
        },
        {
            "key": "project_proof",
            "label": "Project proof",
            "done": bool(getattr(consultant, "project_story_validated", False)),
            "detail": "Project scope, tools, decisions, and outcomes are explainable with clear evidence.",
        },
        {
            "key": "mock_passed",
            "label": "Mock passed",
            "done": bool(getattr(consultant, "mock_interview_passed", False)),
            "detail": "A mock interview for the target role has been passed.",
        },
        {
            "key": "final_evidence_package",
            "label": "Final evidence package",
            "done": bool(getattr(consultant, "marketing_brief_ready", False)),
            "detail": "Architecture, workflow, runbook, screenshots/outputs, stories, and resume bullets are ready.",
        },
    ]
    missing = [gate for gate in gates if not gate["done"]]
    return {
        "gates": gates,
        "missing": missing,
        "ready": not missing,
        "missing_labels": [gate["label"] for gate in missing],
    }


def user_can_mark_marketing_ready(user: object | None, consultant: ConsultantProfile) -> bool:
    role = str(getattr(getattr(user, "role", ""), "value", getattr(user, "role", "")) or "")
    if role == "admin":
        return True
    owner = (consultant.staff_owner or "").strip().lower()
    if not owner:
        return False
    user_values = {
        str(getattr(user, "email", "") or "").strip().lower(),
        str(getattr(user, "name", "") or "").strip().lower(),
        str(getattr(user, "username", "") or "").strip().lower(),
    }
    return owner in user_values


def company_pursue_context(pursuit: object, decision_readiness: dict[str, Any], company_jobs: list[object]) -> dict[str, Any]:
    audit = decision_readiness.get("json_audit") or {}
    role_signals = decision_readiness.get("role_signals") or []
    blockers = list(decision_readiness.get("blockers") or [])
    active_jobs = [job for job in company_jobs if getattr(job, "active", False)]
    checks = [
        {
            "key": "region",
            "label": "Region assigned",
            "done": bool(getattr(pursuit, "region_id", None)),
            "detail": "Company is assigned to a Mintel region.",
        },
        {
            "key": "owner",
            "label": "Company owner assigned",
            "done": bool((getattr(pursuit, "assigned_staff_email", "") or getattr(pursuit, "assigned_staff_name", "") or "").strip()),
            "detail": "One staff member owns the company, while region collaborators can contribute.",
        },
        {
            "key": "current_jobs",
            "label": "Current/recent job postings imported",
            "done": int(audit.get("stored_job_count") or 0) > 0 or bool(active_jobs),
            "detail": "USCIS alone can promote/watch/research, but Pursue Now needs job evidence.",
        },
        {
            "key": "role_fit",
            "label": "Mintel role fit found",
            "done": bool(role_signals),
            "detail": "At least one imported/current posting maps to a Mintel marketing role.",
        },
        {
            "key": "decision_safe",
            "label": "No hard decision blockers",
            "done": not blockers,
            "detail": "USA location, company match, role count, and USCIS evidence are decision-safe.",
        },
        {
            "key": "next_action",
            "label": "Next action set",
            "done": bool((getattr(pursuit, "next_action", "") or "").strip()),
            "detail": "Owner has a concrete next action after pursuing.",
        },
    ]
    missing = [check for check in checks if not check["done"]]
    return {
        "checks": checks,
        "missing": missing,
        "ready": not missing,
        "missing_labels": [check["label"] for check in missing],
    }


def submission_eligibility_context(consultant: ConsultantProfile, *, status: str, admin_override_reason: str = "", user: object | None = None) -> dict[str, Any]:
    status_value = (status or "").strip()
    ready = marketing_ready_context(consultant)
    needs_gate = status_value in ACTIVE_SUBMISSION_STATUSES
    role = str(getattr(getattr(user, "role", ""), "value", getattr(user, "role", "")) or "")
    override_allowed = role == "admin" and bool(admin_override_reason.strip())
    allowed = not needs_gate or ready["ready"] or override_allowed
    reason = ""
    if needs_gate and not ready["ready"]:
        reason = "Consultant is not Marketing Ready: " + ", ".join(ready["missing_labels"])
        if override_allowed:
            reason = "Admin override: " + admin_override_reason.strip()
    return {
        "allowed": allowed,
        "needs_gate": needs_gate,
        "marketing_ready": ready,
        "override_allowed": override_allowed,
        "reason": reason,
    }
