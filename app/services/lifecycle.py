from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


@dataclass(frozen=True)
class LifecycleItem:
    kind: str
    title: str
    state: str
    state_label: str
    owner: str
    next_action: str
    risk: str
    risk_label: str
    age_days: int
    url: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "state_label": self.state_label,
            "owner": self.owner,
            "next_action": self.next_action,
            "risk": self.risk,
            "risk_label": self.risk_label,
            "age_days": self.age_days,
            "url": self.url,
            "detail": self.detail,
        }


COMPANY_STATE_LABELS = {
    "actionable": "Candidate-ready",
    "ready_to_promote": "USCIS source to promote",
    "needs_json": "Needs job intelligence",
    "needs_review": "Staff decision needed",
    "eliminated": "Eliminated first pass",
}

JOURNEY_STATE_LABELS = {
    "role_intake": "Role intake",
    "profile_intake": "Profile intake",
    "training_plan": "Basics and role training",
    "project_story": "Project story",
    "positioning": "Positioning",
    "interview_readiness": "Mock readiness",
    "final_evidence": "Evidence package",
    "company_matching": "Company matching",
    "campaign_active": "Marketing campaign active",
    "submission_pipeline": "Submission pipeline",
    "interview_pipeline": "Interview pipeline",
}


def build_lifecycle_backbone(
    company_rows: list[dict[str, Any]],
    journey_rows: list[dict[str, Any]],
    submission_rows: list[dict[str, Any]],
    mock_rows: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    items = [
        *[_company_lifecycle_item(row, today) for row in company_rows],
        *[_journey_lifecycle_item(row, today) for row in journey_rows],
        *[_submission_lifecycle_item(row, today) for row in submission_rows],
        *[_mock_lifecycle_item(row, today) for row in mock_rows],
    ]
    state_counts = Counter(item.state_label for item in items)
    kind_counts = Counter(item.kind for item in items)
    risk_counts = Counter(item.risk for item in items)
    risk_order = {"blocked": 0, "overdue": 1, "stale": 2, "unowned": 3, "attention": 4, "normal": 5, "ready": 6}
    risk_rows = sorted(
        [item for item in items if item.risk in {"blocked", "overdue", "stale", "unowned", "attention"}],
        key=lambda item: (risk_order.get(item.risk, 9), -item.age_days, item.kind, item.title.lower()),
    )
    state_rows = [
        {"state": state, "count": count}
        for state, count in sorted(state_counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    ]
    return {
        "items": [item.as_dict() for item in items],
        "risk_rows": [item.as_dict() for item in risk_rows[:12]],
        "state_rows": state_rows[:12],
        "summary": {
            "total": len(items),
            "companies": kind_counts["Company"],
            "consultants": kind_counts["Consultant"],
            "submissions": kind_counts["Submission"],
            "mocks": kind_counts["Mock"],
            "blocked": risk_counts["blocked"],
            "overdue": risk_counts["overdue"],
            "stale": risk_counts["stale"],
            "unowned": risk_counts["unowned"],
            "attention": risk_counts["attention"],
        },
    }


def _company_lifecycle_item(row: dict[str, Any], today: date) -> LifecycleItem:
    pursuit = row.get("pursuit")
    state = row.get("action_stage") or "needs_review"
    owner = row.get("assigned_staff") or row.get("suggested_staff") or row.get("suggested_staff_email") or "Unassigned"
    age_days = _age_days(getattr(pursuit, "updated_at", None) or getattr(row.get("company"), "updated_at", None), today)
    next_follow_up = getattr(pursuit, "next_follow_up_date", None) if pursuit else None
    risk = "ready"
    risk_label = "Ready"
    if state == "eliminated":
        risk, risk_label = "normal", "Eliminated"
    elif not pursuit or state == "ready_to_promote":
        risk, risk_label = "attention", "Promote"
    elif not (row.get("assigned_staff_email") or getattr(pursuit, "assigned_staff_email", "")):
        risk, risk_label = "unowned", "Needs owner"
    elif next_follow_up and next_follow_up < today:
        risk, risk_label = "overdue", "Follow-up overdue"
    elif state in {"needs_json", "needs_review"}:
        risk, risk_label = "attention", "Decision needed"
    elif age_days >= 21:
        risk, risk_label = "stale", "No recent movement"
    return LifecycleItem(
        kind="Company",
        title=row.get("company_name") or "Company",
        state=state,
        state_label=COMPANY_STATE_LABELS.get(state, state.replace("_", " ").title()),
        owner=owner,
        next_action=row.get("next_staff_action") or "Review company signal and choose the next action.",
        risk=risk,
        risk_label=risk_label,
        age_days=age_days,
        url=f"/pursuits/{pursuit.id}" if pursuit else f"/companies/{row.get('company_id')}/uscis",
        detail=f"{row.get('watch_score', 0)} score · {row.get('approvals', 0)} approvals · {row.get('region_name', 'Unmapped')}",
    )


def _journey_lifecycle_item(row: dict[str, Any], today: date) -> LifecycleItem:
    state = row.get("journey_stage") or "role_intake"
    owner = row.get("owner_label") or "Unassigned"
    readiness = int(row.get("readiness_score") or 0)
    risk = "normal"
    risk_label = "On track"
    if row.get("blocker_summary") or row.get("gaps_label"):
        risk, risk_label = "blocked", "Blocked"
    elif not owner or owner in {"Unassigned", "Owner not assigned"}:
        risk, risk_label = "unowned", "Needs owner"
    elif readiness < 50:
        risk, risk_label = "attention", "Readiness low"
    elif state in {"positioning", "interview_readiness", "company_matching"}:
        risk, risk_label = "attention", "Action needed"
    return LifecycleItem(
        kind="Consultant",
        title=row.get("consultant_name") or "Consultant",
        state=state,
        state_label=JOURNEY_STATE_LABELS.get(state, row.get("stage_label") or state.replace("_", " ").title()),
        owner=owner,
        next_action=row.get("next_action") or "Advance the consultant to the next checklist item.",
        risk=risk,
        risk_label=risk_label,
        age_days=_age_days(row.get("updated_at"), today),
        url=f"/consultants/{row.get('consultant_id')}/journey",
        detail=f"{row.get('target_role') or 'Role not set'} · {readiness} readiness",
    )


def _submission_lifecycle_item(row: dict[str, Any], today: date) -> LifecycleItem:
    submitted_on = row.get("submitted_on")
    age_days = (today - submitted_on).days if isinstance(submitted_on, date) else 0
    status = row.get("status") or "Submitted"
    risk = "overdue" if age_days >= 7 and status not in {"Offer", "Interview"} else "attention"
    risk_label = "Follow-up overdue" if risk == "overdue" else "Needs follow-up"
    return LifecycleItem(
        kind="Submission",
        title=f"{row.get('consultant_name') or 'Consultant'} · {row.get('company_name') or 'Company'}",
        state=status.lower().replace(" ", "_"),
        state_label=status,
        owner="Recruiter / marketer",
        next_action=row.get("next_step") or "Record next step and follow up.",
        risk=risk,
        risk_label=risk_label,
        age_days=age_days,
        url=f"/consultants/{row.get('consultant_id')}",
        detail=row.get("job_title") or "",
    )


def _mock_lifecycle_item(row: dict[str, Any], today: date) -> LifecycleItem:
    scheduled_on = row.get("scheduled_on")
    status = row.get("status") or "Mock"
    age_days = (today - scheduled_on).days if isinstance(scheduled_on, date) and scheduled_on <= today else 0
    if status in {"Waiting Feedback", "Needs Work"}:
        risk, risk_label = "blocked", status
    elif row.get("needs_attention"):
        risk, risk_label = "attention", "Needs action"
    else:
        risk, risk_label = "normal", "Scheduled"
    return LifecycleItem(
        kind="Mock",
        title=row.get("consultant_name") or "Mock interview",
        state=status.lower().replace(" ", "_"),
        state_label=status,
        owner="Trainer / interviewer",
        next_action=row.get("next_action") or "Prepare or close mock feedback.",
        risk=risk,
        risk_label=risk_label,
        age_days=age_days,
        url=f"/mock-interviews/{row.get('id')}",
        detail=row.get("role") or "",
    )


def _age_days(value: Any, today: date) -> int:
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        return max(0, (today - current.date()).days)
    if isinstance(value, date):
        return max(0, (today - value).days)
    return 0
