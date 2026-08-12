# Campaign Module — Context Notes

Working notes on how the "Campaign" feature actually works in this codebase, how it
differs from the manual Discover/Prospects/LinkedIn pages, and why campaigns are
slower than the manual bulk jobs. Written from reading the actual code, not the
docs/comments — file:line references included so claims can be re-verified as the
code changes.

## 0. Terminology — two unrelated things are both called "campaign"

| Name | What it really is | Model | Where |
|---|---|---|---|
| **Campaign** (dashboard/wizard) | One `AgentConfig` row = one autonomous outreach campaign (goal, schedule, auto-send rules) for a principal | `AgentConfig` | `backend/app/models/agent_config.py` |
| **Agent Run** | One execution of a campaign's pipeline (discover→qualify→draft→send) | `AgentRun` | `backend/app/models/agent_run.py` |
| **Discovery Run** | One Apollo import batch (people found for a campaign, or ad-hoc via Discover/Prospects/LinkedIn pages); carries the `job_*` background-job columns | `DiscoveryRun` | `backend/app/models/discovery_run.py` |
| **Bulk Campaign** (unrelated feature) | Paste-a-list-and-chat email blast tool | `BulkCampaign`, `BulkLookup`, `BulkChatMessage` | `backend/app/models/bulk_campaign.py` |

This doc is about the first one — **Campaign = `AgentConfig`**.

## 1. Data model

**`AgentConfig`** (`backend/app/models/agent_config.py`) — one row per campaign:
- `principal_id`, `name`
- `enabled` (daily auto-run on/off), `paused` (hard stop — blocks runs and sends)
- `run_hour_utc` / `run_hour_local` / `timezone` / `weekdays_only`
- `playbook_id` → `AgentPlaybook` (goal/search criteria)
- `discover_target` — people to find per run
- `qualify_min` (default 0) / `auto_reject_below` (default 0) — relevance-score
  thresholds. Both defaulted to 40/35 until `36f6028`; see §9.3 for why they moved
  together and why existing rows still hold the old values
- `auto_send` (autopilot vs. review-before-send) / `daily_send_cap`
- `followup_enabled`, `followup_days`, `followup_schedule_days`, `max_followups`, `followup_cap`
- `auto_schedule` / `send_window_start_local` / `send_window_end_local`
- `digest_recipients`, `last_run_at`

**`AgentRun`** (`backend/app/models/agent_run.py`) — one execution:
- `status`: `running | completed | failed | cancelled`
- `trigger`: `manual | scheduled`
- Funnel counters: `discovered, duplicates, qualified, rejected, drafted, sent, followups_drafted, followups_sent`
- `summary` (JSON): `{"stages":[...], "people":[...], "errors":[...], "cancel_requested": bool}` — read by the dashboard's live progress view

**`DiscoveryRun`** (`backend/app/models/discovery_run.py`) — one Apollo import batch, also carries the background-job tracking columns:
- `job_kind`: `discovery | reveal | approve | draft_email | draft_linkedin | send_email | send_linkedin | pipeline`
- `job_status`: `running | done | failed`
- `job_total`, `job_done`, `job_sent`, `job_error`, `job_cancel_requested`

## 2. Campaign status — computed, not stored

`backend/app/services/agent/dashboard.py:317-364, 552-565`:

```python
if playbook and not config.enabled and not config.paused:
    config.paused = True   # self-healing: legacy "daily off" == paused
if config.paused or not config.enabled:
    status = "paused"
elif run:                 # an AgentRun with status == "running" exists
    status = "running"
elif playbook:
    status = "ready"       # runs daily
else:
    status = "draft"       # no goal/playbook configured yet
```

Run-level status: `running → completed | failed | cancelled`. A boot-time reaper
(`reap_orphaned_runs`, `backend/app/services/campaign_control.py:302`) marks any
still-`running` `AgentRun` as `failed` on server start, since runs execute on
in-process daemon threads and a deploy/crash would otherwise leave permanent
zombie "running" rows.

## 3. Pause / Resume / Cancel (`backend/app/services/campaign_control.py`)

- **Cancel**: flips the in-flight `AgentRun.status` to `cancelled` and pulls back
  any `SCHEDULED` `EmailDraft`s to `APPROVED` (cancelling the run alone wouldn't
  stop mail already queued for send).
- **Pause**: `paused=True, enabled=False`, cancels the in-flight run, and by
  default also unschedules queued emails (cancels the provider-side deferred
  send first, since Exchange would deliver it regardless of local DB state).
- **Resume**: `paused=False, enabled=True`, re-queues the approved backlog with
  fresh AI send timing, packed under the principal's shared mailbox cap.

## 4. The Campaign pipeline — `backend/app/services/agent/orchestrator.py`

`launch_run()` creates an `AgentRun` row and spawns `execute_run(run_id)` on a
**daemon thread** with its own DB session. Stages, each wrapped so one contact's
failure never aborts the run:

1. **Discover** — `run_discovery(...)` (Apollo import), tags every new `Contact`
   with `campaign_id=config.id`.
2. **Qualify** — for each new contact, in a plain sequential `for` loop:
   skip if no reachable email → cheap rule-based fit-score gate → else
   `generate_insight()` (LLM relevance score) → reject/qualify against
   `config.auto_reject_below` / `config.qualify_min`.
3. **Approve + Reveal + Draft** (`orchestrator.py:~495-570`) — approves qualified
   contacts, reveals email if missing (Apollo `bulk_match` call), checks
   `outreach_draft_blockers`, dedupes against existing open drafts **for this
   campaign specifically**, then `generate_outreach()` (LLM email copy) →
   `EmailDraft` (status = `APPROVED` if `auto_send` else `DRAFT`).
4. **Send** — only if `auto_send`; capped by the **principal's shared**
   `mailbox_daily_cap` (shared across all of that principal's campaigns);
   sends immediately or schedules via AI-picked send time.
5. **Follow-ups** — cadence from `followup_schedule_days`, capped by
   `max_followups`/`followup_cap`.
6. **Finalize** — marks `failed` if it errored *and* achieved nothing
   (discovered/drafted/sent all zero), else `completed`.

**Important: the Campaign pipeline only ever touches `EmailDraft`.**
`orchestrator.py` has zero references to LinkedIn or `LinkedInMessage`
(verified by grep — no matches), and `AgentConfig` has no LinkedIn-related
field. **Campaigns send email only. They never draft or send LinkedIn DMs.**

## 5. The manual background-job engine — `backend/app/services/discovery_jobs.py`

Shared engine anchored to a `DiscoveryRun`, used by the Discover/Prospects/
LinkedIn pages for manual bulk actions (not by the Campaign pipeline directly,
though Campaign's `run_discovery` call is the same discovery code underneath).
Per its own docstring: every heavy, run-level operation runs in a daemon thread
with its own DB session so the browser never times out; progress is written to
the `DiscoveryRun.job_*` columns so the UI can poll it.

Jobs: `launch_run_reveal`, `launch_run_approve`, `launch_run_draft` (email),
`launch_run_linkedin_draft`, `launch_run_pipeline` (approve+draft+send
overlapped via a producer/consumer queue), `launch_run_email_send`,
`launch_run_linkedin_send`.

All of them follow the same pattern — **`ThreadPoolExecutor`, many contacts
processed concurrently, one DB commit per item** (so a crash mid-run only
loses the one item in flight, not the whole batch):

```python
with ThreadPoolExecutor(max_workers=settings.bulk_draft_batch_size) as executor:
    futures = {executor.submit(_draft_one, c.id, ...): c.id for c in to_draft}
    for future in as_completed(futures):
        if _cancelled(db, run):
            executor.shutdown(wait=False, cancel_futures=True); break
        ...
        db.commit()
        _progress(db, run, done)
```

LinkedIn drafting specifically (`launch_run_linkedin_draft` /
`_linkedin_draft_worker`, `discovery_jobs.py:~660-830`) was moved to this
background-job model because drafting 150 prospects sequentially in the old
inline route took ~10 minutes against a browser that gives up after 30s and a
worker killed at 600s — messages were being lost every time.

## 6. Campaign vs. manual pages — same building blocks, different orchestration

| | Trigger | Execution style | Progress tracked in | Auto-send? |
|---|---|---|---|---|
| **Campaign** | Schedule or "Run now" — one continuous run through all stages | `ThreadPoolExecutor` per stage (was sequential until `13b3b79`) | `AgentRun.summary` | Yes, if `auto_send` is on |
| **Manual pages** (Discover/Prospects/LinkedIn) | User clicks a button per stage (Discover, then Approve, then Draft, then Send) | `ThreadPoolExecutor` — several contacts processed in parallel | `DiscoveryRun.job_*` columns | No — always an explicit button |

The underlying functions are genuinely shared (not duplicated logic):
`run_discovery()`, `generate_insight()`, `generate_outreach()` are called by
both the orchestrator and the manual job engine. What differs is *how* they're
called — sequential vs. threaded — and, for LinkedIn, that the orchestrator
never calls it at all.

Qualify/auto-reject also differs in behavior even though the scoring function
is shared:
- Campaign uses real `config.auto_reject_below`/`config.qualify_min`
  thresholds — it **auto-rejects** low-scoring contacts with no human involved.
- The manual research path (`backend/app/services/discovery/process_run.py:151-157`)
  calls the same `batch_research_contacts()` but hardcodes
  `auto_reject_below=0.0` — every contact still gets scored, but nothing is
  ever auto-rejected; a human decides.

## 7. Why campaigns are slow

Three things compound:

1. ~~**No parallelism.**~~ **Fixed in `13b3b79`.** Qualify and draft now each run
   a `ThreadPoolExecutor` (`orchestrator.py:553-560` sized by
   `bulk_approve_workers`, `orchestrator.py:681-688` by `bulk_draft_batch_size`),
   matching the manual job engine. The worker owns a private session, does the
   slow I/O, and returns plain data; every run-level write stays on the main
   thread. That fan-out is also what exposed the connection-pool bug in §9.1.
2. **Multiple external calls per contact.** Each contact can require: an
   Apollo `bulk_match` call to reveal email, an LLM call (`generate_insight`)
   to score, and another LLM call (`generate_outreach`) to draft — each taking
   a few seconds.
3. **These multiply linearly.** 100 contacts × ~2-3 sequential network/LLM
   calls × a few seconds each ≈ minutes of wall-clock time that would drop to
   a fraction of that if parallelized the way the manual job engine already is.

## 8. Key file references

**Backend**
- `backend/app/models/agent_config.py` — Campaign model (`AgentConfig`)
- `backend/app/models/agent_run.py` — Run model (`AgentRun`)
- `backend/app/models/discovery_run.py` — Discovery run + `job_*` columns
- `backend/app/models/enums.py` — status enums
- `backend/app/api/routes/campaigns.py` — Campaign CRUD/lifecycle routes
- `backend/app/services/campaign_control.py` — pause/resume/cancel/reap logic
- `backend/app/services/agent/orchestrator.py` — `launch_run`, `execute_run`,
  `_drip_send`, `_run_followups` (email-only pipeline)
- `backend/app/services/agent/dashboard.py` — status derivation, funnel stats
- `backend/app/services/discovery_jobs.py` — manual background-job engine
  (reveal/approve/draft/send, email + LinkedIn)
- `backend/app/services/discovery/process_run.py` — manual research+reveal
  (auto-reject hardcoded off)
- `backend/app/api/routes/discovery.py` — run-level bulk job routes
- `backend/app/api/routes/linkedin.py` — LinkedIn send/DM state machine,
  acceptance/reply poller
- `backend/app/services/enrichment/apollo.py` — Apollo search + pagination

**Frontend**
- `frontend/src/pages/CampaignWizard.tsx` — campaign creation wizard
- `frontend/src/pages/CampaignDashboard.tsx` — campaign detail/operate page
- `frontend/src/pages/LinkedIn.tsx` — background-draft job polling UI
- `frontend/src/pages/BulkCampaignPage.tsx` — unrelated bulk-email tool
- `frontend/src/api/client.ts` — API client for both campaign and
  discovery/bulk-job endpoints

Added by §9:
- `backend/app/services/linkedin_budget.py` — per-account daily LinkedIn cap
- `backend/app/db/session.py` — engine/pool sizing + boot-time additive migrations

## 9. Session notes — 11–12 Aug 2026

Three bugs found and fixed in one debugging session, plus open items. Two of the
three shared a shape worth remembering, recorded in §9.6.

### 9.1 SQLite got no connection-pool config (fixed, `36f6028`)

Every DB-backed endpoint returned 500 after exactly 30.2s while `/health` stayed
200 and the UI sat on "Loading…" forever.

`session.py` set `pool_size` / `max_overflow` **only for Postgres**, so SQLite ran
on SQLAlchemy defaults: 5 + 10 = **15** connections, `pool_timeout=30`. Once §7.1's
thread pools landed, one campaign run alone fans out to `bulk_approve_workers`
threads, and each worker holds its session across the whole model call
(`orchestrator._qualify_one`, `_draft_one`). Four concurrent runs × 8 workers = 32
threads competing for 15 connections; the 30.2s was `pool_timeout` expiring.

Diagnostic tell: the backend had burned only ~6s CPU — the threads were parked on
network I/O *while holding connections*, not working. The SQLite write lock was
free, ruling out "database is locked".

SQLite now sizes its pool off the real fan-out: `pool_size=max(20, fan_out*2)`,
`max_overflow=max(40, fan_out*6)` (20 + 48 at the current settings). Raising
`BULK_APPROVE_WORKERS` grows the pool automatically.

**A restart did not clear it**, which is its own trap: `reap_orphaned_runs` marks
stuck runs `failed`, then `start_agent_scheduler` immediately relaunches every
enabled campaign whose `run_hour_utc` is the current hour and whose `last_run_at`
isn't today. `last_run_at` is only stamped when a run *finishes* — and starved runs
never finished — so every restart launched four fresh runs straight back into the
starved pool. Observed as runs 29–32 `failed` and 33–36 `running` at the same
second. The scheduler is correct as written; fixing the pool broke the loop.

### 9.2 Boot-time additive migrations were SQLite-only DDL (fixed, `6eb124a`)

In production, `/api/campaigns`, `/api/campaigns/{id}` and `/api/agent/config`
returned 500 while nine other endpoints returned 200 — exactly the three that read
`agent_configs`. The error was
`column agent_configs.require_email_and_linkedin does not exist`.

`_ADDITIVE_COLUMNS` in `session.py` is written in SQLite types but runs on Postgres
too. The new column's DDL was `BOOLEAN DEFAULT 0`, and Postgres refuses to cast 0
to boolean in a DEFAULT clause. SQLite accepts it, so it was invisible locally.

Two amplifiers made one bad statement a full outage:
- All `ALTER`s shared **one transaction**. On Postgres a failed statement aborts the
  transaction and every later statement fails too, so **no** column was added.
- `_apply_lightweight_migrations` guards each step, so boot logged one line and came
  up "healthy" with a dead feature.

Latent scope at the time: 8 × `BOOLEAN DEFAULT 0/1` and 6 × `DATETIME` (no such
type in Postgres) entries. All were already wrong, and silent — columns already
present are skipped, so only the first *new* column of either shape triggers it.

Fix: `_portable_ddl()` translates per dialect (`DATETIME`→`TIMESTAMP`,
`BOOLEAN DEFAULT 0/1`→`FALSE/TRUE`), and each `ALTER` runs in its own transaction,
individually guarded, logging the column by name on failure.

> **Caveat:** verified only by translation output and a scratch-DB resilience test —
> not against a real Postgres. Confirm with `/api/campaigns` after deploying.

### 9.3 Relevance thresholds default to 0, and must move in pairs (`36f6028`)

`auto_reject_below` is checked **before** `qualify_min` (`orchestrator.py:610`):

```python
if score < config.auto_reject_below:      # checked first
    ... REJECTED
elif score >= config.qualify_min:
    ... qualified
```

So lowering `qualify_min` to 0 in the UI changed almost nothing — anyone under 35
was already rejected by the earlier branch. Both defaults are now 0 (no relevance
filtering) and the model carries a comment that they must move together. The UI
only exposes `qualify_min`, so `auto_reject_below` is the invisible half.

Also fixed: `dashboard.py`'s qualified-count used `float(config.qualify_min or 40)`,
which read a deliberate 0 as "unset" and counted against 40 — the campaign total
disagreed with what the run actually qualified.

**Existing rows were deliberately left alone.** Campaigns created before this hold
40/35; only new campaigns get 0/0. Editing an old campaign to 0 in the UI still
leaves its `auto_reject_below` at 35.

### 9.4 LinkedIn daily cap is per sending account (fixed, `d329593`)

The 50/day cap (invites + DMs) was counted globally from `OutreachHistory`, which
has **no account column** — so all connected accounts drew on one budget. After 87
sends from other accounts, the LinkedIn page told the account actually selected for
sending that its cap was "reached" and held all 50 of its messages, while that
account had sent nothing that day. LinkedIn limits per account, so the cap has to
be counted per account.

New `backend/app/services/linkedin_budget.py`:
- `active_send_account_id()` — resolves the account a send would go out from, the
  same way the send resolves it (UI selection → env default).
- `linkedin_sent_today(db, account_id)` — counts from `LinkedInMessage`, which
  stamps `from_account` at send time (`linkedin.py:449`) and timestamps each half of
  the cap separately (`invitation_sent_at`, `sent_at`). Both halves count as
  separate events, exactly as `OutreachHistory` recorded them, so only the **scope**
  of the cap changed, never its meaning.

Call sites switched: `linkedin.py` `send_open` and
`discovery_jobs._linkedin_send_worker`. Net −13 lines.

`automation.py` already capped per account from the same source
(`_linkedin_sent_today`, `automation.py:427`) — it was the precedent, and was left
untouched. Behaviour preserved: accounts genuinely at their limit still stop, rows
with `from_account IS NULL` still count (unchanged single-account behaviour), and an
unresolvable account falls back to the old global count so the cap can never end up
switched off.

### 9.5 Open items — found, diagnosed, NOT fixed

| Where | Issue |
|---|---|
| `CampaignDashboard.tsx:503-506, 567` | Pending-drafts badge requests `limit: 100` and counts `items.length`, so it reads "100 waiting" against 199 real drafts. `/api/emails` already returns a real `total` and allows `limit` up to 1000 (`emails.py:180`). Same cap makes "Approve & send all" send only the loaded 100 per click. |
| `linkedin_outreach.py:68-81, 104-110` | Invitation notes truncate mid-sentence. The note is derived from the DM's first sentences against `INVITE_NOTE_LIMIT` (200) minus the greeting; the prompt writes ~224-char two-clause hooks, so `_first_sentences` falls to its word-boundary hard cap. The UI labels the field "≤300 chars". |
| `outreach_prompts.py` | The hook compresses a `key_facts` entry and can drop its qualifier — a fact sourced as "#2 of 120 sites *on a Phase 3 influenza study*" was drafted as "#2 out of 120 North American trial sites". The fact is real and sourced; the compression is what's lossy. |
| `linkedin_scheduler.py` | No daily-cap check at all (auto-send after invite acceptance). Pre-existing; deliberately not changed. |
| Starlette middleware order | Unhandled 500s come from the outermost error middleware, above `CORSMiddleware`, so the response carries no CORS header; cross-origin the browser blocks it and axios reports no response — the UI shows `apiError.ts:7` "Cannot reach the server" for what is really a 500. Cost real debugging time twice. |
| uvicorn `--reload` on Windows | The worker hung in shutdown three times: port closed, process alive at 0% CPU, no respawn. Killing the worker lets the reloader respawn it. No code fix; running without `--reload` avoids it. |

### 9.6 The pattern behind §9.1 and §9.2

Both were caught, logged thinly, and left the app looking healthy. `/health` returned
200 in both outages because it touches no database — pool exhausted, 200; column
missing, 200. The `try/except` guards that exist so boot never crashes also let a
silently dead feature reach production. Keep the guards, but log *what* failed by
name, and consider a health check that touches the DB.
