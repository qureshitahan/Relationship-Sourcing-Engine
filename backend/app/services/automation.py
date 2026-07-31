"""Backend-only automated daily outreach (opt-in via AUTOMATION_ENABLED).

Nothing here runs unless ``settings.automation_enabled`` is true. When it is:

  * Three EMAIL campaigns are seeded onto the existing autonomous-agent
    scheduler (one per tekhqs mailbox) and run once per weekday at
    ``automation_email_hour_utc`` (08:00 UTC = 1PM PKT). Each discovers →
    qualifies → drafts → auto-sends up to 500/day, then emails a per-account
    confirmation "digest" (the existing agent digest) to the notify address.
  * A daily LINKEDIN job (``run_linkedin_daily``) sends up to 50 DMs/invites per
    connected account (Dalbir/Farah) at ``automation_linkedin_hour_utc``
    (07:00 UTC = 12PM PKT) and emails a per-account confirmation.

Two campaign angles, mapped to accounts:
  * C1 — Dalbir's own voice: pain point → how AI helps → 10x bottom line;
    CEO / board of directors at US multi-site healthcare. (Dalbir email + LI.)
  * C2 — a PharmD colleague of Dalbir (Tek HQS AI): time/quality in 503a/503b,
    5x quality-metric gains, book 15 min; pharmacy & quality leaders/owners at
    503a/503b compounding pharmacies. (Scott + Farah email; Farah LI.)

Everything reuses the tested discovery / drafting / sending / digest services,
so no existing behaviour changes.
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.agent_config import AgentConfig
from app.models.agent_playbook import AgentPlaybook
from app.models.contact import Contact
from app.models.enums import LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal

logger = logging.getLogger(__name__)

# Configs/playbooks created here carry this prefix so reconcile can find them
# without ever touching a user's own campaigns.
AUTO_PREFIX = "[auto] "

# Built-in default LinkedIn account for Farah (overridable via env). Dalbir's is
# the env UNIPILE_ACCOUNT_ID.
_FARAH_LINKEDIN_ACCOUNT_ID = "1L8ehDsyQUe2xNajZ078gg"


# --- Campaign angles --------------------------------------------------------


@dataclass(frozen=True)
class Campaign:
    key: str
    label: str
    objective_prompt: str
    criteria: dict


OBJECTIVE_C1 = (
    "Book a short introductory call with the CEO or a board director of US-based, "
    "multi-site healthcare organizations.\n\n"
    "Personalize every message by: (1) naming a specific operational or financial "
    "pain point their organization likely faces; (2) explaining concretely how AI "
    "can address that pain point; and (3) referencing that we have implemented "
    "something similar before and delivered a 10x improvement to the bottom line. "
    "Keep it credible, specific and concise, ending with a clear ask for a brief call.\n\n"
    "Write in the first person as Dalbir Bains (Tek HQS AI). Target CEOs and members "
    "of the board of directors ONLY, at multi-site healthcare providers/operators in "
    "the United States."
)

CRITERIA_C1 = {
    # Broadened for volume (user-authorized): all senior healthcare
    # decision-makers, not just CEO/board. The objective + qualify step keep the
    # focus on multi-site healthcare leaders; discovery stays wide so the daily
    # target can be met. Discovery also auto-expands toward the target.
    "titles": [
        "CEO", "Chief Executive Officer", "President", "Chief Operating Officer",
        "Chief Financial Officer", "Chief Medical Officer", "Chief Nursing Officer",
        "Owner", "Founder", "Managing Director", "Executive Director",
        "Administrator", "Regional Vice President", "VP Operations", "Board Member",
        "Board Director", "Independent Director", "Chairman",
    ],
    "seniorities": ["owner", "founder", "c_suite", "partner", "vp", "director"],
    "geographies": ["United States"],
    "industries": ["Hospital & Health Care", "Health Care", "Medical Practice"],
    "company_types": [],
    "healthcare_sectors": [
        "Health System", "Multi-site Provider", "Behavioral Health", "Home Health",
        "Hospital", "Nursing Home", "Ambulatory",
    ],
    # Keep keywords/themes EMPTY so Apollo isn't narrowed — title + industry +
    # US is the widest net that still returns healthcare leaders.
    "keywords": [],
    "themes": [],
    "organization_job_titles": [],
    "employee_min": None,
    "employee_max": None,
    "contact_email_status": [],
}

OBJECTIVE_C2 = (
    "Book a 15-minute call with the pharmacy and quality leadership (and owners) of "
    "503a and 503b compounding pharmacies in the United States.\n\n"
    "Write in the first person as a pharmacist and PharmD who has been working with "
    "Dalbir Bains to build agentic AI workflows through my company, Tek HQS AI. Open "
    "by noting that Dalbir provided their contact. Speak to the value of \"time\" and "
    "\"quality\" in a 503a and 503b, and reference that the AI tools we implemented "
    "have produced 5x improvements in the metrics that matter to the quality "
    "department, to customers, and to shareholders. Personalize with a specific pain "
    "point for their facility, then ask to book 15 minutes to share these benefits "
    "with them and their team."
)

CRITERIA_C2 = {
    # Broadened for volume (user-authorized): pharmacy + quality leaders and
    # owners across compounding / specialty / infusion pharmacy, not only the
    # narrow 503a/503b keyword pool. The 5x/503a/503b message + qualify step
    # keep it on-target; discovery stays wide enough to sustain the daily number.
    "titles": [
        "Owner", "President", "CEO", "Chief Executive Officer",
        "Pharmacist in Charge", "Director of Pharmacy", "Pharmacy Director",
        "Chief Pharmacy Officer", "VP Pharmacy", "Pharmacy Manager",
        "Director of Quality", "Quality Director", "VP Quality",
        "Director of Operations", "Compounding Pharmacist", "Clinical Pharmacist",
        "Staff Pharmacist", "General Manager",
    ],
    "seniorities": [
        "owner", "founder", "c_suite", "vp", "director", "head", "manager", "senior",
    ],
    "geographies": ["United States"],
    "industries": ["Pharmaceuticals", "Hospital & Health Care", "Health Care"],
    "company_types": [],
    "healthcare_sectors": [
        "Compounding Pharmacy", "Specialty Pharmacy", "Infusion", "Sterile Compounding",
    ],
    "keywords": [],
    "themes": [],
    "organization_job_titles": [],
    "employee_min": None,
    "employee_max": None,
    "contact_email_status": [],
}

C1 = Campaign("c1", "Multi-site healthcare (10x)", OBJECTIVE_C1, CRITERIA_C1)
C2 = Campaign("c2", "503a/503b pharmacy (5x)", OBJECTIVE_C2, CRITERIA_C2)


# --- Account specs ----------------------------------------------------------


@dataclass(frozen=True)
class EmailSpec:
    key: str
    principal_name: str
    mailbox_id: str
    from_email: str
    campaign: Campaign


@dataclass(frozen=True)
class LinkedInSpec:
    key: str
    label: str
    account_id: str
    mailbox_id: str
    campaign: Campaign


def email_specs() -> list[EmailSpec]:
    return [
        EmailSpec("dalbir", "Dalbir Bains", "dalbir_tekhqs", "dalbir.bains@tekhqs.ai", C1),
        EmailSpec("scott", "Scott", "scott_tekhqs", "scott@tekhqs.ai", C2),
        EmailSpec("farah", "Farah", "farah_tekhqs", "farah.t@tekhqs.ai", C2),
    ]


def linkedin_specs() -> list[LinkedInSpec]:
    """Which connected LinkedIn accounts send DMs, and with which angle.

    Overridable via AUTOMATION_LINKEDIN_ACCOUNTS (JSON list of
    {label, account_id, mailbox_id, campaign?}); otherwise Dalbir (env account)
    → C1 and Farah (built-in id) → C2.
    """
    raw = (settings.automation_linkedin_accounts_env or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            out: list[LinkedInSpec] = []
            for item in data or []:
                if not isinstance(item, dict) or not item.get("account_id"):
                    continue
                camp = C2 if str(item.get("campaign", "")).lower() in ("c2", "503b", "pharmacy") else C1
                out.append(
                    LinkedInSpec(
                        key=str(item.get("label") or item.get("account_id")),
                        label=str(item.get("label") or "LinkedIn"),
                        account_id=str(item["account_id"]),
                        mailbox_id=str(item.get("mailbox_id") or ""),
                        campaign=camp,
                    )
                )
            if out:
                return out
        except json.JSONDecodeError as exc:
            logger.warning("AUTOMATION_LINKEDIN_ACCOUNTS is not valid JSON, using defaults: %s", exc)
    return [
        LinkedInSpec("dalbir", "Dalbir", settings.unipile_account_id or "", "dalbir_tekhqs", C1),
        LinkedInSpec("farah", "Farah", _FARAH_LINKEDIN_ACCOUNT_ID, "farah_tekhqs", C2),
    ]


# --- Principal profiles (shape the outreach voice) --------------------------


def _principal_profile(spec: EmailSpec) -> dict:
    """Profile fields for a sending principal, tuned to its campaign angle."""
    if spec.campaign.key == "c1":
        return {
            "headline": "Healthcare operator & AI transformation advisor",
            "background": (
                f"{spec.principal_name} partners with CEOs and boards of multi-site "
                "healthcare organizations to deploy agentic AI that improves clinical "
                "operations and the bottom line, with a track record of 10x "
                "improvements. Work delivered via Tek HQS AI."
            ),
            "value_props": [
                "Agentic AI for healthcare operations",
                "10x bottom-line improvement",
                "Multi-site healthcare scaling",
            ],
            "target_sectors": ["Multi-site Healthcare", "Health Systems"],
            "target_titles": ["CEO", "Board Director"],
        }
    return {
        "headline": "Pharmacist (PharmD) & agentic-AI builder, Tek HQS AI",
        "background": (
            f"{spec.principal_name} is a pharmacist (PharmD) who builds agentic AI "
            "workflows with Dalbir Bains via Tek HQS AI, focused on time and quality "
            "gains in 503a/503b compounding pharmacies — with 5x improvements in the "
            "quality, customer and shareholder metrics that matter most."
        ),
        "value_props": [
            "Agentic AI for 503a/503b pharmacy",
            "5x quality-metric improvement",
            "Time & quality gains in compounding",
        ],
        "target_sectors": ["503a/503b Compounding Pharmacy", "Specialty Pharmacy"],
        "target_titles": ["Director of Pharmacy", "Owner", "Quality Director"],
    }


# --- Email seeding (idempotent) ---------------------------------------------


def _get_or_create_principal(db: Session, spec: EmailSpec) -> Principal:
    """Find the sending principal by its bound mailbox, else create one.

    Matching on ``outreach_mailbox_id`` guarantees we never touch a pre-existing
    principal that has no mailbox assigned (e.g. a legacy 'Dalbir Bains' row).
    """
    principal = db.execute(
        select(Principal).where(Principal.outreach_mailbox_id == spec.mailbox_id)
    ).scalars().first()
    profile = _principal_profile(spec)
    if principal is None:
        principal = Principal(
            name=spec.principal_name,
            outreach_mailbox_id=spec.mailbox_id,
            mailbox_daily_cap=settings.automation_email_daily_cap,
            geographies=["United States"],
            email_signature=f"{spec.principal_name}\nTek HQS AI\n{spec.from_email}",
            is_active=True,
            **profile,
        )
        db.add(principal)
        db.commit()
        db.refresh(principal)
        logger.info("Automation: created principal %s (%s)", principal.id, spec.principal_name)
    else:
        # Only RAISE the shared cap toward the target (never reduce an existing
        # one); fill in a signature if missing. Never overwrite a name.
        if (principal.mailbox_daily_cap or 0) < settings.automation_email_daily_cap:
            principal.mailbox_daily_cap = settings.automation_email_daily_cap
        if not principal.email_signature:
            principal.email_signature = f"{spec.principal_name}\nTek HQS AI\n{spec.from_email}"
        db.commit()
    return principal


def _playbook_name(spec: EmailSpec) -> str:
    return f"{AUTO_PREFIX}{spec.campaign.label}"


def _config_name(spec: EmailSpec) -> str:
    return f"{AUTO_PREFIX}{spec.principal_name} — {spec.campaign.label}"


def _get_or_create_playbook(db: Session, principal: Principal, spec: EmailSpec) -> AgentPlaybook:
    name = _playbook_name(spec)
    criteria = {**spec.campaign.criteria, "people_limit": settings.automation_email_discover_target}
    pb = db.execute(
        select(AgentPlaybook).where(
            AgentPlaybook.principal_id == principal.id,
            AgentPlaybook.name == name,
        )
    ).scalars().first()
    if pb is None:
        pb = AgentPlaybook(
            principal_id=principal.id,
            name=name,
            objective_prompt=spec.campaign.objective_prompt,
            clarifying_answers={},
            criteria=criteria,
        )
        db.add(pb)
    else:
        pb.objective_prompt = spec.campaign.objective_prompt
        pb.criteria = criteria
    db.commit()
    db.refresh(pb)
    return pb


def _get_or_create_config(
    db: Session, principal: Principal, playbook: AgentPlaybook, spec: EmailSpec
) -> AgentConfig:
    name = _config_name(spec)
    cfg = db.execute(
        select(AgentConfig).where(
            AgentConfig.principal_id == principal.id,
            AgentConfig.name == name,
        )
    ).scalars().first()
    if cfg is None:
        cfg = AgentConfig(principal_id=principal.id, name=name)
        db.add(cfg)
    cfg.playbook_id = playbook.id
    cfg.enabled = True
    cfg.run_hour_utc = settings.automation_email_hour_utc
    cfg.weekdays_only = True
    cfg.auto_send = True
    cfg.discover_target = settings.automation_email_discover_target
    cfg.daily_send_cap = settings.automation_email_daily_cap
    # Let volume through: research/qualify keeps obviously-bad fits out but does
    # not throttle the pipeline below the daily target.
    cfg.qualify_min = 30.0
    cfg.auto_reject_below = 20.0
    # Keep the daily number focused on NEW outreach (no follow-ups eating the cap).
    cfg.followup_enabled = False
    cfg.digest_recipients = [settings.automation_notify_email] if settings.automation_notify_email else []
    db.commit()
    db.refresh(cfg)
    return cfg


def seed_email_automation(db: Session) -> list[int]:
    """Create/refresh the three email campaigns. Returns their config ids."""
    ids: list[int] = []
    for spec in email_specs():
        principal = _get_or_create_principal(db, spec)
        pb = _get_or_create_playbook(db, principal, spec)
        cfg = _get_or_create_config(db, principal, pb, spec)
        ids.append(cfg.id)
        logger.info(
            "Automation email campaign ready: %s -> principal %s, config %s (%s)",
            spec.key, principal.id, cfg.id, spec.campaign.label,
        )
    return ids


def reconcile_automation(db: Session) -> list[int]:
    """Master switch. Enabled => seed/enable the campaigns; disabled => turn any
    previously-seeded automation campaigns OFF (a real kill switch). Only ever
    touches configs created here (``[auto] `` prefix)."""
    if settings.automation_enabled:
        return seed_email_automation(db)
    disabled = 0
    for cfg in db.execute(
        select(AgentConfig).where(AgentConfig.name.like(f"{AUTO_PREFIX}%"))
    ).scalars().all():
        if cfg.enabled:
            cfg.enabled = False
            disabled += 1
    if disabled:
        db.commit()
        logger.info("Automation disabled: turned off %s seeded campaign(s)", disabled)
    return []


# --- LinkedIn daily job -----------------------------------------------------


def _discovery_criteria(campaign: Campaign, people_limit: int):
    from app.services.agent.planner import criteria_from_dict
    from app.services.enrichment.base import DiscoveryCriteria

    norm = criteria_from_dict({**campaign.criteria, "people_limit": people_limit})
    return DiscoveryCriteria(
        industries=norm.get("industries"),
        company_types=norm.get("company_types"),
        healthcare_sectors=norm.get("healthcare_sectors"),
        geographies=norm.get("geographies"),
        titles=norm.get("titles"),
        seniorities=norm.get("seniorities"),
        contact_email_status=[],  # LinkedIn never needs an email reveal.
        organization_domains=norm.get("organization_domains"),
        keywords=norm.get("keywords"),
        themes=norm.get("themes"),
        employee_min=norm.get("employee_min"),
        employee_max=norm.get("employee_max"),
        org_limit=max(people_limit, settings.discovery_org_limit),
        people_limit=people_limit,
        organization_job_titles=norm.get("organization_job_titles"),
    )


def _linkedin_sent_today(db: Session, account_id: str) -> int:
    """Invites + DMs already sent TODAY from a specific connected account."""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(
        select(func.count())
        .select_from(LinkedInMessage)
        .where(
            LinkedInMessage.from_account == account_id,
            or_(
                LinkedInMessage.sent_at >= start,
                LinkedInMessage.invitation_sent_at >= start,
            ),
        )
    ).scalar_one()


def _already_contacted_on_linkedin(
    db: Session, account_id: str, linkedin_url: Optional[str]
) -> bool:
    """True if THIS account already invited/messaged this LinkedIn profile in any
    prior run. Dedup by LinkedIn IDENTITY (public identifier), not the contact
    row — discovery re-surfaces the same people as new rows, and re-inviting them
    triggers Unipile ``cannot_resend_yet``."""
    from app.services.linkedin_providers import public_identifier_from_url

    pid = public_identifier_from_url(linkedin_url or "")
    if not pid:
        return False
    n = db.execute(
        select(func.count())
        .select_from(LinkedInMessage)
        .where(
            LinkedInMessage.from_account == account_id,
            LinkedInMessage.public_identifier == pid,
            LinkedInMessage.status.in_(
                [LinkedInStatus.INVITE_SENT, LinkedInStatus.SENT, LinkedInStatus.REPLIED]
            ),
        )
    ).scalar_one()
    return n > 0


def _run_linkedin_account(db: Session, spec: LinkedInSpec) -> dict:
    """Discover, draft and send up to the daily cap for one LinkedIn account."""
    from app.api.routes.linkedin import SendError, _existing_open_message, perform_linkedin_send
    from app.models.company import Company
    from app.models.relevance_insight import RelevanceInsight
    from app.services.discovery import run_discovery
    from app.services.linkedin_outreach import generate_linkedin_content
    from app.services.linkedin_providers import public_identifier_from_url

    result = {
        "label": spec.label, "account_id": spec.account_id, "campaign": spec.campaign.label,
        "discovered": 0, "messageable": 0, "drafted": 0, "invites": 0, "dms": 0,
        "skipped": 0, "errors": [],
    }
    if not spec.account_id:
        result["errors"].append("no account_id configured")
        return result

    principal = db.execute(
        select(Principal).where(Principal.outreach_mailbox_id == spec.mailbox_id)
    ).scalars().first()
    if principal is None:
        result["errors"].append(f"no principal for mailbox {spec.mailbox_id}")
        return result
    result["principal_id"] = principal.id

    cap = max(0, int(settings.automation_linkedin_daily_cap))
    budget = max(0, cap - _linkedin_sent_today(db, spec.account_id))
    if budget <= 0:
        logger.info("LinkedIn automation %s: daily cap %s already reached", spec.label, cap)
        return result

    # 1) Discover fresh prospects (no email reveal).
    try:
        run = run_discovery(
            db, principal,
            _discovery_criteria(spec.campaign, settings.automation_linkedin_discover_target),
            requested_by="automation", generate_insights=False, people_first=True,
            search_goal=spec.campaign.objective_prompt,
        )
        result["discovered"] = run.people_imported or 0
        contacts = list(
            db.execute(select(Contact).where(Contact.discovery_run_id == run.id)).scalars().all()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("LinkedIn automation %s discovery failed", spec.label)
        result["errors"].append(f"discovery: {exc}")
        contacts = []

    # 1b) Apollo People SEARCH does NOT return LinkedIn profile URLs — only the
    # reveal/enrich step does. Reveal a bounded slice so enough prospects have a
    # personal /in/ URL to message. (Reveal also fills email; costs Apollo credits.)
    if contacts:
        from app.services.contacts import reveal_contacts_bulk

        need_url = [c for c in contacts if not public_identifier_from_url(c.linkedin_url or "")]
        # Reveal ~2x the remaining budget — enough to clear the cap given the high
        # messageable rate, without burning credits on the whole discovery batch.
        reveal_slice = need_url[: max(budget * 2, budget + 20)]
        if reveal_slice:
            try:
                reveal_contacts_bulk(db, reveal_slice)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("LinkedIn automation %s reveal failed: %s", spec.label, exc)
                result["errors"].append(f"reveal: {exc}")
    result["messageable"] = sum(
        1 for c in contacts if public_identifier_from_url(c.linkedin_url or "")
    )

    # 2) Draft + send up to the remaining budget, paced (jittered anti-burst).
    # Stop early if the account starts refusing (rate limit) instead of hammering
    # it with 100 failing calls, and cap total attempts so failures can't run away.
    delay = max(0.0, float(settings.automation_linkedin_send_delay_seconds or 0))
    attempts = 0
    consecutive_failures = 0
    max_attempts = budget + max(10, budget // 5)   # small margin over the cap
    max_consecutive_failures = 5                   # circuit breaker
    for contact in contacts:
        if (result["invites"] + result["dms"]) >= budget or attempts >= max_attempts:
            break
        if consecutive_failures >= max_consecutive_failures:
            result["errors"].append(
                f"stopped early after {consecutive_failures} consecutive send failures "
                "(account likely at a LinkedIn rate limit)"
            )
            break
        if not public_identifier_from_url(contact.linkedin_url or "") or contact.do_not_contact:
            result["skipped"] += 1
            continue
        # Skip if this contact row already has an open message, OR if this account
        # already invited/messaged this LinkedIn identity in a prior run.
        if _existing_open_message(db, principal.id, contact.id) is not None:
            result["skipped"] += 1
            continue
        if _already_contacted_on_linkedin(db, spec.account_id, contact.linkedin_url):
            result["skipped"] += 1
            continue
        attempts += 1
        try:
            company = db.get(Company, contact.company_id) if contact.company_id else None
            insight = db.execute(
                select(RelevanceInsight)
                .where(
                    RelevanceInsight.principal_id == principal.id,
                    RelevanceInsight.contact_id == contact.id,
                )
                .order_by(RelevanceInsight.created_at.desc())
            ).scalars().first()
            content = generate_linkedin_content(
                db, principal, contact, company, insight,
                outreach_goal=spec.campaign.objective_prompt,
            )
            msg = LinkedInMessage(
                principal_id=principal.id,
                company_id=contact.company_id,
                contact_id=contact.id,
                insight_id=insight.id if insight else None,
                body=content.body,
                invitation_note=content.invitation_note,
                status=LinkedInStatus.APPROVED,
                approved_by="automation",
                approved_at=datetime.utcnow(),
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            result["drafted"] += 1
            perform_linkedin_send(db, msg, account_id=spec.account_id)
            if msg.status == LinkedInStatus.SENT:
                result["dms"] += 1
                consecutive_failures = 0
            elif msg.status == LinkedInStatus.INVITE_SENT:
                result["invites"] += 1
                consecutive_failures = 0
            else:
                consecutive_failures += 1
            if delay:
                time.sleep(delay + random.uniform(0, delay * 0.5))
        except SendError as exc:
            db.rollback()
            consecutive_failures += 1
            result["errors"].append(f"{contact.name or contact.id}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            consecutive_failures += 1
            result["errors"].append(f"{contact.name or contact.id}: {exc}")
    return result


def _send_linkedin_confirmation(db: Session, spec: LinkedInSpec, result: dict) -> None:
    from app.services.email_providers import mailbox_for_principal, provider_for_mailbox

    to_email = settings.automation_notify_email
    if not to_email:
        return
    principal = db.execute(
        select(Principal).where(Principal.outreach_mailbox_id == spec.mailbox_id)
    ).scalars().first()
    if principal is None:
        return
    mailbox = mailbox_for_principal(principal, strict=False)
    provider = provider_for_mailbox(mailbox)
    total = result["invites"] + result["dms"]
    subject = (
        f"LinkedIn outreach — {spec.label}: {total} sent "
        f"({datetime.utcnow():%b %d})"
    )
    lines = [
        f"LINKEDIN DAILY OUTREACH — {spec.label}",
        f"{datetime.utcnow():%Y-%m-%d %H:%M} UTC · account {spec.account_id}",
        f"Angle: {spec.campaign.label}",
        "",
        f"  Discovered:     {result['discovered']}",
        f"  Messageable:    {result.get('messageable', 0)}",
        f"  Drafted:        {result['drafted']}",
        f"  Invites sent:   {result['invites']}",
        f"  Direct DMs:     {result['dms']}",
        f"  Skipped:        {result['skipped']}",
        f"  Daily cap:      {settings.automation_linkedin_daily_cap}",
        "",
    ]
    if result["errors"]:
        lines += ["ISSUES", *[f"  - {e}" for e in result["errors"][:10]], ""]
    lines += ["—", "Sent automatically by the outreach engine."]
    provider.send(
        to_email=to_email,
        subject=subject,
        body="\n".join(lines),
        from_email=mailbox.from_email,
        from_name=mailbox.from_name or spec.label,
    )


def run_linkedin_daily() -> None:
    """Run the LinkedIn DM job for every configured account (own DB session)."""
    if not settings.automation_enabled:
        return
    db = SessionLocal()
    try:
        for spec in linkedin_specs():
            try:
                result = _run_linkedin_account(db, spec)
                logger.info("LinkedIn automation %s: %s", spec.label, result)
            except Exception:  # noqa: BLE001
                logger.exception("LinkedIn automation account %s crashed", spec.label)
                continue
            try:
                _send_linkedin_confirmation(db, spec, result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LinkedIn confirmation (%s) failed: %s", spec.label, exc)
    finally:
        db.close()


def run_all_now() -> None:
    """Trigger ONE full paced outreach cycle immediately (email + LinkedIn).

    Email: launches a real agent run per enabled automation campaign. The agent's
    drip scheduler (``auto_schedule``) spreads each account's sends across the
    business day, so 500/account never goes out as a burst — the email_scheduler
    delivers them paced. LinkedIn: runs the daily job now (spaced + capped).

    Requires a persistently-running backend (the email_scheduler must stay up to
    deliver the drip). No-op unless automation is enabled.
    """
    if not settings.automation_enabled:
        logger.info("run_all_now skipped: automation disabled")
        return

    # Email — one agent run per enabled automation campaign (drips across the day).
    try:
        from app.services.agent.orchestrator import launch_run

        db = SessionLocal()
        try:
            configs = db.execute(
                select(AgentConfig).where(
                    AgentConfig.name.like(f"{AUTO_PREFIX}%"),
                    AgentConfig.enabled.is_(True),
                )
            ).scalars().all()
            targets = [(c.principal_id, c.id, c.playbook_id) for c in configs]
            # Stamp last_run_at NOW so today's agent_scheduler tick (same run_hour)
            # sees these as already-run and won't launch a duplicate concurrent run.
            _now = datetime.utcnow()
            for c in configs:
                c.last_run_at = _now
            db.commit()
        finally:
            db.close()
        for principal_id, campaign_id, playbook_id in targets:
            launch_run(
                principal_id, trigger="automation-now",
                campaign_id=campaign_id, playbook_id=playbook_id,
            )
            logger.info("run_all_now: launched email run for campaign %s", campaign_id)
    except Exception:  # noqa: BLE001
        logger.exception("run_all_now: email launch failed")

    # LinkedIn — run the daily job now (paced + capped internally).
    try:
        run_linkedin_daily()
    except Exception:  # noqa: BLE001
        logger.exception("run_all_now: LinkedIn run failed")
