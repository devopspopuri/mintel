from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.consultant import ConsultantProfile
from app.models.operations import MockInterview
from app.models.training import TrainingProgram
from app.models.user import User, UserRole


def is_consultant_user(user: User | object | None) -> bool:
    role = getattr(user, "role", "")
    return getattr(role, "value", role) == UserRole.CONSULTANT.value


def consultant_profile_for_user(db: Session, user: User | object | None) -> ConsultantProfile | None:
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email:
        return None
    return db.scalar(select(ConsultantProfile).where(func.lower(ConsultantProfile.email) == email, ConsultantProfile.active.is_(True)))


def consultant_unlocks(profile: ConsultantProfile | None) -> dict[str, bool]:
    if not profile:
        return {
            "training": True,
            "training_program": False,
            "portal": False,
            "account": False,
            "onboarding": False,
            "positioning": False,
            "mock_interviews": False,
            "marketing_activity": False,
            "full_profile": False,
            "placement_support": False,
        }
    profile_ready = bool(profile.profile_intake_complete)
    basics_ready = bool(getattr(profile, "basics_prep_complete", False))
    training_ready = bool(basics_ready and profile.training_plan_assigned)
    project_ready = bool(profile.latest_project_updated or profile.project_story_validated)
    positioning_ready = bool(profile.resume_tailoring_complete and profile.project_story_validated)
    mock_ready = bool(profile.glossary_review_complete and profile.project_story_validated)
    marketing_ready = bool(profile.marketing_brief_ready and profile.mock_interview_passed)
    placed_ready = (getattr(profile, "marketing_status", "") or "") in {"placed", "post_placement"} or bool(getattr(profile, "placement_company", ""))
    return {
        "training": True,
        "training_program": training_ready,
        "portal": bool(profile_ready or basics_ready),
        "account": bool(profile_ready),
        "onboarding": bool(profile_ready and training_ready),
        "positioning": bool(project_ready or positioning_ready),
        "mock_interviews": mock_ready,
        "marketing_activity": marketing_ready,
        "full_profile": marketing_ready,
        "placement_support": placed_ready,
    }


def consultant_access_gate_plan(profile: ConsultantProfile | object | None) -> list[dict[str, Any]]:
    """Human-readable unlock plan for staff and consultant onboarding views."""
    if not profile:
        return [
            {
                "key": "basics",
                "label": "Basics Preparation",
                "unlocked": True,
                "status": "current",
                "reason": "Initial consultant access starts here.",
            }
        ]
    role_locked = bool(getattr(profile, "marketing_role_id", None))
    domain_locked = bool((getattr(profile, "target_industry_domain", "") or "").strip())
    profile_ready = bool(getattr(profile, "profile_intake_complete", False))
    basics_ready = bool(getattr(profile, "basics_prep_complete", False))
    training_ready = bool(basics_ready and getattr(profile, "training_plan_assigned", False) and role_locked and domain_locked)
    project_ready = bool(getattr(profile, "project_story_validated", False) and getattr(profile, "latest_project_updated", False))
    resume_ready = bool(getattr(profile, "resume_tailoring_complete", False))
    mock_ready = bool(getattr(profile, "glossary_review_complete", False) and project_ready)
    marketing_ready = bool(getattr(profile, "mock_interview_passed", False) and getattr(profile, "marketing_brief_ready", False))
    placed = bool((getattr(profile, "placement_company", "") or "").strip() or (getattr(profile, "marketing_status", "") or "") in {"placed", "post_placement"})
    gates = [
        ("basics", "Basics Preparation", True, "Initial access; foundation material is always visible."),
        ("role_lock", "Role And Domain Lock", role_locked and domain_locked, "Select one marketing role and one domain before role training opens."),
        ("training_program", "Assigned Training Program", training_ready, "Complete Basics and connect the matching role/domain training program."),
        ("onboarding", "Onboarding Checklist", profile_ready and training_ready, "Open after profile intake and assigned training are in place."),
        ("positioning", "Project And Resume Positioning", project_ready and resume_ready, "Open when project story and resume work are ready for staff review."),
        ("mock_interviews", "Mock Interviews", mock_ready, "Open after glossary and project story evidence are strong enough for interview practice."),
        ("marketing_activity", "Submissions And Campaigns", marketing_ready, "Open after mock pass and final evidence package approval."),
        ("placement_support", "Placement Support", placed, "Open after offer or placement information is recorded."),
    ]
    first_locked_seen = False
    plan = []
    for key, label, unlocked, reason in gates:
        status = "unlocked" if unlocked else "locked"
        if not unlocked and not first_locked_seen:
            status = "next"
            first_locked_seen = True
        plan.append({"key": key, "label": label, "unlocked": unlocked, "status": status, "reason": reason})
    return plan


def consultant_training_scope_matches(profile: ConsultantProfile | object | None, program: TrainingProgram | object | None) -> bool:
    if not profile or not program:
        return False
    role_id = getattr(profile, "marketing_role_id", None)
    profile_domain = (getattr(profile, "target_industry_domain", "") or "").strip().lower()
    program_domain = (getattr(program, "industry_domain", "") or "").strip().lower()
    return bool(
        getattr(profile, "basics_prep_complete", False)
        and getattr(profile, "training_plan_assigned", False)
        and getattr(program, "active", False)
        and role_id
        and role_id > 0
        and profile_domain
        and getattr(program, "marketing_role_id", None) == role_id
        and program_domain == profile_domain
    )


def consultant_nav_links(db: Session, user: User | object | None) -> list[dict[str, str]]:
    profile = consultant_profile_for_user(db, user)
    unlocks = consultant_unlocks(profile)
    links = [
        {"href": "/training-basics", "label": "Basics Preparation", "key": "training"},
    ]
    if unlocks["training_program"]:
        links.append({"href": "/training-programs", "label": "Training Programs", "key": "training_program"})
    if unlocks["portal"]:
        links.append({"href": "/landings/consultant", "label": "Consultant Portal", "key": "portal"})
    if profile and unlocks["onboarding"]:
        links.append({"href": f"/consultants/{profile.id}/onboarding", "label": "Onboarding", "key": "onboarding"})
        links.append({"href": f"/consultants/{profile.id}/journey", "label": "Readiness Checklist", "key": "onboarding"})
    if profile and unlocks["positioning"]:
        links.append({"href": f"/consultants/{profile.id}/positioning", "label": "Positioning", "key": "positioning"})
    if unlocks["mock_interviews"]:
        links.append({"href": "/mock-interviews", "label": "Mock Interviews", "key": "mock_interviews"})
    if unlocks["marketing_activity"]:
        links.append({"href": "/submissions", "label": "Submissions", "key": "marketing_activity"})
        links.append({"href": "/targeting-campaigns", "label": "Targeting Campaigns", "key": "marketing_activity"})
    if profile and unlocks["full_profile"]:
        links.append({"href": f"/consultants/{profile.id}", "label": "Profile", "key": "full_profile"})
    if profile and unlocks["placement_support"]:
        links.append({"href": f"/consultants/{profile.id}/journey", "label": "Post-Placement Support", "key": "placement_support"})
    return links


def consultant_allowed_path(path: str, user: User | object | None = None, db: Session | None = None) -> bool:
    if path in {"/", "/dashboard"}:
        return True
    if path.startswith("/training-basics"):
        return True
    if not db:
        return False
    profile = consultant_profile_for_user(db, user)
    unlocks = consultant_unlocks(profile)
    if not profile:
        return False
    if path.startswith("/training-programs"):
        if not unlocks["training_program"]:
            return False
        if path == "/training-programs":
            return True
        match = re.match(r"^/training-programs/(\d+)(?:/.*)?$", path)
        if not match:
            return False
        program = db.get(TrainingProgram, int(match.group(1)))
        return consultant_training_scope_matches(profile, program)
    if path == "/account":
        return unlocks["account"]
    if path == "/landings/consultant":
        return unlocks["portal"]
    if path == f"/consultants/{profile.id}/onboarding":
        return unlocks["onboarding"]
    if path == f"/consultants/{profile.id}/journey":
        return unlocks["onboarding"] or unlocks["placement_support"]
    if path == f"/consultants/{profile.id}/positioning":
        return unlocks["positioning"]
    if path == f"/consultants/{profile.id}":
        return unlocks["full_profile"]
    if path.startswith("/mock-interviews"):
        return unlocks["mock_interviews"] and _mock_path_belongs_to_consultant(path, db, profile.id)
    if path.startswith("/submissions") or path.startswith("/targeting-campaigns"):
        return unlocks["marketing_activity"]
    if path.startswith("/reports/candidate-company-matches"):
        return unlocks["marketing_activity"]
    return False


def _mock_path_belongs_to_consultant(path: str, db: Session, consultant_id: int) -> bool:
    if path in {"/mock-interviews", "/mock-interviews/export.csv"}:
        return True
    if path.startswith("/mock-interviews/consultant-availability"):
        return True
    match = re.match(r"^/mock-interviews/(\d+)(?:/(?:acknowledge|request))?$", path)
    if not match:
        return False
    mock_id = int(match.group(1))
    return bool(db.scalar(select(MockInterview.id).where(MockInterview.id == mock_id, MockInterview.consultant_id == consultant_id)))
