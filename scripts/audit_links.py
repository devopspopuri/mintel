from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path("/app")))

from app.main import app
from app.web.auth import require_admin, require_manager, require_user


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(("get", "href", values["href"] or ""))
        if tag == "form" and values.get("action"):
            self.links.append(((values.get("method") or "get").lower(), "action", values["action"] or ""))


def main() -> None:
    admin = SimpleNamespace(
        id=1,
        email="audit@local",
        name="Audit Admin",
        role="admin",
        active=True,
        timezone="America/Chicago",
        username="audit",
        password_hash="",
    )
    app.dependency_overrides[require_user] = lambda: admin
    app.dependency_overrides[require_manager] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    client = TestClient(app, base_url="http://localhost")
    seed = [
        "/dashboard",
        "/uscis/analysis",
        "/uscis/import",
        "/reports",
        "/reports/companies-by-region",
        "/companies/tools",
        "/pursuits",
        "/consultants",
        "/jobs",
        "/marketing-roles",
        "/marketing-roles/glossary",
        "/training-programs",
        "/staff",
        "/staff/access",
        "/staff-assignments",
        "/regions",
        "/account",
        "/docs",
    ]
    seen: set[str] = set()
    checked_links: dict[str, int] = {}
    queue = list(seed)
    broken: list[tuple[str, int, str]] = []
    checked: list[tuple[str, int]] = []
    while queue and len(checked) < 120:
        url = queue.pop(0)
        parsed = urlparse(url)
        path_key = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if path_key in seen or _skip_path(parsed.path):
            continue
        seen.add(path_key)
        response = client.get(url, follow_redirects=False)
        checked.append((url, response.status_code))
        if response.status_code >= 400:
            broken.append((url, response.status_code, "page"))
            continue
        if "text/html" not in response.headers.get("content-type", ""):
            continue
        parser = LinkParser()
        parser.feed(response.text)
        for method, attr, href in parser.links:
            target = _internal_target(url, href)
            if not target:
                continue
            target_path = urlparse(target).path
            if _skip_path(target_path):
                continue
            if method == "get":
                result_status = checked_links.get(target)
                if result_status is None:
                    result = client.get(target, follow_redirects=False)
                    result_status = result.status_code
                    checked_links[target] = result_status
                if result_status >= 400:
                    broken.append((target, result_status, f"{attr} from {url}"))
                target_key = urlparse(target).path + (f"?{urlparse(target).query}" if urlparse(target).query else "")
                if target_key not in seen and _can_enqueue(target):
                    queue.append(target)
    print(f"checked={len(checked)} link_checks={len(checked_links)} seen={len(seen)} broken={len(broken)}")
    for url, status, source in broken:
        print(f"{status} {url} {source}")


def _internal_target(current_url: str, href: str) -> str:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "http://", "https://")):
        return ""
    parsed = urlparse(urljoin(current_url, href))
    if parsed.netloc and parsed.netloc != "localhost":
        return ""
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _skip_path(path: str) -> bool:
    return path.startswith(("/api/", "/logout")) or path in {"/openapi.json"}


def _can_enqueue(target: str) -> bool:
    parsed = urlparse(target)
    query = parsed.query
    if "page=" in query and "page=1" not in query and "page=2" not in query:
        return False
    return True


if __name__ == "__main__":
    main()
