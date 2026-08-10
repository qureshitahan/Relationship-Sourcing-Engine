"""Application configuration.

All settings load from environment variables (see `.env.example`). The app is
designed to run fully on stub/mock providers, so every integration key is
optional. Real keys can be added incrementally as milestones progress.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_cors_origins(raw: str) -> List[str]:
    """Accept comma-separated or JSON-array CORS_ORIGINS from env (Azure-friendly)."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    app_name: str = "Relationship Sourcing Engine"
    environment: str = "development"
    database_url: str = "sqlite:///./data/relationship_engine.db"
    # Stored as plain str (not List[str]) so pydantic-settings 2.3.x on Azure does not
    # json-decode the env value before we can split it. Use settings.cors_origins for the list.
    cors_origins_env: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias=AliasChoices("cors_origins", "CORS_ORIGINS"),
    )
    # Public HTTPS base URL for inbound webhooks (e.g. ngrok tunnel to port 8000).
    app_public_url: str = ""

    # --- Discovery (Apollo-driven ICP search) ---
    # Provider: apollo | stub
    discovery_provider: str = "apollo"
    # Default page sizes for ICP discovery (cost control).
    discovery_org_limit: int = 25
    discovery_people_limit: int = 25
    # Hard ceiling on how many prospects one discovery run can request/import,
    # regardless of the requested people_limit. Applied in relationship_discovery
    # and in the Apollo provider's own pagination so a single run can scale up to
    # this many people in one go (e.g. "Max prospects = 1000").
    discovery_max_people_limit: int = 1000
    # Default headcount ceiling applied when a run doesn't specify employee_max.
    # Set to 0 to disable (broadest search). Mega-brand filtering still applies
    # post-search in relationship_discovery.
    discovery_employee_max_default: int = 0

    # --- Enrichment ---
    enrichment_provider: str = "stub"
    apollo_api_key: str = ""
    apollo_base_url: str = "https://api.apollo.io/api/v1"
    # Max contacts to request per company from Apollo People Search.
    apollo_contacts_per_company: int = 10
    # People Search expands to similar job titles when enabled (broader net).
    apollo_include_similar_titles: bool = True
    # Reveal real emails/phones via People Enrichment (consumes credits).
    # Default OFF: discovery (search) is cheap; reveals are deliberate and gated
    # to high board-fit, human-approved prospects only (see board_fit_* below).
    apollo_reveal_contacts: bool = False
    # How many top-ranked contacts per company to reveal emails for (cost control).
    apollo_enrich_contacts_limit: int = 5
    # Also reveal personal emails (in addition to work emails) — extra credits.
    apollo_reveal_personal_emails: bool = False
    # Phone reveal is delivered asynchronously to a webhook; requires a public URL.
    # Default OFF: board sourcing is email-first; phone reveals burn extra credits.
    apollo_reveal_phone_number: bool = False

    # --- Automated agent gating (optional) ---
    # When > 0, the autonomous agent skips research/reveal on contacts below this
    # usefulness score. Manual Prospects-page reveals ignore this entirely.
    board_fit_reveal_threshold: float = 0.0
    # Drop discovered people below this board-fit during a run (don't even save
    # them). Keeps the prospect sheet focused on board-relevant decision-makers.
    board_fit_min_keep: float = 45.0
    # Deprecated — not enforced. Bulk mode reveals all has_email candidates.
    discovery_reveal_budget: int = 10
    # Full webhook URL passed to Apollo. If empty, built from APP_PUBLIC_URL + secret.
    apollo_phone_webhook_url: str = ""
    # Shared secret appended as ?token=... on the webhook URL (recommended).
    apollo_phone_webhook_secret: str = ""
    zoominfo_api_key: str = ""

    # --- Email ---
    # Provider: stub | microsoft_graph | outlook (alias) | gmail | google | postmark | sendgrid
    email_provider: str = "stub"
    postmark_server_token: str = ""
    sendgrid_api_key: str = ""
    # Gmail / Google Workspace (used when email_provider=gmail). SMTP send +
    # IMAP reply tracking via an App Password (no OAuth). Blank = inactive, so
    # this has no effect on existing (Graph/stub) deployments.
    gmail_address: str = ""
    gmail_app_password: str = ""
    # Optional multi-sender support: a JSON array of selectable mailboxes (see
    # services/email_providers/mailboxes.py). Blank = single legacy sender built
    # from email_provider + outreach_from_email, so existing behaviour is intact.
    outreach_mailboxes_env: str = Field(
        default="",
        validation_alias=AliasChoices("outreach_mailboxes", "OUTREACH_MAILBOXES"),
    )
    # Mailbox id used when a draft/principal has none. Only applies as a fallback
    # for non-outreach mail (digests); principal outreach requires an explicit
    # mailbox when several are configured.
    default_outreach_mailbox_id: str = ""
    outreach_from_email: str = "dalbir.bains@galaxypharma.net"
    outreach_from_name: str = "Dalbir Bains"
    # Appended to outreach email signatures when the principal has no linkedin_url set.
    outreach_linkedin_url: str = "https://www.linkedin.com/in/dalbir-bains/"
    # Open tracking: embed a 1x1 pixel so we can tell when an email is opened.
    # Sends emails as HTML when on. Requires APP_PUBLIC_URL to be reachable.
    # NOTE: many clients block/prefetch images, so open data is approximate.
    track_opens: bool = True
    # Secret used to sign the open-tracking pixel URL (falls back if empty).
    tracking_secret: str = ""
    # Microsoft 365 / Outlook (Graph API, application Mail.Send permission).
    microsoft_tenant_id: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    # Mailbox UPN to send from (must match an M365 user in the tenant).
    microsoft_send_as_user: str = "dalbir.bains@galaxypharma.net"

    # --- LinkedIn outreach (Unipile) ---
    # Provider: stub | unipile  (stub never sends — safe default).
    linkedin_provider: str = "stub"
    unipile_api_key: str = ""
    # The connected LinkedIn account id in Unipile to send from.
    unipile_account_id: str = ""
    # Unipile DSN as host:port (e.g. api28.unipile.com:15824). The base URL is
    # built as https://{dsn}/api/v1.
    unipile_dsn: str = ""

    # --- Voice (Vapi + ElevenLabs + Claude) ---
    # Provider: stub | vapi
    voice_provider: str = "stub"
    vapi_api_key: str = ""
    # Outbound caller ID imported in the Vapi dashboard (Phone Numbers tab).
    vapi_phone_number_id: str = ""
    # Optional saved assistant in Vapi. If empty, a transient assistant is sent per call.
    vapi_assistant_id: str = ""
    # ElevenLabs voice id for 11labs provider in Vapi (e.g. Sarah, Chris).
    vapi_elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    vapi_webhook_secret: str = ""
    # Legacy Twilio fields (only needed if importing Twilio into Vapi manually).
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    elevenlabs_api_key: str = ""

    # --- LLM / AI insight engine ---
    # Provider: anthropic | stub (stub returns deterministic insights, no API key)
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # Cheaper model used for writing emails when the optimized pipeline's
    # cheap_draft_model flag is switched on. Research never uses this.
    optimized_draft_model: str = "claude-haiku-4-5"
    # Max web searches per prospect insight research call. "Moderate" research =
    # one LLM call with up to this many quick web searches (cost control).
    insight_web_search_max_uses: int = 2
    # Max web searches when identifying one pasted person for a bulk campaign.
    # Higher than insight research: a name plus a job description usually needs
    # a couple of searches before their employer and LinkedIn profile are clear.
    bulk_lookup_web_search_max_uses: int = 4
    # Concurrent identity lookups. Each is one Claude call with web search, so
    # this is the main lever on how hard a big paste hits the API.
    bulk_lookup_workers: int = 4
    # Only spend LLM research on prospects whose rule-based board-fit (0-100) is at
    # least this. People are imported above board_fit_min_keep (45) but we research
    # only the more plausible fits to keep cost down while staying personalized.
    research_gate_min: float = 50.0
    # Optimized pipeline: reuse an existing brief for a prospect researched
    # within this many days instead of paying to research them again.
    insight_reuse_days: int = 30
    # Kept for backwards compatibility; not used by the insight engine.
    openai_api_key: str = ""

    # --- Bulk send-from-run pacing (background jobs) ---
    # Seconds between successive Apollo /people/bulk_match calls (each call
    # covers up to 10 people). A 500-prospect reveal is ~50 such calls; a small
    # gap keeps Apollo from rate-limiting the batch, which otherwise fails
    # silently (soft_fail) and looks like "these people have no email on file".
    # Reveal runs in the background, so pacing does not block the UI.
    apollo_reveal_pace_seconds: float = 0.25
    # Seconds between individual sends in a run-level bulk send. Spacing protects
    # deliverability (a burst of identical-origin sends looks like spam). Sending
    # runs in the background, so pacing does not block the UI.
    bulk_email_send_delay_seconds: float = 2.0
    # LinkedIn is far more rate-limited than email; keep sends well spaced so the
    # account is not flagged/restricted.
    bulk_linkedin_send_delay_seconds: float = 20.0
    # Hard daily ceiling on LinkedIn sends (invites + DMs) so the account is not
    # flagged. A run-level bulk LinkedIn send stops once today's total reaches this.
    # LinkedIn realistically tolerates well under this; 50 is an aggressive max.
    linkedin_daily_send_cap: int = 50
    # How many drafts to generate CONCURRENTLY in a run-level bulk draft job —
    # each draft is one Claude call, and Claude calls are I/O-bound (network
    # wait), so running several at once cuts wall-clock roughly linearly
    # without extra Anthropic cost. Kept modest to stay well under Anthropic's
    # per-account concurrent-request limits.
    bulk_draft_batch_size: int = 8
    # How many prospects to research+reveal+approve CONCURRENTLY in a
    # run-level bulk approve job. Approval was previously driven one-request-
    # at-a-time from the browser (2 in flight) with a full web-search Claude
    # call inside every request — the single biggest reason bulk outreach on
    # ~500 prospects took hours instead of minutes.
    bulk_approve_workers: int = 8

    # --- Sending cadence (drip) ---
    # When the agent auto-sends, it sends in small batches with a pause between
    # them so the mailbox is never blasted (reduces spam/blocking risk).
    send_batch_size: int = 10
    send_batch_delay_seconds: int = 120

    # --- Safety / compliance ---
    max_outreach_per_company: int = 3
    outreach_cooldown_days: int = 14

    # --- Automated daily outreach (backend-only; opt-in via AUTOMATION_ENABLED) ---
    # Master switch. False (default) => nothing is seeded and no automation
    # scheduler runs, so existing behaviour is completely unchanged.
    automation_enabled: bool = False
    # Independent LinkedIn on/off. When false, EMAIL still runs but the LinkedIn
    # daily job is skipped entirely (no discovery, no reveals, no Unipile calls) —
    # use it to pause LinkedIn while accounts recover from a rate limit.
    automation_linkedin_enabled: bool = True
    # Per-account confirmation/digest emails are delivered here.
    automation_notify_email: str = "m.usama@tekhqs.com"
    # Daily fire times (UTC). 1PM Pakistan (PKT=UTC+5) = 08:00 UTC for email;
    # 12PM PKT = 07:00 UTC for LinkedIn. Both run Mon-Fri only.
    automation_email_hour_utc: int = 8
    automation_linkedin_hour_utc: int = 7
    # Per-account daily send targets.
    automation_email_daily_cap: int = 500
    automation_linkedin_daily_cap: int = 50
    # Discover well above the caps so qualify/dedupe/reachability attrition still
    # leaves enough sendable. Email: ~67% of discovered have a reachable email, so
    # ~800 discovered -> ~500 sendable. LinkedIn: reveal yields ~90% messageable.
    automation_email_discover_target: int = 800
    automation_linkedin_discover_target: int = 120
    # Pacing (anti-burst): seconds between LinkedIn sends (jittered). 50 invites
    # at ~45s spreads each account's daily batch over ~40+ minutes. Email is
    # spread across business hours by the agent's drip scheduler (auto_schedule).
    automation_linkedin_send_delay_seconds: float = 45.0
    # When true, run ONE full paced outreach cycle immediately on startup (in
    # addition to the daily schedule). Guarded to fire at most once per UTC day
    # even if left on, so restarts don't stack duplicate runs.
    automation_run_on_startup: bool = False

    # DB connection pool (Postgres only; ignored for SQLite). Headroom so the
    # background jobs (agent runs, LinkedIn worker, bulk reveals) don't starve
    # web requests -> "QueuePool limit reached" timeouts.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # SQLite journal mode. Blank = AUTO: WAL locally (better read concurrency),
    # but the rollback journal (DELETE) on network-share paths like Azure's
    # /home. WAL CANNOT work over a network filesystem (its -shm shared-memory
    # index isn't maintainable over SMB), which is what corrupts the file on
    # Azure ("database disk image is malformed"). Override to DELETE/WAL/TRUNCATE
    # only if you need to force one.
    sqlite_journal_mode: str = ""
    # LinkedIn rejects connection-invitation notes above a length that is well
    # under the documented 300 for many accounts; keep it conservative.
    linkedin_invite_note_max_chars: int = 200
    # Optional JSON override of which connected LinkedIn accounts send DMs, e.g.
    # [{"label":"Dalbir","account_id":"…","mailbox_id":"dalbir_tekhqs"}, …].
    # Blank => Dalbir (UNIPILE_ACCOUNT_ID) + Farah (built-in id) mapped to their
    # tekhqs mailboxes.
    automation_linkedin_accounts_env: str = Field(
        default="",
        validation_alias=AliasChoices(
            "automation_linkedin_accounts", "AUTOMATION_LINKEDIN_ACCOUNTS"
        ),
    )

    @property
    def cors_origins(self) -> List[str]:
        return _parse_cors_origins(self.cors_origins_env)


@lru_cache
def get_settings() -> Settings:
    """Load settings from backend/.env (cached until process reload)."""
    return Settings()


settings = get_settings()


def resolve_apollo_phone_webhook_url(cfg: Settings | None = None) -> str:
    """Return the HTTPS URL Apollo should POST phone reveals to.

    Priority:
      1. APOLLO_PHONE_WEBHOOK_URL if set explicitly
      2. {APP_PUBLIC_URL}/api/webhooks/apollo/phone[?token=SECRET]
    """
    cfg = cfg or settings
    explicit = (cfg.apollo_phone_webhook_url or "").strip()
    if explicit:
        return explicit

    public = (cfg.app_public_url or "").strip().rstrip("/")
    if not public:
        return ""

    url = f"{public}/api/webhooks/apollo/phone"
    secret = (cfg.apollo_phone_webhook_secret or "").strip()
    if secret:
        url = f"{url}?token={secret}"
    return url


def resolve_vapi_webhook_url(cfg: Settings | None = None) -> str:
    """Return the HTTPS URL Vapi should POST call events to."""
    cfg = cfg or settings
    public = (cfg.app_public_url or "").strip().rstrip("/")
    if not public:
        return ""
    url = f"{public}/api/webhooks/vapi"
    secret = (cfg.vapi_webhook_secret or "").strip()
    if secret:
        url = f"{url}?token={secret}"
    return url
