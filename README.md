# Mintel

Mintel is the company, jobs, and interviews intelligence service for MIDH. It starts with USCIS/H-1B sponsorship signals and OPT-friendly company identification, while submissions remain in MAAS for now.

## Architecture Choice

Mintel is scaffolded as a FastAPI microservice instead of a Django app. That gives us:

- API-first service boundaries from day one.
- OpenAPI docs at `/docs` for other services and frontends.
- Independent deployment with its own Postgres database.
- Lightweight service-to-service calls through typed HTTP clients.
- Room to split later into company, jobs, interviews, and import services without fighting a monolith.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8007
```

## Service Boundaries

- `companies`: canonical employer records, aliases, sponsorship tier, OPT friendliness score.
- `h1b_disclosures`: imported H-1B records used as evidence for sponsorship/OPT signals.
- `jobs`: company-linked job opportunities.
- `interviews`: company/job-linked interview intelligence.
- `clients`: outbound clients for MAAS and future services.

## H-1B Import

The first importer accepts CSV files and maps common disclosure columns into Mintel:

```bash
python -m app.importers.h1b_csv path/to/h1b.csv --fiscal-year 2025
```

The importer is intentionally tolerant of common column names so we can adapt as the USCIS/DOL source files vary.
