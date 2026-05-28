from fastapi.templating import Jinja2Templates
import json
from html import escape
from markupsafe import Markup

from app.db.session import SessionLocal
from app.services.consultant_access import consultant_nav_links
from app.services.rbac import has_permission, is_admin, can_manage, role_value


templates = Jinja2Templates(directory="templates")


def _consultant_nav_links_for_user(user) -> list[dict[str, str]]:
    with SessionLocal() as db:
        return consultant_nav_links(db, user)


def _flash_messages(request) -> list[dict[str, str]]:
    return request.session.pop("_flash", [])


def _from_json(value, fallback=None):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback if fallback is not None else {}


def _summary_text(value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return _format_summary_value(parsed).strip() or text


def _summary_html(value) -> Markup:
    text = "" if value is None else str(value).strip()
    if not text:
        return Markup("")
    try:
        parsed = json.loads(text)
    except Exception:
        return Markup(f"<div class=\"summary-copy\">{_paragraphize_text(text)}</div>")
    return Markup(_format_summary_html(parsed) or f"<div class=\"summary-copy\">{escape(text)}</div>")


def _format_summary_value(value, heading: str = "") -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        if heading:
            lines.append(f"{_human_label(heading)}:")
        for key, item in value.items():
            label = _human_label(str(key))
            if isinstance(item, dict):
                lines.append(f"{label}:")
                nested = _format_summary_value(item)
                if nested:
                    lines.extend(f"  {line}" if line else "" for line in nested.splitlines())
            elif isinstance(item, list):
                lines.append(f"{label}:")
                if item:
                    for entry in item:
                        if isinstance(entry, dict):
                            lines.append(f"- {_inline_dict(entry)}")
                        else:
                            lines.append(f"- {entry}")
                else:
                    lines.append("- None found")
            else:
                lines.append(f"{label}: {_format_scalar(item)}")
            lines.append("")
        return "\n".join(lines).strip()
    if isinstance(value, list):
        return "\n".join(f"- {_format_scalar(item)}" for item in value)
    return _format_scalar(value)


def _format_summary_html(value, heading: str = "") -> str:
    if isinstance(value, dict):
        sections: list[str] = []
        if heading:
            sections.append(f"<section class=\"summary-section\"><h3>{escape(_human_label(heading))}</h3>")
            sections.append(_format_summary_html_body(value))
            sections.append("</section>")
        else:
            for key, item in value.items():
                body = _format_summary_html_body(item)
                if not body:
                    body = "<p class=\"muted\">No details provided.</p>"
                sections.append(
                    f"<section class=\"summary-section\"><h3>{escape(_human_label(str(key)))}</h3>{body}</section>"
                )
        return "".join(sections)
    return _format_summary_html_body(value)


def _format_summary_html_body(value) -> str:
    if isinstance(value, dict):
        rows = []
        nested = []
        for key, item in value.items():
            label = escape(_human_label(str(key)))
            if isinstance(item, (dict, list)):
                nested_body = _format_summary_html_body(item)
                nested.append(f"<div class=\"summary-nested\"><strong>{label}</strong>{nested_body}</div>")
            else:
                rows.append(f"<div><dt>{label}</dt><dd>{escape(_format_scalar(item))}</dd></div>")
        output = ""
        if rows:
            output += f"<dl class=\"summary-kv\">{''.join(rows)}</dl>"
        if nested:
            output += "".join(nested)
        return output
    if isinstance(value, list):
        if not value:
            return "<p class=\"muted\">None found.</p>"
        items = []
        for item in value:
            if isinstance(item, dict):
                items.append(f"<li>{escape(_inline_dict(item))}</li>")
            else:
                items.append(f"<li>{escape(_format_scalar(item))}</li>")
        return f"<ul class=\"summary-list\">{''.join(items)}</ul>"
    return f"<p>{escape(_format_scalar(value))}</p>"


def _paragraphize_text(value: str) -> str:
    paragraphs = [line.strip() for line in value.splitlines() if line.strip()]
    if not paragraphs:
        return ""
    return "".join(f"<p>{escape(line)}</p>" for line in paragraphs)


def _human_label(value: str) -> str:
    known = {
        "priority_marketing_roles": "Priority marketing roles",
        "priority_marketing_role": "Priority marketing role",
        "technologies_to_teach_first": "Technologies to teach first",
        "project_use_cases_to_add": "Project use cases to add",
        "interview_scenarios_to_prepare": "Interview scenarios to prepare",
        "resume_keywords_to_emphasize": "Resume keywords to emphasize",
        "best_profiles_to_market": "Best profiles to market",
        "likely_buying_departments": "Likely buying departments",
        "preferred_locations": "Preferred locations",
        "submission_notes": "Submission notes",
        "role_counts": "Role counts",
        "total_eligible_usa_job_signal": "Total eligible USA job signal",
        "verified_below_8_year_usa_jobs": "Verified below-8-year USA jobs",
        "estimated_below_8_year_usa_jobs": "Estimated below-8-year USA jobs",
        "actual_evidence_window": "Actual evidence window",
        "is_full_window_coverage": "Full 12-month coverage",
    }
    if value in known:
        return known[value]
    return value.replace("_", " ").replace("-", " ").title()


def _format_scalar(value) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return "Not specified"
    return str(value)


def _inline_dict(value: dict) -> str:
    return "; ".join(f"{_human_label(str(key))}: {_format_scalar(item)}" for key, item in value.items() if item not in (None, "", [], {}))


templates.env.globals["role_value"] = role_value
templates.env.globals["can_manage"] = can_manage
templates.env.globals["is_admin"] = is_admin
templates.env.globals["can"] = has_permission
templates.env.globals["consultant_nav_links"] = _consultant_nav_links_for_user
templates.env.globals["flash_messages"] = _flash_messages
templates.env.filters["from_json"] = _from_json
templates.env.filters["summary_text"] = _summary_text
templates.env.filters["summary_html"] = _summary_html
