from __future__ import annotations

from typing import Any

from app.models.company import Company


def build_company_research_prompt(company: Company, context: dict[str, Any]) -> str:
    totals = context.get("totals", {})
    region_signal = context.get("region_signal", {})
    region = region_signal.get("region")
    states = ", ".join(region_signal.get("states") or [])
    locations = context.get("locations") or []
    top_locations = ", ".join(
        f"{row.get('petitioner_city') or 'Unknown'}, {row.get('petitioner_state') or 'Unknown'}"
        for row in locations[:8]
    )

    return f"""You are a company pursuit research analyst for Mintel.

Research company:
- Company name: {company.name}
- Website currently known: {company.website or "unknown"}
- Industry currently known: {company.industry or "unknown"}
- Recommended Mintel region: {region.name if region else "unknown"}
- USCIS filing states: {states or "unknown"}
- Top USCIS filing locations: {top_locations or "unknown"}
- USCIS approvals: {totals.get("approvals", 0)}
- USCIS denials: {totals.get("denials", 0)}
- USCIS approval rate: {totals.get("approval_rate", 0)}%
- New employment approvals: {totals.get("new_employment", 0)}
- H1B transfer approvals: {totals.get("change_employer", 0)}
- Continuation approvals: {totals.get("continuation", 0)}

Goal:
Help MINTEL staff decide whether this USCIS company should be pursued, watched, or rejected. USCIS data is the source of truth for the company signal. Public job-posting JSON is supporting evidence for role fit, tech stack, and marketing opportunity.

Research window:
- Requested window is always last_12_months.
- If public sources only expose current active postings, do not claim full 12-month coverage.
- Report the actual evidence window found.

Target roles:
1. Cloud Platform Engineer
2. Data Platform Engineer
3. DevOps Engineer
4. MLOps / AI Platform Engineer
5. Site Reliability / AIOps Engineer

Counting rules:
- Count USA jobs only.
- Count each job under exactly one primary marketing role.
- Keep official company or ATS URLs first.
- Exclude non-USA, unclear-location, duplicate, role-mismatch, missing-URL, and explicit 8+ years jobs.
- Jobs may pass below-8-year eligibility by explicit experience evidence or title-based estimation.
- Do not extrapolate missing closed jobs.

Decision rules:
- Strong target company: USCIS signal plus 5 or more eligible USA postings, official URLs, and clear role concentration.
- Good target company: USCIS signal plus 2 to 4 eligible USA postings.
- Limited target company: USCIS signal exists but job evidence is weak or current-only.
- Watch: alias/source/coverage needs validation.
- Do not pursue: no reliable USCIS/job-posting combination, non-USA-only evidence, or senior-only/non-target postings.

Return valid JSON only. Use this shape. Empty unknown fields must be empty arrays or empty strings, not invented data:
{{
  "company_name": "{company.name}",
  "company_normalized_name": "",
  "requested_research_window": "last_12_months",
  "actual_evidence_window": "",
  "requested_location": "USA",
  "research_date": "",
  "count_type": "",
  "is_full_window_coverage": false,
  "coverage_gap_reason": "",
  "do_not_extrapolate": true,
  "total_eligible_usa_job_signal": 0,
  "verified_below_8_year_usa_jobs": 0,
  "estimated_below_8_year_usa_jobs": 0,
  "role_counts": {{
    "cloud_infrastructure_engineer": {{"display_name": "Cloud Platform Engineer", "verified_below_8_yoe_usa_jobs": 0, "estimated_below_8_yoe_usa_jobs": 0, "excluded_location": 0, "excluded_seniority_risk": 0, "total_eligible_usa_signal": 0}},
    "dataops_engineer": {{"display_name": "Data Platform Engineer", "verified_below_8_yoe_usa_jobs": 0, "estimated_below_8_yoe_usa_jobs": 0, "excluded_location": 0, "excluded_seniority_risk": 0, "total_eligible_usa_signal": 0}},
    "devops_engineer": {{"display_name": "DevOps Engineer", "verified_below_8_yoe_usa_jobs": 0, "estimated_below_8_yoe_usa_jobs": 0, "excluded_location": 0, "excluded_seniority_risk": 0, "total_eligible_usa_signal": 0}},
    "mlops_engineer": {{"display_name": "MLOps / AI Platform Engineer", "verified_below_8_yoe_usa_jobs": 0, "estimated_below_8_yoe_usa_jobs": 0, "excluded_location": 0, "excluded_seniority_risk": 0, "total_eligible_usa_signal": 0}},
    "site_reliability_engineer": {{"display_name": "Site Reliability / AIOps Engineer", "verified_below_8_yoe_usa_jobs": 0, "estimated_below_8_yoe_usa_jobs": 0, "excluded_location": 0, "excluded_seniority_risk": 0, "total_eligible_usa_signal": 0}}
  }},
  "jobs": [
    {{
      "job_import_id": "",
      "company_name": "",
      "company_normalized_name": "",
      "job_title": "",
      "job_id": "",
      "external_job_id": "",
      "ats_platform": "",
      "source_type": "official_company_careers",
      "source_name": "",
      "official_job_url": "",
      "supporting_urls": [],
      "location": "",
      "city": "",
      "state": "",
      "country": "USA",
      "usa_location_confirmed": true,
      "work_mode": "",
      "published_date": "",
      "date_evidence_type": "",
      "is_currently_active": true,
      "primary_marketing_role": "",
      "primary_role_slug": "",
      "secondary_marketing_roles": [],
      "confidence_score": 0,
      "match_strength": "",
      "role_match_reason": "",
      "experience_requirement_mentioned": false,
      "exact_experience_text_from_jd": "",
      "minimum_years_required": null,
      "maximum_years_required": null,
      "experience_evidence_type": "",
      "estimated_experience_band": "",
      "experience_filter_result": "",
      "experience_filter_reason": "",
      "technology_signals": [],
      "extracted_tech_stack": {{"cloud_platforms": [], "compute_containers": [], "infrastructure_as_code": [], "cicd": [], "source_control": [], "scripting_programming": [], "observability_monitoring": [], "sre_incident_management": [], "data_platform": [], "databases": [], "mlops_ai_platform": [], "security_iam": [], "networking": [], "operating_systems": [], "other_tools": []}},
      "primary_use_cases": [],
      "role_specific_use_cases": {{"cloud_platform_use_cases": [], "devops_use_cases": [], "data_platform_use_cases": [], "sre_aiops_use_cases": [], "mlops_ai_platform_use_cases": [], "security_governance_use_cases": [], "business_use_cases": []}},
      "resume_positioning_use_cases": [],
      "interview_preparation_use_cases": [],
      "why_counted": "",
      "dedupe_key": "",
      "duplicate_check": "",
      "duplicate_source_urls": [],
      "import_status": "ready_to_import",
      "import_notes": ""
    }}
  ],
  "excluded_jobs_due_to_location": [],
  "excluded_jobs_due_to_experience_or_seniority": [],
  "excluded_jobs_due_to_role_mismatch": [],
  "excluded_jobs_due_to_missing_url": [],
  "excluded_jobs_due_to_duplicate": [],
  "company_tech_stack_summary": {{
    "most_frequent_technologies": [],
    "cloud_platforms": [],
    "compute_containers": [],
    "devops_tools": [],
    "data_platform_tools": [],
    "mlops_ai_tools": [],
    "observability_sre_tools": [],
    "security_governance_tools": []
  }},
  "company_level_use_cases": [],
  "role_wise_tech_stack": {{"cloud_infrastructure_engineer": [], "dataops_engineer": [], "devops_engineer": [], "mlops_engineer": [], "site_reliability_engineer": []}},
  "role_wise_use_cases": {{"cloud_infrastructure_engineer": [], "dataops_engineer": [], "devops_engineer": [], "mlops_engineer": [], "site_reliability_engineer": []}},
  "mintel_training_recommendation": {{
    "priority_marketing_roles": [],
    "technologies_to_teach_first": [],
    "project_use_cases_to_add": [],
    "interview_scenarios_to_prepare": [],
    "resume_keywords_to_emphasize": []
  }},
  "top_marketing_role": "",
  "second_best_role": "",
  "company_rating": "",
  "data_quality_notes": ""
}}"""
