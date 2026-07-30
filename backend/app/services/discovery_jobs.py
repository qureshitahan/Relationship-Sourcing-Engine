"""Background jobs anchored to a discovery run.

Every heavy, run-level operation (importing prospects, drafting a few hundred
emails, sending them, sending LinkedIn messages) runs in a daemon thread with its
own DB session — the same pattern the email scheduler and bulk-email runner use —
so a long job never blocks the HTTP request and the browser never times out.

Progress is written back onto the ``DiscoveryRun`` row (``job_*`` columns) so the
UI can poll it and show a live progress bar, and cancel it. The discovery import
phase itself uses the run's own ``status`` field (running -> completed/failed).

These jobs REUSE the existing, tested send/draft paths (``perform_send``,
``perform_linkedin_send``, ``generate_outreach_batch``) rather than reimplement
them, so behaviour matches the single-item flows exactly — just batched and paced.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.enums import DiscoveryStatus, EmailStatus, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.services.enrichment.base import DiscoveryCriteria

logger = logging.getLogger(__name__)

# Job kinds recorded on DiscoveryRun.job_kind.
JOB_DISCOVERY = "discovery"
JOB_REVEAL = "reveal"
JOB_DRAFT_EMAIL = "draft_email"
JOB_SEND_EMAIL = "send_email"
JOB_SEND_LINKEDIN = "send_linkedin"

# Job statuses recorded on DiscoveryRun.job_status.
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"


# --- shared job bookkeeping ------------------------------------------------

def _mark_starting(run_id: int, kind: str) -> None:
    """Flip the run's job to 'running' synchronously, before the worker spawns.

    Without this there is a race: the worker sets ``job_status='running'`` on its
    own thread, so a client that polls immediately after launch could still read
    the PREVIOUS job's ``done`` state and think this new job already finished.
    """
    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is not None:
            run.job_kind = kind
            run.job_status = JOB_RUNNING
            run.job_total = 0
            run.job_done = 0
            run.job_error = None
            run.job_cancel_requested = False
            db.commit()
    finally:
        db.close()


def _start_job(db: Session, run: DiscoveryRun, kind: str, total: int) -> None:
    run.job_kind = kind
    run.job_status = JOB_RUNNING
    run.job_total = total
    run.job_done = 0
    run.job_error = None
    run.job_cancel_requested = False
    db.commit()


def _progress(db: Session, run: DiscoveryRun, done: int) -> None:
    run.job_done = done
    db.commit()


def _finish_job(
    db: Session, run: DiscoveryRun, status: str, error: Optional[str] = None
) -> None:
    run.job_status = status
    run.job_error = error
    db.commit()


def _cancelled(db: Session, run: DiscoveryRun) -> bool:
    db.refresh(run)
    return bool(run.job_cancel_requested)


def _busy(run: DiscoveryRun) -> bool:
    """True when a bulk job (not discovery) is already running for this run."""
    return run.job_status == JOB_RUNNING and run.job_kind != JOB_DISCOVERY


# --- discovery import ------------------------------------------------------

def launch_discovery(
    run_id: int,
    principal_id: int,
    criteria: DiscoveryCriteria,
    *,
    search_definition_id: Optional[int] = None,
    requested_by: str = "user",
    generate_insights: bool = False,
    include_organizations: bool = False,
    people_first: bool = True,
    auto_expand_to_target: bool = True,
    search_goal: Optional[str] = None,
    auto_process: bool = False,
) -> None:
    """Run an ICP discovery import in the background against a pre-created run."""
    threading.Thread(
        target=_discovery_worker,
        args=(run_id, principal_id, criteria),
        kwargs=dict(
            search_definition_id=search_definition_id,
            requested_by=requested_by,
            generate_insights=generate_insights,
            include_organizations=include_organizations,
            people_first=people_first,
            auto_expand_to_target=auto_expand_to_target,
            search_goal=search_goal,
            auto_process=auto_process,
        ),
        name=f"discovery-{run_id}",
        daemon=True,
    ).start()


def _discovery_worker(
    run_id: int,
    principal_id: int,
    criteria: DiscoveryCriteria,
    *,
    search_definition_id: Optional[int],
    requested_by: str,
    generate_insights: bool,
    include_organizations: bool,
    people_first: bool,
    auto_expand_to_target: bool,
    search_goal: Optional[str],
    auto_process: bool,
) -> None:
    from app.services.discovery import run_discovery
    from app.services.discovery.process_run import process_discovery_run

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        principal = db.get(Principal, principal_id)
        if run is None or principal is None:
            return
        run_discovery(
            db,
            principal,
            criteria,
            search_definition_id=search_definition_id,
            requested_by=requested_by,
            generate_insights=generate_insights,
            include_organizations=include_organizations,
            people_first=people_first,
            auto_expand_to_target=auto_expand_to_target,
            search_goal=search_goal,
            run=run,
        )
        # Optional: research + reveal every imported prospect (same behaviour as
        # the old synchronous auto_process, just off the request thread).
        if auto_process and run.status == DiscoveryStatus.COMPLETED:
            try:
                process_discovery_run(
                    db, run_id, principal=principal, skip_existing_research=False
                )
            except Exception as exc:  # noqa: BLE001
                run.error_message = (
                    (run.error_message or "") + f" Auto-process failed: {exc}"
                ).strip()
                db.commit()
    except Exception:  # noqa: BLE001 - never let the worker die silently
        logger.exception("Discovery worker failed for run %s", run_id)
        run = db.get(DiscoveryRun, run_id)
        if run is not None and run.status not in (
            DiscoveryStatus.COMPLETED,
            DiscoveryStatus.FAILED,
        ):
            run.status = DiscoveryStatus.FAILED
            db.commit()
    finally:
        db.close()


# --- bulk email reveal (Apollo) --------------------------------------------

def launch_run_reveal(run_id: int) -> None:
    """Reveal email/phone for every unrevealed prospect in the run (background)."""
    _mark_starting(run_id, JOB_REVEAL)
    threading.Thread(
        target=_reveal_worker,
        args=(run_id,),
        name=f"run-reveal-{run_id}",
        daemon=True,
    ).start()


def _reveal_worker(run_id: int) -> None:
    from app.services.contacts import RevealNotAllowed, reveal_contact

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        targets = list(
            db.execute(
                select(Contact)
                .where(
                    Contact.discovery_run_id == run_id,
                    (Contact.email.is_(None)) | (Contact.email == ""),
                )
                .order_by(Contact.id)
            ).scalars().all()
        )
        targets = [c for c in targets if not c.do_not_contact]
        _start_job(db, run, JOB_REVEAL, len(targets))
        if not targets:
            _finish_job(db, run, JOB_DONE)
            return

        revealed = 0
        failures: list[str] = []
        for index, contact in enumerate(targets):
            if _cancelled(db, run):
                break
            try:
                reveal_contact(db, contact)
                db.commit()
                if (contact.email or "").strip():
                    revealed += 1
            except RevealNotAllowed as exc:
                db.rollback()
                failures.append(f"{contact.name or contact.id}: {exc}")
            except Exception as exc:  # noqa: BLE001 - keep revealing the rest
                db.rollback()
                logger.exception("Bulk reveal failed for contact %s", contact.id)
                failures.append(f"{contact.name or contact.id}: {exc}")
            _progress(db, run, index + 1)

        error = (
            f"{len(failures)} could not be revealed: " + "; ".join(failures[:5])
            if failures
            else None
        )
        _finish_job(db, run, JOB_DONE, error)
        logger.info("Run %s bulk reveal: %s revealed, %s failed", run_id, revealed, len(failures))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk reveal worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


# --- bulk email drafting ---------------------------------------------------

def launch_run_draft(run_id: int, *, outreach_goal: Optional[str] = None) -> None:
    _mark_starting(run_id, JOB_DRAFT_EMAIL)
    threading.Thread(
        target=_draft_worker,
        args=(run_id, outreach_goal),
        name=f"run-draft-{run_id}",
        daemon=True,
    ).start()


def _draft_worker(run_id: int, outreach_goal: Optional[str]) -> None:
    from app.api.routes.emails import _principal_mailbox_id, _latest_insight
    from app.models.enums import AuditAction
    from app.services.audit import log_action
    from app.services.insights.engine import generate_outreach_batch
    from app.services.outreach_eligibility import outreach_draft_blockers
    from app.services.outreach_goal import outreach_goal_for_run

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        principal = db.get(Principal, run.principal_id) if run.principal_id else None
        if principal is None:
            _start_job(db, run, JOB_DRAFT_EMAIL, 0)
            _finish_job(db, run, JOB_FAILED, "This run has no principal.")
            return

        # Approved prospects in the run that don't already have a live draft.
        approved = list(
            db.execute(
                select(Contact).where(
                    Contact.discovery_run_id == run_id,
                    Contact.approved_for_outreach.is_(True),
                ).order_by(Contact.id)
            ).scalars().all()
        )
        drafted_ids = set(
            db.execute(
                select(EmailDraft.contact_id).where(
                    EmailDraft.principal_id == principal.id,
                    EmailDraft.status.in_(
                        [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                    ),
                )
            ).scalars().all()
        )
        to_draft = [
            c
            for c in approved
            if c.id not in drafted_ids
            and not outreach_draft_blockers(db, principal_id=principal.id, contact=c)
        ]

        _start_job(db, run, JOB_DRAFT_EMAIL, len(to_draft))
        if not to_draft:
            _finish_job(db, run, JOB_DONE)
            return

        goal = (outreach_goal or "").strip() or outreach_goal_for_run(run.criteria)
        mailbox_id = _principal_mailbox_id(principal)
        batch_size = max(1, int(settings.bulk_draft_batch_size))
        done = 0
        generated = 0
        for start in range(0, len(to_draft), batch_size):
            if _cancelled(db, run):
                break
            chunk = to_draft[start : start + batch_size]
            try:
                pairs = generate_outreach_batch(db, principal, chunk, outreach_goal=goal)
                result_by_contact = {c.id: r for c, r in pairs}
                for contact in chunk:
                    result = result_by_contact.get(contact.id)
                    if not result:
                        continue
                    insight = _latest_insight(db, principal.id, contact.id)
                    db.add(
                        EmailDraft(
                            principal_id=principal.id,
                            company_id=contact.company_id,
                            contact_id=contact.id,
                            insight_id=insight.id if insight else None,
                            subject=result.subject,
                            body=result.body,
                            from_mailbox=mailbox_id,
                            status=EmailStatus.DRAFT,
                        )
                    )
                    generated += 1
                db.commit()
            except Exception as exc:  # noqa: BLE001 - keep drafting the rest
                db.rollback()
                logger.warning("Bulk draft chunk failed for run %s: %s", run_id, exc)
            done += len(chunk)
            _progress(db, run, done)

        if generated:
            log_action(
                db,
                AuditAction.EMAIL_DRAFT,
                entity_type="discovery_run",
                entity_id=run_id,
                summary=f"Bulk-drafted {generated} email(s) for run {run_id}",
            )
            db.commit()
        _finish_job(db, run, JOB_DONE)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk draft worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


# --- bulk email send -------------------------------------------------------

def launch_run_email_send(run_id: int) -> None:
    _mark_starting(run_id, JOB_SEND_EMAIL)
    threading.Thread(
        target=_email_send_worker,
        args=(run_id,),
        name=f"run-email-send-{run_id}",
        daemon=True,
    ).start()


def _email_send_worker(run_id: int) -> None:
    """QUANTITY bulk send (Discover page 'Send email' button).

    Emails EVERYONE in the run who has a revealed email address — no research
    gate, no manual-approval gate — writing a draft on the spot for anyone who
    doesn't have one, then sending, paced. This is the volume path.

    The Prospects page keeps the strict QUALITY gate (research -> reveal ->
    approve -> send) untouched. If a prospect happens to be researched, that
    insight is used for a better email; if not, a solid non-researched email is
    written from their title/company.
    """
    from datetime import datetime

    from app.api.routes.emails import (
        SendError,
        _latest_insight,
        _principal_mailbox_id,
        perform_send,
    )
    from app.models.company import Company
    from app.services.insights.engine import generate_outreach

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        principal = db.get(Principal, run.principal_id) if run.principal_id else None
        if principal is None:
            _start_job(db, run, JOB_SEND_EMAIL, 0)
            _finish_job(db, run, JOB_FAILED, "This run has no principal.")
            return

        # Quantity: everyone in the run with a revealed email, not suppressed.
        targets = list(
            db.execute(
                select(Contact)
                .where(
                    Contact.discovery_run_id == run_id,
                    Contact.email.is_not(None),
                    Contact.email != "",
                )
                .order_by(Contact.id)
            ).scalars().all()
        )
        targets = [c for c in targets if not c.do_not_contact]
        _start_job(db, run, JOB_SEND_EMAIL, len(targets))
        if not targets:
            _finish_job(
                db,
                run,
                JOB_DONE,
                "No prospects in this run have a revealed email yet. Reveal emails "
                "first (Prospects page -> Reveal all), then send.",
            )
            return

        mailbox_id = _principal_mailbox_id(principal)
        delay = max(0.0, float(settings.bulk_email_send_delay_seconds))
        sent = 0
        failures: list[str] = []
        for index, contact in enumerate(targets):
            if _cancelled(db, run):
                break
            try:
                # Reuse an existing unsent draft, or write one now.
                draft = db.execute(
                    select(EmailDraft)
                    .where(
                        EmailDraft.principal_id == principal.id,
                        EmailDraft.contact_id == contact.id,
                        EmailDraft.status.in_(
                            [EmailStatus.DRAFT, EmailStatus.APPROVED]
                        ),
                    )
                    .order_by(EmailDraft.id.desc())
                ).scalars().first()
                if draft is None:
                    company = (
                        db.get(Company, contact.company_id)
                        if contact.company_id
                        else None
                    )
                    insight = _latest_insight(db, principal.id, contact.id)
                    content = generate_outreach(db, principal, contact, company, insight)
                    draft = EmailDraft(
                        principal_id=principal.id,
                        company_id=contact.company_id,
                        contact_id=contact.id,
                        insight_id=insight.id if insight else None,
                        subject=content.subject,
                        body=content.body,
                        from_mailbox=mailbox_id,
                        status=EmailStatus.DRAFT,
                    )
                    db.add(draft)
                    db.commit()
                if draft.status == EmailStatus.DRAFT:
                    draft.status = EmailStatus.APPROVED
                    draft.approved_by = "bulk-run-send"
                    draft.approved_at = datetime.utcnow()
                    db.commit()
                perform_send(db, draft)
                sent += 1
            except SendError as exc:
                db.rollback()
                failures.append(f"{contact.name or contact.id}: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                logger.exception("Bulk send failed for contact %s", contact.id)
                failures.append(f"{contact.name or contact.id}: {exc}")
            _progress(db, run, index + 1)
            if delay and index < len(targets) - 1:
                time.sleep(delay)

        error = (
            f"{len(failures)} email(s) could not be sent: " + "; ".join(failures[:5])
            if failures
            else None
        )
        _finish_job(db, run, JOB_DONE, error)
        logger.info("Run %s bulk email send: %s sent, %s failed", run_id, sent, len(failures))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk email send worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


# --- bulk LinkedIn send ----------------------------------------------------

def launch_run_linkedin_send(run_id: int) -> None:
    _mark_starting(run_id, JOB_SEND_LINKEDIN)
    threading.Thread(
        target=_linkedin_send_worker,
        args=(run_id,),
        name=f"run-linkedin-send-{run_id}",
        daemon=True,
    ).start()


def _linkedin_send_worker(run_id: int) -> None:
    from datetime import datetime

    from app.api.routes.linkedin import SendError, perform_linkedin_send
    from app.models.suppression import OutreachHistory

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        messages = list(
            db.execute(
                select(LinkedInMessage)
                .join(Contact, LinkedInMessage.contact_id == Contact.id)
                .where(
                    Contact.discovery_run_id == run_id,
                    LinkedInMessage.status.in_(
                        [LinkedInStatus.DRAFT, LinkedInStatus.APPROVED]
                    ),
                )
                .order_by(LinkedInMessage.id)
            ).scalars().all()
        )

        # Daily-cap guard: never exceed linkedin_daily_send_cap sends (invites +
        # DMs) per day so the LinkedIn account is not flagged. Today's total is
        # counted from OutreachHistory (written on every LinkedIn send/invite),
        # so ALL send paths — bulk, scheduler, manual — count toward one budget.
        now = datetime.utcnow()
        today_start = datetime(now.year, now.month, now.day)
        sent_today = db.execute(
            select(func.count())
            .select_from(OutreachHistory)
            .where(
                OutreachHistory.channel == "linkedin",
                OutreachHistory.created_at >= today_start,
            )
        ).scalar_one()
        cap = max(0, int(settings.linkedin_daily_send_cap))
        remaining = max(0, cap - sent_today)
        held = 0
        if len(messages) > remaining:
            held = len(messages) - remaining
            messages = messages[:remaining]

        _start_job(db, run, JOB_SEND_LINKEDIN, len(messages))
        if not messages:
            _finish_job(
                db,
                run,
                JOB_DONE,
                (
                    f"Daily LinkedIn cap of {cap} reached ({sent_today} already sent "
                    "today). The rest are held - run this again tomorrow."
                )
                if remaining <= 0
                else None,
            )
            return

        delay = max(0.0, float(settings.bulk_linkedin_send_delay_seconds))
        sent = 0
        failures: list[str] = []
        for index, msg in enumerate(messages):
            if _cancelled(db, run):
                break
            if msg.status == LinkedInStatus.DRAFT:
                msg.status = LinkedInStatus.APPROVED
                msg.approved_by = "bulk-run-send"
                msg.approved_at = datetime.utcnow()
                db.commit()
            try:
                perform_linkedin_send(db, msg)
                sent += 1
            except SendError as exc:
                db.rollback()
                failures.append(f"Message {msg.id}: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                logger.exception("Bulk LinkedIn send failed for message %s", msg.id)
                failures.append(f"Message {msg.id}: {exc}")
            _progress(db, run, index + 1)
            if delay and index < len(messages) - 1:
                time.sleep(delay)

        notes: list[str] = []
        if failures:
            notes.append(
                f"{len(failures)} message(s) could not be sent: "
                + "; ".join(failures[:5])
            )
        if held:
            notes.append(
                f"{held} held to stay under the daily LinkedIn cap of {cap} - "
                "run this again tomorrow to continue."
            )
        _finish_job(db, run, JOB_DONE, " ".join(notes) or None)
        logger.info(
            "Run %s bulk LinkedIn send: %s sent, %s failed, %s held (cap %s)",
            run_id, sent, len(failures), held, cap,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk LinkedIn send worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


def _fail(db: Session, run_id: int, message: str) -> None:
    db.rollback()
    run = db.get(DiscoveryRun, run_id)
    if run is not None:
        _finish_job(db, run, JOB_FAILED, message)
