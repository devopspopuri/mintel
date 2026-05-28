from types import SimpleNamespace
from datetime import date, datetime, timedelta

from app.services.companies import normalize_company_name, slugify_company
from app.services.lifecycle import build_lifecycle_backbone
from app.services.operating_rules import company_pursue_context, marketing_ready_context, submission_eligibility_context
from app.services.pursuit_intelligence import decision_readiness_context
from app.services.regions import STATE_REGION_CODE, region_code_for_state, states_for_region
from app.web.router import _consultant_lifecycle_operating_model, _consultant_onboarding_questionnaire_context, _public_intake_education_summary


def test_slugify_company():
    assert slugify_company("Acme, Inc.") == "acme-inc"


def test_normalize_company_name():
    assert normalize_company_name("Acme, Inc.") == "ACME INC"


def test_all_states_have_it_market_regions():
    states = {
        "AL", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
    assert set(STATE_REGION_CODE) >= states
    assert len({state: STATE_REGION_CODE[state] for state in states}) == 48


def test_region_state_mapping():
    assert region_code_for_state("ca") == "california-tech-core"
    assert region_code_for_state("TX") == "western-texas-growth"
    assert region_code_for_state("VA") == "southeast-mid-atlantic-business"
    assert states_for_region("northeast") == ("NY", "NJ", "MA", "CT", "RI", "NH", "VT", "ME")


def test_decision_readiness_combines_uscis_and_imported_jobs():
    pursuit = SimpleNamespace(
        company=SimpleNamespace(name="Fidelity Technology Group LLC D/B/A Fidelity Investments"),
        decision="pursue",
        next_action="Convert strongest posting",
        next_follow_up_date="2026-05-20",
    )
    snapshot = SimpleNamespace(
        company_name="Fidelity Investments",
        company_rating="Good target company",
        total_eligible_usa_job_signal=2,
        verified_below_8_year_usa_jobs=2,
        estimated_below_8_year_usa_jobs=0,
        is_full_window_coverage=False,
        actual_evidence_window="current_active_public_postings",
        role_counts_json='{"devops_engineer":{"display_name":"DevOps Engineer","verified_below_8_yoe_usa_jobs":2,"estimated_below_8_yoe_usa_jobs":0,"total_eligible_usa_signal":2}}',
    )
    jobs = [
        SimpleNamespace(
            job_title="DevOps Engineer",
            confidence_score=90,
            official_job_url="https://company.example/jobs/1",
            usa_location_confirmed=True,
            primary_role_slug="devops_engineer",
            primary_marketing_role="DevOps Engineer",
            experience_evidence_type="verified_experience_below_8",
        ),
        SimpleNamespace(
            job_title="Cloud DevOps Engineer",
            confidence_score=85,
            official_job_url="https://company.example/jobs/2",
            usa_location_confirmed=True,
            primary_role_slug="devops_engineer",
            primary_marketing_role="DevOps Engineer",
            experience_evidence_type="verified_experience_below_8",
        ),
    ]
    context = decision_readiness_context(
        pursuit,
        {"totals": {"approvals": 25, "denials": 2, "approval_rate": 92.6}, "locations": []},
        {"intelligence_snapshots": [snapshot], "job_postings": jobs, "excluded_job_postings": []},
        [],
    )

    assert context["suggested_decision"] == "pursue"
    assert context["json_audit"]["stored_job_count"] == 2
    assert context["json_audit"]["official_job_urls"] == 2
    assert context["role_signals"][0]["name"] == "DevOps Engineer"
    assert context["warnings"] == [
        "The imported evidence is not full last-12-month coverage; treat counts as a minimum current/recent public signal."
    ]


def test_lifecycle_backbone_surfaces_cross_module_risks():
    today = date(2026, 5, 23)
    pursuit = SimpleNamespace(
        id=7,
        updated_at=datetime(2026, 4, 20),
        assigned_staff_email="",
        next_follow_up_date=None,
    )
    company_rows = [
        {
            "company_id": 10,
            "company_name": "Fidelity Investments",
            "pursuit": pursuit,
            "action_stage": "needs_json",
            "stage_label": "Needs job intelligence",
            "next_staff_action": "Import current postings JSON.",
            "watch_score": 82,
            "approvals": 42,
            "region_name": "Northeast Finance & Enterprise",
        }
    ]
    journeys = [
        {
            "consultant_id": 22,
            "consultant_name": "Asha",
            "journey_stage": "positioning",
            "target_role": "Cloud Platform Engineer",
            "owner_label": "Trainer",
            "readiness_score": 48,
            "gaps_label": "Project story missing",
            "next_action": "Finish evidence package.",
        }
    ]
    submissions = [
        {
            "consultant_id": 22,
            "consultant_name": "Asha",
            "company_name": "Fidelity Investments",
            "job_title": "Cloud Engineer",
            "status": "Submitted",
            "submitted_on": today - timedelta(days=9),
            "next_step": "Follow up with vendor.",
        }
    ]
    mocks = [
        {
            "id": 4,
            "consultant_name": "Asha",
            "role": "Cloud Platform Engineer",
            "status": "Waiting Feedback",
            "needs_attention": True,
            "scheduled_on": today - timedelta(days=1),
            "next_action": "Capture feedback.",
        }
    ]

    context = build_lifecycle_backbone(company_rows, journeys, submissions, mocks, today=today)

    assert context["summary"]["total"] == 4
    assert context["summary"]["companies"] == 1
    assert context["summary"]["blocked"] == 2
    assert context["summary"]["overdue"] == 1
    assert context["summary"]["unowned"] == 1
    assert context["risk_rows"][0]["risk"] == "blocked"
    assert {row["state"] for row in context["state_rows"]} >= {"Needs job intelligence", "Positioning", "Submitted", "Waiting Feedback"}


def test_marketing_ready_requires_owner_resume_project_mock_and_evidence():
    consultant = SimpleNamespace(
        email="asha@example.com",
        staff_owner="asha.owner@example.com",
        resume_tailoring_complete=True,
        project_story_validated=True,
        mock_interview_passed=True,
        marketing_brief_ready=False,
    )

    context = marketing_ready_context(consultant)

    assert not context["ready"]
    assert context["missing_labels"] == ["Final evidence package"]


def test_company_pursue_requires_current_jobs_and_role_fit():
    pursuit = SimpleNamespace(region_id=1, assigned_staff_email="owner@example.com", assigned_staff_name="", next_action="Call vendor")
    decision_readiness = {
        "json_audit": {"stored_job_count": 0},
        "role_signals": [],
        "blockers": [],
    }

    context = company_pursue_context(pursuit, decision_readiness, [])

    assert not context["ready"]
    assert "Current/recent job postings imported" in context["missing_labels"]
    assert "Mintel role fit found" in context["missing_labels"]


def test_active_submission_blocks_until_marketing_ready():
    consultant = SimpleNamespace(email="asha@example.com", staff_owner="owner@example.com")

    context = submission_eligibility_context(consultant, status="submitted", user=SimpleNamespace(role="manager"))

    assert not context["allowed"]
    assert "Consultant is not Marketing Ready" in context["reason"]


def test_consultant_lifecycle_operating_model_connects_owner_training_and_placement():
    consultant = SimpleNamespace(
        email="asha@example.com",
        name="Asha",
        staff_owner="asha.owner@example.com",
        recruiter_owner="recruiter@example.com",
        marketing_role=SimpleNamespace(name="Cloud Platform Engineer"),
        target_industry_domain="Healthcare / Health Insurance",
        latest_project_domain="",
        marketing_status="profile_intake",
        profile_intake_complete=True,
        glossary_review_complete=True,
        training_plan_assigned=True,
        project_story_validated=False,
        resume_tailoring_complete=False,
        mock_interview_passed=False,
        marketing_brief_ready=False,
        submissions=[],
        marketing_readiness_percent=40,
    )
    journey = SimpleNamespace(
        current_stage="training_plan",
        status="active",
        readiness_score=35,
        next_action="Complete role/domain use cases.",
        assigned_trainer=None,
    )
    program = SimpleNamespace(title="Healthcare / Health Insurance - Cloud Platform Engineer Training Program", industry_domain="Healthcare / Health Insurance")

    model = _consultant_lifecycle_operating_model(
        consultant,
        journey=journey,
        program=program,
        activities=[],
        evidence_package={"completed": 2, "total": 8, "percent": 25},
    )

    assert model["owner_label"] == "asha.owner@example.com"
    assert model["assigned_program"] == program.title
    assert model["stage_label"] == "Training plan"
    assert model["next_action"] == "Complete role/domain use cases."
    assert [item["label"] for item in model["lifecycle"]] == [
        "Profile Intake",
        "Basics Preparation",
        "Role / Domain Training",
        "Resume And Project Positioning",
        "Mock Interview Readiness",
        "Marketing Ready Approval",
        "Submissions And Interview Pipeline",
        "Offer And Joining Plan",
        "Placement And Post-Placement Support",
    ]
    assert model["lifecycle"][0]["state"] == "completed"
    assert any(item["stage"] == "Placement" for item in model["staff_responsibilities"])
    assert any(item["stage"] == "Offer" for item in model["staff_responsibilities"])
    assert any(item["title"] == "Offer, Start, And Support" for item in model["training_path"])
    assert any(item["title"] == "Role And Domain Training" for item in model["training_path"])


def test_onboarding_questionnaire_collects_consultant_facts_not_staff_workflow():
    context = _consultant_onboarding_questionnaire_context()
    text = " ".join(
        [section["title"] + " " + section["purpose"] for section in context["questionnaire_sections"]]
        + [label for section in context["questionnaire_sections"] for _key, label, _source, _expected in section["questions"]]
    )

    assert "Undergraduate degree, university, start date, and end date" in text
    assert "Master's degree, university, start date, and end date" in text
    assert "Previous experience before current role" in text
    assert "What did you learn in the last 6 months?" in text
    assert "Resume, Recent Learning, And Documents" in text
    assert "Basics Prep status" not in text
    assert "Assigned training program" not in text
    assert "Staff owner" not in text
    assert "Consent to use profile" not in text
    assert "Interview availability constraints" not in text


def test_public_intake_education_summary_preserves_grad_timelines():
    summary = _public_intake_education_summary(
        "BS Computer Science",
        "State University",
        "08/2016",
        "05/2020",
        "MS Data Science",
        "Tech University",
        "08/2021",
        "05/2023",
    )

    assert "Undergraduate: BS Computer Science | State University | 08/2016 to 05/2020" in summary
    assert "Master's: MS Data Science | Tech University | 08/2021 to 05/2023" in summary
