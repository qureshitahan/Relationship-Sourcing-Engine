"""Application configuration.

All settings load from environment variables (see `.env.example`). The app is
designed to run fully on stub/mock providers, so every integration key is
optional. Real keys can be added incrementally as milestones progress.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # NoDecode: stop pydantic-settings from JSON-parsing the env value so a plain
    # or comma-separated string (e.g. "https://a.com,https://b.com") is accepted
    # by _split_csv below instead of crashing on json.loads.
    cors_origins: Annotated[List[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    # Public HTTPS base URL for inbound webhooks (e.g. ngrok tunnel to port 8000).
    app_public_url: str = ""

    # --- Discovery (Apollo-driven ICP search) ---
    # Provider: apollo | stub
    discovery_provider: str = "apollo"
    # Default page sizes for ICP discovery (cost control).
    discovery_org_limit: int = 25
    discovery_people_limit: int = 25
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
    # Provider: stub | microsoft_graph | outlook (alias) | postmark | sendgrid
    email_provider: str = "stub"
    postmark_server_token: str = ""
    sendgrid_api_key: str = ""
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
    # Max web searches per prospect insight research call. "Moderate" research =
    # one LLM call with up to this many quick web searches (cost control).
    insight_web_search_max_uses: int = 2
    # Only spend LLM research on prospects whose rule-based board-fit (0-100) is at
    # least this. People are imported above board_fit_min_keep (45) but we research
    # only the more plausible fits to keep cost down while staying personalized.
    research_gate_min: float = 50.0
    # Kept for backwards compatibility; not used by the insight engine.
    openai_api_key: str = ""

    # --- Sending cadence (drip) ---
    # When the agent auto-sends, it sends in small batches with a pause between
    # them so the mailbox is never blasted (reduces spam/blocking risk).
    send_batch_size: int = 10
    send_batch_delay_seconds: int = 120

    # --- Safety / compliance ---
    max_outreach_per_company: int = 3
    outreach_cooldown_days: int = 14

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value):
        """Allow comma-separated strings from env vars to become lists."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


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
