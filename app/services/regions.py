from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Region
from app.models.uscis import UscisEmployerYearlyStat


DEFAULT_REGIONS = [
    (
        "california-tech-core",
        "California Tech Core",
        "Tier A",
        "USCIS-heavy California market for software, cloud, AI, enterprise platforms, and high-volume technology hiring.",
        ("CA",),
    ),
    (
        "western-texas-growth",
        "Western & Texas Growth",
        "Tier B",
        "Balanced western and Texas-led growth market covering Pacific Northwest, Mountain West, Southwest, Texas, and Arkansas petition demand.",
        ("WA", "OR", "ID", "MT", "WY", "NV", "UT", "CO", "AZ", "NM", "TX", "OK", "AR"),
    ),
    (
        "midwest-pennsylvania-enterprise",
        "Midwest & Pennsylvania Enterprise",
        "Tier B",
        "USCIS-balanced enterprise market covering Great Lakes, central states, and Pennsylvania demand.",
        ("IL", "MI", "OH", "IN", "WI", "MN", "IA", "MO", "KS", "NE", "ND", "SD", "PA"),
    ),
    (
        "northeast",
        "Northeast Finance & Enterprise",
        "Tier A",
        "Dense finance, enterprise IT, biotech, healthcare, and metro technology demand.",
        ("NY", "NJ", "MA", "CT", "RI", "NH", "VT", "ME"),
    ),
    (
        "southeast-mid-atlantic-business",
        "Southeast & Mid-Atlantic Business",
        "Tier B",
        "USCIS-balanced business, federal, consulting, healthcare, finance, insurance, logistics, and Sun Belt technology market.",
        ("VA", "MD", "DE", "WV", "DC", "NC", "SC", "GA", "FL", "AL", "MS", "LA", "TN", "KY"),
    ),
]

STATE_REGION_CODE = {
    state: code
    for code, _name, _tier, _description, states in DEFAULT_REGIONS
    for state in states
}


def all_region_metadata() -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "name": name,
            "tier": tier,
            "description": description,
            "states": states,
        }
        for code, name, tier, description, states in DEFAULT_REGIONS
    ]


def states_for_region(code: str) -> tuple[str, ...]:
    for region_code, _name, _tier, _description, states in DEFAULT_REGIONS:
        if region_code == code:
            return states
    return ()


def region_code_for_state(state: str) -> str:
    return STATE_REGION_CODE.get((state or "").strip().upper(), "")


def ensure_default_regions(db: Session) -> None:
    existing = {region.code: region for region in db.scalars(select(Region)).all()}
    active_codes = {code for code, _name, _tier, _description, _states in DEFAULT_REGIONS}
    for code, region in existing.items():
        if code not in active_codes and region.active:
            region.active = False
    for code, name, tier, description, _states in DEFAULT_REGIONS:
        region = existing.get(code)
        if region is None:
            db.add(Region(code=code, name=name, description=f"{tier}. {description}"))
            continue
        region.name = name
        region.description = f"{tier}. {description}"
        region.active = True
    db.commit()


def region_for_state(db: Session, state: str) -> Region | None:
    code = region_code_for_state(state)
    if not code:
        return None
    return db.scalar(select(Region).where(Region.code == code))


def recommended_region_for_company(db: Session, company_id: int) -> Region | None:
    rows = db.execute(
        select(
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_decisions).label("decisions"),
        )
        .where(UscisEmployerYearlyStat.company_id == company_id)
        .group_by(UscisEmployerYearlyStat.petitioner_state)
    ).mappings()

    region_scores: dict[str, tuple[int, int]] = {}
    for row in rows:
        code = region_code_for_state(row["petitioner_state"])
        if not code:
            continue
        current_approvals, current_decisions = region_scores.get(code, (0, 0))
        region_scores[code] = (
            current_approvals + int(row["approvals"] or 0),
            current_decisions + int(row["decisions"] or 0),
        )
    if not region_scores:
        return None

    code = max(region_scores.items(), key=lambda item: item[1])[0]
    return db.scalar(select(Region).where(Region.code == code))


def region_signal_for_company(db: Session, company_id: int) -> dict[str, object]:
    region = recommended_region_for_company(db, company_id)
    return {
        "region": region,
        "states": db.scalars(
            select(UscisEmployerYearlyStat.petitioner_state)
            .where(UscisEmployerYearlyStat.company_id == company_id, UscisEmployerYearlyStat.petitioner_state != "")
            .distinct()
            .order_by(UscisEmployerYearlyStat.petitioner_state)
        ).all(),
    }


def region_signals_for_companies(db: Session, company_ids: list[int]) -> dict[int, dict[str, object]]:
    if not company_ids:
        return {}

    ids = list(dict.fromkeys(company_ids))
    regions_by_code = {region.code: region for region in db.scalars(select(Region)).all()}
    state_rows = db.execute(
        select(
            UscisEmployerYearlyStat.company_id,
            UscisEmployerYearlyStat.petitioner_state,
            func.sum(UscisEmployerYearlyStat.total_approvals).label("approvals"),
            func.sum(UscisEmployerYearlyStat.total_decisions).label("decisions"),
        )
        .where(UscisEmployerYearlyStat.company_id.in_(ids), UscisEmployerYearlyStat.petitioner_state != "")
        .group_by(UscisEmployerYearlyStat.company_id, UscisEmployerYearlyStat.petitioner_state)
    ).mappings()

    states_by_company: dict[int, set[str]] = {company_id: set() for company_id in ids}
    scores_by_company: dict[int, dict[str, tuple[int, int]]] = {company_id: {} for company_id in ids}
    for row in state_rows:
        company_id = int(row["company_id"])
        state = row["petitioner_state"]
        states_by_company.setdefault(company_id, set()).add(state)
        code = region_code_for_state(state)
        if not code:
            continue
        approvals, decisions = scores_by_company.setdefault(company_id, {}).get(code, (0, 0))
        scores_by_company[company_id][code] = (
            approvals + int(row["approvals"] or 0),
            decisions + int(row["decisions"] or 0),
        )

    signals: dict[int, dict[str, object]] = {}
    for company_id in ids:
        region_scores = scores_by_company.get(company_id, {})
        region = None
        if region_scores:
            region_code = max(region_scores.items(), key=lambda item: item[1])[0]
            region = regions_by_code.get(region_code)
        signals[company_id] = {"region": region, "states": sorted(states_by_company.get(company_id, set()))}
    return signals
