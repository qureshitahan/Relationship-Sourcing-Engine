# Relationship Sourcing Engine

An executive networking and business-development platform. For a given
**principal** (e.g. a healthcare operator and board/advisory candidate like
Dalbir Bains), it discovers relevant **organizations** and **people**
(executives, investors, operating partners, board members, founders, and other
decision-makers) via Apollo, scores their strategic **relevance**, explains *why*
each prospect matters, and drafts **warm, personalized executive outreach** —
all with a human in the loop.

> **Safety first:** nothing is emailed or called automatically. Every prospect,
> outreach draft, and call requires explicit human approval. The default
> integration providers are *stubs* that never transmit anything.

---

## Architecture

```
Relationship_sourcing_engine/
├── backend/          FastAPI + SQLAlchemy (Python) REST API
│   ├── app/
│   │   ├── core/         config (env vars)
│   │   ├── db/           engine, session, base
│   │   ├── models/       SQLAlchemy data model (the schema)
│   │   ├── schemas/      Pydantic request/response models
│   │   ├── services/     business logic (see below)
│   │   └── api/routes/   HTTP endpoints
│   ├── scripts/seed.py   end-to-end smoke test + demo data
│   └── tests/            unit tests
└── frontend/         React + Vite + TypeScript + Tailwind dashboard
    └── src/
        ├── api/          typed API client
        ├── components/   layout + UI primitives + InsightCard
        └── pages/        Dashboard, Principals, Discover, Prospects,
                          Organizations, Outreach Drafts, Call Queue
```

### Core concepts

| Entity | Meaning |
|---|---|
| **Principal** | The executive whose strategic network you are building. Discovery and relevance are always scored relative to a principal. Multiple principals are supported. |
| **SearchDefinition** | A reusable Ideal-Customer-Profile (industries, company types, size, geography, titles, seniority, keywords, themes, healthcare sectors). |
| **DiscoveryRun** | One Apollo-driven ICP discovery; records counts of organizations/people found and imported. |
| **Organization** | A discovered company, fund, or firm (operating company, PE/VC, family office, …). |
| **Prospect** | A discovered person with a `role_category` (investor, operating_partner, board_member, ceo, founder, …). |
| **RelevanceInsight** | The AI-generated assessment: why relevant, why speak with the principal, strategic connection, common ground, relevant experience, signals, and talking points. |

### Service layer (modular, swappable)

| Concern | Module | Notes |
|---|---|---|
| ICP discovery | `services/discovery/relationship_discovery.py` | Orchestrates Apollo org + people discovery, upsert, and insight scoring. |
| Apollo client | `services/enrichment/apollo.py` | `discover_organizations` / `discover_people` (ICP search) + enrichment + email/phone reveal. |
| Enrichment | `services/enrichment/` | `stub` / `apollo` / `zoominfo` behind one interface. |
| Prospect ranking | `services/contacts.py` | Role classification + seniority + usefulness scoring. |
| AI insight engine | `services/insights/` | `anthropic` / `stub`: `score_relevance` + `generate_outreach`. |
| Voice scripts | `services/voice.py` | Transparent AI-disclosure call script grounded in the insight. |
| Audit log | `services/audit.py` | Every decision + human action is recorded. |

---

## Quick start

### 1. Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional; stub defaults work out of the box

# (optional) seed a sample principal + ICP and run an offline smoke test:
python -m scripts.seed

# start the API
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend (React)

```bash
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:5173  (it proxies `/api` to the backend on :8000)

---

## Workflow

1. **Principals** — create a profile: background, focus areas, target sectors,
   investment/acquisition themes, target titles/seniorities, geographies,
   opportunity types (advisory/board/consulting/M&A/…), and your value props.
2. **Discover** — pick a principal, define the ICP (industries, company types,
   size, geography, titles, seniority, keywords, themes, healthcare sectors), and
   run discovery. Organizations and prospects are imported and scored.
3. **Prospects** — review discovered people, filtered by role and relevance.
   Open a prospect to read the strategic insight (why relevant, common ground,
   talking points) and approve them for outreach.
4. **Outreach** — generate a warm, personalized email or a call script grounded
   in the insight. Edit and approve; nothing sends automatically.

---

## Configuration

All config is environment-driven — see `backend/.env.example`. Everything runs
on stub providers with no keys. Add keys incrementally:

- `DISCOVERY_PROVIDER` + `APOLLO_API_KEY` — Apollo ICP discovery
- `ENRICHMENT_PROVIDER` + `APOLLO_API_KEY` / `ZOOMINFO_API_KEY` — enrichment
- `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (+ `ANTHROPIC_MODEL`) — AI insights & outreach
- `EMAIL_PROVIDER` + `POSTMARK_SERVER_TOKEN` / `SENDGRID_API_KEY` — sending
- `VOICE_PROVIDER` + `TWILIO_*` / `ELEVENLABS_API_KEY` — voice calls

> The AI insight engine **degrades gracefully**: with no Anthropic key (or on any
> API error) it falls back to the deterministic stub so the platform always works.

## Compliance & safety

- Do-not-contact flags at organization and prospect level (`Suppression`).
- Outreach history for cooldowns / rate limits (`OutreachHistory`).
- Audit log of every discovery, insight, draft, approval, send, and call.
- No mass-blasting: per-step human approval is required by design.

## Notes

- MVP uses SQLite and `create_all` on startup. For production, switch
  `DATABASE_URL` to Postgres and introduce Alembic migrations.
- The AI insight provider interface is designed so additional LLM backends can be
  added without changing callers.
