"""Status constants and shared vocabularies used across models.

Kept as plain string classes (not DB enums) so values are easy to extend
without migrations during the MVP phase.
"""
from __future__ import annotations


class DiscoveryProvider:
    APOLLO = "apollo"
    STUB = "stub"


class DiscoveryStatus:
    """Lifecycle of an Apollo-driven ICP discovery run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CompanyType:
    """High-level type of organization we discover."""

    OPERATING_COMPANY = "operating_company"
    PRIVATE_EQUITY = "private_equity"
    VENTURE_CAPITAL = "venture_capital"
    FAMILY_OFFICE = "family_office"
    HEDGE_FUND = "hedge_fund"
    INVESTMENT_BANK = "investment_bank"
    ADVISORY = "advisory"
    OTHER = "other"


class RoleCategory:
    """What kind of decision-maker a prospect is, relative to networking.

    Ordered loosely by relevance to board-seat sourcing. The board-specific
    personas below are the gatekeepers and sponsors who influence independent
    director appointments (see services/contacts.board_fit_score).
    """

    # --- Board-access gatekeepers (Tier 1) ---
    BOARD_SEARCH_CONSULTANT = "board_search_consultant"  # executive/board search firms
    TALENT_PARTNER = "talent_partner"                    # PE talent / human-capital partners
    OPERATING_PARTNER = "operating_partner"              # PE operating / value-creation partners

    # --- Board peers / sponsors (Tier 2) ---
    AUDIT_COMMITTEE = "audit_committee"                  # audit committee chairs/members
    GOVERNANCE = "governance"                            # nominating & governance committee
    INDEPENDENT_DIRECTOR = "independent_director"        # independent / non-executive directors
    BOARD_MEMBER = "board_member"                        # board chair / generic director

    # --- Operators & investors (Tier 3) ---
    INVESTOR = "investor"
    CEO = "ceo"
    FOUNDER = "founder"
    EXECUTIVE = "executive"
    DECISION_MAKER = "decision_maker"
    # --- Technical / IC roles (hiring, partnerships, recruiting) ---
    ENGINEER = "engineer"
    OTHER = "other"


class OpportunityType:
    """The kind of relationship opportunity a prospect represents."""

    ADVISORY = "advisory"
    BOARD = "board"
    CONSULTING = "consulting"
    INVESTMENT = "investment"
    ACQUISITION = "acquisition"
    PARTNERSHIP = "partnership"
    EXECUTIVE_ROLE = "executive_role"
    NETWORKING = "networking"


class ProspectStatus:
    """Lifecycle of a discovered person as we work them through outreach."""

    NEW = "new"                # just discovered
    REVIEW = "review"          # scored, awaiting human review
    APPROVED = "approved"      # approved for outreach
    REJECTED = "rejected"      # not a fit
    OUTREACH = "outreach"      # outreach in progress
    CONNECTED = "connected"    # warm relationship established
    CLOSED = "closed"          # done / archived


class EnrichmentStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ENRICHED = "enriched"
    FAILED = "failed"


class PhoneRevealStatus:
    """Async Apollo phone reveal lifecycle (webhook delivery)."""

    PENDING = "pending"
    REVEALED = "revealed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    SKIPPED = "skipped"


class EmailStatus:
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    REPLIED = "replied"
    BOUNCED = "bounced"
    NOT_INTERESTED = "not_interested"
    FOLLOW_UP_NEEDED = "follow_up_needed"


class BulkCampaignStatus:
    """Lifecycle of a pasted-list bulk email campaign."""

    COLLECTING = "collecting"  # gathering recipients + brief in the chat
    LOOKING_UP = "looking_up"  # background job is finding missing email addresses
    DRAFTING = "drafting"      # background job is writing the emails
    READY = "ready"            # drafts written, awaiting review/approval
    SENDING = "sending"        # background job is sending approved emails
    SENT = "sent"              # everything drafted has been sent


class BulkLookupStatus:
    """Lifecycle of the hunt for one pasted person's email address.

    A lookup only ever proposes an address: nothing reaches the recipient list
    until a human moves it to ACCEPTED.
    """

    PENDING = "pending"        # queued, not looked up yet
    FOUND = "found"            # an address was found, awaiting the user's call
    NOT_FOUND = "not_found"    # identified the person, but no address available
    AMBIGUOUS = "ambiguous"    # could not tell which real person this is
    ACCEPTED = "accepted"      # user took the address; the contact can be emailed
    REJECTED = "rejected"      # user dismissed this person
    ERROR = "error"            # the lookup itself failed (API/network)


class CallStatus:
    QUEUED = "queued"
    APPROVED = "approved"
    DIALING = "dialing"
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    HANDOFF_NEEDED = "handoff_needed"
    MEETING_REQUESTED = "meeting_requested"
    FAILED = "failed"


class LinkedInStatus:
    """Lifecycle of a LinkedIn outreach message.

    draft -> approved -> [invite_sent (awaiting acceptance) -> sent] or [sent]
    sent -> replied. failed/not_interested are terminal.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    # Not connected yet: a connection invitation was sent; the message auto-sends
    # once the invite is accepted (detected by the poller).
    INVITE_SENT = "invite_sent"
    # Direct message delivered (either they were connected, or invite accepted).
    SENT = "sent"
    REPLIED = "replied"
    NOT_INTERESTED = "not_interested"
    FAILED = "failed"


class ApprovalStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SuppressionScope:
    CONTACT = "contact"
    COMPANY = "company"
    DOMAIN = "domain"
    EMAIL = "email"


class AuditAction:
    """High-level categories for the audit log."""

    DISCOVER = "discover"
    ENRICHMENT = "enrichment"
    PHONE_REVEAL = "phone_reveal"
    INSIGHT = "insight"
    PROSPECT_APPROVAL = "prospect_approval"
    PRINCIPAL = "principal"
    SEARCH_DEFINITION = "search_definition"
    EMAIL_DRAFT = "email_draft"
    EMAIL_APPROVAL = "email_approval"
    EMAIL_SEND = "email_send"
    CALL_SCRIPT = "call_script"
    CALL_APPROVAL = "call_approval"
    CALL_PLACED = "call_placed"
    LINKEDIN_DRAFT = "linkedin_draft"
    LINKEDIN_APPROVAL = "linkedin_approval"
    LINKEDIN_SEND = "linkedin_send"
    LINKEDIN_INVITE = "linkedin_invite"
    SUPPRESSION = "suppression"
