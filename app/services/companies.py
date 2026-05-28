from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyAlias, SponsorshipTier
from app.models.h1b import CaseStatus, H1BDisclosure
from app.schemas.company import CompanyCreate


def slugify_company(value: str) -> str:
    chars = []
    previous_dash = False
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-")[:260] or "company"


def normalize_company_name(value: str) -> str:
    return " ".join(value.upper().replace(",", " ").replace(".", " ").split())


def create_company_from_import(db: Session, payload: CompanyCreate) -> Company:
    base_slug = slugify_company(payload.name)
    slug = base_slug
    suffix = 2
    while db.scalar(select(Company.id).where(Company.slug == slug)):
        slug = f"{base_slug}-{suffix}"[:280]
        suffix += 1

    company = Company(slug=slug, **payload.model_dump())
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def company_for_employer(db: Session, raw_name: str) -> Company:
    normalized = normalize_company_name(raw_name)
    alias = db.scalar(select(CompanyAlias).where(CompanyAlias.normalized_name == normalized).limit(1))
    if alias:
        return alias.company

    company = create_company_from_import(db, CompanyCreate(name=" ".join(raw_name.split()).title()))
    db.add(CompanyAlias(company_id=company.id, raw_name=raw_name, normalized_name=normalized))
    db.commit()
    db.refresh(company)
    return company


def refresh_company_signal(db: Session, company: Company) -> Company:
    approvals = db.scalar(
        select(func.count(H1BDisclosure.id)).where(
            H1BDisclosure.company_id == company.id,
            H1BDisclosure.case_status.in_([CaseStatus.CERTIFIED, CaseStatus.CERTIFIED_WITHDRAWN]),
        )
    ) or 0
    denials = db.scalar(
        select(func.count(H1BDisclosure.id)).where(
            H1BDisclosure.company_id == company.id,
            H1BDisclosure.case_status == CaseStatus.DENIED,
        )
    ) or 0
    total = approvals + denials
    score = Decimal("0.00") if total == 0 else (Decimal(approvals) / Decimal(total) * Decimal("100")).quantize(Decimal("0.01"))

    if approvals >= 25 and score >= 80:
        tier = SponsorshipTier.HIGH
    elif approvals >= 5 and score >= 60:
        tier = SponsorshipTier.MEDIUM
    elif approvals > 0:
        tier = SponsorshipTier.LOW
    else:
        tier = SponsorshipTier.UNKNOWN

    company.h1b_approval_count = approvals
    company.h1b_denial_count = denials
    company.opt_friendliness_score = score
    company.opt_friendly = approvals >= 3 and score >= 60
    company.sponsorship_tier = tier
    db.add(company)
    db.commit()
    db.refresh(company)
    return company
