from types import SimpleNamespace

from app.models.user import UserRole
from app.services.consultant_access import consultant_access_gate_plan, consultant_allowed_path, consultant_training_scope_matches, consultant_unlocks
from app.services.rbac import Permission, has_permission, permissions_for_role
from app.web.router import (
    _bounded_query_int,
    _can_access_consultant,
    _can_edit_pursuit_workspace,
    _can_manage_consultant_journey,
    _can_manage_mock_interviews,
    _can_view_all_mock_interviews,
    _consultant_visibility_clause,
    _optional_query_int,
    _safe_next_url,
    _staff_assigned_to_region,
    _staff_assigned_to_marketing_role,
    _staff_marketing_role_ids,
    _staff_region_ids,
    _visible_mock_marketing_role_ids,
)


def _user(role: UserRole):
    return SimpleNamespace(role=role)


def _assignment(marketing_role_id: int, active: bool = True):
    return SimpleNamespace(marketing_role_id=marketing_role_id, active=active)


def _region_assignment(region_id: int, active: bool = True):
    return SimpleNamespace(region_id=region_id, active=active)


def _region_group_membership(region_ids: list[int], active: bool = True, group_active: bool = True):
    group = SimpleNamespace(
        active=group_active,
        regions=[SimpleNamespace(region_id=region_id, active=True, region=SimpleNamespace(active=True)) for region_id in region_ids],
    )
    return SimpleNamespace(active=active, group=group)


def test_admin_has_all_permissions():
    assert set(Permission) <= permissions_for_role(UserRole.ADMIN)


def test_regional_staff_can_edit_pursuit_intelligence_but_not_assign_or_import():
    user = _user(UserRole.REGIONAL_STAFF)
    assert has_permission(user, Permission.MANAGE_PURSUIT_WORKSPACE)
    assert not has_permission(user, Permission.ASSIGN_PURSUITS)
    assert not has_permission(user, Permission.IMPORT_USCIS)


def test_viewer_is_read_only():
    user = _user(UserRole.VIEWER)
    assert has_permission(user, Permission.VIEW_REPORTS)
    assert not has_permission(user, Permission.MANAGE_PURSUIT_WORKSPACE)
    assert not has_permission(user, Permission.MANAGE_STAFF)


def test_login_next_url_rejects_external_redirects():
    assert _safe_next_url("/dashboard") == "/dashboard"
    assert _safe_next_url("https://example.com") == "/dashboard"
    assert _safe_next_url("//example.com") == "/dashboard"
    assert _safe_next_url("dashboard") == "/dashboard"


def test_consultant_initial_access_is_training_only():
    assert consultant_allowed_path("/")
    assert consultant_allowed_path("/dashboard")
    assert consultant_allowed_path("/training-basics")
    assert consultant_allowed_path("/training-basics/topics/1")
    assert not consultant_allowed_path("/training-programs")
    assert not consultant_allowed_path("/training-programs/56")
    assert not consultant_allowed_path("/account")
    assert not consultant_allowed_path("/consultants/1/onboarding")
    assert not consultant_allowed_path("/consultant-onboarding-workbench")
    assert not consultant_allowed_path("/jobs")
    assert not consultant_allowed_path("/submissions")


def test_consultant_sections_unlock_from_readiness_flags():
    initial = SimpleNamespace(
        profile_intake_complete=False,
        basics_prep_complete=False,
        training_plan_assigned=False,
        latest_project_updated=False,
        project_story_validated=False,
        resume_tailoring_complete=False,
        glossary_review_complete=False,
        marketing_brief_ready=False,
        mock_interview_passed=False,
    )
    training = SimpleNamespace(**{**initial.__dict__, "basics_prep_complete": True, "training_plan_assigned": True})
    onboarding = SimpleNamespace(**{**training.__dict__, "profile_intake_complete": True})
    positioning = SimpleNamespace(**{**onboarding.__dict__, "latest_project_updated": True, "project_story_validated": True, "resume_tailoring_complete": True})
    marketing = SimpleNamespace(**{**positioning.__dict__, "glossary_review_complete": True, "marketing_brief_ready": True, "mock_interview_passed": True})

    assert consultant_unlocks(initial)["training"]
    assert not consultant_unlocks(initial)["training_program"]
    assert not consultant_unlocks(initial)["onboarding"]
    assert consultant_unlocks(training)["training_program"]
    assert consultant_unlocks(onboarding)["onboarding"]
    assert consultant_unlocks(positioning)["positioning"]
    assert not consultant_unlocks(positioning)["mock_interviews"]
    assert consultant_unlocks(marketing)["mock_interviews"]
    assert consultant_unlocks(marketing)["marketing_activity"]
    assert consultant_unlocks(marketing)["full_profile"]


def test_consultant_access_gate_plan_shows_next_locked_stage():
    profile = SimpleNamespace(
        marketing_role_id=10,
        target_industry_domain="Healthcare / Health Insurance",
        staff_owner="owner@example.com",
        profile_intake_complete=True,
        basics_prep_complete=True,
        training_plan_assigned=False,
        latest_project_updated=False,
        project_story_validated=False,
        resume_tailoring_complete=False,
        glossary_review_complete=False,
        mock_interview_passed=False,
        marketing_brief_ready=False,
        marketing_status="training",
        placement_company="",
    )

    plan = consultant_access_gate_plan(profile)

    assert [item["key"] for item in plan][:3] == ["basics", "role_lock", "training_program"]
    assert plan[0]["status"] == "unlocked"
    assert plan[1]["status"] == "unlocked"
    assert plan[2]["status"] == "next"
    assert not plan[2]["unlocked"]


def test_consultant_training_program_requires_exact_role_domain_and_readiness():
    profile = SimpleNamespace(
        basics_prep_complete=True,
        training_plan_assigned=True,
        marketing_role_id=10,
        target_industry_domain="Healthcare / Health Insurance ",
    )
    program = SimpleNamespace(
        active=True,
        marketing_role_id=10,
        industry_domain="healthcare / health insurance",
    )

    assert consultant_training_scope_matches(profile, program)
    assert not consultant_training_scope_matches(SimpleNamespace(**{**profile.__dict__, "basics_prep_complete": False}), program)
    assert not consultant_training_scope_matches(SimpleNamespace(**{**profile.__dict__, "training_plan_assigned": False}), program)
    assert not consultant_training_scope_matches(profile, SimpleNamespace(**{**program.__dict__, "active": False}))
    assert not consultant_training_scope_matches(profile, SimpleNamespace(**{**program.__dict__, "marketing_role_id": 11}))
    assert not consultant_training_scope_matches(profile, SimpleNamespace(**{**program.__dict__, "industry_domain": "Banking / Financial Services"}))


def test_blank_numeric_query_values_are_ignored():
    assert _optional_query_int("") is None
    assert _optional_query_int(None) is None
    assert _optional_query_int("abc") is None
    assert _optional_query_int("7") == 7
    assert _bounded_query_int("", 10, minimum=0) == 10
    assert _bounded_query_int("150", 80, minimum=0, maximum=100) == 100


def test_mock_interview_visibility_uses_staff_marketing_role_assignments():
    user = SimpleNamespace(
        role=UserRole.REGIONAL_STAFF,
        marketing_role_assignments=[_assignment(10), _assignment(20, active=False), _assignment(30)],
    )

    assert _staff_marketing_role_ids(user) == {10, 30}
    assert _visible_mock_marketing_role_ids(user) == {10, 30}
    assert _staff_assigned_to_marketing_role(user, 10)
    assert not _staff_assigned_to_marketing_role(user, 20)


def test_mock_interview_admin_manager_visibility_is_unrestricted():
    admin = _user(UserRole.ADMIN)
    manager = _user(UserRole.MANAGER)

    assert _can_view_all_mock_interviews(admin)
    assert _can_view_all_mock_interviews(manager)
    assert _visible_mock_marketing_role_ids(admin) is None
    assert _visible_mock_marketing_role_ids(manager) is None


def test_regional_staff_can_manage_only_assigned_mock_interviews():
    staff = SimpleNamespace(role=UserRole.REGIONAL_STAFF, marketing_role_assignments=[_assignment(7)])
    viewer = SimpleNamespace(role=UserRole.VIEWER, marketing_role_assignments=[_assignment(7)])

    assert _can_manage_mock_interviews(staff)
    assert _visible_mock_marketing_role_ids(staff) == {7}
    assert not _can_manage_mock_interviews(viewer)
    assert _visible_mock_marketing_role_ids(viewer) == {7}


def test_region_group_assignment_controls_pursuit_workspace_editing():
    staff = SimpleNamespace(
        role=UserRole.REGIONAL_STAFF,
        email="owner@example.com",
        region_assignments=[_region_assignment(5), _region_assignment(8, active=False)],
    )
    owned_by_region = SimpleNamespace(region_id=5, assigned_staff_email="")
    owned_by_email = SimpleNamespace(region_id=9, assigned_staff_email="owner@example.com")
    other_region = SimpleNamespace(region_id=9, assigned_staff_email="other@example.com")

    assert _staff_region_ids(staff) == {5}
    assert _staff_assigned_to_region(staff, 5)
    assert not _staff_assigned_to_region(staff, 8)
    assert _can_edit_pursuit_workspace(staff, owned_by_region)
    assert _can_edit_pursuit_workspace(staff, owned_by_email)
    assert not _can_edit_pursuit_workspace(staff, other_region)


def test_region_groups_grant_region_pursuit_permissions():
    staff = SimpleNamespace(
        role=UserRole.REGIONAL_STAFF,
        email="member@example.com",
        region_assignments=[],
        region_group_memberships=[
            _region_group_membership([3, 4]),
            _region_group_membership([9], active=False),
            _region_group_membership([10], group_active=False),
        ],
    )
    pursuit = SimpleNamespace(region_id=4, assigned_staff_email="")

    assert _staff_region_ids(staff) == {3, 4}
    assert _staff_assigned_to_region(staff, 4)
    assert not _staff_assigned_to_region(staff, 9)
    assert _can_edit_pursuit_workspace(staff, pursuit)


def test_consultant_visibility_uses_owner_and_marketing_role_assignment():
    staff = SimpleNamespace(
        role=UserRole.REGIONAL_STAFF,
        email="owner@example.com",
        name="Owner User",
        first_name="Owner",
        last_name="User",
        marketing_role_assignments=[_assignment(42)],
    )
    assigned_by_owner = SimpleNamespace(marketing_role_id=7, staff_owner="owner@example.com", recruiter_owner="")
    assigned_by_role = SimpleNamespace(marketing_role_id=42, staff_owner="", recruiter_owner="")
    unrelated = SimpleNamespace(marketing_role_id=8, staff_owner="someone@example.com", recruiter_owner="")
    manager = _user(UserRole.MANAGER)

    assert _consultant_visibility_clause(staff) is not None
    assert _can_access_consultant(assigned_by_owner, staff)
    assert _can_access_consultant(assigned_by_role, staff)
    assert not _can_access_consultant(unrelated, staff)
    assert _can_manage_consultant_journey(staff, assigned_by_owner)
    assert _can_access_consultant(unrelated, manager)
