from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_CSV = "/Users/krishna/workspace.codex/maas/exports/mintel/mintel_marketing_role_job_links_last_30_days.csv"
DEFAULT_CACHE = "/Users/krishna/workspace.mintel/exports/mintel/maas_job_description_cache.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch MAAS job descriptions from exported job URLs.")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--role", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_existing(cache_path)
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    candidates = []
    for row in rows:
        if row.get("is_active", "").strip().lower() != "true":
            continue
        if row.get("approval_status", "").strip().lower() == "rejected":
            continue
        if args.role and args.role.lower() not in row.get("marketing_role", "").lower():
            continue
        key = row.get("maas_job_id") or row.get("job_id") or row.get("url")
        if not args.force and key in existing and existing[key].get("description"):
            continue
        candidates.append(row)

    if args.limit > 0:
        candidates = candidates[: args.limit]

    fetched = []
    for index, row in enumerate(candidates, start=1):
        item = fetch_row(row)
        fetched.append(item)
        print(f"{index}/{len(candidates)} {item['status']} {row.get('marketing_role')} {row.get('title')}")
        time.sleep(0.25)

    merged = {**existing}
    for item in fetched:
        for key in (item.get("maas_job_id"), item.get("job_id"), item.get("url")):
            if key:
                merged[str(key)] = item

    unique_items = {}
    for item in merged.values():
        key = item.get("maas_job_id") or item.get("job_id") or item.get("url")
        if key:
            unique_items[key] = item

    with cache_path.open("w", encoding="utf-8") as handle:
        for item in unique_items.values():
            handle.write(json.dumps(item, ensure_ascii=True, sort_keys=True) + "\n")

    print(f"cached={len(unique_items)} path={cache_path}")
    return 0


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    existing: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in (item.get("maas_job_id"), item.get("job_id"), item.get("url")):
            if key:
                existing[str(key)] = item
    return existing


def fetch_row(row: dict[str, str]) -> dict[str, str]:
    url = (row.get("url") or "").strip()
    result = {
        "marketing_role": row.get("marketing_role", ""),
        "job_id": row.get("job_id", ""),
        "maas_job_id": row.get("maas_job_id", ""),
        "title": row.get("title", ""),
        "company": row.get("company", ""),
        "url": url,
        "status": "not_fetched",
        "description": "",
    }
    if not url:
        result["status"] = "missing_url"
        return result
    body, content_type, error = fetch_url(url)
    if error:
        result["status"] = error
        return result
    text = body.decode("utf-8", errors="ignore")
    description = extract_text(text, content_type)
    result["description"] = description[:20000]
    result["status"] = "fetched" if len(description.split()) >= 40 else "fetched_short"
    return result


def fetch_url(url: str) -> tuple[bytes, str, str]:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 MintelJDEnricher/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.read(2_000_000), response.headers.get("content-type", ""), ""
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        urllib_reason = str(getattr(exc, "reason", exc))[:120]

    try:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--max-time",
                "15",
                "-A",
                "Mozilla/5.0 MintelJDEnricher/1.0",
                url,
            ],
            check=False,
            capture_output=True,
            timeout=18,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return b"", "", f"fetch_error:curl:{str(exc)[:120]}"
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="ignore").strip()
        return b"", "", f"fetch_error:urllib:{urllib_reason};curl:{stderr[:120]}"
    return completed.stdout[:2_000_000], "text/html", ""


def extract_text(text: str, content_type: str) -> str:
    if "json" in content_type:
        text = re.sub(r'["{}\\[\\],:]', " ", text)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text


if __name__ == "__main__":
    raise SystemExit(main())
