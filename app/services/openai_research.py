from __future__ import annotations

import json

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.company import CompanyPursuit
from app.models.pursuit_intelligence import PursuitResearchJob, ResearchJobStatus
from app.services.pursuit_intelligence import activity, ingest_research_json


def create_research_job(db: Session, pursuit: CompanyPursuit, prompt: str) -> PursuitResearchJob:
    job = PursuitResearchJob(
        pursuit_id=pursuit.id,
        status=ResearchJobStatus.QUEUED.value,
        model=settings.openai_model,
        prompt=prompt,
    )
    db.add(job)
    activity(db, pursuit.id, "system", "research_queued", "Queued OpenAI company research")
    return job


async def run_research_job(db: Session, job: PursuitResearchJob, pursuit: CompanyPursuit) -> PursuitResearchJob:
    job.status = ResearchJobStatus.RUNNING.value
    db.add(job)
    db.commit()
    if not settings.openai_api_key:
        job.status = ResearchJobStatus.FAILED.value
        job.error = "OPENAI_API_KEY is not configured."
        activity(db, pursuit.id, "system", "research_failed", job.error)
        db.add(job)
        db.commit()
        return job

    try:
        raw = await _call_openai(job.prompt)
        parsed_text = _extract_output_text(raw)
        job.raw_response = parsed_text
        ingest_research_json(db, pursuit, parsed_text, actor="openai")
        job.status = ResearchJobStatus.COMPLETED.value
        activity(db, pursuit.id, "openai", "research_completed", "OpenAI research completed and parsed into structured tabs")
    except Exception as exc:  # pragma: no cover - network failure shape varies
        job.status = ResearchJobStatus.FAILED.value
        job.error = str(exc)
        activity(db, pursuit.id, "openai", "research_failed", job.error)
    db.add(job)
    db.commit()
    return job


async def _call_openai(prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.openai_model,
                "input": prompt,
                "text": {"format": {"type": "json_object"}},
            },
        )
        response.raise_for_status()
        return response.json()


def _extract_output_text(payload: dict) -> str:
    if payload.get("output_text"):
        return payload["output_text"]
    chunks = []
    for output in payload.get("output", []):
        for content in output.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    return json.dumps(payload)
