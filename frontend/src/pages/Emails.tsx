import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  deleteEmail,
  generateRunDrafts,
  getDiscoveryRun,
  listDiscoveryRuns,
  listEmails,
  listProspects,
  regenerateEmail,
  regenerateRunDrafts,
  scheduleEmail,
  sendEmail,
  setEmailStatus,
  unscheduleEmail,
  updateEmail,
} from "../api/client";
import type { EmailDraft } from "../types";
import WorkflowSteps from "../components/WorkflowSteps";
import { outreachGoalFromCriteria } from "../utils/outreachGoal";
import {
  canDraftProspect,
  draftBlockers,
} from "../utils/prospectReady";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
} from "../components/ui";

const STATUS_TABS = [
  { key: "", label: "All" },
  { key: "draft", label: "Draft" },
  { key: "approved", label: "Approved" },
  { key: "scheduled", label: "Scheduled" },
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
] as const;

/** Default the schedule picker to ~1 hour out, formatted for datetime-local. */
function defaultScheduleValue(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setMinutes(0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

function FieldRow({
  label,
  children,
  warn,
}: {
  label: string;
  children: ReactNode;
  warn?: string;
}) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 border-b border-slate-100 py-2.5 last:border-0">
      <span className="pt-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </span>
      <div>
        {children}
        {warn && <p className="mt-1 text-xs text-amber-600">{warn}</p>}
      </div>
    </div>
  );
}

function DraftCard({
  draft,
  selected,
  onToggleSelect,
}: {
  draft: EmailDraft;
  selected: boolean;
  onToggleSelect: (id: number) => void;
}) {
  const qc = useQueryClient();
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [showSchedule, setShowSchedule] = useState(false);
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue);

  // Keep the textarea in sync when the server body changes (e.g. bulk signature).
  useEffect(() => {
    setSubject(draft.subject);
    setBody(draft.body);
  }, [draft.id, draft.subject, draft.body]);
  const dirty = subject !== draft.subject || body !== draft.body;
  const lineCount = body.split("\n").filter((l) => l.trim()).length;
  const hasDashes = /[—–]|\s-\s/.test(body) || /[—–]/.test(subject);
  const locked = draft.status === "sent" || draft.status === "scheduled";

  const save = useMutation({
    mutationFn: () => updateEmail(draft.id, { subject, body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["emails"] }),
  });
  const status = useMutation({
    mutationFn: (s: string) => setEmailStatus(draft.id, s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["emails"] }),
  });
  const send = useMutation({
    mutationFn: () => sendEmail(draft.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["emails"] }),
  });
  const remove = useMutation({
    mutationFn: () => deleteEmail(draft.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["emails"] }),
  });
  const schedule = useMutation({
    mutationFn: () =>
      // datetime-local is local time; convert to a UTC ISO string for the API.
      scheduleEmail(draft.id, new Date(scheduleAt).toISOString()),
    onSuccess: () => {
      setShowSchedule(false);
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
  });
  const unschedule = useMutation({
    mutationFn: () => unscheduleEmail(draft.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["emails"] }),
  });
  const regenerate = useMutation({
    mutationFn: () => regenerateEmail(draft.id),
    onSuccess: (updated) => {
      setSubject(updated.subject);
      setBody(updated.body);
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
  });

  const step =
    draft.status === "replied" || draft.status === "sent"
      ? 4
      : draft.status === "approved" || draft.status === "scheduled"
        ? 3
        : 2;

  return (
    <Card className="overflow-hidden">
      {/* Recipient header */}
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-slate-50/80 px-5 py-4">
        <div className="flex items-start gap-3">
          {draft.status !== "sent" && draft.status !== "replied" && (
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggleSelect(draft.id)}
              className="mt-1"
              aria-label={`Select draft for ${draft.contact_name ?? draft.contact_id}`}
            />
          )}
          <div>
          <div className="flex flex-wrap items-center gap-2">
            {draft.contact_id ? (
              <Link
                to={`/prospects/${draft.contact_id}`}
                className="text-base font-semibold text-slate-900 hover:underline"
              >
                {draft.contact_name ?? `Prospect #${draft.contact_id}`}
              </Link>
            ) : (
              <span className="text-base font-semibold text-slate-900">Unknown recipient</span>
            )}
            <StatusBadge status={draft.status} />
          </div>
          <div className="mt-0.5 text-sm text-slate-500">
            {draft.contact_title && <span>{draft.contact_title}</span>}
            {draft.contact_title && draft.company_name && <span> · </span>}
            {draft.company_name && <span>{draft.company_name}</span>}
          </div>
          </div>
        </div>
        <div className="text-right text-xs text-slate-400">
          Step {step} of 4
          <div className="mt-1 font-medium text-slate-600">
            {draft.status === "draft" && "Review & approve"}
            {draft.status === "approved" && "Ready to send"}
            {draft.status === "scheduled" && "Scheduled"}
            {draft.status === "sent" && "Delivered · awaiting reply"}
            {draft.status === "replied" && "Replied"}
          </div>
        </div>
      </div>

      {draft.status === "replied" && (
        <div className="border-b border-emerald-100 bg-emerald-50 px-5 py-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
            Reply received
            {draft.replied_at && (
              <span className="ml-2 font-normal normal-case text-emerald-600">
                {new Date(draft.replied_at).toLocaleString()}
              </span>
            )}
          </div>
          {draft.reply_snippet && (
            <p className="mt-1 text-sm text-emerald-900">"{draft.reply_snippet}"</p>
          )}
        </div>
      )}

      {draft.status === "scheduled" && draft.scheduled_at && (
        <div className="flex items-center justify-between gap-3 border-b border-purple-100 bg-purple-50 px-5 py-3">
          <div className="text-sm text-purple-900">
            <span className="font-semibold">Scheduled to send</span>{" "}
            {new Date(draft.scheduled_at).toLocaleString()}
            <span className="ml-2 text-xs text-purple-600">
              (fires only while the backend is running)
            </span>
          </div>
        </div>
      )}

      {/* Email compose fields */}
      <div className="px-5 py-2">
        <FieldRow label="From">
          <span className="text-sm text-slate-800">
            {draft.principal_name ?? "Principal"}
            {draft.from_email && (
              <span className="text-slate-500"> &lt;{draft.from_email}&gt;</span>
            )}
          </span>
        </FieldRow>

        <FieldRow
          label="To"
          warn={
            !draft.contact_email
              ? "No email on file. Reveal the prospect's email before sending."
              : undefined
          }
        >
          {draft.contact_email ? (
            <span className="text-sm text-slate-800">
              {draft.contact_name ?? "Recipient"}
              <span className="text-slate-500"> &lt;{draft.contact_email}&gt;</span>
            </span>
          ) : (
            <Badge tone="amber">Email not revealed</Badge>
          )}
        </FieldRow>

        <FieldRow label="Subject">
          <input
            value={subject}
            disabled={locked}
            onChange={(e) => setSubject(e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 focus:border-slate-400 focus:outline-none"
            placeholder="Email subject"
          />
        </FieldRow>

        <FieldRow label="Body">
          <textarea
            value={body}
            disabled={locked}
            onChange={(e) => setBody(e.target.value)}
            rows={5}
            className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-relaxed text-slate-800 focus:border-slate-400 focus:outline-none"
            placeholder="4-5 short lines. No dashes."
          />
          <p
            className={`mt-1 text-xs ${
              lineCount > 5 || hasDashes ? "text-rose-600" : "text-slate-400"
            }`}
          >
            {lineCount} line{lineCount !== 1 ? "s" : ""} (aim for 4-5)
            {hasDashes && " · Remove dashes before sending"}
          </p>
        </FieldRow>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/50 px-5 py-3">
        <span className="text-xs text-slate-400">
          Sending as {draft.principal_name ?? "principal"} via Outlook
        </span>
        <div className="flex flex-wrap gap-2">
          {dirty && !locked && (
            <Button variant="secondary" onClick={() => save.mutate()}>
              Save edits
            </Button>
          )}
          {draft.status === "draft" && (
            <>
              <Button
                variant="ghost"
                onClick={() => regenerate.mutate()}
                disabled={regenerate.isPending}
              >
                {regenerate.isPending ? "Regenerating…" : "Regenerate"}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm("Delete this draft?")) remove.mutate();
                }}
                disabled={remove.isPending}
              >
                Delete
              </Button>
              <Button
                onClick={() => status.mutate("approved")}
                disabled={!draft.contact_email}
                title={!draft.contact_email ? "Reveal prospect email first" : undefined}
              >
                Approve to send
              </Button>
            </>
          )}
          {draft.status === "approved" && (
            <>
              <Button
                variant="ghost"
                onClick={() => regenerate.mutate()}
                disabled={regenerate.isPending}
              >
                {regenerate.isPending ? "Regenerating…" : "Regenerate"}
              </Button>
              <Button variant="ghost" onClick={() => status.mutate("draft")}>
                Back to draft
              </Button>
              <Button
                variant="secondary"
                onClick={() => setShowSchedule((s) => !s)}
              >
                Schedule…
              </Button>
              <Button onClick={() => send.mutate()} disabled={send.isPending}>
                {send.isPending ? "Sending…" : "Send now"}
              </Button>
            </>
          )}
          {draft.status === "scheduled" && (
            <>
              <Button
                variant="ghost"
                onClick={() => unschedule.mutate()}
                disabled={unschedule.isPending}
              >
                Cancel schedule
              </Button>
              <Button onClick={() => send.mutate()} disabled={send.isPending}>
                {send.isPending ? "Sending…" : "Send now"}
              </Button>
            </>
          )}
          {draft.status === "sent" && (
            <Button variant="ghost" onClick={() => status.mutate("replied")}>
              Mark replied
            </Button>
          )}
        </div>
        {showSchedule && draft.status === "approved" && (
          <div className="mt-2 flex w-full flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
            <label className="text-xs font-medium text-slate-500">
              Send at
            </label>
            <input
              type="datetime-local"
              value={scheduleAt}
              min={defaultScheduleValue()}
              onChange={(e) => setScheduleAt(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            />
            <Button
              onClick={() => schedule.mutate()}
              disabled={schedule.isPending || !scheduleAt}
            >
              {schedule.isPending ? "Scheduling…" : "Confirm schedule"}
            </Button>
          </div>
        )}
        {schedule.isError && (
          <p className="w-full text-right text-xs text-rose-600">
            {(schedule.error as { response?: { data?: { detail?: string } } })
              ?.response?.data?.detail ||
              (schedule.error as Error)?.message ||
              "Could not schedule"}
          </p>
        )}
        {send.isError && (
          <p className="w-full text-right text-xs text-rose-600">
            {(send.error as Error)?.message || "Send failed"}
          </p>
        )}
      </div>
    </Card>
  );
}

export default function Emails() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run")
    ? Number(searchParams.get("run"))
    : undefined;
  const [statusFilter, setStatusFilter] = useState("draft");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkNote, setBulkNote] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [manualPurpose, setManualPurpose] = useState("");

  const { data: runs } = useQuery({
    queryKey: ["discovery-runs"],
    queryFn: () => listDiscoveryRuns({ limit: 25 }),
  });

  const { data: run } = useQuery({
    queryKey: ["discovery-run", runId],
    queryFn: () => getDiscoveryRun(runId!),
    enabled: !!runId,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["emails", statusFilter, runId],
    queryFn: () =>
      listEmails({
        limit: 500,
        ...(statusFilter ? { status: statusFilter } : {}),
        ...(runId ? { discovery_run_id: runId } : {}),
      }),
  });

  const { data: allForScope } = useQuery({
    queryKey: ["emails", "tab-counts", runId],
    queryFn: () =>
      listEmails({
        limit: 1000,
        ...(runId ? { discovery_run_id: runId } : {}),
      }),
  });

  const { data: approvedProspects } = useQuery({
    queryKey: ["prospects", "approved-for-drafts", runId],
    queryFn: () =>
      listProspects({
        discovery_run_id: runId!,
        approved: true,
        limit: 500,
      }),
    enabled: !!runId,
  });

  const inferredPurpose = useMemo(
    () => outreachGoalFromCriteria(run?.criteria ?? null),
    [run?.criteria]
  );
  const outreachPurpose =
    inferredPurpose || manualPurpose.trim() || null;

  const approvedWithoutDraft = useMemo(() => {
    const draftContactIds = new Set(
      (allForScope?.items ?? [])
        .filter((d) =>
          ["draft", "approved", "scheduled"].includes(d.status)
        )
        .map((d) => d.contact_id)
    );
    return (approvedProspects?.items ?? []).filter(
      (p) => !draftContactIds.has(p.id) && canDraftProspect(p)
    );
  }, [approvedProspects, allForScope]);

  const approvedBlockedFromDraft = useMemo(() => {
    const draftContactIds = new Set(
      (allForScope?.items ?? [])
        .filter((d) =>
          ["draft", "approved", "scheduled"].includes(d.status)
        )
        .map((d) => d.contact_id)
    );
    return (approvedProspects?.items ?? []).filter(
      (p) => !draftContactIds.has(p.id) && !canDraftProspect(p)
    );
  }, [approvedProspects, allForScope]);

  const tabCounts = useMemo(() => {
    const counts: Record<string, number> = {
      "": 0,
      draft: 0,
      approved: 0,
      scheduled: 0,
      sent: 0,
      replied: 0,
    };
    for (const d of allForScope?.items ?? []) {
      counts[""] += 1;
      if (d.status in counts) counts[d.status] += 1;
    }
    return counts;
  }, [allForScope]);

  const regenerateRun = useMutation({
    mutationFn: () =>
      regenerateRunDrafts({
        discovery_run_id: runId!,
        only_statuses: ["draft", "approved"],
      }),
    onSuccess: (res) => {
      const warn =
        res.provider_warnings?.length
          ? ` Provider issue: ${res.provider_warnings.join(" ")}`
          : "";
      setBulkNote(
        `Regenerated ${res.regenerated} of ${res.candidates} draft(s)` +
          (res.errors.length ? `. ${res.errors.length} error(s) — check logs.` : "") +
          "." +
          warn
      );
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
    onError: () => setBulkNote("Bulk regenerate failed — check backend logs."),
  });

  const draftApproved = useMutation({
    mutationFn: () =>
      generateRunDrafts({
        discovery_run_id: runId!,
        principal_id: run?.principal_id ?? undefined,
        outreach_goal: outreachPurpose ?? undefined,
      }),
    onSuccess: (res) => {
      const warn =
        res.provider_warnings?.length
          ? ` Provider issue: ${res.provider_warnings.join(" ")}`
          : "";
      setBulkNote(
        `Drafted ${res.generated} email(s) for approved prospects` +
          (res.skipped ? ` (${res.skipped} already had drafts)` : "") +
          (res.errors.length ? `. ${res.errors.length} error(s).` : ".") +
          warn
      );
      qc.invalidateQueries({ queryKey: ["emails"] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
    },
    onError: () => setBulkNote("Could not draft approved prospects — check backend logs."),
  });

  const items = data?.items ?? [];

  const selectableItems = items.filter(
    (d) => d.status !== "sent" && d.status !== "replied"
  );
  const selectedDrafts = items.filter((d) => selected.has(d.id));
  const allSelectableSelected =
    selectableItems.length > 0 &&
    selectableItems.every((d) => selected.has(d.id));

  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected((prev) => {
      if (selectableItems.length > 0 && selectableItems.every((d) => prev.has(d.id)))
        return new Set();
      return new Set(selectableItems.map((d) => d.id));
    });
  const clearSelection = () => setSelected(new Set());

  const setRunFilter = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("run", value);
    else next.delete("run");
    setSearchParams(next);
    setSelected(new Set());
  };

  async function runBulk(
    label: string,
    targets: EmailDraft[],
    perItem: (d: EmailDraft) => Promise<"ok" | "skipped">
  ) {
    if (targets.length === 0) return;
    setBulkBusy(true);
    setBulkNote(null);
    let ok = 0;
    let skipped = 0;
    let failed = 0;
    // Sequential to stay gentle on the Outlook/Graph send limits.
    for (const draft of targets) {
      try {
        const r = await perItem(draft);
        if (r === "ok") ok += 1;
        else skipped += 1;
      } catch {
        failed += 1;
      }
    }
    await qc.invalidateQueries({ queryKey: ["emails"] });
    await qc.invalidateQueries({ queryKey: ["prospects"] });
    setBulkBusy(false);
    setBulkNote(
      `${label}: ${ok} succeeded` +
        (skipped ? `, ${skipped} skipped` : "") +
        (failed ? `, ${failed} failed` : "") +
        `.`
    );
    clearSelection();
  }

  function bulkApprove() {
    runBulk("Approve", selectedDrafts, async (d) => {
      if (!d.contact_email) return "skipped";
      if (d.status === "draft") {
        await setEmailStatus(d.id, "approved");
        return "ok";
      }
      return "skipped";
    });
  }

  function bulkDelete() {
    const deletable = selectedDrafts.filter(
      (d) => d.status !== "sent" && d.status !== "replied"
    );
    if (deletable.length === 0) {
      setBulkNote("Select one or more unsent drafts to delete.");
      return;
    }
    if (
      !window.confirm(
        `Delete ${deletable.length} unsent draft(s)? Sent emails are never deleted. This cannot be undone.`
      )
    )
      return;
    runBulk("Delete", deletable, async (d) => {
      await deleteEmail(d.id);
      return "ok";
    });
  }

  function bulkSend() {
    const sendable = selectedDrafts.filter((d) => d.contact_email);
    if (sendable.length === 0) {
      setBulkNote("None of the selected drafts have a revealed email to send to.");
      return;
    }
    if (
      !window.confirm(
        `Send ${sendable.length} email(s) now from your connected Outlook? This approves any drafts and sends immediately.`
      )
    )
      return;
    runBulk("Send", selectedDrafts, async (d) => {
      if (!d.contact_email) return "skipped";
      if (d.status === "draft") {
        await setEmailStatus(d.id, "approved");
      }
      await sendEmail(d.id);
      return "ok";
    });
  }

  function bulkApproveAll() {
    if (selectableItems.length === 0) {
      setBulkNote("No drafts to approve in this view.");
      return;
    }
    runBulk("Approve", selectableItems, async (d) => {
      if (!d.contact_email) return "skipped";
      if (d.status === "draft") {
        await setEmailStatus(d.id, "approved");
        return "ok";
      }
      return "skipped";
    });
  }

  function bulkSendAll() {
    const sendable = selectableItems.filter((d) => d.contact_email);
    if (sendable.length === 0) {
      setBulkNote("None of these drafts have a revealed email to send to.");
      return;
    }
    if (
      !window.confirm(
        `Send ${sendable.length} email(s) now from your connected Outlook? This approves any drafts and sends immediately.`
      )
    )
      return;
    runBulk("Send", selectableItems, async (d) => {
      if (!d.contact_email) return "skipped";
      if (d.status === "draft") {
        await setEmailStatus(d.id, "approved");
      }
      await sendEmail(d.id);
      return "ok";
    });
  }

  return (
    <div>
      <WorkflowSteps active={3} />

      <PageHeader
        title={runId ? `Drafts — Run #${runId}` : "Drafts"}
        subtitle={
          runId
            ? "Approved prospects only get drafted here. Edit each email, approve, then send from Outlook."
            : "Pick a discovery run to draft outreach for approved prospects, then send and track replies."
        }
      />

      {runId && (
        <Card className="mb-4 border-indigo-200 bg-indigo-50/60 p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-600">
            Outreach purpose
          </p>
          {inferredPurpose ? (
            <p className="mt-1 text-sm text-indigo-950">{inferredPurpose}</p>
          ) : (
            <div className="mt-2 space-y-2">
              <p className="text-sm text-indigo-900">
                No AI goal stored for this run. Describe why you are reaching out — drafts
                will use this as context.
              </p>
              <textarea
                className="w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm"
                rows={2}
                placeholder="e.g. Introduce our 503B compounding services to hospital pharmacy directors"
                value={manualPurpose}
                onChange={(e) => setManualPurpose(e.target.value)}
              />
              {manualPurpose.trim() && (
                <p className="text-xs text-indigo-700">
                  Purpose will apply when you click <strong>Draft all approved</strong>.
                </p>
              )}
            </div>
          )}
        </Card>
      )}

      {bulkNote && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {bulkNote}
        </div>
      )}

      {runId && approvedBlockedFromDraft.length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-950">
          <strong>{approvedBlockedFromDraft.length}</strong> approved prospect
          {approvedBlockedFromDraft.length === 1 ? " is" : "s are"} missing research
          or email — drafts are skipped to save tokens.{" "}
          <Link to={`/prospects?run=${runId}`} className="font-medium underline">
            Fix on Prospects →
          </Link>
          <ul className="mt-2 list-inside list-disc text-xs text-amber-900">
            {approvedBlockedFromDraft.slice(0, 5).map((p) => (
              <li key={p.id}>
                {p.name}: {draftBlockers(p).join(", ")}
              </li>
            ))}
            {approvedBlockedFromDraft.length > 5 && (
              <li>…and {approvedBlockedFromDraft.length - 5} more</li>
            )}
          </ul>
        </div>
      )}

      {runId && approvedWithoutDraft.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-950">
          <span>
            <strong>{approvedWithoutDraft.length}</strong> approved prospect
            {approvedWithoutDraft.length === 1 ? "" : "s"} without a draft yet.
            {!outreachPurpose && (
              <span className="ml-1 text-amber-800">
                Add an outreach purpose above before drafting.
              </span>
            )}
          </span>
          <Button
            onClick={() => draftApproved.mutate()}
            disabled={
              draftApproved.isPending ||
              bulkBusy ||
              !outreachPurpose
            }
          >
            {draftApproved.isPending
              ? "Drafting…"
              : `Draft all ${approvedWithoutDraft.length} approved`}
          </Button>
        </div>
      )}

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium text-slate-600">Discovery run</label>
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={runId ?? ""}
            onChange={(e) => setRunFilter(e.target.value)}
          >
            <option value="">All runs (mixed)</option>
            {runs?.items.map((r) => (
              <option key={r.id} value={r.id}>
                Run #{r.id} · {r.people_imported ?? 0} prospects ·{" "}
                {r.insights_generated != null
                  ? `${r.insights_generated} researched`
                  : new Date(r.created_at).toLocaleDateString()}
              </option>
            ))}
          </select>
          {runId && (
            <>
              <Link
                to={`/prospects?run=${runId}`}
                className="text-sm font-medium text-blue-700 underline hover:text-blue-900"
              >
                View prospects →
              </Link>
              <button
                type="button"
                onClick={() => setRunFilter("")}
                className="text-sm text-slate-500 underline hover:text-slate-700"
              >
                Clear run filter
              </button>
            </>
          )}
        </div>
        {runId && run && (
          <p className="mt-2 text-sm text-slate-600">
            Run #{runId} · {run.people_imported ?? 0} prospects
            {run.insights_generated != null && ` · ${run.insights_generated} researched`}
            {" · "}
            {new Date(run.created_at).toLocaleString()}
          </p>
        )}
      </Card>

      {runId && !isLoading && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-900">
          <div>
            <strong>Run #{runId}</strong>
            {allForScope && (
              <span className="ml-2 text-blue-800">
                {tabCounts.draft} draft · {tabCounts.approved} approved ·{" "}
                {tabCounts.scheduled} scheduled · {tabCounts.sent} sent ·{" "}
                {tabCounts.replied} replied
              </span>
            )}
            {statusFilter === "sent" && tabCounts.sent === 0 && (
              <span className="mt-1 block text-xs text-blue-700">
                No sent emails for this run yet. Clear the run filter to see sent mail
                from other runs.
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {tabCounts.draft + tabCounts.approved > 0 && (
              <Button
                variant="secondary"
                onClick={() => {
                  if (
                    window.confirm(
                      `Regenerate all ${tabCounts.draft + tabCounts.approved} editable draft(s) for run #${runId}? This replaces subject and body with fresh AI copy.`
                    )
                  ) {
                    regenerateRun.mutate();
                  }
                }}
                disabled={regenerateRun.isPending || bulkBusy}
              >
                {regenerateRun.isPending ? "Regenerating…" : "Regenerate all drafts"}
              </Button>
            )}
            {items.length > 0 && statusFilter === "draft" && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => setSelected(new Set(selectableItems.map((d) => d.id)))}
                  disabled={bulkBusy || selectableItems.length === 0}
                >
                  Select all {selectableItems.length}
                </Button>
                <Button
                  onClick={bulkApproveAll}
                  disabled={bulkBusy || selectableItems.length === 0}
                >
                  Approve all
                </Button>
                <Button
                  onClick={bulkSendAll}
                  disabled={bulkBusy || selectableItems.length === 0}
                >
                  Approve & send all
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key || "all"}
            type="button"
            onClick={() => {
              setStatusFilter(tab.key);
              clearSelection();
            }}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              statusFilter === tab.key
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {tab.label}
            <span
              className={`ml-1.5 tabular-nums ${
                statusFilter === tab.key ? "text-slate-300" : "text-slate-400"
              }`}
            >
              {tabCounts[tab.key] ?? 0}
            </span>
          </button>
        ))}
        <Link
          to="/outreach"
          className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-blue-700 ring-1 ring-blue-200 transition hover:bg-blue-50"
        >
          Track replies on Conversations →
        </Link>
      </div>

      {/* Bulk action toolbar */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-300 bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">
          <span className="font-medium">{selected.size} selected</span>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={bulkApprove} disabled={bulkBusy}>
              Approve selected
            </Button>
            <Button variant="secondary" onClick={bulkSend} disabled={bulkBusy}>
              {bulkBusy ? "Working…" : "Approve & send selected"}
            </Button>
            <Button variant="danger" onClick={bulkDelete} disabled={bulkBusy}>
              Delete selected
            </Button>
            <button
              type="button"
              onClick={clearSelection}
              className="px-2 text-slate-300 underline hover:text-white"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            message={
              runId
                ? `No ${statusFilter || ""} drafts for discovery run #${runId}. Try another status tab, or pick a different run above.`
                : "No drafts yet. Research prospects and draft emails from the Prospects page, or filter by discovery run above."
            }
          />
        </Card>
      ) : (
        <>
          {selectableItems.length > 0 && (
            <label className="mb-3 flex items-center gap-2 px-1 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={allSelectableSelected}
                onChange={toggleAll}
              />
              Select all {selectableItems.length} on this tab
            </label>
          )}
          <div className="space-y-4">
            {items.map((d) => (
              <DraftCard
                key={d.id}
                draft={d}
                selected={selected.has(d.id)}
                onToggleSelect={toggleOne}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
