"""The autonomous outreach agent.

One :func:`execute_run` call performs the entire daily pipeline end-to-end with
no human input:

  1. Discover new people from the principal's saved search (deduped).
  2. Research & score each; auto-reject the weak ones.
  3. Auto-approve the strong ones, reveal their email.
  4. Draft a personalized first-person outreach for each.
  5. Optionally auto-send up to a daily cap (the rest wait as ready drafts).
  6. Draft / send follow-ups to anyone silent past the threshold.
  7. Record a full summary and email a digest to configured recipients.

Everything is wrapped so a failure in one prospect never aborts the run.
"""
from __future__ import annotations

import copy
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.agent_config import AgentConfig
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.agent_variant import AgentVariant
from app.models.company import Company
from app.models.contact import Contact
from app.models.email_draft import EmailDraft
from app.models.enums import AuditAction, EmailStatus, ProspectStatus
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.models.search_definition import SearchDefinition
from app.services.agent.planner import criteria_from_dict
from app.services.audit import log_action
from app.services.discovery import run_discovery
from app.services.email_providers import (
    MailboxUnassignedError,
    mailbox_for_principal,
    provider_for_mailbox,
)
from app.services.enrichment.base import DiscoveryCriteria
from app.services import optimization
from app.services.insights.engine import generate_insight, generate_outreach
from app.services.outreach_eligibility import outreach_draft_blockers

logger = logging.getLogger(__name__)


# --- Config -----------------------------------------------------------------

def get_or_create_config(db: Session, principal_id: int) -> AgentConfig:
    """Return the principal's primary (lowest-id) campaign config, creating one
    if none exists. With multi-campaign support a principal may own several
    AgentConfig rows; this returns the first as a safe default."""
    config = db.execute(
        select(AgentConfig)
        .where(AgentConfig.principal_id == principal_id)
        .order_by(AgentConfig.id.asc())
    ).scalars().first()
    if config is None:
        config = AgentConfig(principal_id=principal_id, digest_recipients=[])
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def resolve_campaign(
    db: Session, principal_id: int, campaign_id: Optional[int]
) -> AgentConfig:
    """Load the AgentConfig (campaign) for a run, falling back to the primary."""
    if campaign_id is not None:
        config = db.get(AgentConfig, campaign_id)
        if config is not None and config.principal_id == principal_id:
            return config
    return get_or_create_config(db, principal_id)


def _resolve_search_definition(
    db: Session, principal_id: int, config: AgentConfig
) -> Optional[SearchDefinition]:
    if config.search_definition_id:
        sd = db.get(SearchDefinition, config.search_definition_id)
        if sd:
            return sd
    return db.execute(
        select(SearchDefinition)
        .where(SearchDefinition.principal_id == principal_id)
        .order_by(SearchDefinition.created_at.desc())
    ).scalars().first()


def _criteria_from_dict(data: dict, people_limit: int) -> DiscoveryCriteria:
    norm = criteria_from_dict({**data, "people_limit": people_limit})
    # Cost guard: for an email outreach campaign, only surface people Apollo can
    # actually reach. The people-search "has_email" signal is free, so filtering
    # out unreachable contacts here avoids spending expensive LLM research on
    # people we could never email. Callers can still override per playbook.
    email_status = norm.get("contact_email_status") or [
        "verified",
        "unverified",
        "likely to engage",
    ]
    return DiscoveryCriteria(
        industries=norm.get("industries"),
        company_types=norm.get("company_types"),
        healthcare_sectors=norm.get("healthcare_sectors"),
        geographies=norm.get("geographies"),
        titles=norm.get("titles"),
        seniorities=norm.get("seniorities"),
        contact_email_status=email_status,
        organization_domains=norm.get("organization_domains"),
        keywords=norm.get("keywords"),
        themes=norm.get("themes"),
        employee_min=norm.get("employee_min"),
        employee_max=norm.get("employee_max"),
        org_limit=max(people_limit, settings.discovery_org_limit),
        people_limit=people_limit,
        organization_job_titles=norm.get("organization_job_titles"),
    )


def _resolve_playbook(
    db: Session, principal_id: int, config: AgentConfig, playbook_id: Optional[int]
) -> Optional[AgentPlaybook]:
    if playbook_id:
        pb = db.get(AgentPlaybook, playbook_id)
        if pb and pb.principal_id == principal_id:
            return pb
    if config.playbook_id:
        return db.get(AgentPlaybook, config.playbook_id)
    return db.execute(
        select(AgentPlaybook)
        .where(AgentPlaybook.principal_id == principal_id)
        .order_by(AgentPlaybook.updated_at.desc())
    ).scalars().first()


def _set_summary(run: AgentRun, **changes) -> None:
    """Write keys into ``run.summary`` so the change is actually persisted.

    ``summary`` is a plain JSON column, so SQLAlchemy decides whether to write it
    by comparing the new value against the loaded one. Helpers here used to build
    that new value from the loaded structure and edit it in place — a shallow
    copy shares its inner dicts, and ``.get("stages", [])`` shares the list
    itself — which mutated the comparison baseline at the same time. The two
    then matched, no change was detected, and the write was skipped.

    That is why run 15 finished with qualified=41 while its summary still showed
    every person as "discovered" with no score, carried only the discovery stage,
    and reported no errors even though the draft loop had recorded 40 of them.

    Deep-copy the base, apply the change to the copy, and flag the attribute
    dirty explicitly so a write happens whatever the comparison concludes.
    """
    base = copy.deepcopy(dict(run.summary or {}))
    base.update(changes)
    run.summary = base
    flag_modified(run, "summary")


def _track_person(
    run: AgentRun,
    contact: Contact,
    company: Optional[Company],
    *,
    status: str,
    score: Optional[float] = None,
) -> None:
    people = list((run.summary or {}).get("people") or [])
    people.append(
        {
            "id": contact.id,
            "name": contact.name,
            "title": contact.title,
            "company": company.name if company else None,
            "score": score,
            "status": status,
        }
    )
    _set_summary(run, people=people)


def _update_person_status(
    run: AgentRun, contact_id: int, status: str, *, score: Optional[float] = None
) -> None:
    people = copy.deepcopy(list((run.summary or {}).get("people") or []))
    for p in people:
        if p.get("id") == contact_id:
            p["status"] = status
            if score is not None:
                p["score"] = score
    _set_summary(run, people=people)


# --- Run lifecycle ----------------------------------------------------------

def create_run(
    db: Session,
    principal_id: int,
    trigger: str,
    *,
    playbook_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    resume: bool = False,
    skip_discovery: bool = False,
) -> AgentRun:
    run = AgentRun(
        principal_id=principal_id,
        campaign_id=campaign_id,
        playbook_id=playbook_id,
        status="running",
        trigger=trigger,
        started_at=datetime.utcnow(),
        summary={
            "stages": [],
            "people": [],
            "resume": bool(resume),
            # Resume-only: pick up the existing backlog without a fresh Discover
            # step. Meaningless (and ignored) unless resume is also set.
            "skip_discovery": bool(skip_discovery) and bool(resume),
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def launch_run(
    principal_id: int,
    trigger: str = "manual",
    *,
    playbook_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    resume: bool = False,
    skip_discovery: bool = False,
) -> AgentRun:
    """Create a run row and execute it on a background daemon thread."""
    db = SessionLocal()
    try:
        run = create_run(
            db,
            principal_id,
            trigger,
            playbook_id=playbook_id,
            campaign_id=campaign_id,
            resume=resume,
            skip_discovery=skip_discovery,
        )
        run_id = run.id
    finally:
        db.close()
    threading.Thread(
        target=execute_run, args=(run_id,), name=f"agent-run-{run_id}", daemon=True
    ).start()
    db2 = SessionLocal()
    try:
        return db2.get(AgentRun, run_id)
    finally:
        db2.close()


def _stage(run: AgentRun, name: str, **data) -> None:
    stages = copy.deepcopy(list((run.summary or {}).get("stages") or []))
    stages.append({"stage": name, **data})
    _set_summary(run, stages=stages)


def _is_cancelled(db: Session, run: AgentRun) -> bool:
    db.refresh(run)
    return bool((run.summary or {}).get("cancel_requested"))


def _abort_if_cancelled(db: Session, run: AgentRun, errors: list[str]) -> bool:
    if not _is_cancelled(db, run):
        return False
    run.status = "cancelled"
    run.finished_at = datetime.utcnow()
    _set_summary(run, errors=errors[:50])
    db.commit()
    return True


# --- Per-prospect workers (run concurrently; see the qualify/draft stages) ---
#
# Both stages were a plain for-loop: one Claude call per prospect, one after the
# next. A 50-person run spent ~28 minutes almost entirely waiting on the network,
# which is why campaigns felt slow while the Discovery flow — which has used a
# thread pool since bulk drafting landed — felt fast.
#
# These mirror discovery_jobs.py exactly: the worker owns a private session, does
# the slow I/O, and returns plain data. Every run-level write (counters, summary,
# draft rows) stays on the main thread, so no ORM object or session is ever
# shared across threads.


def _commit_with_retry(db: Session, *, attempts: int = 5, base_delay: float = 0.3) -> None:
    """Commit, retrying briefly on SQLite lock contention from concurrent workers.

    Several qualify/draft workers each write via their own SessionLocal() at
    once; SQLite serializes writers, and one occasionally loses that race even
    inside its own busy_timeout window under heavy fan-out (bulk_approve_workers
    researching in parallel). A short local retry with backoff clears the
    transient lock instead of failing that prospect's research outright.
    """
    from sqlalchemy.exc import OperationalError

    for attempt in range(attempts):
        try:
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2**attempt))


def _qualify_one(
    contact_id: int, principal_id: int, outreach_goal: Optional[str]
) -> tuple[int, Optional[float], Optional[str]]:
    """Research one prospect. Returns (contact_id, score, error)."""
    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        principal = db.get(Principal, principal_id)
        if contact is None or principal is None:
            return contact_id, None, "prospect or principal not found"
        company = db.get(Company, contact.company_id) if contact.company_id else None
        insight = generate_insight(
            db,
            principal,
            contact=contact,
            company=company,
            outreach_goal=outreach_goal,
            allow_reuse=True,
        )
        score = insight.relevance_score or 0.0
        _commit_with_retry(db)
        return contact_id, score, None
    except Exception as exc:  # noqa: BLE001 - one prospect must not stop the run
        db.rollback()
        logger.warning("Agent qualify failed for contact %s: %s", contact_id, exc)
        return contact_id, None, str(exc)
    finally:
        db.close()


def _draft_one(
    contact_id: int,
    principal_id: int,
    outreach_goal: Optional[str],
    style: Optional[dict],
) -> tuple[int, Any, Optional[int], list[str]]:
    """Reveal + write one outreach email.

    Returns (contact_id, OutreachResult-or-None, insight_id, blockers). The caller
    creates the EmailDraft row; nothing here writes to it.
    """
    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        principal = db.get(Principal, principal_id)
        if contact is None or principal is None:
            return contact_id, None, None, ["prospect or principal not found"]

        # Reveal before checking blockers: Apollo search never returns emails, so
        # "email not revealed" is expected until bulk_match has run.
        reveal_errors: list[str] = []
        if not (contact.email or "").strip():
            _reveal_email(db, contact, reveal_errors)
            db.refresh(contact)

        blockers = outreach_draft_blockers(
            db, principal_id=principal.id, contact=contact
        )
        if blockers:
            return contact_id, None, None, blockers

        company = db.get(Company, contact.company_id) if contact.company_id else None
        insight = _latest_insight(db, principal.id, contact.id)
        content = generate_outreach(
            db, principal, contact, company, insight,
            outreach_goal=outreach_goal, style=style,
        )
        return contact_id, content, (insight.id if insight else None), []
    except Exception as exc:  # noqa: BLE001 - one prospect must not stop the run
        db.rollback()
        logger.warning("Agent draft failed for contact %s: %s", contact_id, exc)
        return contact_id, None, None, [str(exc)]
    finally:
        db.close()


# Caps how many runs execute at once, across every trigger (daily scheduler,
# "Run now", automation). The scheduler starts every campaign due in the same
# hour in its own thread, and each run holds one DB connection for its whole
# duration plus one per concurrent worker — so uncapped, demand outgrew any pool
# size and starved the web requests, which is what put the UI on "Loading...".
#
# Acquired BEFORE the session is opened. A queued run must not sit holding a
# connection while it waits, or it would consume the very resource the cap
# exists to protect.
_RUN_SLOTS = threading.BoundedSemaphore(
    max(1, int(getattr(settings, "agent_max_concurrent_runs", 3)))
)


def execute_run(run_id: int) -> None:
    """Execute the full pipeline for an existing AgentRun (own DB session).

    Waits for a free slot when ``agent_max_concurrent_runs`` runs are already in
    flight, then runs exactly as before. Every caller is a background thread, so
    queueing here delays a run and never an HTTP request.
    """
    with _RUN_SLOTS:
        _execute_run(run_id)


def _execute_run(run_id: int) -> None:
    db = SessionLocal()
    errors: list[str] = []
    try:
        run = db.get(AgentRun, run_id)
        if not run or not run.principal_id:
            return
        principal = db.get(Principal, run.principal_id)
        if not principal:
            run.status = "failed"
            run.error_message = "Principal not found"
            run.finished_at = datetime.utcnow()
            db.commit()
            return
        config = resolve_campaign(db, principal.id, run.campaign_id)
        # Make sure the run is linked to its campaign (older runs / manual calls).
        if run.campaign_id is None:
            run.campaign_id = config.id
            db.commit()

        # 1) DISCOVERY (people-first, broad US search) -----------------------
        playbook = _resolve_playbook(db, principal.id, config, run.playbook_id)
        if not playbook:
            run.status = "failed"
            run.error_message = (
                "No search playbook configured. Describe your goal on the Agent "
                "page, answer the clarifying questions, and save a playbook first."
            )
            run.finished_at = datetime.utcnow()
            db.commit()
            return

        run.playbook_id = playbook.id

        # A/B SEARCH: pick a variant (untested first, then best reply rate).
        from app.services.agent.experiments import ensure_variants, select_variant

        variant = None
        try:
            variants = ensure_variants(db, principal, playbook)
            variant = select_variant(db, variants)
            run.variant_id = variant.id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Variant selection failed, using base playbook: %s", exc)

        variant_criteria = variant.criteria if variant else (playbook.criteria or {})
        # Campaign discover_target wins over playbook criteria (UI edits target there).
        people_limit = int(
            config.discover_target
            or variant_criteria.get("people_limit")
            or (playbook.criteria or {}).get("people_limit")
            or 50
        )
        criteria = _criteria_from_dict(variant_criteria, people_limit)
        _set_summary(
            run,
            playbook_name=playbook.name,
            objective=playbook.objective_prompt,
            criteria=variant_criteria,
            variant_label=variant.label if variant else None,
        )

        new_contact_ids: list[int] = []
        # Resume + skip_discovery: the point is to finish the existing backlog
        # without spending on a fresh Discover step. Everything else below is
        # unchanged — the resume pickup right after this still runs the same
        # way whether discovery ran or was skipped.
        if bool((run.summary or {}).get("skip_discovery")):
            _stage(run, "discovery", imported=0, duplicates=0, skipped=True)
        else:
            try:
                discovery_run = run_discovery(
                    db,
                    principal,
                    criteria,
                    requested_by="agent",
                    generate_insights=False,
                    people_first=True,
                    search_goal=playbook.objective_prompt,
                    campaign_id=config.id,
                    require_email_and_linkedin=bool(config.require_email_and_linkedin),
                )
                run.discovery_run_id = discovery_run.id
                run.discovered = discovery_run.people_imported or 0
                run.duplicates = discovery_run.duplicates or 0
                new_contacts = db.execute(
                    select(Contact).where(Contact.discovery_run_id == discovery_run.id)
                ).scalars().all()
                new_contact_ids = [c.id for c in new_contacts]
                for c in new_contacts:
                    # Attribute each discovered person to the A/B variant that found them.
                    if variant is not None:
                        c.variant_id = variant.id
                    c.campaign_id = config.id
                    company = db.get(Company, c.company_id) if c.company_id else None
                    _track_person(run, c, company, status="discovered")
                _stage(
                    run,
                    "discovery",
                    imported=run.discovered,
                    duplicates=run.duplicates,
                    search=playbook.name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent discovery failed")
                errors.append(f"Discovery: {exc}")
        db.commit()

        if _abort_if_cancelled(db, run, errors):
            return

        # Resume mode only: pick up contacts from earlier runs of this campaign
        # that never got a draft (e.g. a prior run crashed during qualify/draft).
        # Normal "Run now" processes only people found in this run's discovery.
        if config.id and bool((run.summary or {}).get("resume")):
            drafted_contact_ids = {
                cid
                for (cid,) in db.execute(
                    select(EmailDraft.contact_id).where(
                        EmailDraft.campaign_id == config.id,
                        EmailDraft.contact_id.isnot(None),
                    )
                ).all()
                if cid is not None
            }
            for (cid,) in db.execute(
                select(Contact.id).where(Contact.campaign_id == config.id)
            ).all():
                if cid not in drafted_contact_ids and cid not in new_contact_ids:
                    new_contact_ids.append(cid)
                    contact = db.get(Contact, cid)
                    if contact:
                        company = (
                            db.get(Company, contact.company_id)
                            if contact.company_id
                            else None
                        )
                        existing_ids = {
                            p.get("id")
                            for p in (run.summary or {}).get("people") or []
                        }
                        if cid not in existing_ids:
                            _track_person(run, contact, company, status="discovered")

        if _abort_if_cancelled(db, run, errors):
            return

        # 2) QUALIFY (per-person research for every new import) --------------
        # The free rejections happen first, on this thread — they are pure DB
        # writes and must not occupy a worker. Only the prospects that actually
        # need a Claude call go to the pool.
        qualified_ids: list[int] = []
        gate_min = optimization.current_state().research_gate_min
        to_research: list[int] = []
        for cid in new_contact_ids:
            contact = db.get(Contact, cid)
            if not contact:
                continue
            # Cost guard: skip the expensive LLM research for people Apollo has no
            # reachable email for. They can't be emailed, so researching them
            # spends tokens for nothing (the common "researched, then email not
            # revealed" waste). Apollo's people-search has_email flag is free.
            if not getattr(contact, "has_email", False) and not (contact.email or "").strip():
                contact.status = ProspectStatus.REJECTED
                run.rejected += 1
                _update_person_status(run, cid, "skipped (no reachable email)")
                continue
            # Optimized pipeline only: don't buy research for a title the free
            # rule-based fit score already rates too low to survive qualifying.
            # Unscored contacts are always researched. The status text names the
            # score so skipped people stay auditable in the run view.
            fit = contact.usefulness_score
            if (
                fit is not None
                and fit < gate_min
                and optimization.is_on("research_gate")
            ):
                contact.status = ProspectStatus.REJECTED
                run.rejected += 1
                _update_person_status(run, cid, f"skipped (low fit {fit:.0f})")
                continue
            to_research.append(cid)
        db.commit()

        workers = max(1, int(settings.bulk_approve_workers))
        logger.info(
            "Agent run %s qualify: %s to research, %s pre-rejected, %s workers",
            run_id, len(to_research), run.rejected, workers,
        )
        qualify_started = time.monotonic()
        if to_research:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _qualify_one, cid, principal.id, playbook.objective_prompt
                    ): cid
                    for cid in to_research
                }
                for future in as_completed(futures):
                    if _is_cancelled(db, run):
                        executor.shutdown(wait=False, cancel_futures=True)
                        _abort_if_cancelled(db, run, errors)
                        return
                    cid, score, error = future.result()
                    if error is not None:
                        # Say so on the person too, not just in the error list.
                        # Leaving them at "discovered" makes a prospect whose
                        # research was paid for and failed look untouched, and
                        # indistinguishable from one a cancelled run never
                        # reached.
                        errors.append(f"Qualify #{cid}: {error}")
                        _update_person_status(run, cid, "research failed")
                        db.commit()
                        continue
                    contact = db.get(Contact, cid)
                    if contact is None:
                        continue
                    score = score or 0.0
                    if score < config.auto_reject_below:
                        contact.status = ProspectStatus.REJECTED
                        run.rejected += 1
                        _update_person_status(run, cid, "rejected", score=score)
                    elif score >= config.qualify_min:
                        qualified_ids.append(cid)
                        run.qualified += 1
                        _update_person_status(
                            run, cid, f"qualified ({score:.0f})", score=score
                        )
                    else:
                        contact.status = ProspectStatus.REJECTED
                        run.rejected += 1
                        _update_person_status(
                            run, cid, f"below {config.qualify_min:.0f}", score=score
                        )
                    db.commit()
        logger.info(
            "Agent run %s qualify done in %.1fs: %s qualified, %s rejected",
            run_id, time.monotonic() - qualify_started, run.qualified, run.rejected,
        )
        _stage(run, "qualify", qualified=run.qualified, rejected=run.rejected)
        db.commit()

        if _abort_if_cancelled(db, run, errors):
            return

        # 3) APPROVE + REVEAL + DRAFT ---------------------------------------
        # Email-COPY A/B: maintain copy variants per playbook and assign one to
        # each draft (epsilon-greedy by reply rate). This is the second learning
        # lever alongside the search variants picked above.
        from app.services.agent.experiments import (
            ensure_copy_variants,
            select_copy_variant,
        )

        copy_variants: list = []
        try:
            copy_variants = ensure_copy_variants(db, principal, playbook)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Copy variant setup failed; using default copy: %s", exc)

        # This principal's own send-from mailbox. Stop the run rather than draft
        # a batch that would go out from somebody else's address.
        try:
            principal_mailbox_id = mailbox_for_principal(principal).id
        except MailboxUnassignedError as exc:
            # Finish the run rather than returning from the middle of it. A bare
            # return left status at "running" forever: the dashboard showed
            # "Running now" indefinitely, and the in-flight guard then blocked
            # every later run of this campaign, manual or scheduled.
            errors.append(str(exc))
            _stage(run, "draft", drafted=0, note=str(exc))
            db.commit()
            _finalize_run(db, principal, config, run, errors)
            return

        drafted_ids: list[int] = []
        drafted_people: list[str] = []

        # Approve up front, on this thread, in one pass. Idempotency is resolved
        # here too: a prospect this campaign has already drafted must not occupy a
        # worker only to be discarded. Both are pure DB work.
        already_drafted = set(
            db.execute(
                select(EmailDraft.contact_id).where(
                    EmailDraft.campaign_id == config.id,
                    EmailDraft.status.in_(
                        [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                    ),
                )
            ).scalars().all()
        )
        to_draft: list[int] = []
        for cid in qualified_ids:
            contact = db.get(Contact, cid)
            if not contact or contact.do_not_contact:
                continue
            contact.approved_for_outreach = True
            contact.status = ProspectStatus.APPROVED
            # Other campaigns of the same principal may still draft to this
            # person; campaigns are independent for prospects.
            if contact.id in already_drafted:
                continue
            to_draft.append(cid)
        db.commit()

        # Pick each prospect's A/B copy variant here — the worker needs the style
        # before it can write, and variant selection touches the shared session.
        variant_by_contact: dict[int, Any] = {}
        if copy_variants:
            for cid in to_draft:
                variant_by_contact[cid] = select_copy_variant(db, copy_variants)

        workers = max(1, int(settings.bulk_draft_batch_size))
        logger.info(
            "Agent run %s draft: %s to draft (%s already had one), %s workers",
            run_id, len(to_draft), len(qualified_ids) - len(to_draft), workers,
        )
        draft_started = time.monotonic()
        if to_draft:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _draft_one,
                        cid,
                        principal.id,
                        playbook.objective_prompt,
                        (
                            variant_by_contact[cid].style
                            if variant_by_contact.get(cid) is not None
                            else None
                        ),
                    ): cid
                    for cid in to_draft
                }
                for future in as_completed(futures):
                    if _is_cancelled(db, run):
                        executor.shutdown(wait=False, cancel_futures=True)
                        _abort_if_cancelled(db, run, errors)
                        return
                    cid, content, insight_id, blockers = future.result()
                    contact = db.get(Contact, cid)
                    if contact is None:
                        continue
                    if content is None:
                        errors.append(
                            f"Skip draft {contact.name or cid}: {', '.join(blockers)}"
                        )
                        _update_person_status(
                            run, cid, f"not drafted ({', '.join(blockers)})"
                        )
                        db.commit()
                        continue
                    copy_variant = variant_by_contact.get(cid)
                    # Auto-send mode pre-approves the draft so it's ready to send.
                    draft = EmailDraft(
                        principal_id=principal.id,
                        campaign_id=config.id,
                        company_id=contact.company_id,
                        contact_id=contact.id,
                        insight_id=insight_id,
                        copy_variant_id=copy_variant.id if copy_variant else None,
                        subject=content.subject,
                        body=content.body,
                        # Stamp the principal's own mailbox so the sender is
                        # explicit in the review UI, not a global default
                        # resolved at send time.
                        from_mailbox=principal_mailbox_id,
                        status=(
                            EmailStatus.APPROVED if config.auto_send else EmailStatus.DRAFT
                        ),
                    )
                    if copy_variant is not None:
                        copy_variant.drafted = (copy_variant.drafted or 0) + 1
                    if config.auto_send:
                        draft.approved_by = "agent"
                        draft.approved_at = datetime.utcnow()
                    db.add(draft)
                    log_action(
                        db,
                        AuditAction.EMAIL_DRAFT,
                        entity_type="email_draft",
                        summary=f"Agent drafted outreach for prospect {contact.id}",
                    )
                    # Count the draft BEFORE committing. This used to increment
                    # after the commit, leaving the change pending — and the next
                    # iteration opened with _is_cancelled(), whose db.refresh(run)
                    # discards anything still pending. Every run therefore
                    # reported drafted=1 (whatever the last iteration set) no
                    # matter how many it wrote: 41 qualified, 41 drafts on disk,
                    # "1" shown on the dashboard.
                    run.drafted += 1
                    _update_person_status(run, cid, "drafted")
                    db.commit()
                    db.refresh(draft)
                    drafted_ids.append(draft.id)
                    drafted_people.append(contact.name)
        logger.info(
            "Agent run %s draft done in %.1fs: %s drafted",
            run_id, time.monotonic() - draft_started, run.drafted,
        )
        _stage(run, "draft", drafted=run.drafted, people=drafted_people[:50])
        db.commit()

        if _abort_if_cancelled(db, run, errors):
            return

        # 4) SEND (drip: small batches with a pause, capped per day) ---------
        if config.auto_send:
            sendable = [did for did in drafted_ids]
            remaining = _remaining_daily_cap(db, principal)
            if remaining <= 0:
                _stage(
                    run,
                    "send",
                    sent=0,
                    note=f"Shared mailbox cap ({principal.mailbox_daily_cap}/day) already "
                    f"reached across this principal's campaigns; {len(sendable)} drafts "
                    f"left ready to send.",
                )
            else:
                to_send = sendable[:remaining]
                _drip_send(db, principal, config, run, to_send, errors)
            db.commit()

        # 5) FOLLOW-UPS ------------------------------------------------------
        if config.followup_enabled:
            _run_followups(db, principal, config, run, errors)
            db.commit()

        # Roll the run's funnel into the variant's cumulative A/B stats.
        if variant is not None:
            try:
                v = db.get(AgentVariant, variant.id)
                if v is not None:
                    v.runs = (v.runs or 0) + 1
                    v.discovered = (v.discovered or 0) + (run.discovered or 0)
                    v.drafted = (v.drafted or 0) + (run.drafted or 0)
                    v.sent = (v.sent or 0) + (run.sent or 0)
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.warning("Variant stat update failed: %s", exc)

        _finalize_run(db, principal, config, run, errors)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run %s crashed", run_id)
        try:
            run = db.get(AgentRun, run_id)
            if run:
                run.status = "failed"
                run.error_message = str(exc)
                run.finished_at = datetime.utcnow()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        db.close()


def _finalize_run(
    db: Session,
    principal: Principal,
    config: AgentConfig,
    run: AgentRun,
    errors: list[str],
) -> None:
    """Shared run wrap-up: status, summary, digest, audit (both pipeline modes)."""
    # A run that errored and achieved nothing is a failure, not a completion.
    # Discovery exceptions are caught so one bad stage cannot abort the pipeline,
    # but that left a crashed run reporting "completed" with every counter at
    # zero — indistinguishable on screen from a run that was never started, which
    # is why "Run now" read as a dead button. Say what actually happened.
    achieved_nothing = not (run.discovered or run.drafted or run.sent)
    if errors and achieved_nothing:
        run.status = "failed"
        if not run.error_message:
            run.error_message = errors[0]
    else:
        run.status = "completed"
    _set_summary(run, errors=errors[:50])
    run.finished_at = datetime.utcnow()
    config.last_run_at = datetime.utcnow()
    db.commit()

    try:
        from app.services.agent.reply_adaptation import analyze_replies_and_adapt
        from app.models.agent_playbook import AgentPlaybook

        playbook = db.get(AgentPlaybook, run.playbook_id) if run.playbook_id else None
        adaptation = analyze_replies_and_adapt(
            db, principal=principal, playbook=playbook
        )
        if adaptation.get("adaptations") or adaptation.get("analyzed"):
            _set_summary(run, reply_adaptation=adaptation)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reply adaptation failed: %s", exc)

    try:
        _send_digest(db, principal, config, run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent digest email failed: %s", exc)

    log_action(
        db,
        AuditAction.DISCOVER,
        entity_type="agent_run",
        entity_id=run.id,
        actor="agent",
        summary=(
            f"Agent run #{run.id}: {run.discovered} discovered, "
            f"{run.qualified} qualified, {run.drafted} drafted, {run.sent} sent, "
            f"{run.followups_sent} follow-ups sent."
        ),
        commit=True,
    )


def _sent_today(db: Session, principal_id: int) -> int:
    """How many emails this principal has already sent today (UTC)."""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(
        select(func.count())
        .select_from(EmailDraft)
        .where(
            EmailDraft.principal_id == principal_id,
            EmailDraft.status.in_([EmailStatus.SENT, EmailStatus.REPLIED]),
            EmailDraft.sent_at.isnot(None),
            EmailDraft.sent_at >= start,
        )
    ).scalar_one()


def _remaining_daily_cap(db: Session, principal: Principal) -> int:
    """Remaining sends today against the principal's SHARED mailbox cap (counts
    every campaign that sends from this principal's mailbox)."""
    cap = max(0, int(getattr(principal, "mailbox_daily_cap", None) or 0))
    if cap == 0:
        return 0
    return max(0, cap - _sent_today(db, principal.id))


def _drip_send(
    db: Session,
    principal: Principal,
    config: AgentConfig,
    run: AgentRun,
    draft_ids: list[int],
    errors: list[str],
) -> None:
    """Schedule or send approved drafts.

    Autonomous timing (config.auto_schedule, default): each email is scheduled for
    a good business hour in the RECIPIENT'S local timezone, alternating between
    A/B time-of-day buckets so we learn what gets replies. Otherwise we fall back
    to a fixed local send window for the principal.
    """
    from app.api.routes.emails import SendError, perform_send
    from app.services.agent.experiments import EXPLORE_EPSILON, best_send_bucket
    from app.services.agent.send_timing import (
        AB_SEND_BUCKETS,
        personalized_send_time_utc,
        staggered_send_times_utc,
    )

    delay = max(0, int(settings.send_batch_delay_seconds or 120))
    gap_minutes = max(1, delay // 60) if delay else 2
    auto_schedule = bool(getattr(config, "auto_schedule", True))

    # Send-time learning: once we have enough data, exploit the best-replying
    # bucket most of the time, but keep exploring the others so we keep learning.
    winning_bucket = best_send_bucket(db, principal)

    send_times: list[datetime] = []
    if not auto_schedule:
        send_times = staggered_send_times_utc(
            count=len(draft_ids),
            timezone=config.timezone or "America/New_York",
            start_hour=int(getattr(config, "send_window_start_local", None) or 9),
            end_hour=int(getattr(config, "send_window_end_local", None) or 17),
            gap_minutes=gap_minutes,
        )
    sent_people: list[str] = []
    scheduled_people: list[str] = []
    now = datetime.utcnow()

    for idx, did in enumerate(draft_ids):
        draft = db.get(EmailDraft, did)
        if not draft or draft.status != EmailStatus.APPROVED:
            continue
        contact = db.get(Contact, draft.contact_id) if draft.contact_id else None
        if not contact or not contact.email:
            continue
        bucket_index: Optional[int] = None
        if auto_schedule:
            # Exploit the winning bucket ~70% of the time once known; otherwise
            # alternate buckets per recipient so every window keeps getting data.
            if winning_bucket is not None and random.random() < (1 - EXPLORE_EPSILON):
                bucket_index = winning_bucket
            else:
                bucket_index = idx % len(AB_SEND_BUCKETS)
            when = personalized_send_time_utc(
                location=contact.location,
                order_index=idx,
                ab_index=bucket_index,
            )
        else:
            when = send_times[idx] if idx < len(send_times) else now
        # Record the bucket so reply-rate learning can attribute results to it.
        draft.send_bucket_index = bucket_index
        try:
            if when <= now:
                perform_send(db, draft)
                run.sent += 1
                sent_people.append(contact.name)
                _update_person_status(run, contact.id, "sent")
            else:
                draft.status = EmailStatus.SCHEDULED
                draft.scheduled_at = when
                draft.outlook_scheduled = False
                if not draft.approved_at:
                    draft.approved_at = datetime.utcnow()
                run.sent += 1  # counts toward daily cap once queued
                scheduled_people.append(f"{contact.name} @ {when:%H:%M} UTC")
                _update_person_status(run, contact.id, "scheduled")
            db.commit()
        except SendError as exc:
            errors.append(f"Send #{did}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(f"Send #{did}: {exc}")

    _stage(
        run,
        "send",
        sent=run.sent,
        people=sent_people[:50],
        scheduled=scheduled_people[:20],
    )


def _reveal_email(db: Session, contact: Contact, errors: list[str]) -> None:
    from app.services.contacts import RevealNotAllowed, reveal_contact

    try:
        reveal_contact(db, contact)
        _commit_with_retry(db)
    except RevealNotAllowed as exc:
        db.rollback()
        errors.append(f"Reveal {contact.name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Agent reveal failed for contact %s: %s", contact.id, exc)
        errors.append(f"Reveal {contact.name}: {exc}")


def _latest_insight(
    db: Session, principal_id: int, contact_id: int
) -> Optional[RelevanceInsight]:
    return db.execute(
        select(RelevanceInsight)
        .where(
            RelevanceInsight.principal_id == principal_id,
            RelevanceInsight.contact_id == contact_id,
        )
        .order_by(RelevanceInsight.created_at.desc())
    ).scalars().first()


def _run_followups(
    db: Session,
    principal: Principal,
    config: AgentConfig,
    run: AgentRun,
    errors: list[str],
) -> None:
    """Draft (and optionally send) follow-ups for silent contacts."""
    from app.api.routes.emails import (
        SendError,
        _build_followup,
        _sent_count,
        perform_send,
    )

    schedule = list(config.followup_schedule_days or [config.followup_days, 10, 15, 30])
    if not schedule:
        schedule = [config.followup_days]

    sent_drafts = db.execute(
        select(EmailDraft)
        .where(EmailDraft.status == EmailStatus.SENT, EmailDraft.sent_at.isnot(None))
        .order_by(EmailDraft.sent_at.asc())
    ).scalars().all()

    first_sent_by_contact: dict[int, datetime] = {}
    latest_by_contact: dict[int, EmailDraft] = {}
    for d in sent_drafts:
        if d.contact_id is None or d.principal_id != principal.id:
            continue
        # Only follow up on this campaign's own sent emails.
        if d.campaign_id is not None and d.campaign_id != config.id:
            continue
        if d.contact_id not in first_sent_by_contact and d.sent_at:
            first_sent_by_contact[d.contact_id] = d.sent_at
        latest_by_contact[d.contact_id] = d

    followed_people: list[str] = []
    now = datetime.utcnow()
    for contact_id, last in latest_by_contact.items():
        if run.followups_drafted >= config.followup_cap:
            break
        first_sent = first_sent_by_contact.get(contact_id)
        if first_sent is None or last.sent_at is None:
            continue
        sent_count = _sent_count(db, contact_id)
        followup_index = sent_count - 1  # 0 = first follow-up wave
        if followup_index < 0 or followup_index >= len(schedule):
            continue
        if followup_index >= max(0, config.max_followups):
            continue
        days_required = max(1, int(schedule[followup_index]))
        if (now - first_sent).days < days_required:
            continue
        replied = db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.contact_id == contact_id,
                EmailDraft.status == EmailStatus.REPLIED,
            )
        ).scalar_one()
        if replied:
            continue
        contact = db.get(Contact, contact_id)
        if not contact or not contact.email or contact.do_not_contact:
            continue
        pending = db.execute(
            select(func.count())
            .select_from(EmailDraft)
            .where(
                EmailDraft.contact_id == contact_id,
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
                EmailDraft.created_at > last.sent_at,
            )
        ).scalar_one()
        if pending:
            continue
        try:
            draft = _build_followup(db, last, approve=config.auto_send)
            draft.campaign_id = last.campaign_id or config.id
            db.commit()
            db.refresh(draft)
            run.followups_drafted += 1
            followed_people.append(contact.name)
            if config.auto_send:
                try:
                    perform_send(db, draft)
                    run.followups_sent += 1
                except SendError as exc:
                    errors.append(f"Follow-up send #{draft.id}: {exc.message}")
        except SendError as exc:
            db.rollback()
            errors.append(f"Follow-up #{contact_id}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            errors.append(f"Follow-up #{contact_id}: {exc}")
    _stage(
        run,
        "followups",
        drafted=run.followups_drafted,
        sent=run.followups_sent,
        people=followed_people[:50],
    )


def _send_digest(
    db: Session, principal: Principal, config: AgentConfig, run: AgentRun
) -> None:
    recipients = [r for r in (config.digest_recipients or []) if r and "@" in r]
    if not recipients:
        return
    if not settings.outreach_from_email:
        return

    people = (run.summary or {}).get("people") or []
    sent_today = [
        p for p in people if isinstance(p, dict) and p.get("status") in ("sent", "scheduled")
    ]
    if not sent_today and isinstance(people, list):
        # Legacy summary stores plain status strings on tracked people.
        pass

    variant = (run.summary or {}).get("variant_label") or "Base search"
    objective = ((run.summary or {}).get("objective") or "")[:200]

    campaign_label = (config.name or "Campaign").strip()
    subject = (
        f"Campaign report — {campaign_label} ({principal.name}): {run.sent} queued/sent, "
        f"{run.qualified} qualified ({datetime.utcnow():%b %d})"
    )
    lines = [
        f"CAMPAIGN DAILY REPORT — {campaign_label} · {principal.name}",
        f"Run #{run.id} · {datetime.utcnow():%Y-%m-%d %H:%M} UTC",
        "",
        "GOAL",
        f"  {objective or '(see campaign setup)'}",
        "",
        "SEARCH VARIANT (A/B)",
        f"  {variant}",
        "",
        "FUNNEL",
        f"  Discovered:      {run.discovered} new ({run.duplicates} duplicates skipped)",
        f"  Researched:      {run.discovered}",
        f"  Qualified (≥{config.qualify_min:.0f}): {run.qualified}",
        f"  Rejected:        {run.rejected}",
        f"  Drafted:         {run.drafted}",
        f"  Sent / queued:   {run.sent}",
        f"  Follow-ups:      {run.followups_sent} sent ({run.followups_drafted} drafted)",
        "",
    ]

    # People table from run summary.
    tracked = (run.summary or {}).get("people") or []
    if tracked:
        lines.append("PEOPLE THIS RUN")
        for entry in tracked[:40]:
            if isinstance(entry, dict):
                lines.append(
                    f"  · {entry.get('name', '?')} — {entry.get('title', '')} @ "
                    f"{entry.get('company', '')} → {entry.get('status', '')}"
                )
        if len(tracked) > 40:
            lines.append(f"  … and {len(tracked) - 40} more in the app")
        lines.append("")

    stages = (run.summary or {}).get("stages") or []
    if stages:
        lines.append("PIPELINE STAGES")
        for s in stages[-6:]:
            if isinstance(s, dict):
                lines.append(f"  · {s.get('stage', '?')}: {json.dumps({k: v for k, v in s.items() if k != 'stage'}, default=str)}")
        lines.append("")

    adaptation = (run.summary or {}).get("reply_adaptation") or {}
    if adaptation.get("adaptations"):
        lines.append("LEARNING (reply → search)")
        for note in adaptation["adaptations"]:
            lines.append(f"  · {note}")
        lines.append("")

    errs = (run.summary or {}).get("errors", [])
    if errs:
        lines += ["ISSUES", *[f"  - {e}" for e in errs[:10]], ""]

    lines += [
        "—",
        "Open Campaigns in the app for full dashboard and reply threads.",
    ]
    body = "\n".join(lines)

    # Internal digest to the operator — not prospect outreach, so a default
    # sender is acceptable here.
    mailbox = mailbox_for_principal(principal, strict=False)
    provider = provider_for_mailbox(mailbox)
    for to_email in recipients:
        try:
            provider.send(
                to_email=to_email,
                subject=subject,
                body=body,
                from_email=mailbox.from_email or settings.outreach_from_email,
                from_name=mailbox.from_name or settings.outreach_from_name or "Sourcing Agent",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Digest send to %s failed: %s", to_email, exc)
