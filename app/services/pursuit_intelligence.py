from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.company import CompanyPursuit
from app.models.job import JobOpportunity
from app.models.pursuit_intelligence import (
    PursuitActivity,
    PursuitC2CManager,
    PursuitContact,
    PursuitEvidence,
    PursuitIntelligenceSnapshot,
    PursuitJobPostingEvidence,
    PursuitNote,
    PursuitPrimeVendor,
    PursuitRequirement,
    PursuitResearchJob,
    PursuitTechnology,
)
from app.services.companies import normalize_company_name
from app.services.marketing_roles import classify_marketing_role


def activity(db: Session, pursuit_id: int, actor: str, activity_type: str, summary: str, due_at: str | None = None) -> None:
    db.add(PursuitActivity(pursuit_id=pursuit_id, actor=actor, activity_type=activity_type, summary=summary, due_at=due_at))
    pursuit = db.get(CompanyPursuit, pursuit_id)
    if pursuit:
        pursuit.last_activity_at = datetime.now(timezone.utc)


def structured_context(db: Session, pursuit_id: int) -> dict[str, list[Any]]:
    return {
        "requirements": db.scalars(select(PursuitRequirement).where(PursuitRequirement.pursuit_id == pursuit_id).order_by(PursuitRequirement.created_at.desc())).all(),
        "technologies": db.scalars(select(PursuitTechnology).where(PursuitTechnology.pursuit_id == pursuit_id).order_by(PursuitTechnology.category, PursuitTechnology.name)).all(),
        "intelligence_snapshots": db.scalars(select(PursuitIntelligenceSnapshot).where(PursuitIntelligenceSnapshot.pursuit_id == pursuit_id).order_by(PursuitIntelligenceSnapshot.created_at.desc()).limit(5)).all(),
        "job_postings": db.scalars(select(PursuitJobPostingEvidence).where(PursuitJobPostingEvidence.pursuit_id == pursuit_id, PursuitJobPostingEvidence.included.is_(True)).order_by(PursuitJobPostingEvidence.primary_role_slug, PursuitJobPostingEvidence.job_title)).all(),
        "excluded_job_postings": db.scalars(select(PursuitJobPostingEvidence).where(PursuitJobPostingEvidence.pursuit_id == pursuit_id, PursuitJobPostingEvidence.included.is_(False)).order_by(PursuitJobPostingEvidence.exclusion_group, PursuitJobPostingEvidence.job_title)).all(),
        "contacts": db.scalars(select(PursuitContact).where(PursuitContact.pursuit_id == pursuit_id).order_by(PursuitContact.name)).all(),
        "vendors": db.scalars(select(PursuitPrimeVendor).where(PursuitPrimeVendor.pursuit_id == pursuit_id).order_by(PursuitPrimeVendor.vendor_name)).all(),
        "managers": db.scalars(select(PursuitC2CManager).where(PursuitC2CManager.pursuit_id == pursuit_id).order_by(PursuitC2CManager.name)).all(),
        "evidence": db.scalars(select(PursuitEvidence).where(PursuitEvidence.pursuit_id == pursuit_id).order_by(PursuitEvidence.kind, PursuitEvidence.label)).all(),
        "notes": db.scalars(select(PursuitNote).where(PursuitNote.pursuit_id == pursuit_id, PursuitNote.active.is_(True)).order_by(PursuitNote.pinned.desc(), PursuitNote.created_at.desc())).all(),
        "activities": db.scalars(select(PursuitActivity).where(PursuitActivity.pursuit_id == pursuit_id).order_by(PursuitActivity.created_at.desc()).limit(25)).all(),
        "jobs": db.scalars(select(PursuitResearchJob).where(PursuitResearchJob.pursuit_id == pursuit_id).order_by(PursuitResearchJob.created_at.desc()).limit(10)).all(),
    }


def decision_readiness_context(
    pursuit: CompanyPursuit,
    company_context: dict[str, Any],
    structured: dict[str, list[Any]],
    company_jobs: list[JobOpportunity],
) -> dict[str, Any]:
    snapshots = structured.get("intelligence_snapshots", [])
    latest_snapshot = snapshots[0] if snapshots else None
    imported_jobs = structured.get("job_postings", [])
    excluded_jobs = structured.get("excluded_job_postings", [])
    totals = company_context.get("totals") or {}
    approval_count = _int(totals.get("approvals"))
    denial_count = _int(totals.get("denials"))
    total_decisions = approval_count + denial_count
    eligible_jobs = len(imported_jobs)
    verified_jobs = latest_snapshot.verified_below_8_year_usa_jobs if latest_snapshot else 0
    estimated_jobs = latest_snapshot.estimated_below_8_year_usa_jobs if latest_snapshot else 0
    official_job_urls = len([job for job in imported_jobs if job.official_job_url])
    usa_confirmed_jobs = len([job for job in imported_jobs if job.usa_location_confirmed])
    role_counts = _loads(latest_snapshot.role_counts_json, {}) if latest_snapshot else {}
    role_total = sum(_int(row.get("total_eligible_usa_signal")) for row in role_counts.values() if isinstance(row, dict))
    company_name_match = _company_name_matches(pursuit.company.name, latest_snapshot.company_name if latest_snapshot else "")
    role_signals = _role_signals(role_counts, imported_jobs)
    top_jobs = sorted(imported_jobs, key=lambda job: (job.confidence_score, bool(job.official_job_url), job.job_title), reverse=True)[:3]

    warnings: list[str] = []
    blockers: list[str] = []
    if not total_decisions:
        blockers.append("No USCIS approval or denial history is available for this company. Verify the company alias before using imported job evidence.")
    if not latest_snapshot:
        blockers.append("No imported JSON intelligence snapshot is available yet.")
    if latest_snapshot and not company_name_match:
        warnings.append("Imported company name does not clearly match the USCIS company name. Review aliases before making a decision.")
    if latest_snapshot and latest_snapshot.total_eligible_usa_job_signal != eligible_jobs:
        warnings.append("Imported total eligible job signal does not match the number of counted job records stored in Mintel.")
    if role_total and role_total != eligible_jobs:
        warnings.append("Role-count totals do not match counted job records. Review role classification before using the counts.")
    if imported_jobs and usa_confirmed_jobs != eligible_jobs:
        blockers.append("One or more counted jobs do not have confirmed USA location evidence.")
    if imported_jobs and official_job_urls != eligible_jobs:
        warnings.append("One or more counted jobs are missing an official job URL.")
    if latest_snapshot and not latest_snapshot.is_full_window_coverage:
        warnings.append("The imported evidence is not full last-12-month coverage; treat counts as a minimum current/recent public signal.")
    if excluded_jobs:
        warnings.append(f"{len(excluded_jobs)} excluded job(s) need staff awareness before final approval.")
    if not role_signals:
        blockers.append("No role-level job signal is available for MINTEL marketing roles.")

    suggested_decision, rating, rationale = _suggest_decision(approval_count, eligible_jobs, verified_jobs, estimated_jobs, latest_snapshot, blockers)
    next_actions = _readiness_next_actions(pursuit, latest_snapshot, blockers, warnings, company_jobs, imported_jobs)
    score = _readiness_score(
        approval_count=approval_count,
        eligible_jobs=eligible_jobs,
        official_job_urls=official_job_urls,
        usa_confirmed_jobs=usa_confirmed_jobs,
        latest_snapshot=latest_snapshot,
        role_signals=role_signals,
        blockers=blockers,
        warnings=warnings,
        pursuit=pursuit,
    )
    return {
        "score": score,
        "ready_for_admin_review": score >= 80 and not blockers and bool(pursuit.decision),
        "suggested_decision": suggested_decision,
        "staff_decision": pursuit.decision,
        "rating": rating,
        "rationale": rationale,
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions,
        "uscis": {
            "approvals": approval_count,
            "denials": denial_count,
            "approval_rate": totals.get("approval_rate", 0),
            "new_employment": _int(totals.get("new_employment")),
            "change_employer": _int(totals.get("change_employer")),
            "continuation": _int(totals.get("continuation")),
            "total_decisions": total_decisions,
            "top_locations": _top_uscis_locations(company_context.get("locations") or []),
            "yearly": company_context.get("yearly") or [],
        },
        "json_audit": {
            "has_snapshot": bool(latest_snapshot),
            "company_name_match": company_name_match if latest_snapshot else None,
            "imported_company_name": latest_snapshot.company_name if latest_snapshot else "",
            "stored_job_count": eligible_jobs,
            "reported_job_signal": latest_snapshot.total_eligible_usa_job_signal if latest_snapshot else 0,
            "role_count_total": role_total,
            "official_job_urls": official_job_urls,
            "usa_confirmed_jobs": usa_confirmed_jobs,
            "excluded_jobs": len(excluded_jobs),
            "actual_evidence_window": latest_snapshot.actual_evidence_window if latest_snapshot else "",
            "full_window_coverage": bool(latest_snapshot and latest_snapshot.is_full_window_coverage),
        },
        "role_signals": role_signals,
        "top_jobs": top_jobs,
    }


def consolidated_tech_stack_context(structured: dict[str, list[Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, dict[str, Any]]] = {label: {} for label in _TECH_CATEGORY_ORDER}
    latest_snapshot = (structured.get("intelligence_snapshots") or [None])[0]
    if latest_snapshot:
        summary = _loads(latest_snapshot.company_tech_stack_summary_json, {})
        if isinstance(summary, dict):
            for category, values in summary.items():
                for value in _as_list(values):
                    _add_consolidated_tech(buckets, category, _str(value), "company summary")
        role_stack = _loads(latest_snapshot.role_wise_tech_stack_json, {})
        if isinstance(role_stack, dict):
            for values in role_stack.values():
                for value in _as_list(values):
                    _add_consolidated_tech(buckets, "role_wise_tech_stack", _str(value), "role signal")

    for job in structured.get("job_postings", []):
        stack = _loads(job.extracted_tech_stack_json, {})
        if isinstance(stack, dict):
            for category, values in stack.items():
                for value in _as_list(values):
                    _add_consolidated_tech(buckets, category, _str(value), "job posting", job.primary_marketing_role)
        for value in _as_list(_loads(job.technology_signals_json, [])):
            _add_consolidated_tech(buckets, "technology_signals", _str(value), "job signal", job.primary_marketing_role)

    for item in structured.get("technologies", []):
        _add_consolidated_tech(buckets, item.category, item.name, item.confidence or "structured evidence")

    grouped: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    for category in _TECH_CATEGORY_ORDER:
        technologies = sorted(
            buckets[category].values(),
            key=lambda row: (-row["posting_count"], row["name"].lower()),
        )
        if not technologies:
            continue
        grouped.append({"category": category, "technologies": technologies})
        flat.extend({"category": category, **row} for row in technologies)
    featured = sorted(
        flat,
        key=lambda row: (-row["posting_count"], 0 if row["roles"] else 1, row["name"].lower()),
    )[:12]
    categories = [
        {
            "category": group["category"],
            "count": len(group["technologies"]),
            "posting_support": sum(_int(row["posting_count"]) for row in group["technologies"]),
            "top": group["technologies"][:4],
        }
        for group in grouped
    ]
    role_map: dict[str, dict[str, Any]] = {}
    for row in flat:
        for role in row["roles"]:
            role_row = role_map.setdefault(role, {"role": role, "technologies": [], "posting_support": 0})
            role_row["technologies"].append(row)
            role_row["posting_support"] += _int(row["posting_count"])
    role_groups = sorted(role_map.values(), key=lambda row: (-row["posting_support"], row["role"].lower()))
    context = {
        "grouped": grouped,
        "flat": flat,
        "featured": featured,
        "categories": categories,
        "role_groups": role_groups,
        "total": len(flat),
        "core": flat[:20],
    }
    return _serializable_consolidated_tech(context)


def job_posting_review_context(structured: dict[str, list[Any]]) -> dict[str, Any]:
    jobs = list(structured.get("job_postings") or [])
    excluded = list(structured.get("excluded_job_postings") or [])
    sorted_jobs = sorted(
        jobs,
        key=lambda job: (
            -_int(getattr(job, "confidence_score", 0)),
            0 if getattr(job, "official_job_url", "") else 1,
            _str(getattr(job, "published_date", "")),
            _str(getattr(job, "job_title", "")).lower(),
        ),
    )
    role_map: dict[str, dict[str, Any]] = {}
    for job in sorted_jobs:
        role_name = _str(job.primary_marketing_role) or "Unclassified"
        row = role_map.setdefault(
            role_name,
            {
                "name": role_name,
                "slug": _str(job.primary_role_slug),
                "total": 0,
                "verified": 0,
                "estimated": 0,
                "official_urls": 0,
                "top_jobs": [],
            },
        )
        row["total"] += 1
        if job.experience_evidence_type == "verified_experience_below_8":
            row["verified"] += 1
        elif job.experience_evidence_type == "estimated_experience_below_8":
            row["estimated"] += 1
        if job.official_job_url:
            row["official_urls"] += 1
        if len(row["top_jobs"]) < 3:
            row["top_jobs"].append(job)

    tech_counts: dict[str, int] = {}
    for job in sorted_jobs:
        for value in _as_list(_loads(job.technology_signals_json, [])):
            name = _clean_tech_name(_str(value))
            if name:
                tech_counts[name] = tech_counts.get(name, 0) + 1

    official_count = len([job for job in jobs if job.official_job_url])
    usa_count = len([job for job in jobs if job.usa_location_confirmed])
    verified_count = len([job for job in jobs if job.experience_evidence_type == "verified_experience_below_8"])
    estimated_count = len([job for job in jobs if job.experience_evidence_type == "estimated_experience_below_8"])
    confidence_values = [_int(job.confidence_score) for job in jobs if _int(job.confidence_score)]
    return {
        "jobs": sorted_jobs,
        "excluded": excluded,
        "total": len(jobs),
        "verified": verified_count,
        "estimated": estimated_count,
        "official_urls": official_count,
        "usa_confirmed": usa_count,
        "excluded_count": len(excluded),
        "average_confidence": round(sum(confidence_values) / len(confidence_values)) if confidence_values else 0,
        "role_groups": sorted(role_map.values(), key=lambda row: (-row["total"], row["name"].lower())),
        "top_technologies": [
            {"name": name, "count": count}
            for name, count in sorted(tech_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:12]
        ],
    }


def ingest_research_json(db: Session, pursuit: CompanyPursuit, raw_json: str, actor: str = "system") -> dict[str, int]:
    payload = _parse_json(raw_json)
    counts = {"requirements": 0, "technologies": 0, "contacts": 0, "vendors": 0, "managers": 0, "evidence": 0, "jobs": 0, "excluded_jobs": 0, "snapshots": 0}
    pursuit_id = pursuit.id
    seen_technologies: set[tuple[str, str]] = set()

    for model in (PursuitRequirement, PursuitTechnology, PursuitContact, PursuitPrimeVendor, PursuitC2CManager, PursuitEvidence, PursuitJobPostingEvidence):
        db.execute(delete(model).where(model.pursuit_id == pursuit_id))

    snapshot = _snapshot_from_payload(pursuit_id, payload, raw_json, actor)
    db.add(snapshot)
    db.flush()
    counts["snapshots"] = 1
    _update_company_from_payload(pursuit, payload)

    for item in _as_list(payload.get("jobs")):
        job = _job_evidence_from_payload(pursuit_id, snapshot.id, item, included=True)
        db.add(job)
        counts["jobs"] += 1
        counts["evidence"] += _add_evidence(db, pursuit_id, "job_posting", job.job_title, job.official_job_url, _score_confidence(job.confidence_score))
        counts["technologies"] += _add_job_technologies(db, pursuit_id, job, seen_technologies)
        _add_requirement_from_job(db, pursuit_id, item, job)
        counts["requirements"] += 1

    for group_name in (
        "excluded_jobs_due_to_location",
        "excluded_jobs_due_to_experience_or_seniority",
        "excluded_jobs_due_to_role_mismatch",
        "excluded_jobs_due_to_missing_url",
        "excluded_jobs_due_to_duplicate",
    ):
        for item in _as_list(payload.get(group_name)):
            job = _job_evidence_from_payload(pursuit_id, snapshot.id, item, included=False, exclusion_group=group_name)
            db.add(job)
            counts["excluded_jobs"] += 1

    for item in _as_list(payload.get("recent_requirements")):
        role = classify_marketing_role(db, f"{_str(item.get('title'))} {' '.join(_as_list(item.get('technologies')))} {_str(item.get('sponsorship_or_work_auth_language'))}")
        db.add(
            PursuitRequirement(
                pursuit_id=pursuit_id,
                marketing_role_id=role.id if role else None,
                title=_str(item.get("title")),
                location=_str(item.get("location")),
                posted_or_seen_date=_str(item.get("posted_or_seen_date")),
                employment_type=_str(item.get("employment_type")),
                technologies=", ".join(_as_list(item.get("technologies"))),
                work_auth_language=_str(item.get("sponsorship_or_work_auth_language")),
                source_url=_str(item.get("source_url")),
                confidence=_confidence(item),
            )
        )
        counts["requirements"] += 1
        counts["evidence"] += _add_evidence(db, pursuit_id, "requirement", _str(item.get("title")), _str(item.get("source_url")), _confidence(item))

    stack = payload.get("technology_stack") or {}
    if isinstance(stack, dict):
        for category, values in stack.items():
            for value in _as_list(values):
                counts["technologies"] += _add_technology(db, pursuit_id, category, _str(value), "", "medium", seen_technologies)

    tech_summary = payload.get("company_tech_stack_summary") or {}
    if isinstance(tech_summary, dict):
        for category, values in tech_summary.items():
            for value in _as_list(values):
                counts["technologies"] += _add_technology(db, pursuit_id, category, _str(value), "Imported from company tech stack summary", "medium", seen_technologies)

    role_wise_tech_stack = payload.get("role_wise_tech_stack") if isinstance(payload.get("role_wise_tech_stack"), dict) else {}
    for category, values in role_wise_tech_stack.items():
        for value in _as_list(values):
            counts["technologies"] += _add_technology(db, pursuit_id, f"role:{category}", _str(value), "Imported from role-wise tech stack", "medium", seen_technologies)

    for item in _as_list(payload.get("company_contacts")):
        db.add(
            PursuitContact(
                pursuit_id=pursuit_id,
                name=_str(item.get("name")),
                title=_str(item.get("title")),
                department=_str(item.get("department")),
                location=_str(item.get("location")),
                email=_str(item.get("email")),
                phone=_str(item.get("phone")),
                linkedin_url=_str(item.get("linkedin_url")),
                source_url=_str(item.get("source_url")),
                confidence=_confidence(item),
            )
        )
        counts["contacts"] += 1
        counts["evidence"] += _add_evidence(db, pursuit_id, "contact", _str(item.get("name")), _str(item.get("source_url") or item.get("linkedin_url")), _confidence(item))

    for item in _as_list(payload.get("prime_vendors")):
        db.add(
            PursuitPrimeVendor(
                pursuit_id=pursuit_id,
                vendor_name=_str(item.get("vendor_name")),
                relationship_evidence=_str(item.get("relationship_evidence")),
                technology_or_role_focus=_str(item.get("technology_or_role_focus")),
                source_url=_str(item.get("source_url")),
                confidence=_confidence(item),
            )
        )
        counts["vendors"] += 1
        counts["evidence"] += _add_evidence(db, pursuit_id, "prime_vendor", _str(item.get("vendor_name")), _str(item.get("source_url")), _confidence(item))

    for item in _as_list(payload.get("c2c_managers")):
        db.add(
            PursuitC2CManager(
                pursuit_id=pursuit_id,
                name=_str(item.get("name")),
                company_or_vendor=_str(item.get("company_or_vendor")),
                title=_str(item.get("title")),
                role_focus=_str(item.get("role_focus")),
                linkedin_url=_str(item.get("linkedin_url")),
                source_url=_str(item.get("source_url")),
                confidence=_confidence(item),
            )
        )
        counts["managers"] += 1
        counts["evidence"] += _add_evidence(db, pursuit_id, "c2c_manager", _str(item.get("name")), _str(item.get("source_url") or item.get("linkedin_url")), _confidence(item))

    submission = payload.get("submission_intelligence")
    recommendation = payload.get("pursuit_recommendation")
    mintel_recommendation = payload.get("mintel_training_recommendation")
    if isinstance(mintel_recommendation, dict):
        pursuit.submission_intelligence = _format_recommendation_text(mintel_recommendation)
    elif isinstance(submission, dict):
        pursuit.submission_intelligence = _format_grouped_text(submission)
    if isinstance(recommendation, dict):
        pursuit.decision = _str(recommendation.get("decision"))
        if isinstance(recommendation.get("priority"), int):
            pursuit.priority = recommendation["priority"]
        pursuit.research_summary = _str(recommendation.get("reason"))
        next_actions = recommendation.get("next_actions")
        if next_actions:
            pursuit.next_action = "\n".join(_str(item) for item in _as_list(next_actions))
    elif isinstance(mintel_recommendation, dict):
        pursuit.research_summary = _str(payload.get("data_quality_notes") or payload.get("coverage_gap_reason"))
        pursuit.next_action = "\n".join(_str(item) for item in _as_list(mintel_recommendation.get("interview_scenarios_to_prepare")))
    elif payload.get("company_rating") or payload.get("top_marketing_role"):
        pursuit.research_summary = _decision_summary_from_payload(payload)
        pursuit.next_action = _default_next_action_from_payload(payload)

    pursuit.recent_requirements = _format_requirement_summary_text(payload)
    pursuit.technology_stack = _format_grouped_text(payload.get("company_tech_stack_summary") or {})

    activity(db, pursuit_id, actor, "research_import", f"Imported structured research: {counts}")
    db.add(pursuit)
    return counts


def _company_name_matches(uscis_name: str, imported_name: str) -> bool:
    if not imported_name:
        return False
    normalized_uscis = normalize_company_name(uscis_name or "")
    normalized_imported = normalize_company_name(imported_name or "")
    if not normalized_uscis or not normalized_imported:
        return False
    return normalized_uscis == normalized_imported or normalized_uscis in normalized_imported or normalized_imported in normalized_uscis


_TECH_CATEGORY_ORDER = [
    "Cloud Platforms",
    "Compute / Containers",
    "Infrastructure as Code",
    "CI/CD",
    "Source Control",
    "Programming / Scripting",
    "Observability / SRE",
    "SRE / Incident Management",
    "Data Platform",
    "Databases",
    "MLOps / AI",
    "Security / IAM",
    "Networking",
    "Operating Systems",
    "Enterprise / Other Tools",
]


_TECH_CATEGORY_ALIASES = {
    "cloud": "Cloud Platforms",
    "cloud_platforms": "Cloud Platforms",
    "compute_containers": "Compute / Containers",
    "compute / containers": "Compute / Containers",
    "containers": "Compute / Containers",
    "infrastructure_as_code": "Infrastructure as Code",
    "iac": "Infrastructure as Code",
    "cicd": "CI/CD",
    "ci/cd": "CI/CD",
    "devops_tools": "CI/CD",
    "source_control": "Source Control",
    "languages": "Programming / Scripting",
    "programming": "Programming / Scripting",
    "scripting_programming": "Programming / Scripting",
    "programming / scripting": "Programming / Scripting",
    "observability_monitoring": "Observability / SRE",
    "observability_sre_tools": "Observability / SRE",
    "sre_incident_management": "SRE / Incident Management",
    "data_platform": "Data Platform",
    "data_platforms": "Data Platform",
    "data_platform_tools": "Data Platform",
    "databases": "Databases",
    "database": "Databases",
    "mlops_ai_platform": "MLOps / AI",
    "mlops_ai_tools": "MLOps / AI",
    "security_iam": "Security / IAM",
    "security_governance_tools": "Security / IAM",
    "networking": "Networking",
    "operating_systems": "Operating Systems",
    "enterprise_tools": "Enterprise / Other Tools",
    "other_tools": "Enterprise / Other Tools",
    "technology_signals": "Enterprise / Other Tools",
    "most_frequent_technologies": "Enterprise / Other Tools",
    "role_wise_tech_stack": "Enterprise / Other Tools",
}


def _add_consolidated_tech(
    buckets: dict[str, dict[str, dict[str, Any]]],
    raw_category: str,
    raw_name: str,
    source: str,
    role: str = "",
) -> None:
    name = _clean_tech_name(raw_name)
    if not name:
        return
    category = _normalize_tech_category(raw_category, name)
    bucket = buckets.setdefault(category, {})
    key = name.lower()
    row = bucket.setdefault(
        key,
        {
            "name": name,
            "posting_count": 0,
            "sources": set(),
            "roles": set(),
        },
    )
    if source in {"job posting", "job signal"}:
        row["posting_count"] += 1
    row["sources"].add(source)
    if role:
        row["roles"].add(role)


def _clean_tech_name(value: str) -> str:
    name = (value or "").strip()
    if not name or name in {"[]", "{}"}:
        return ""
    if len(name) > 80:
        return ""
    lowered = name.lower()
    if lowered in {"monitoring", "automation", "logs", "metrics", "traces", "production support", "data operations"}:
        return name.title()
    return name


def _normalize_tech_category(category: str, name: str) -> str:
    normalized = (category or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized.startswith("role:"):
        return "Enterprise / Other Tools"
    if normalized in _TECH_CATEGORY_ALIASES:
        return _TECH_CATEGORY_ALIASES[normalized]
    lower_name = name.lower()
    if lower_name in {"aws", "azure", "gcp", "oracle oci", "oracle cloud"}:
        return "Cloud Platforms"
    if lower_name in {"kubernetes", "docker", "eks", "aks", "gke", "lambda", "ec2", "s3", "sqs", "sns"}:
        return "Compute / Containers"
    if lower_name in {"terraform", "opentofu", "cloudformation", "ansible", "chef", "puppet", "pulumi"}:
        return "Infrastructure as Code"
    if lower_name in {"jenkins", "github actions", "gitlab ci", "azure devops", "ci/cd", "artifactory"}:
        return "CI/CD"
    if lower_name in {"python", "java", "go", "bash", "powershell", "sql", "node.js", "nodejs"}:
        return "Programming / Scripting"
    if lower_name in {"datadog", "grafana", "prometheus", "opentelemetry", "elk", "opensearch", "cloudwatch", "splunk"}:
        return "Observability / SRE"
    if lower_name in {"snowflake", "databricks", "airflow", "kafka", "informatica", "idmc", "spark", "dbt"}:
        return "Data Platform"
    if lower_name in {"sagemaker", "mlflow", "kubeflow", "vertex ai", "azure ml", "mlops", "aiops"}:
        return "MLOps / AI"
    return "Enterprise / Other Tools"


def _serializable_consolidated_tech(context: dict[str, Any]) -> dict[str, Any]:
    def clean_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": row["name"],
            "posting_count": row["posting_count"],
            "sources": sorted(row["sources"]),
            "roles": sorted(row["roles"]),
        }

    grouped = []
    for group in context["grouped"]:
        grouped.append({"category": group["category"], "technologies": [clean_row(row) for row in group["technologies"]]})
    flat = [clean_row(row) | {"category": row["category"]} for row in context["flat"]]
    featured = [clean_row(row) | {"category": row["category"]} for row in context["featured"]]
    categories = [
        {
            "category": row["category"],
            "count": row["count"],
            "posting_support": row["posting_support"],
            "top": [clean_row(item) for item in row["top"]],
        }
        for row in context["categories"]
    ]
    role_groups = [
        {
            "role": row["role"],
            "posting_support": row["posting_support"],
            "technologies": [clean_row(item) | {"category": item["category"]} for item in sorted(row["technologies"], key=lambda item: (-item["posting_count"], item["name"].lower()))[:10]],
        }
        for row in context["role_groups"]
    ]
    return {"grouped": grouped, "flat": flat, "featured": featured, "categories": categories, "role_groups": role_groups, "total": context["total"], "core": flat[:20]}


def _role_signals(role_counts: dict[str, Any], imported_jobs: list[PursuitJobPostingEvidence]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if role_counts:
        for slug, row in role_counts.items():
            if not isinstance(row, dict):
                continue
            total = _int(row.get("total_eligible_usa_signal"))
            if not total:
                continue
            signals.append(
                {
                    "slug": slug,
                    "name": _str(row.get("display_name")) or slug.replace("_", " ").title(),
                    "total": total,
                    "verified": _int(row.get("verified_below_8_yoe_usa_jobs")),
                    "estimated": _int(row.get("estimated_below_8_yoe_usa_jobs")),
                    "excluded_location": _int(row.get("excluded_location")),
                    "excluded_seniority": _int(row.get("excluded_seniority_risk")),
                }
            )
    if not signals and imported_jobs:
        counts: dict[str, dict[str, Any]] = {}
        for job in imported_jobs:
            slug = job.primary_role_slug or job.primary_marketing_role or "unclassified"
            row = counts.setdefault(
                slug,
                {
                    "slug": slug,
                    "name": job.primary_marketing_role or slug.replace("_", " ").title(),
                    "total": 0,
                    "verified": 0,
                    "estimated": 0,
                    "excluded_location": 0,
                    "excluded_seniority": 0,
                },
            )
            row["total"] += 1
            if job.experience_evidence_type == "verified_experience_below_8":
                row["verified"] += 1
            elif job.experience_evidence_type == "estimated_experience_below_8":
                row["estimated"] += 1
        signals = list(counts.values())
    return sorted(signals, key=lambda row: (row["total"], row["verified"]), reverse=True)


def _suggest_decision(
    approval_count: int,
    eligible_jobs: int,
    verified_jobs: int,
    estimated_jobs: int,
    latest_snapshot: PursuitIntelligenceSnapshot | None,
    blockers: list[str],
) -> tuple[str, str, str]:
    if "No USCIS approval or denial history" in " ".join(blockers):
        return ("watch", "Needs USCIS verification", "USCIS is the source of truth, so staff should verify alias/source history before pursuing.")
    rating = latest_snapshot.company_rating if latest_snapshot else ""
    if approval_count >= 20 and eligible_jobs >= 5 and verified_jobs >= 3:
        return ("pursue", rating or "Strong target company", "USCIS history is meaningful and imported job evidence shows multiple eligible USA roles.")
    if approval_count >= 5 and eligible_jobs >= 2:
        return ("pursue", rating or "Good target company", "USCIS sponsorship signal and recent eligible job evidence are both present.")
    if approval_count > 0 and (eligible_jobs > 0 or verified_jobs + estimated_jobs > 0):
        return ("watch", rating or "Limited target company", "USCIS signal exists, but job evidence is limited; staff should gather more postings or contacts.")
    if approval_count > 0:
        return ("watch", rating or "USCIS-only signal", "USCIS signal exists, but imported job evidence is missing.")
    return ("do_not_pursue", rating or "Not enough USA evidence", "No reliable USCIS/job-posting combination supports pursuit yet.")


def _readiness_next_actions(
    pursuit: CompanyPursuit,
    latest_snapshot: PursuitIntelligenceSnapshot | None,
    blockers: list[str],
    warnings: list[str],
    company_jobs: list[JobOpportunity],
    imported_jobs: list[PursuitJobPostingEvidence],
) -> list[str]:
    actions: list[str] = []
    if not latest_snapshot:
        actions.append("Import the richer company job intelligence JSON before making a company decision.")
    if blockers:
        actions.append("Resolve blocker items before admin review.")
    if warnings:
        actions.append("Review warning items and document the final owner judgment in Decision Notes.")
    if imported_jobs and not any(job.active for job in company_jobs):
        actions.append("Convert the strongest eligible posting into a Mintel job opportunity when staff is ready to market profiles.")
    if not pursuit.next_follow_up_date:
        actions.append("Set a follow-up date for the assigned owner.")
    if not pursuit.decision:
        actions.append("Set staff decision to pursue, watch, or do not pursue.")
    return actions or ["Ready for admin review once the owner confirms the decision notes."]


def _readiness_score(
    *,
    approval_count: int,
    eligible_jobs: int,
    official_job_urls: int,
    usa_confirmed_jobs: int,
    latest_snapshot: PursuitIntelligenceSnapshot | None,
    role_signals: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    pursuit: CompanyPursuit,
) -> int:
    score = 0
    if approval_count >= 20:
        score += 20
    elif approval_count > 0:
        score += 12
    if latest_snapshot:
        score += 15
    if eligible_jobs >= 5:
        score += 20
    elif eligible_jobs >= 2:
        score += 14
    elif eligible_jobs == 1:
        score += 8
    if eligible_jobs and official_job_urls == eligible_jobs:
        score += 10
    if eligible_jobs and usa_confirmed_jobs == eligible_jobs:
        score += 10
    if role_signals:
        score += 10
    if pursuit.decision:
        score += 8
    if pursuit.next_action and pursuit.next_follow_up_date:
        score += 7
    score -= min(30, len(blockers) * 12)
    score -= min(15, len(warnings) * 4)
    return max(0, min(100, score))


def _top_uscis_locations(locations: list[Any]) -> list[str]:
    result: list[str] = []
    for row in locations[:5]:
        city = _str(row.get("petitioner_city") if hasattr(row, "get") else getattr(row, "petitioner_city", ""))
        state = _str(row.get("petitioner_state") if hasattr(row, "get") else getattr(row, "petitioner_state", ""))
        approvals = _int(row.get("approvals") if hasattr(row, "get") else getattr(row, "approvals", 0))
        label = ", ".join(part for part in [city, state] if part)
        if label:
            result.append(f"{label} ({approvals} approvals)")
    return result


def _update_company_from_payload(pursuit: CompanyPursuit, payload: dict[str, Any]) -> None:
    company = pursuit.company
    jobs = _as_list(payload.get("jobs"))
    if jobs:
        first_job = jobs[0] if isinstance(jobs[0], dict) else {}
        if not company.ats_platform and _str(first_job.get("ats_platform")):
            company.ats_platform = _str(first_job.get("ats_platform"))
        if not company.careers_url and _str(first_job.get("official_job_url")):
            company.careers_url = _career_root_from_url(_str(first_job.get("official_job_url")))
        if not company.location:
            locations = sorted({_str(job.get("location")) for job in jobs if isinstance(job, dict) and _str(job.get("location"))})
            company.location = "; ".join(locations[:3])
    tech_summary = payload.get("company_tech_stack_summary")
    if isinstance(tech_summary, dict) and not company.tech_stack:
        company.tech_stack = ", ".join(_dedupe(_str(item) for values in tech_summary.values() for item in _as_list(values)))
    recommendation = payload.get("mintel_training_recommendation")
    if isinstance(recommendation, dict) and not company.submission_guidance:
        company.submission_guidance = _format_recommendation_text(recommendation)
    if payload.get("company_rating") and not company.notes:
        company.notes = _decision_summary_from_payload(payload)


def _career_root_from_url(url: str) -> str:
    if not url:
        return ""
    marker_paths = ("/jobs/", "/job/", "/careers/")
    for marker in marker_paths:
        if marker in url:
            return url.split(marker, 1)[0] + marker.rstrip("/")
    return url


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        result.append(value)
    return result


def _decision_summary_from_payload(payload: dict[str, Any]) -> str:
    parts = [
        f"Company rating: {_str(payload.get('company_rating')) or 'Not rated'}",
        f"Top marketing role: {_str(payload.get('top_marketing_role')) or 'Not set'}",
        f"Second best role: {_str(payload.get('second_best_role')) or 'Not set'}",
        f"Eligible USA job signal: {_int(payload.get('total_eligible_usa_job_signal'))}",
    ]
    notes = _str(payload.get("data_quality_notes") or payload.get("coverage_gap_reason"))
    if notes:
        parts.append(f"Data quality: {notes}")
    return "\n".join(parts)


def _default_next_action_from_payload(payload: dict[str, Any]) -> str:
    if payload.get("is_full_window_coverage") is False:
        return "Review current-public-posting coverage gap, confirm strongest official job links, and set pursue/watch decision."
    return "Review imported job evidence, confirm role fit, and set pursue/watch decision."


def _snapshot_from_payload(pursuit_id: int, payload: dict[str, Any], raw_json: str, actor: str) -> PursuitIntelligenceSnapshot:
    company_name = _str(payload.get("company_normalized_name") or payload.get("company_name"))
    return PursuitIntelligenceSnapshot(
        pursuit_id=pursuit_id,
        company_name=company_name,
        requested_research_window=_str(payload.get("requested_research_window")) or "last_12_months",
        actual_evidence_window=_str(payload.get("actual_evidence_window")),
        requested_location=_str(payload.get("requested_location")) or "USA",
        research_date=_str(payload.get("research_date")),
        count_type=_str(payload.get("count_type")),
        is_full_window_coverage=bool(payload.get("is_full_window_coverage")),
        coverage_gap_reason=_str(payload.get("coverage_gap_reason")),
        total_eligible_usa_job_signal=_int(payload.get("total_eligible_usa_job_signal")),
        verified_below_8_year_usa_jobs=_int(payload.get("verified_below_8_year_usa_jobs")),
        estimated_below_8_year_usa_jobs=_int(payload.get("estimated_below_8_year_usa_jobs")),
        role_counts_json=_json(payload.get("role_counts") or {}),
        company_tech_stack_summary_json=_json(payload.get("company_tech_stack_summary") or {}),
        company_level_use_cases_json=_json(payload.get("company_level_use_cases") or []),
        role_wise_tech_stack_json=_json(payload.get("role_wise_tech_stack") or {}),
        role_wise_use_cases_json=_json(payload.get("role_wise_use_cases") or {}),
        mintel_training_recommendation_json=_json(payload.get("mintel_training_recommendation") or {}),
        top_marketing_role=_str(payload.get("top_marketing_role")),
        second_best_role=_str(payload.get("second_best_role")),
        company_rating=_str(payload.get("company_rating")),
        data_quality_notes=_str(payload.get("data_quality_notes")),
        raw_json=raw_json,
        imported_by=actor,
    )


def _job_evidence_from_payload(pursuit_id: int, snapshot_id: int | None, item: Any, *, included: bool, exclusion_group: str = "") -> PursuitJobPostingEvidence:
    item = item if isinstance(item, dict) else {}
    source_type = _str(item.get("source_type"))
    source_parts = [part for part in [source_type, _str(item.get("source_name")), _str(item.get("ats_platform"))] if part]
    return PursuitJobPostingEvidence(
        pursuit_id=pursuit_id,
        snapshot_id=snapshot_id,
        included=included,
        exclusion_group=exclusion_group,
        exclusion_reason=_str(item.get("exclusion_reason") or item.get("experience_filter_reason")),
        job_title=_str(item.get("job_title") or item.get("Job Title")),
        company=_str(item.get("company") or item.get("company_normalized_name") or item.get("company_name")),
        job_id=_str(item.get("job_id") or item.get("external_job_id") or item.get("job_import_id")),
        location=_str(item.get("location") or item.get("location_found")),
        usa_location_confirmed=bool(item.get("usa_location_confirmed")),
        work_mode=_str(item.get("work_mode")),
        published_date=_str(item.get("published_date")),
        source_type=" / ".join(dict.fromkeys(source_parts)),
        official_job_url=_str(item.get("official_job_url") or item.get("job_url")),
        supporting_urls_json=_json(_as_list(item.get("supporting_urls"))),
        primary_marketing_role=_str(item.get("primary_marketing_role") or item.get("matched_marketing_role")),
        primary_role_slug=_str(item.get("primary_role_slug")),
        secondary_marketing_roles_json=_json(_as_list(item.get("secondary_marketing_roles"))),
        confidence_score=_int(item.get("confidence_score")),
        match_strength=_str(item.get("match_strength")),
        experience_requirement_mentioned=bool(item.get("experience_requirement_mentioned")),
        exact_experience_text_from_jd=_str(item.get("exact_experience_text_from_jd") or item.get("experience_text")),
        minimum_years_required=_nullable_int(item.get("minimum_years_required")),
        maximum_years_required=_nullable_int(item.get("maximum_years_required")),
        experience_evidence_type=_str(item.get("experience_evidence_type")),
        estimated_experience_band=_str(item.get("estimated_experience_band")),
        experience_filter_result=_str(item.get("experience_filter_result")),
        experience_filter_reason=_str(item.get("experience_filter_reason")),
        technology_signals_json=_json(_as_list(item.get("technology_signals"))),
        extracted_tech_stack_json=_json(item.get("extracted_tech_stack") or {}),
        primary_use_cases_json=_json(_as_list(item.get("primary_use_cases"))),
        role_specific_use_cases_json=_json(item.get("role_specific_use_cases") or {}),
        resume_positioning_use_cases_json=_json(_as_list(item.get("resume_positioning_use_cases"))),
        interview_preparation_use_cases_json=_json(_as_list(item.get("interview_preparation_use_cases"))),
        why_counted=_str(item.get("why_counted")),
        duplicate_check=_str(item.get("duplicate_check")),
        duplicate_source_urls_json=_json(_as_list(item.get("duplicate_source_urls"))),
        raw_json=_json(item),
    )


def _add_requirement_from_job(db: Session, pursuit_id: int, item: Any, job: PursuitJobPostingEvidence) -> None:
    text = f"{job.job_title} {job.primary_marketing_role} {' '.join(_as_list(item.get('technology_signals') if isinstance(item, dict) else []))}"
    role = classify_marketing_role(db, text)
    db.add(
        PursuitRequirement(
            pursuit_id=pursuit_id,
            marketing_role_id=role.id if role else None,
            title=job.job_title,
            location=job.location,
            posted_or_seen_date=job.published_date,
            employment_type=job.work_mode,
            technologies=", ".join(_flatten_tech_stack(item.get("extracted_tech_stack") if isinstance(item, dict) else {})),
            work_auth_language=job.experience_filter_reason,
            source_url=job.official_job_url,
            confidence=_score_confidence(job.confidence_score),
        )
    )


def _add_job_technologies(db: Session, pursuit_id: int, job: PursuitJobPostingEvidence, seen: set[tuple[str, str]]) -> int:
    count = 0
    stack = _loads(job.extracted_tech_stack_json, {})
    if isinstance(stack, dict):
        for category, values in stack.items():
            for value in _as_list(values):
                count += _add_technology(db, pursuit_id, category, _str(value), f"Seen in {job.job_title}", _score_confidence(job.confidence_score), seen)
    for value in _as_list(_loads(job.technology_signals_json, [])):
        count += _add_technology(db, pursuit_id, "technology_signals", _str(value), f"Seen in {job.job_title}", _score_confidence(job.confidence_score), seen)
    return count


def _add_technology(db: Session, pursuit_id: int, category: str, name: str, evidence: str = "", confidence: str = "", seen: set[tuple[str, str]] | None = None) -> int:
    name = name.strip()
    category = category.strip()
    if not name or name in {"[]", "{}"}:
        return 0
    key = (category.lower(), name.lower())
    if seen is not None and key in seen:
        return 0
    existing = db.scalar(select(PursuitTechnology).where(PursuitTechnology.pursuit_id == pursuit_id, PursuitTechnology.category == category, PursuitTechnology.name == name))
    if existing:
        if seen is not None:
            seen.add(key)
        if evidence and evidence not in existing.evidence:
            existing.evidence = "; ".join(part for part in [existing.evidence, evidence] if part)
        if confidence and not existing.confidence:
            existing.confidence = confidence
        db.add(existing)
        return 0
    db.add(PursuitTechnology(pursuit_id=pursuit_id, category=category, name=name, evidence=evidence, confidence=confidence))
    if seen is not None:
        seen.add(key)
    return 1


def _parse_json(raw_json: str) -> dict[str, Any]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ValueError("Research payload must be a JSON object")
    return data


def _format_requirement_summary_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Total eligible USA job signal: {_int(payload.get('total_eligible_usa_job_signal'))}",
        f"Verified below-8-year USA jobs: {_int(payload.get('verified_below_8_year_usa_jobs'))}",
        f"Estimated below-8-year USA jobs: {_int(payload.get('estimated_below_8_year_usa_jobs'))}",
        f"Actual evidence window: {_str(payload.get('actual_evidence_window')) or 'unknown'}",
        f"Full 12-month coverage: {'Yes' if payload.get('is_full_window_coverage') else 'No'}",
    ]
    gap = _str(payload.get("coverage_gap_reason"))
    if gap:
        lines.append(f"Coverage gap: {gap}")
    role_counts = payload.get("role_counts") if isinstance(payload.get("role_counts"), dict) else {}
    if role_counts:
        lines.append("")
        lines.append("Role counts:")
        for slug, item in role_counts.items():
            if not isinstance(item, dict):
                continue
            name = _str(item.get("display_name")) or slug
            verified = _int(item.get("verified_below_8_yoe_usa_jobs"))
            estimated = _int(item.get("estimated_below_8_yoe_usa_jobs"))
            total = _int(item.get("total_eligible_usa_signal"))
            lines.append(f"- {name}: {total} total ({verified} verified, {estimated} estimated)")
    return "\n".join(lines)


def _format_recommendation_text(value: dict[str, Any]) -> str:
    labels = {
        "priority_marketing_role": "Priority marketing role",
        "priority_marketing_roles": "Priority marketing roles",
        "technologies_to_teach_first": "Technologies to teach first",
        "project_use_cases_to_add": "Project use cases to add",
        "interview_scenarios_to_prepare": "Interview scenarios to prepare",
        "resume_keywords_to_emphasize": "Resume keywords to emphasize",
    }
    return _format_grouped_text(value, labels)


def _format_grouped_text(value: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    lines: list[str] = []
    for key, raw in value.items():
        title = labels.get(key, key.replace("_", " ").title())
        lines.append(f"{title}:")
        items = _as_list(raw)
        if not items:
            lines.append("- None found")
        else:
            for item in items:
                if isinstance(item, dict):
                    lines.append(f"- {_format_inline_dict(item)}")
                else:
                    lines.append(f"- {_str(item)}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_inline_dict(value: dict[str, Any]) -> str:
    return "; ".join(f"{key.replace('_', ' ')}: {_str(item)}" for key, item in value.items() if _str(item))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True)


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value).strip()


def _int(value: Any) -> int:
    parsed = _nullable_int(value)
    return parsed if parsed is not None else 0


def _nullable_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _score_confidence(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    if score > 0:
        return "low"
    return ""


def _flatten_tech_stack(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    result: list[str] = []
    for values in value.values():
        result.extend(_str(item) for item in _as_list(values) if _str(item))
    return result


def _confidence(item: dict[str, Any]) -> str:
    return _str(item.get("confidence") if isinstance(item, dict) else "")[:20]


def _add_evidence(db: Session, pursuit_id: int, kind: str, label: str, url: str, confidence: str) -> int:
    if not url:
        return 0
    db.add(PursuitEvidence(pursuit_id=pursuit_id, kind=kind, label=label, url=url, confidence=confidence))
    return 1
