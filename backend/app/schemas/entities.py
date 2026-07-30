"""Read/response schemas mirroring the ORM models.

All use `from_attributes=True` so they can be built directly from ORM objects.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int


class BatchInsightSummary(BaseModel):
    """Result of batch research across many prospects."""

    total: int
    researched: int
    skipped: int
    failed: int
    qualified: int  # relevance >= 50
    auto_rejected: int  # marked rejected (below auto_reject_below)
    errors: List[str] = []


class BatchRevealSummary(BaseModel):
    """Result of batch Apollo reveal across many prospects."""

    total: int
    revealed: int
    skipped: int
    failed: int
    errors: List[str] = []


class DiscoveryProcessSummary(BaseModel):
    """Post-discovery: research + reveal everyone in a run."""

    run_id: int
    research: BatchInsightSummary
    reveal: BatchRevealSummary

class PrincipalOut(ORMModel):
    id: int
    name: str
    headline: Optional[str] = None
    linkedin_url: Optional[str] = None
    phone: Optional[str] = None
    email_signature: Optional[str] = None
    outreach_mailbox_id: Optional[str] = None
    objective: Optional[str] = None
    document_focus: Optional[str] = None
    bio: Optional[str] = None
    background: Optional[str] = None
    focus_areas: Optional[List[str]] = None
    target_sectors: Optional[List[str]] = None
    investment_themes: Optional[List[str]] = None
    acquisition_themes: Optional[List[str]] = None
    target_titles: Optional[List[str]] = None
    target_seniorities: Optional[List[str]] = None
    geographies: Optional[List[str]] = None
    opportunity_types: Optional[List[str]] = None
    value_props: Optional[List[str]] = None
    is_active: bool
    created_at: datetime


class SearchDefinitionOut(ORMModel):
    id: int
    principal_id: int
    name: str
    industries: Optional[List[str]] = None
    company_types: Optional[List[str]] = None
    healthcare_sectors: Optional[List[str]] = None
    geographies: Optional[List[str]] = None
    titles: Optional[List[str]] = None
    seniorities: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    themes: Optional[List[str]] = None
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None
    created_at: datetime


class OrganizationOut(ORMModel):
    id: int
    name: str
    domain: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    headquarters: Optional[str] = None
    phone: Optional[str] = None
    funding: Optional[str] = None
    revenue: Optional[str] = None
    company_type: Optional[str] = None
    sectors: Optional[List[str]] = None
    themes: Optional[List[str]] = None
    signals: Optional[List[str]] = None
    discovery_run_id: Optional[int] = None
    enrichment_status: str
    enrichment_source: Optional[str] = None
    do_not_contact: bool
    created_at: datetime


class ProspectOut(ORMModel):
    id: int
    company_id: Optional[int] = None
    name: str
    title: Optional[str] = None
    role_category: Optional[str] = None
    seniority: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None
    phone: Optional[str] = None
    phone_reveal_status: Optional[str] = None
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    source: Optional[str] = None
    discovery_run_id: Optional[int] = None
    confidence_score: Optional[float] = None
    usefulness_score: Optional[float] = None
    relevance_score: Optional[float] = None
    rank_reason: Optional[str] = None
    status: str
    approved_for_outreach: bool
    do_not_contact: bool
    created_at: datetime
    # Latest outreach email status for this prospect (draft/approved/sent/replied),
    # surfaced so Prospects can act as the workflow hub.
    outreach_status: Optional[str] = None
    # Provider that produced the latest insight; "...stub fallback" means real
    # research did NOT run (so the score is not trustworthy yet).
    insight_provider: Optional[str] = None


class RelevanceInsightOut(ORMModel):
    id: int
    principal_id: int
    contact_id: Optional[int] = None
    company_id: Optional[int] = None
    relevance_score: float
    why_relevant: Optional[str] = None
    why_speak_with_principal: Optional[str] = None
    strategic_connection: Optional[str] = None
    common_ground: Optional[str] = None
    relevant_experience: Optional[str] = None
    signals: Optional[List[str]] = None
    talking_points: Optional[List[str]] = None
    snapshot: Optional[str] = None
    key_facts: Optional[List[Any]] = None
    sources: Optional[List[dict]] = None
    identity_verified: bool = True
    identity_warnings: Optional[List[str]] = None
    opportunity_type: Optional[str] = None
    generated_by: Optional[str] = None
    created_at: datetime


class EmailDraftOut(ORMModel):
    id: int
    principal_id: Optional[int] = None
    # Set when the email came from a bulk campaign instead of an agent campaign.
    bulk_campaign_id: Optional[int] = None
    company_id: Optional[int] = None
    contact_id: Optional[int] = None
    insight_id: Optional[int] = None
    subject: str
    body: str
    status: str
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    approved_by: Optional[str] = None
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    outlook_scheduled: bool = False
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    reply_snippet: Optional[str] = None
    reply_body: Optional[str] = None
    last_reply_check_at: Optional[datetime] = None
    open_count: int = 0
    first_opened_at: Optional[datetime] = None
    last_opened_at: Optional[datetime] = None
    # Selected sending mailbox (null = default). ``from_email``/``from_name`` are
    # resolved from it for the review UI.
    from_mailbox: Optional[str] = None
    from_name: Optional[str] = None
    # Enriched for the outreach review UI (not stored on the draft row).
    principal_name: Optional[str] = None
    from_email: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_title: Optional[str] = None
    company_name: Optional[str] = None
    discovery_run_id: Optional[int] = None


class LinkedInMessageOut(ORMModel):
    id: int
    principal_id: Optional[int] = None
    campaign_id: Optional[int] = None
    company_id: Optional[int] = None
    contact_id: Optional[int] = None
    insight_id: Optional[int] = None
    body: str
    invitation_note: Optional[str] = None
    status: str
    provider: Optional[str] = None
    network_distance: Optional[str] = None
    connected: bool = False
    public_identifier: Optional[str] = None
    provider_chat_id: Optional[str] = None
    approved_by: Optional[str] = None
    created_at: datetime
    invitation_sent_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    reply_snippet: Optional[str] = None
    reply_body: Optional[str] = None
    last_reply_check_at: Optional[datetime] = None
    last_status_check_at: Optional[datetime] = None
    error: Optional[str] = None
    # Enriched for the review UI (not stored on the row).
    principal_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    company_name: Optional[str] = None
    linkedin_url: Optional[str] = None
    discovery_run_id: Optional[int] = None


class CallOut(ORMModel):
    id: int
    principal_id: Optional[int] = None
    company_id: Optional[int] = None
    contact_id: Optional[int] = None
    insight_id: Optional[int] = None
    phone_number: Optional[str] = None
    script: Optional[str] = None
    status: str
    transcript: Optional[str] = None
    outcome_notes: Optional[str] = None
    human_handoff_needed: bool
    meeting_requested: bool
    provider: Optional[str] = None
    provider_call_id: Optional[str] = None
    placed_at: Optional[datetime] = None
    created_at: datetime
    principal_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    company_name: Optional[str] = None


class AuditLogOut(ORMModel):
    id: int
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    actor: str
    summary: Optional[str] = None
    detail: Optional[dict] = None
    created_at: datetime


class DiscoveryRunOut(ORMModel):
    id: int
    principal_id: Optional[int] = None
    search_definition_id: Optional[int] = None
    provider: str
    criteria: Optional[dict] = None
    status: str
    organizations_found: Optional[int] = None
    organizations_imported: Optional[int] = None
    people_found: Optional[int] = None
    people_imported: Optional[int] = None
    duplicates: Optional[int] = None
    insights_generated: Optional[int] = None
    error_message: Optional[str] = None
    requested_by: Optional[str] = None
    created_at: datetime
    provider_warnings: List[str] = []
    # Background bulk-job progress (draft/send emails, send LinkedIn) for this run.
    job_kind: Optional[str] = None
    job_status: Optional[str] = None
    job_total: Optional[int] = None
    job_done: Optional[int] = None
    job_error: Optional[str] = None


class AgentRunOut(ORMModel):
    id: int
    principal_id: Optional[int] = None
    discovery_run_id: Optional[int] = None
    playbook_id: Optional[int] = None
    status: str
    trigger: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    discovered: int = 0
    duplicates: int = 0
    qualified: int = 0
    rejected: int = 0
    drafted: int = 0
    sent: int = 0
    followups_drafted: int = 0
    followups_sent: int = 0
    summary: Optional[dict] = None
    created_at: datetime


class AgentConfigOut(ORMModel):
    id: int
    principal_id: int
    enabled: bool
    run_hour_utc: int
    playbook_id: Optional[int] = None
    search_definition_id: Optional[int] = None
    mode: str = "research"
    discover_target: int
    qualify_min: float
    auto_reject_below: float
    sanity_min: float = 20.0
    draft_batch_size: int = 8
    auto_send: bool
    daily_send_cap: int
    followup_enabled: bool
    followup_days: int
    max_followups: int
    followup_cap: int
    followup_schedule_days: List[int] = [3, 10, 15, 30]
    timezone: str = "America/New_York"
    auto_schedule: bool = True
    run_hour_local: int = 9
    send_window_start_local: int = 9
    send_window_end_local: int = 17
    digest_recipients: List[str] = []
    last_run_at: Optional[datetime] = None

    @field_validator("followup_schedule_days", mode="before")
    @classmethod
    def _default_followup_schedule(cls, v: object) -> list:
        return v if v else [3, 10, 15, 30]

    @field_validator("digest_recipients", mode="before")
    @classmethod
    def _default_digest(cls, v: object) -> list:
        return v if v else []

    @field_validator("timezone", mode="before")
    @classmethod
    def _default_timezone(cls, v: object) -> str:
        return v or "America/New_York"

    @field_validator("run_hour_local", mode="before")
    @classmethod
    def _default_run_hour(cls, v: object) -> int:
        return 9 if v is None else int(v)

    @field_validator("send_window_start_local", mode="before")
    @classmethod
    def _default_window_start(cls, v: object) -> int:
        return 9 if v is None else int(v)

    @field_validator("send_window_end_local", mode="before")
    @classmethod
    def _default_window_end(cls, v: object) -> int:
        return 17 if v is None else int(v)


class CampaignSummaryOut(BaseModel):
    """One campaign card for the multi-campaign list (a campaign == AgentConfig)."""

    id: int  # campaign id (AgentConfig.id)
    name: str
    principal_id: int
    principal_name: str
    playbook_id: Optional[int] = None
    playbook_name: Optional[str] = None
    objective_preview: Optional[str] = None
    enabled: bool = False
    paused: bool = False
    status: str  # running | paused | ready | draft
    current_run_id: Optional[int] = None
    current_run_discovered: Optional[int] = None
    current_run_sent: Optional[int] = None
    last_run_at: Optional[datetime] = None
    totals_sent_14d: int = 0
    totals_replies_14d: int = 0
    # True when a prior run was killed by a restart and nothing completed after.
    needs_continue: bool = False
    interrupted_discovered: int = 0


class CampaignListOut(BaseModel):
    items: List[CampaignSummaryOut]
    running_count: int = 0


class CampaignDetailOut(BaseModel):
    """Full per-campaign dashboard payload (header + funnel + daily activity)."""

    id: int
    name: str
    principal_id: int
    principal_name: str
    enabled: bool = False
    paused: bool = False
    # Emails queued to send that a pause/stop would pull back.
    scheduled_count: int = 0
    # Approved emails with no send time yet (schedulable in one click).
    approved_unscheduled: int = 0
    status: str  # running | paused | ready | draft
    objective: Optional[str] = None
    playbook_id: Optional[int] = None
    playbook_name: Optional[str] = None
    criteria: dict = {}
    mailbox_daily_cap: int = 50
    discover_target: int = 0
    auto_send: bool = False
    auto_schedule: bool = True
    pending_drafts: int = 0
    last_run_at: Optional[datetime] = None
    current_run_id: Optional[int] = None
    totals: dict = {}
    reply_rate: float = 0.0
    days: List[dict] = []
    last_run: Optional[dict] = None
    current_run: Optional[dict] = None
    # Set when a prior run was killed by a server restart and needs Continue.
    interrupted_run: Optional[dict] = None


class CampaignProspectOut(BaseModel):
    """A prospect surfaced by a campaign, with its latest email/reply state."""

    contact_id: int
    name: str
    title: Optional[str] = None
    company_name: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    status: str
    pipeline_status: Optional[str] = None
    relevance_score: Optional[float] = None
    email_status: Optional[str] = None  # draft | approved | scheduled | sent | replied
    email_subject: Optional[str] = None
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    reply_snippet: Optional[str] = None
    last_email_id: Optional[int] = None


class CampaignProspectsOut(BaseModel):
    campaign_id: int
    items: List[CampaignProspectOut]
    total: int = 0


class BulkChatMessageOut(ORMModel):
    """One turn of a bulk campaign chat."""

    id: int
    role: str
    content: str
    meta: Optional[dict] = None
    created_at: datetime


class BulkRecipientOut(BaseModel):
    """A pasted recipient plus the state of the email written to them."""

    contact_id: int
    name: str
    email: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    notes: Optional[str] = None
    draft_id: Optional[int] = None
    draft_status: Optional[str] = None
    subject: Optional[str] = None
    sent_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
    reply_snippet: Optional[str] = None
    open_count: int = 0


class BulkLookupOut(BaseModel):
    """A proposed email address for a pasted person, with its evidence."""

    id: int
    contact_id: int
    status: str
    # The person as the user pasted them.
    name: str
    source_text: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    # Who the web search decided they are.
    resolved_name: Optional[str] = None
    resolved_title: Optional[str] = None
    resolved_org: Optional[str] = None
    resolved_domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    location: Optional[str] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    evidence: Optional[List[dict]] = None
    # The proposal itself.
    email: Optional[str] = None
    email_status: Optional[str] = None
    manual: bool = False
    error: Optional[str] = None
    created_at: datetime


class BulkCampaignOut(BaseModel):
    """Header + counters for one bulk email campaign."""

    id: int
    name: str
    mailbox_id: Optional[str] = None
    mailbox_label: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    status: str
    purpose: Optional[str] = None
    signature: Optional[str] = None
    recipients: int = 0
    drafted: int = 0
    approved: int = 0
    sent: int = 0
    replied: int = 0
    # People pasted without an address: still to search, already proposed, and
    # the total that cannot be emailed yet.
    lookup_pending: int = 0
    lookup_found: int = 0
    needs_email: int = 0
    progress_total: int = 0
    progress_done: int = 0
    last_error: Optional[str] = None
    created_at: datetime


class BulkCampaignListOut(BaseModel):
    items: List[BulkCampaignOut]


class BulkCampaignDetailOut(BulkCampaignOut):
    messages: List[BulkChatMessageOut] = []
    recipients_pending_draft: int = 0


class AgentPlaybookOut(ORMModel):
    id: int
    principal_id: int
    name: str
    objective_prompt: str
    clarifying_answers: Optional[dict] = None
    criteria: dict
    created_at: datetime
    updated_at: datetime


class AgentPlanOut(BaseModel):
    questions: List[dict] = []
    criteria: dict = {}
    rationale: Optional[str] = None


class DashboardStats(BaseModel):
    principals_total: int
    organizations_total: int
    prospects_total: int
    prospects_by_status: dict
    prospects_by_role: dict
    insights_total: int
    high_relevance_prospects: int
    email_drafts_total: int
    calls_total: int
    discovery_runs_total: int
    # --- Outreach funnel ---
    prospects_approved: int = 0
    prospects_researched: int = 0
    emails_sent: int = 0
    emails_opened: int = 0
    emails_replied: int = 0
    open_rate: float = 0.0
    reply_rate: float = 0.0
    emails_by_status: dict = {}


class ProviderStatusOut(BaseModel):
    provider: str
    label: str
    configured: bool
    expected: bool
    status: str
    message: Optional[str] = None
    using_stub: bool = False
    last_error_at: Optional[str] = None
    last_success_at: Optional[str] = None
    extra: Optional[dict] = None


class ProviderHealthOut(BaseModel):
    providers: List[ProviderStatusOut]
    has_blocking_issues: bool
    warnings: List[str] = []
