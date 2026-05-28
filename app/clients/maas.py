from __future__ import annotations

import httpx

from app.core.config import settings


class MaasClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.maas_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.maas_api_key

    async def get_application_context(self, candidate_id: int) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=10) as client:
            response = await client.get(f"/api/candidates/{candidate_id}/")
            response.raise_for_status()
            return response.json()

    async def get_active_job_descriptions(self, marketing_role: str, industry_domain: str, limit: int = 7) -> list[dict]:
        if not self.base_url:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        params = {"marketing_role": marketing_role, "industry_domain": industry_domain, "limit": limit, "active": "true"}
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=10) as client:
            response = await client.get("/api/job-descriptions/", params=params)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                return payload[:limit]
            if isinstance(payload, dict):
                rows = payload.get("items") or payload.get("results") or payload.get("data") or []
                return rows[:limit] if isinstance(rows, list) else []
            return []
