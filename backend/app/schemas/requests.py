"""Request/input schemas for write endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


def _require_non_empty(value: str | None, field_label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_label} is required")
    return text


class PrincipalRequest(BaseModel):
    """Create or update a principal (the executive whose network we build)."""

    name: str
    headline: Optional[str] = None
    linkedin_url: str = ""
    phone: str = ""
    # Exact sign-off appended to outreach emails. Blank = default name + LinkedIn.
    email_signature: Optional[str] = None
    # Which configured mailbox this principal's outreach is sent FROM.
    outreach_mailbox_id: Optional[str] = None
    objective: Optional[str] = None  # deprecated — use Agent/Discover goal instead
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
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        name = _require_non_empty(v, "Full name")
        if len(name.split()) < 2:
            raise ValueError("Use full name (first and last)")
        return name

    @field_validator("linkedin_url")
    @classmethod
    def validate_linkedin(cls, v: str) -> str:
        url = _require_non_empty(v, "LinkedIn URL")
        if "linkedin.com" not in url.lower():
            raise ValueError("LinkedIn URL must be a linkedin.com profile link")
        return url

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _require_non_empty(v, "Phone number")


class SearchDefinitionRequest(BaseModel):
    """Create or update a reusable ICP search definition for a principal."""

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


class DiscoveryRunRequest(BaseModel):
    """Run an Apollo-driven ICP discovery.

    Either reference a saved search_definition_id, or pass ad-hoc criteria.
    """

    principal_id: int
    search_definition_id: Optional[int] = None

    # Ad-hoc criteria (used when search_definition_id is not provided).
    industries: Optional[List[str]] = None
    company_types: Optional[List[str]] = None
    healthcare_sectors: Optional[List[str]] = None
    geographies: Optional[List[str]] = None
    titles: Optional[List[str]] = None
    seniorities: Optional[List[str]] = None
    contact_email_status: Optional[List[str]] = None
    organization_domains: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    themes: Optional[List[str]] = None
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None
    # Active employer job postings to match (Apollo q_organization_job_titles).
    organization_job_titles: Optional[List[str]] = None

    org_limit: Optional[int] = None
    people_limit: Optional[int] = None
    # Generate per-person AI relevance insights during discovery (gated to the
    # more plausible fits to control cost). Turn off for a faster, cheaper run.
    generate_insights: bool = False
    # People-first broad search (Sales Navigator style). Defaults True in bulk mode.
    people_first: Optional[bool] = None
    # When True (default), widen criteria automatically if fewer than people_limit import.
    auto_expand_to_target: bool = True
    # After import, research every prospect and reveal contact details (LLM + Apollo).
    auto_process: bool = False
    # People-first by default: organizations are used only to scope people by
    # industry and are not persisted as standalone records. Set true to also
    # build the organization directory.
    include_organizations: bool = False
    requested_by: Optional[str] = "user"
    # Plain-language goal from AI-assisted setup (stored on the run for research).
    search_goal: Optional[str] = None


class EnrichRequest(BaseModel):
    """Find + reveal prospects for an organization (Apollo enrichment)."""

    max_contacts: int = 5


class InsightGenerateRequest(BaseModel):
    """Generate (or refresh) a relevance insight for a principal + prospect/org."""

    principal_id: int
    contact_id: Optional[int] = None
    company_id: Optional[int] = None


class InsightBatchGenerateRequest(BaseModel):
    """Research many prospects in one request (qualification at scale)."""

    principal_id: int
    discovery_run_id: Optional[int] = None
    contact_ids: Optional[List[int]] = None
    skip_existing: bool = True
    # Prospects scoring below this after research are auto-marked rejected. 0 = off.
    auto_reject_below: float = 0.0


class ProspectBatchRevealRequest(BaseModel):
    """Reveal email/phone for many prospects (Apollo credits)."""

    discovery_run_id: Optional[int] = None
    contact_ids: Optional[List[int]] = None


class EmailRegenerateRunRequest(BaseModel):
    """Re-draft first-touch emails for all prospects in a discovery run."""

    discovery_run_id: int
    principal_id: Optional[int] = None
    # Only replace drafts in these statuses (default: draft only).
    only_statuses: Optional[List[str]] = None


class EmailGenerateRunRequest(BaseModel):
    """Create first-touch drafts for approved prospects in a discovery run."""

    discovery_run_id: int
    principal_id: Optional[int] = None
    # Override run criteria search_goal when drafting (e.g. manual purpose on Drafts page).
    outreach_goal: Optional[str] = None


class EmailGenerateRequest(BaseModel):
    principal_id: int
    contact_id: int
    insight_id: Optional[int] = None
    # When false (default), return an existing draft/approved email instead of
    # creating a duplicate for the same principal + prospect pair.
    regenerate: bool = False


class EmailUpdateRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    # Selectable sending mailbox id (see email_providers/mailboxes.py).
    from_mailbox: Optional[str] = None


class EmailStatusRequest(BaseModel):
    status: str
    approved_by: Optional[str] = "user"


class EmailScheduleRequest(BaseModel):
    # UTC ISO timestamp at which the scheduler should send the email.
    scheduled_at: datetime
    approved_by: Optional[str] = "user"


class EmailReplyRequest(BaseModel):
    # Plain-text reply to send to the prospect within the same thread.
    body: str


class LinkedInGenerateRequest(BaseModel):
    principal_id: int
    contact_id: int
    outreach_goal: Optional[str] = None
    regenerate: bool = False


class LinkedInGenerateRunRequest(BaseModel):
    discovery_run_id: int
    principal_id: Optional[int] = None
    outreach_goal: Optional[str] = None


class LinkedInUpdateRequest(BaseModel):
    body: Optional[str] = None
    invitation_note: Optional[str] = None


class LinkedInStatusRequest(BaseModel):
    status: str
    approved_by: Optional[str] = "user"


class LinkedInReplyRequest(BaseModel):
    body: str


class LinkedInConnectRequest(BaseModel):
    # Optional label to identify the connection in Unipile.
    name: Optional[str] = None


class LinkedInSelectAccountRequest(BaseModel):
    account_id: str


class LinkedInSendOpenRequest(BaseModel):
    """Bulk approve + send all open (draft/approved) LinkedIn messages.

    Optional ``discovery_run_id`` scopes the send to one run; omitted = all runs.
    """

    discovery_run_id: Optional[int] = None


class AgentRunRequest(BaseModel):
    """Trigger an autonomous agent run now for a principal."""

    principal_id: Optional[int] = None
    playbook_id: Optional[int] = None


class AgentPlanRequest(BaseModel):
    """Describe a goal; get clarifying questions + suggested search criteria."""

    objective_prompt: str
    principal_id: Optional[int] = None
    clarifying_answers: Optional[dict] = None


class AgentPlaybookRequest(BaseModel):
    name: str
    objective_prompt: str
    clarifying_answers: Optional[dict] = None
    criteria: dict
    set_active: bool = True
    principal_id: Optional[int] = None


class AgentConfigRequest(BaseModel):
    """Update the autonomous agent configuration for a principal."""

    enabled: Optional[bool] = None
    run_hour_utc: Optional[int] = None
    playbook_id: Optional[int] = None
    search_definition_id: Optional[int] = None
    discover_target: Optional[int] = None
    qualify_min: Optional[float] = None
    auto_reject_below: Optional[float] = None
    auto_send: Optional[bool] = None
    daily_send_cap: Optional[int] = None
    followup_enabled: Optional[bool] = None
    followup_days: Optional[int] = None
    max_followups: Optional[int] = None
    followup_cap: Optional[int] = None
    followup_schedule_days: Optional[List[int]] = None
    timezone: Optional[str] = None
    run_hour_local: Optional[int] = None
    send_window_start_local: Optional[int] = None
    send_window_end_local: Optional[int] = None
    digest_recipients: Optional[List[str]] = None


class CampaignCreateRequest(BaseModel):
    """Create a campaign from the wizard: goal + criteria + operational settings.

    Builds the playbook, the AgentConfig (campaign), and optionally launches.
    """

    principal_id: int
    name: str
    objective_prompt: str
    clarifying_answers: Optional[dict] = None
    criteria: dict = {}

    # Operational settings.
    discover_target: Optional[int] = None          # prospects to find per run
    mailbox_daily_cap: Optional[int] = None         # shared per-principal send cap
    auto_send: Optional[bool] = None
    auto_schedule: Optional[bool] = None            # AI decides per-recipient send time
    followup_enabled: Optional[bool] = None
    followup_schedule_days: Optional[List[int]] = None
    timezone: Optional[str] = None
    run_hour_local: Optional[int] = None
    send_window_start_local: Optional[int] = None
    send_window_end_local: Optional[int] = None
    digest_recipients: Optional[List[str]] = None

    enabled: bool = False        # turn on the daily auto-run
    run_now: bool = False        # kick off a run immediately after creation

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _require_non_empty(v, "Campaign name")

    @field_validator("objective_prompt")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        return _require_non_empty(v, "Goal")


class CampaignUpdateRequest(BaseModel):
    """Edit an existing campaign's name, goal/criteria, or operational settings."""

    name: Optional[str] = None
    objective_prompt: Optional[str] = None
    clarifying_answers: Optional[dict] = None
    criteria: Optional[dict] = None
    enabled: Optional[bool] = None
    discover_target: Optional[int] = None
    mailbox_daily_cap: Optional[int] = None
    qualify_min: Optional[float] = None
    auto_reject_below: Optional[float] = None
    auto_send: Optional[bool] = None
    auto_schedule: Optional[bool] = None
    followup_enabled: Optional[bool] = None
    followup_schedule_days: Optional[List[int]] = None
    timezone: Optional[str] = None
    run_hour_local: Optional[int] = None
    send_window_start_local: Optional[int] = None
    send_window_end_local: Optional[int] = None
    digest_recipients: Optional[List[str]] = None


class BulkCampaignCreateRequest(BaseModel):
    """Start a bulk email campaign: a name and the mailbox it sends from."""

    name: str
    mailbox_id: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _require_non_empty(v, "Campaign name")

    @field_validator("mailbox_id")
    @classmethod
    def validate_mailbox(cls, v: str) -> str:
        return _require_non_empty(v, "Sending mailbox")


class BulkCampaignUpdateRequest(BaseModel):
    name: Optional[str] = None
    mailbox_id: Optional[str] = None
    purpose: Optional[str] = None
    signature: Optional[str] = None


class BulkChatRequest(BaseModel):
    """One user turn: pasted recipients, instructions, or both."""

    message: str


class BulkDraftRequest(BaseModel):
    # Replace existing unsent drafts instead of only writing missing ones.
    regenerate: bool = False


class BulkSendRequest(BaseModel):
    # Limit the send to specific drafts; omit to send every reviewed draft.
    draft_ids: Optional[List[int]] = None


class BulkLookupRequest(BaseModel):
    # Search again for people previously not found or errored, as well as the
    # ones never tried.
    retry_failed: bool = False


class BulkLookupDecisionRequest(BaseModel):
    # Which proposed addresses to take (or dismiss).
    lookup_ids: List[int]


class BulkLookupEmailRequest(BaseModel):
    # Type in an address the search could not find.
    email: str


class FollowupGenerateRequest(BaseModel):
    # Draft follow-ups for people with no reply this many days after the last send.
    days: int = 3
    # Cap how many follow-up drafts to create in one run.
    limit: int = 25
    # If true, create them pre-approved (ready to send) instead of as drafts.
    approve: bool = False
    principal_id: Optional[int] = None


class CallGenerateRequest(BaseModel):
    principal_id: int
    contact_id: int
    insight_id: Optional[int] = None


class CallStatusRequest(BaseModel):
    status: str
    transcript: Optional[str] = None
    outcome_notes: Optional[str] = None
    human_handoff_needed: Optional[bool] = None
    meeting_requested: Optional[bool] = None
    approved_by: Optional[str] = "user"


class ProspectApprovalRequest(BaseModel):
    approved_for_outreach: bool
    approved_by: Optional[str] = "user"


class ProspectStatusRequest(BaseModel):
    status: str
    actor: Optional[str] = "user"


class SuppressionRequest(BaseModel):
    scope: str            # company | domain | email | contact
    value: str
    reason: Optional[str] = None
