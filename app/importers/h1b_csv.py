from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.h1b import CaseStatus, H1BDisclosure
from app.services.companies import company_for_employer, refresh_company_signal


STATUS_MAP = {
    "CERTIFIED": CaseStatus.CERTIFIED,
    "CERTIFIED-WITHDRAWN": CaseStatus.CERTIFIED_WITHDRAWN,
    "CERTIFIED WITHDRAWN": CaseStatus.CERTIFIED_WITHDRAWN,
    "DENIED": CaseStatus.DENIED,
    "WITHDRAWN": CaseStatus.WITHDRAWN,
}


def import_h1b_rows(db: Session, file_obj: BinaryIO | TextIO, fiscal_year: int, source_file: str) -> dict[str, int]:
    reader = csv.DictReader(_text_lines(file_obj))
    imported = 0
    updated = 0
    skipped = 0

    for row in reader:
        employer_name = _pick(row, "EMPLOYER_NAME", "Employer", "Employer Name", "employer_name")
        if not employer_name:
            skipped += 1
            continue

        company = company_for_employer(db, employer_name)
        case_number = _pick(row, "CASE_NUMBER", "Case Number", "case_number")
        disclosure = None
        if case_number:
            disclosure = db.scalar(select(H1BDisclosure).where(H1BDisclosure.case_number == case_number, H1BDisclosure.fiscal_year == fiscal_year))

        if disclosure is None:
            disclosure = H1BDisclosure(fiscal_year=fiscal_year, case_number=case_number)
            imported += 1
        else:
            updated += 1

        disclosure.company_id = company.id
        disclosure.employer_name_raw = employer_name
        disclosure.job_title = _pick(row, "JOB_TITLE", "Job Title", "job_title")
        disclosure.soc_code = _pick(row, "SOC_CODE", "SOC Code", "soc_code")
        disclosure.soc_title = _pick(row, "SOC_TITLE", "SOC Title", "soc_title")
        disclosure.case_status = _status(_pick(row, "CASE_STATUS", "Case Status", "case_status"))
        disclosure.worksite_city = _pick(row, "WORKSITE_CITY", "Worksite City", "worksite_city")
        disclosure.worksite_state = _pick(row, "WORKSITE_STATE", "Worksite State", "worksite_state")
        disclosure.wage_rate_from = _decimal(_pick(row, "WAGE_RATE_OF_PAY_FROM", "Wage Rate From", "wage_rate_from"))
        disclosure.wage_unit = _pick(row, "WAGE_UNIT_OF_PAY", "Wage Unit", "wage_unit")
        disclosure.source_file = source_file
        db.add(disclosure)
        db.commit()
        refresh_company_signal(db, company)

    return {"imported": imported, "updated": updated, "skipped": skipped}


def _text_lines(file_obj: BinaryIO | TextIO):
    for line in file_obj:
        if isinstance(line, bytes):
            yield line.decode("utf-8-sig")
        else:
            yield line


def _pick(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name]:
            return row[name].strip()
    return ""


def _status(value: str) -> CaseStatus:
    return STATUS_MAP.get(value.strip().upper(), CaseStatus.UNKNOWN)


def _decimal(value: str) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--fiscal-year", type=int, required=True)
    args = parser.parse_args()

    path = Path(args.csv_path)
    with SessionLocal() as db, path.open(newline="", encoding="utf-8-sig") as handle:
        result = import_h1b_rows(db, handle, fiscal_year=args.fiscal_year, source_file=path.name)
    print(result)


if __name__ == "__main__":
    main()
