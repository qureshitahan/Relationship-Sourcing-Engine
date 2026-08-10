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
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.enums import DiscoveryStatus, EmailStatus, LinkedInStatus
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.relevance_insight import RelevanceInsight
from app.services.enrichment.base import DiscoveryCriteria

logger = logging.getLogger(__name__)

# Job kinds recorded on DiscoveryRun.job_kind.
JOB_DISCOVERY = "discovery"
JOB_REVEAL = "reveal"
JOB_APPROVE = "approve"
JOB_DRAFT_EMAIL = "draft_email"
JOB_SEND_EMAIL = "send_email"
JOB_SEND_LINKEDIN = "send_linkedin"
JOB_PIPELINE = "pipeline"

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
            run.job_sent = 0
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
    run.job_sent = 0
    run.job_error = None
    run.job_cancel_requested = False
    db.commit()


def _progress(db: Session, run: DiscoveryRun, done: int) -> None:
    run.job_done = done
    db.commit()


def _progress_sent(db: Session, run_id: int, sent: int) -> None:
    """Persist the pipeline's live send count separately from job_done (which
    tracks approve+draft progress) — the sender runs on its own thread and can
    lag behind, so the UI needs its own number to show while a batch is running."""
    run = db.get(DiscoveryRun, run_id)
    if run is not None:
        run.job_sent = sent
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


def _summarize_failures(failures: list[str], verb: str) -> Optional[str]:
    """Turn "who: why" failure strings into a compact job_error summary.

    Bulk jobs commonly fail a whole batch for the SAME reason (a rate limit,
    a daily send cap) — without grouping, that produced one near-identical
    line per contact (e.g. 20 lines all reading "...daily sending limit
    exceeded"). Identical reasons collapse into one counted line instead.
    """
    if not failures:
        return None
    grouped: dict[str, list[str]] = {}
    for item in failures:
        who, _, reason = item.partition(": ")
        grouped.setdefault(reason or who, []).append(who)
    if len(grouped) == 1:
        ((reason, whos),) = grouped.items()
        return f"{len(whos)} {verb}: {reason}"
    parts = [
        f"{len(whos)}x {reason}" if len(whos) > 1 else f"{whos[0]}: {reason}"
        for reason, whos in sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    ]
    return f"{len(failures)} {verb}: " + "; ".join(parts[:5])


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
    require_email_and_linkedin: bool = False,
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
            require_email_and_linkedin=require_email_and_linkedin,
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
    require_email_and_linkedin: bool = False,
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
            require_email_and_linkedin=require_email_and_linkedin,
        )
        if auto_process and run.status == DiscoveryStatus.COMPLETED:
            # Full auto-process: research + reveal every imported prospect (same
            # behaviour as the old synchronous auto_process, just off the request
            # thread).
            try:
                process_discovery_run(
                    db, run_id, principal=principal, skip_existing_research=False
                )
            except Exception as exc:  # noqa: BLE001
                run.error_message = (
                    (run.error_message or "") + f" Auto-process failed: {exc}"
                ).strip()
                db.commit()
        elif run.status == DiscoveryStatus.COMPLETED:
            # Every discovery run — no matter the target count — should come back
            # with as complete a profile as Apollo can give us (email + LinkedIn
            # URL), not just names/titles. Apollo's People Search never returns an
            # email and often masks the LinkedIn URL, so run the same reveal pass
            # used by the manual "Reveal emails" button automatically, right after
            # import. Best-effort: some prospects genuinely have no email in
            # Apollo's database, so a handful may still come back unrevealed.
            try:
                _reveal_worker(run_id)
            except Exception as exc:  # noqa: BLE001
                run.error_message = (
                    (run.error_message or "") + f" Auto-reveal failed: {exc}"
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


RUN_REVEAL_CHUNK_SIZE = 25


def _reveal_worker(run_id: int) -> None:
    from app.services.contacts import reveal_contacts_bulk

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

        # Batch through Apollo's bulk_match path (up to 10 contacts per HTTP
        # call, paced inside the provider) instead of one Apollo call per
        # contact. A 500-prospect run previously fired ~500 individual reveal
        # requests, which tripped Apollo's rate limit partway through; failures
        # there are soft-swallowed, so the run "completed" but silently left most
        # prospects unrevealed. Chunking here is only to keep the progress bar
        # incremental — reveal_contacts_bulk still applies email/LinkedIn/name.
        revealed = 0
        chunk_failures = 0
        for start in range(0, len(targets), RUN_REVEAL_CHUNK_SIZE):
            if _cancelled(db, run):
                break
            chunk = targets[start : start + RUN_REVEAL_CHUNK_SIZE]
            try:
                revealed += reveal_contacts_bulk(db, chunk)
                db.commit()
            except Exception:  # noqa: BLE001 - keep revealing the rest
                db.rollback()
                chunk_failures += 1
                logger.exception(
                    "Bulk reveal chunk failed for run %s (contacts %s-%s)",
                    run_id,
                    chunk[0].id,
                    chunk[-1].id,
                )
            _progress(db, run, min(start + RUN_REVEAL_CHUNK_SIZE, len(targets)))

        notes: list[str] = []
        if chunk_failures:
            notes.append(f"{chunk_failures} batch(es) errored and were skipped.")
        if targets and revealed < len(targets) * 0.3:
            notes.append(
                f"Only {revealed}/{len(targets)} prospects got an email. Apollo may "
                "not have contact data for this audience, or the account hit a rate "
                "limit — check backend logs for 'bulk_match' warnings."
            )
        error = "; ".join(notes) or None
        _finish_job(db, run, JOB_DONE, error)
        logger.info("Run %s bulk reveal: %s revealed of %s", run_id, revealed, len(targets))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk reveal worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


# --- bulk approve (research + reveal + approve, concurrently) --------------

def launch_run_approve(run_id: int, contact_ids: Optional[list[int]] = None) -> None:
    """Approve many prospects at once, in the background, several at a time.

    Previously the "Approve" button drove this one HTTP request per prospect
    from the browser (2 in flight) — and each request could run a full
    web-search research call synchronously. On ~500 prospects that was the
    single biggest reason bulk outreach took hours: this fans the exact same
    per-contact work (see contacts.approve_contact) across a thread pool
    instead, so several prospects are researched/revealed/approved at once.

    ``contact_ids`` limits the run to a specific selection (e.g. checkboxes
    on the Prospects page); omit to approve every not-yet-approved prospect
    in the run.
    """
    _mark_starting(run_id, JOB_APPROVE)
    threading.Thread(
        target=_approve_worker,
        args=(run_id, contact_ids),
        name=f"run-approve-{run_id}",
        daemon=True,
    ).start()


def _approve_one(contact_id: int, principal_id: int) -> tuple[int, bool, Optional[str]]:
    """Runs in a worker thread with its own DB session/connection — approve_contact
    commits its own work, so no session is shared across threads. Returns
    (contact_id, ok, error_message_or_None)."""
    from app.services.contacts import ApprovalBlocked, RevealNotAllowed, approve_contact

    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        principal = db.get(Principal, principal_id)
        if contact is None or principal is None:
            return contact_id, False, "not found"
        if contact.approved_for_outreach:
            return contact_id, True, None
        try:
            approve_contact(db, contact, principal, approved_by="bulk-approve")
            return contact_id, True, None
        except (ApprovalBlocked, RevealNotAllowed) as exc:
            return contact_id, False, str(exc)
    except Exception as exc:  # noqa: BLE001 - keep approving the rest
        logger.exception("Bulk approve failed for contact %s", contact_id)
        return contact_id, False, str(exc)
    finally:
        db.close()


def _approve_worker(run_id: int, contact_ids: Optional[list[int]]) -> None:
    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        principal = db.get(Principal, run.principal_id) if run.principal_id else None
        if principal is None:
            _start_job(db, run, JOB_APPROVE, 0)
            _finish_job(db, run, JOB_FAILED, "This run has no principal.")
            return

        query = select(Contact).where(Contact.discovery_run_id == run_id)
        if contact_ids:
            query = query.where(Contact.id.in_(contact_ids))
        else:
            query = query.where(Contact.approved_for_outreach.is_(False))
        targets = list(db.execute(query.order_by(Contact.id)).scalars().all())
        targets = [c for c in targets if not c.approved_for_outreach and not c.do_not_contact]

        _start_job(db, run, JOB_APPROVE, len(targets))
        if not targets:
            _finish_job(db, run, JOB_DONE)
            return

        workers = max(1, int(settings.bulk_approve_workers))
        approved = 0
        failures: list[str] = []
        by_id = {c.id: c for c in targets}
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_approve_one, c.id, principal.id): c.id for c in targets
            }
            for future in as_completed(futures):
                if _cancelled(db, run):
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                contact_id, ok, err = future.result()
                if ok:
                    approved += 1
                elif err:
                    contact = by_id.get(contact_id)
                    failures.append(f"{contact.name if contact else contact_id}: {err}")
                done += 1
                _progress(db, run, done)

        error = _summarize_failures(failures, "could not be approved")
        _finish_job(db, run, JOB_DONE, error)
        logger.info("Run %s bulk approve: %s approved of %s", run_id, approved, len(targets))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk approve worker failed for run %s", run_id)
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


def _draft_one(
    contact_id: int, principal_id: int, outreach_goal: str
) -> tuple[int, object, Optional[int]]:
    """Runs in a worker thread with its own DB session — the slow part (one
    Claude call) is I/O-bound, so several of these run concurrently instead of
    one after another. Returns (contact_id, OutreachResult-or-None,
    insight_id-or-None); the caller does the actual DB write."""
    from app.services.insights.engine import generate_outreach

    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        principal = db.get(Principal, principal_id)
        if contact is None or principal is None:
            return contact_id, None, None
        company = db.get(Company, contact.company_id) if contact.company_id else None
        insight = db.execute(
            select(RelevanceInsight)
            .where(
                RelevanceInsight.principal_id == principal_id,
                RelevanceInsight.contact_id == contact_id,
            )
            .order_by(RelevanceInsight.created_at.desc())
        ).scalars().first()
        result = generate_outreach(
            db, principal, contact, company, insight, outreach_goal=outreach_goal
        )
        return contact_id, result, (insight.id if insight else None)
    except Exception:  # noqa: BLE001 - keep drafting the rest
        logger.exception("Draft generation failed for contact %s", contact_id)
        return contact_id, None, None
    finally:
        db.close()


def _draft_worker(run_id: int, outreach_goal: Optional[str]) -> None:
    from app.api.routes.emails import _principal_mailbox_id
    from app.models.enums import AuditAction
    from app.services.audit import log_action
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
        workers = max(1, int(settings.bulk_draft_batch_size))
        by_id = {c.id: c for c in to_draft}
        done = 0
        generated = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_draft_one, c.id, principal.id, goal): c.id
                for c in to_draft
            }
            for future in as_completed(futures):
                if _cancelled(db, run):
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                contact_id, result, insight_id = future.result()
                contact = by_id.get(contact_id)
                if result and contact:
                    try:
                        db.add(
                            EmailDraft(
                                principal_id=principal.id,
                                company_id=contact.company_id,
                                contact_id=contact_id,
                                insight_id=insight_id,
                                subject=result.subject,
                                body=result.body,
                                from_mailbox=mailbox_id,
                                status=EmailStatus.DRAFT,
                            )
                        )
                        db.commit()
                        generated += 1
                    except Exception as exc:  # noqa: BLE001 - keep drafting the rest
                        db.rollback()
                        logger.warning(
                            "Saving draft failed for contact %s in run %s: %s",
                            contact_id, run_id, exc,
                        )
                done += 1
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


# --- pipeline (approve + draft + send, overlapped) -------------------------
#
# The three jobs above (approve, draft, send) are each fast internally now
# (Approach A: thread-pool concurrency), but the "Approve all -> Draft all ->
# Send all" flow still runs them one whole pass after another: Send cannot
# start until every contact has been drafted, and Draft cannot start until
# every contact has been approved. On ~500 prospects that adds the three
# stages' wall-clock times together even though Send is I/O-bound on a totally
# different resource (SMTP, paced) than Approve/Draft (Anthropic + Apollo).
#
# This pipeline overlaps them: a pool of worker threads runs approve+draft per
# contact (same tested per-contact logic as the two jobs above), and as soon
# as a contact's draft is ready it is pushed onto a queue. A single dedicated
# sender thread drains that queue continuously — sending immediately, paced by
# ``bulk_email_send_delay_seconds`` — so sending overlaps with still-in-flight
# approve/draft work instead of waiting for it to fully finish.

_PIPELINE_SENTINEL = None


def launch_run_pipeline(
    run_id: int,
    contact_ids: Optional[list[int]] = None,
    *,
    outreach_goal: Optional[str] = None,
) -> None:
    """Approve + draft + send every targeted prospect, with sending overlapped
    against still-in-progress approve/draft work (background)."""
    _mark_starting(run_id, JOB_PIPELINE)
    threading.Thread(
        target=_pipeline_worker,
        args=(run_id, contact_ids, outreach_goal),
        name=f"run-pipeline-{run_id}",
        daemon=True,
    ).start()


def _pipeline_produce_one(
    contact_id: int, principal_id: int, outreach_goal: str
) -> tuple[int, Optional[int], Optional[str]]:
    """Approve (if needed) then draft (if needed) one contact. Runs in a worker
    thread with its own DB session. Returns (contact_id, draft_id_or_None,
    error_or_None); a draft_id of None with no error means the contact was
    skipped (e.g. draft-blocked) rather than failed."""
    from app.api.routes.emails import _principal_mailbox_id
    from app.services.contacts import ApprovalBlocked, RevealNotAllowed, approve_contact
    from app.services.insights.engine import generate_outreach
    from app.services.outreach_eligibility import outreach_draft_blockers

    db = SessionLocal()
    try:
        contact = db.get(Contact, contact_id)
        principal = db.get(Principal, principal_id)
        if contact is None or principal is None:
            return contact_id, None, "not found"

        if not contact.approved_for_outreach:
            try:
                approve_contact(db, contact, principal, approved_by="bulk-pipeline")
            except (ApprovalBlocked, RevealNotAllowed) as exc:
                return contact_id, None, f"approve: {exc}"
            db.refresh(contact)

        existing = db.execute(
            select(EmailDraft)
            .where(
                EmailDraft.principal_id == principal.id,
                EmailDraft.contact_id == contact_id,
                EmailDraft.status.in_(
                    [EmailStatus.DRAFT, EmailStatus.APPROVED, EmailStatus.SCHEDULED]
                ),
            )
            .order_by(EmailDraft.id.desc())
        ).scalars().first()
        if existing:
            return contact_id, existing.id, None

        if outreach_draft_blockers(db, principal_id=principal.id, contact=contact):
            return contact_id, None, None

        company = db.get(Company, contact.company_id) if contact.company_id else None
        insight = db.execute(
            select(RelevanceInsight)
            .where(
                RelevanceInsight.principal_id == principal_id,
                RelevanceInsight.contact_id == contact_id,
            )
            .order_by(RelevanceInsight.created_at.desc())
        ).scalars().first()
        result = generate_outreach(
            db, principal, contact, company, insight, outreach_goal=outreach_goal
        )
        draft = EmailDraft(
            principal_id=principal.id,
            company_id=contact.company_id,
            contact_id=contact_id,
            insight_id=insight.id if insight else None,
            subject=result.subject,
            body=result.body,
            from_mailbox=_principal_mailbox_id(principal),
            status=EmailStatus.DRAFT,
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return contact_id, draft.id, None
    except Exception as exc:  # noqa: BLE001 - keep the pipeline running
        logger.exception("Pipeline approve+draft failed for contact %s", contact_id)
        return contact_id, None, str(exc)
    finally:
        db.close()


def _pipeline_sender(
    run_id: int, work_queue: "queue.Queue", results: dict, stop_event: threading.Event
) -> None:
    """Dedicated thread: drains ``work_queue`` and sends each draft as soon as
    it arrives, paced. Runs concurrently with the approve/draft thread pool
    that is still feeding the queue. Exits once it pulls the sentinel that the
    orchestrator pushes after every producer has finished."""
    from datetime import datetime

    from app.api.routes.emails import SendError, perform_send

    db = SessionLocal()
    sent = 0
    failures: list[str] = []
    first = True
    # Reused across every draft in this batch (see perform_send's
    # provider_cache param) — for Gmail this keeps one authenticated SMTP
    # connection open for the whole run instead of reconnecting per email,
    # which was the single biggest reason bulk sends were slow.
    provider_cache: dict = {}
    try:
        while True:
            item = work_queue.get()
            if item is _PIPELINE_SENTINEL:
                break
            contact_id, draft_id = item
            if stop_event.is_set():
                continue
            delay = max(0.0, float(settings.bulk_email_send_delay_seconds))
            if not first and delay:
                time.sleep(delay)
            first = False
            try:
                draft = db.get(EmailDraft, draft_id)
                if draft is None:
                    continue
                if draft.status == EmailStatus.DRAFT:
                    draft.status = EmailStatus.APPROVED
                    draft.approved_by = "bulk-pipeline"
                    draft.approved_at = datetime.utcnow()
                    db.commit()
                perform_send(db, draft, provider_cache=provider_cache)
                sent += 1
                _progress_sent(db, run_id, sent)
            except SendError as exc:
                db.rollback()
                failures.append(f"{contact_id}: {exc.message}")
            except Exception as exc:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                logger.exception("Pipeline send failed for contact %s", contact_id)
                failures.append(f"{contact_id}: {exc}")
    finally:
        for provider in provider_cache.values():
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning("Failed to close email provider connection", exc_info=True)
        db.close()
        results["sent"] = sent
        results["send_failures"] = failures


def _pipeline_worker(
    run_id: int, contact_ids: Optional[list[int]], outreach_goal: Optional[str]
) -> None:
    from app.services.outreach_goal import outreach_goal_for_run

    db = SessionLocal()
    try:
        run = db.get(DiscoveryRun, run_id)
        if run is None:
            return
        principal = db.get(Principal, run.principal_id) if run.principal_id else None
        if principal is None:
            _start_job(db, run, JOB_PIPELINE, 0)
            _finish_job(db, run, JOB_FAILED, "This run has no principal.")
            return

        query = select(Contact).where(Contact.discovery_run_id == run_id)
        if contact_ids:
            query = query.where(Contact.id.in_(contact_ids))
        targets = list(db.execute(query.order_by(Contact.id)).scalars().all())
        targets = [c for c in targets if not c.do_not_contact]

        _start_job(db, run, JOB_PIPELINE, len(targets))
        if not targets:
            _finish_job(db, run, JOB_DONE)
            return

        goal = (outreach_goal or "").strip() or outreach_goal_for_run(run.criteria)

        # The sender runs in its own thread for the whole job, started before
        # any producing begins, so it can start sending the moment the first
        # contact is ready instead of waiting on the pool.
        work_queue: "queue.Queue" = queue.Queue()
        send_results: dict = {}
        stop_event = threading.Event()
        sender_thread = threading.Thread(
            target=_pipeline_sender,
            args=(run_id, work_queue, send_results, stop_event),
            name=f"run-pipeline-sender-{run_id}",
            daemon=True,
        )
        sender_thread.start()

        workers = max(1, int(settings.bulk_approve_workers))
        produced = 0
        skipped = 0
        produce_failures: list[str] = []
        by_id = {c.id: c for c in targets}
        done = 0
        cancelled = False
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_pipeline_produce_one, c.id, principal.id, goal): c.id
                for c in targets
            }
            for future in as_completed(futures):
                if _cancelled(db, run):
                    cancelled = True
                    stop_event.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                contact_id, draft_id, err = future.result()
                if draft_id is not None:
                    produced += 1
                    if not cancelled:
                        work_queue.put((contact_id, draft_id))
                elif err:
                    contact = by_id.get(contact_id)
                    produce_failures.append(f"{contact.name if contact else contact_id}: {err}")
                else:
                    skipped += 1
                done += 1
                # Approve+draft is done for `done` contacts; the sender is still
                # working through its own queue behind this, so this progress
                # number reflects "queued for send", not "fully sent" — the
                # send count is folded into the final summary below.
                _progress(db, run, done)

        # Signal the sender that no more work is coming, then wait for it to
        # drain whatever is still queued.
        work_queue.put(_PIPELINE_SENTINEL)
        sender_thread.join()

        sent = send_results.get("sent", 0)
        send_failures = send_results.get("send_failures", [])

        notes: list[str] = []
        produce_summary = _summarize_failures(produce_failures, "could not be approved/drafted")
        if produce_summary:
            notes.append(produce_summary)
        send_summary = _summarize_failures(send_failures, "could not be sent")
        if send_summary:
            notes.append(send_summary)
        if skipped:
            notes.append(f"{skipped} skipped (already handled or not eligible).")
        if cancelled:
            notes.append("Cancelled - remaining prospects were not processed.")
        _finish_job(db, run, JOB_DONE, "; ".join(notes) or None)
        logger.info(
            "Run %s pipeline: %s produced, %s sent, %s skipped of %s targets",
            run_id, produced, sent, skipped, len(targets),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline worker failed for run %s", run_id)
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
    # Reused across every draft in this batch (see perform_send's
    # provider_cache param) — for Gmail this keeps one authenticated SMTP
    # connection open for the whole run instead of reconnecting per email.
    provider_cache: dict = {}
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
                perform_send(db, draft, provider_cache=provider_cache)
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

        error = _summarize_failures(failures, "email(s) could not be sent")
        _finish_job(db, run, JOB_DONE, error)
        logger.info("Run %s bulk email send: %s sent, %s failed", run_id, sent, len(failures))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk email send worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        for provider in provider_cache.values():
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning("Failed to close email provider connection", exc_info=True)
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
        failure_summary = _summarize_failures(failures, "message(s) could not be sent")
        if failure_summary:
            notes.append(failure_summary)
        if held:
            notes.append(
                f"{held} held to stay under the daily LinkedIn cap of {cap} - "
                "run this again tomorrow to continue."
            )
        _finish_job(db, run, JOB_DONE, "; ".join(notes) or None)
        logger.info(
            "Run %s bulk LinkedIn send: %s sent, %s failed, %s held (cap %s)",
            run_id, sent, len(failures), held, cap,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bulk LinkedIn send worker failed for run %s", run_id)
        _fail(db, run_id, str(exc))
    finally:
        db.close()


# --- LinkedIn "approve & send all" (not run-anchored) -----------------------

def launch_linkedin_message_send(message_ids: list[int]) -> None:
    """Approve (if draft) + send specific LinkedIn messages, paced, in background.

    Used by the LinkedIn page's "Approve & send all" button. The caller has
    already applied the daily cap (it passes only the ids that fit today's
    budget), so this simply sends the given ids in order, spaced by
    ``bulk_linkedin_send_delay_seconds`` to protect the account. Reuses the exact
    tested single-send path (``perform_linkedin_send``) — DM if connected, else a
    connection invite — so behaviour matches sending one at a time.
    """
    if not message_ids:
        return
    threading.Thread(
        target=_linkedin_message_send_worker,
        args=(list(message_ids),),
        name=f"linkedin-bulk-send-{len(message_ids)}",
        daemon=True,
    ).start()


def _linkedin_message_send_worker(message_ids: list[int]) -> None:
    from datetime import datetime

    from app.api.routes.linkedin import SendError, perform_linkedin_send

    db = SessionLocal()
    try:
        delay = max(0.0, float(settings.bulk_linkedin_send_delay_seconds))
        sent = 0
        failed = 0
        for index, mid in enumerate(message_ids):
            msg = db.get(LinkedInMessage, mid)
            if msg is None or msg.status not in (
                LinkedInStatus.DRAFT,
                LinkedInStatus.APPROVED,
            ):
                continue
            if msg.status == LinkedInStatus.DRAFT:
                msg.status = LinkedInStatus.APPROVED
                msg.approved_by = "bulk-approve-send"
                msg.approved_at = datetime.utcnow()
                db.commit()
            try:
                perform_linkedin_send(db, msg)
                sent += 1
            except SendError as exc:
                db.rollback()
                failed += 1
                logger.warning(
                    "Bulk approve+send failed for message %s: %s", mid, exc.message
                )
            except Exception:  # noqa: BLE001 - keep sending the rest
                db.rollback()
                failed += 1
                logger.exception("Bulk approve+send crashed for message %s", mid)
            if delay and index < len(message_ids) - 1:
                time.sleep(delay)
        logger.info(
            "LinkedIn bulk approve+send: %s sent, %s failed of %s",
            sent, failed, len(message_ids),
        )
    except Exception:  # noqa: BLE001 - never let the worker die silently
        logger.exception("LinkedIn bulk message-send worker failed")
    finally:
        db.close()


def _fail(db: Session, run_id: int, message: str) -> None:
    db.rollback()
    run = db.get(DiscoveryRun, run_id)
    if run is not None:
        _finish_job(db, run, JOB_FAILED, message)
