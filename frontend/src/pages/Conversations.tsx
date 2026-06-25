import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import {
  checkEmailReplies,
  generateFollowups,
  listEmails,
  scheduleEmail,
  sendEmail,
  setEmailStatus,
  unscheduleEmail,
} from "../api/client";
import WorkflowSteps from "../components/WorkflowSteps";
import {
  Badge,
  Button,
  Card,
  Loading,
  PageHeader,
} from "../components/ui";
import type { EmailDraft } from "../types";

/** Default the schedule picker to ~tomorrow 9am, formatted for datetime-local. */
function defaultScheduleValue(): string {
  const d = new Date(Date.now() + 24 * 60 * 60 * 1000);
  d.setHours(9, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

type Tone = "green" | "blue" | "amber" | "purple" | "slate";

function daysSince(iso?: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

function relativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = daysSince(iso);
  if (d === null) return "—";
  if (d === 0) return "today";
  if (d === 1) return "yesterday";
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** One person with all their email activity rolled up. */
type PersonRow = {
  contactId: number;
  name: string;
  title?: string | null;
  company?: string | null;
  drafts: EmailDraft[];
  replied: boolean;
  lastReplyAt?: string | null;
  replyPreview?: string | null;
  sentCount: number;
  opened: boolean;
  lastSentAt?: string | null;
  daysSilent: number | null;
  lastActivityAt: string;
  status: { label: string; tone: Tone };
  bucket: "replied" | "awaiting" | "draft";
  // The unsent draft we'd send/schedule for this person (most recent draft/approved).
  pendingDraft?: EmailDraft;
  // An already-scheduled draft (queued to send), if any.
  scheduledDraft?: EmailDraft;
  // The best draft to show in the inline preview.
  previewDraft?: EmailDraft;
  // Email is revealed, so the pending draft can actually be sent.
  canSend: boolean;
};

function maxIso(...vals: (string | null | undefined)[]): string | undefined {
  const valid = vals.filter(Boolean) as string[];
  if (valid.length === 0) return undefined;
  return valid.reduce((a, b) => (new Date(a) > new Date(b) ? a : b));
}

function buildPerson(contactId: number, drafts: EmailDraft[]): PersonRow {
  const first = drafts[0];
  const replied = drafts.find((d) => d.status === "replied");
  const sent = drafts
    .filter((d) => (d.status === "sent" || d.status === "replied") && d.sent_at)
    .sort((a, b) => +new Date(b.sent_at!) - +new Date(a.sent_at!));
  const lastSentAt = sent[0]?.sent_at ?? null;
  const daysSilent = daysSince(lastSentAt);

  const byNewest = (a: EmailDraft, b: EmailDraft) =>
    +new Date(b.created_at) - +new Date(a.created_at);
  const pendingDraft = drafts
    .filter((d) => d.status === "draft" || d.status === "approved")
    .sort(byNewest)[0];
  const scheduledDraft = drafts
    .filter((d) => d.status === "scheduled")
    .sort(byNewest)[0];
  const previewDraft =
    pendingDraft ?? scheduledDraft ?? [...drafts].sort(byNewest)[0];
  const canSend = Boolean(pendingDraft && pendingDraft.contact_email);

  let status: { label: string; tone: Tone };
  let bucket: PersonRow["bucket"];
  if (replied) {
    status = { label: "Replied", tone: "green" };
    bucket = "replied";
  } else if (lastSentAt) {
    const d = daysSilent ?? 0;
    status = {
      label: d === 0 ? "Awaiting reply · today" : `Awaiting reply · ${d}d`,
      tone: d >= 4 ? "amber" : "blue",
    };
    bucket = "awaiting";
  } else if (drafts.some((d) => d.status === "scheduled")) {
    status = { label: "Send scheduled", tone: "purple" };
    bucket = "draft";
  } else if (drafts.some((d) => d.status === "approved")) {
    status = { label: "Approved — not sent", tone: "amber" };
    bucket = "draft";
  } else {
    status = { label: "Draft", tone: "slate" };
    bucket = "draft";
  }

  return {
    contactId,
    name: first?.contact_name || `Contact #${contactId}`,
    title: first?.contact_title,
    company: first?.company_name,
    drafts,
    replied: Boolean(replied),
    lastReplyAt: replied?.replied_at ?? null,
    replyPreview: replied?.reply_snippet ?? replied?.reply_body ?? null,
    sentCount: sent.length,
    opened: sent.some((d) => (d.open_count ?? 0) > 0),
    lastSentAt,
    daysSilent,
    lastActivityAt:
      maxIso(
        ...drafts.flatMap((d) => [
          d.replied_at,
          d.sent_at,
          d.scheduled_at,
          d.created_at,
        ])
      ) || first?.created_at || new Date().toISOString(),
    status,
    bucket,
    pendingDraft,
    scheduledDraft,
    previewDraft,
    canSend,
  };
}

const VIEWS = [
  { key: "awaiting", label: "Awaiting reply" },
  { key: "replied", label: "Replied" },
  { key: "draft", label: "Not sent yet" },
  { key: "all", label: "All" },
] as const;
type ViewKey = (typeof VIEWS)[number]["key"];

const SILENCE_PRESETS = [
  { days: 0, label: "Any" },
  { days: 1, label: "1d+" },
  { days: 2, label: "2d+" },
  { days: 3, label: "3d+" },
  { days: 7, label: "7d+" },
];

export default function Conversations() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [view, setView] = useState<ViewKey>("awaiting");
  const [silence, setSilence] = useState(0);
  const [search, setSearch] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());

  const { data, isLoading } = useQuery({
    queryKey: ["emails", "all-for-conversations"],
    queryFn: () => listEmails({ limit: 500 }),
  });

  const checkReplies = useMutation({
    mutationFn: () => checkEmailReplies(),
    onSuccess: (res) => {
      setNote(res.message ?? "Checked for replies.");
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
    onError: () => setNote("Reply check failed — see backend logs."),
  });

  const draftFollowups = useMutation({
    mutationFn: (days: number) => generateFollowups({ days, limit: 20 }),
    onSuccess: (res) => {
      setNote(
        res.created > 0
          ? `Drafted ${res.created} follow-up${res.created === 1 ? "" : "s"} (${res.candidates} silent, ${res.skipped_pending} already had a pending draft). Review them on Drafts or each person's timeline.`
          : `No new follow-ups needed — ${res.candidates} silent, ${res.skipped_pending} already have a pending draft.`
      );
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
    onError: () => setNote("Follow-up drafting failed — see backend logs."),
  });

  const toggleSelect = (contactId: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(contactId)) next.delete(contactId);
      else next.add(contactId);
      return next;
    });

  const toggleExpand = (contactId: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(contactId)) next.delete(contactId);
      else next.add(contactId);
      return next;
    });

  const clearSelection = () => setSelected(new Set());

  /** Run an async action over each selected person who has a sendable draft. */
  async function runBulk(
    targets: PersonRow[],
    perItem: (p: PersonRow) => Promise<void>,
    verb: string
  ) {
    const actionable = targets.filter((p) => p.canSend && p.pendingDraft);
    if (actionable.length === 0) {
      setNote(
        "None of the selected people have a ready draft with a revealed email. Reveal the email and draft an outreach first."
      );
      return;
    }
    setBulkBusy(true);
    setNote(null);
    let ok = 0;
    let failed = 0;
    for (const p of actionable) {
      try {
        await perItem(p);
        ok += 1;
      } catch {
        failed += 1;
      }
    }
    await qc.invalidateQueries({ queryKey: ["emails"] });
    await qc.invalidateQueries({ queryKey: ["stats"] });
    await qc.invalidateQueries({ queryKey: ["prospects"] });
    setBulkBusy(false);
    const skipped = targets.length - actionable.length;
    setNote(
      `${verb}: ${ok} succeeded` +
        (failed ? `, ${failed} failed` : "") +
        (skipped ? `, ${skipped} skipped (no ready draft/email)` : "") +
        "."
    );
    clearSelection();
    setShowSchedule(false);
  }

  function bulkSendNow(targets: PersonRow[]) {
    const actionable = targets.filter((p) => p.canSend && p.pendingDraft);
    if (actionable.length === 0) {
      setNote("None of the selected people have a ready draft with a revealed email.");
      return;
    }
    if (
      !window.confirm(
        `Send ${actionable.length} email(s) now from your connected Outlook? This approves any drafts and sends immediately.`
      )
    )
      return;
    runBulk(
      targets,
      async (p) => {
        const d = p.pendingDraft!;
        if (d.status === "draft") await setEmailStatus(d.id, "approved");
        await sendEmail(d.id);
      },
      "Send"
    );
  }

  function bulkSchedule(targets: PersonRow[]) {
    const iso = new Date(scheduleAt).toISOString();
    if (new Date(scheduleAt).getTime() <= Date.now()) {
      setNote("Pick a future date and time to schedule.");
      return;
    }
    runBulk(
      targets,
      async (p) => {
        await scheduleEmail(p.pendingDraft!.id, iso);
      },
      "Schedule"
    );
  }

  const people = useMemo<PersonRow[]>(() => {
    const byContact = new Map<number, EmailDraft[]>();
    for (const d of data?.items ?? []) {
      if (d.contact_id == null) continue;
      const arr = byContact.get(d.contact_id) ?? [];
      arr.push(d);
      byContact.set(d.contact_id, arr);
    }
    return Array.from(byContact.entries()).map(([id, drafts]) =>
      buildPerson(id, drafts)
    );
  }, [data]);

  const counts = useMemo(
    () => ({
      awaiting: people.filter((p) => p.bucket === "awaiting").length,
      replied: people.filter((p) => p.bucket === "replied").length,
      draft: people.filter((p) => p.bucket === "draft").length,
      all: people.length,
    }),
    [people]
  );

  const metrics = useMemo(() => {
    const drafts = data?.items ?? [];
    const sentDrafts = drafts.filter(
      (d) => d.status === "sent" || d.status === "replied" || d.sent_at
    );
    const sent = sentDrafts.length;
    const opened = sentDrafts.filter((d) => (d.open_count ?? 0) > 0).length;
    const replied = sentDrafts.filter(
      (d) => d.status === "replied" || d.replied_at
    ).length;
    const pct = (n: number) => (sent ? Math.round((n / sent) * 100) : 0);
    return {
      sent,
      opened,
      replied,
      openRate: pct(opened),
      replyRate: pct(replied),
    };
  }, [data]);

  const rows = useMemo(() => {
    let list = people;
    if (view !== "all") list = list.filter((p) => p.bucket === view);
    if (view === "awaiting" && silence > 0)
      list = list.filter((p) => (p.daysSilent ?? 0) >= silence);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.company ?? "").toLowerCase().includes(q)
      );
    }
    return [...list].sort((a, b) => {
      if (view === "awaiting")
        return (b.daysSilent ?? 0) - (a.daysSilent ?? 0);
      return +new Date(b.lastActivityAt) - +new Date(a.lastActivityAt);
    });
  }, [people, view, silence, search]);

  const selectableRows = useMemo(
    () => rows.filter((p) => p.pendingDraft),
    [rows]
  );
  const allSelectableSelected =
    selectableRows.length > 0 &&
    selectableRows.every((p) => selected.has(p.contactId));
  const selectedPeople = people.filter((p) => selected.has(p.contactId));

  const toggleSelectAll = () =>
    setSelected((prev) => {
      if (
        selectableRows.length > 0 &&
        selectableRows.every((p) => prev.has(p.contactId))
      )
        return new Set();
      return new Set(selectableRows.map((p) => p.contactId));
    });

  if (isLoading) return <Loading />;

  return (
    <div>
      <WorkflowSteps active={5} />

      <PageHeader
        title="Conversations"
        subtitle="Everyone you've emailed — reply status, silence, and follow-ups. Open a person for the full thread."
        actions={
          <Button
            variant="secondary"
            onClick={() => checkReplies.mutate()}
            disabled={checkReplies.isPending}
          >
            {checkReplies.isPending ? "Checking…" : "Check for replies"}
          </Button>
        }
      />

      {note && (
        <div className="mb-4 rounded-lg bg-blue-50 px-4 py-2 text-sm text-blue-800">
          {note}
        </div>
      )}

      {/* Metrics strip */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {[
          { label: "Sent", value: metrics.sent },
          { label: "Opened", value: metrics.opened },
          { label: "Replied", value: metrics.replied },
          { label: "Open rate", value: `${metrics.openRate}%` },
          { label: "Reply rate", value: `${metrics.replyRate}%` },
        ].map((m) => (
          <Card key={m.label} className="p-3">
            <div className="text-xs text-slate-400">{m.label}</div>
            <div className="text-xl font-semibold text-slate-900">{m.value}</div>
          </Card>
        ))}
      </div>

      {/* View tabs */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            onClick={() => setView(v.key)}
            className={`rounded-full px-3 py-1.5 text-sm font-medium transition ${
              view === v.key
                ? "bg-slate-900 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {v.label}
            <span
              className={`ml-1.5 ${
                view === v.key ? "text-slate-300" : "text-slate-400"
              }`}
            >
              {counts[v.key]}
            </span>
          </button>
        ))}
      </div>

      {/* Flexible "who's gone quiet" filter — only meaningful for awaiting */}
      {view === "awaiting" && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
          <span className="text-sm font-medium text-slate-600">
            No reply for:
          </span>
          {SILENCE_PRESETS.map((p) => (
            <button
              key={p.days}
              onClick={() => setSilence(p.days)}
              className={`rounded-md px-2.5 py-1 text-sm font-medium transition ${
                silence === p.days
                  ? "bg-amber-500 text-white"
                  : "bg-white text-slate-600 ring-1 ring-inset ring-slate-200 hover:bg-slate-100"
              }`}
            >
              {p.label}
            </button>
          ))}
          <div className="flex items-center gap-1 pl-2">
            <span className="text-xs text-slate-400">custom</span>
            <input
              type="number"
              min={0}
              value={silence}
              onChange={(e) => setSilence(Math.max(0, Number(e.target.value)))}
              className="w-16 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <span className="text-xs text-slate-400">days</span>
          </div>
          <div className="ml-auto">
            <Button
              onClick={() => draftFollowups.mutate(Math.max(1, silence))}
              disabled={draftFollowups.isPending}
              title={`Auto-draft a follow-up for everyone silent for ${Math.max(1, silence)}+ days`}
            >
              {draftFollowups.isPending
                ? "Drafting follow-ups…"
                : `Draft follow-ups (${Math.max(1, silence)}d+)`}
            </Button>
          </div>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input
          type="search"
          placeholder="Search by name or company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        {selectableRows.length > 0 && (
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={allSelectableSelected}
              onChange={toggleSelectAll}
            />
            Select all {selectableRows.length} with a draft
          </label>
        )}
      </div>

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 mb-4 rounded-lg border border-slate-300 bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="font-medium">{selected.size} selected</span>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                onClick={() => bulkSendNow(selectedPeople)}
                disabled={bulkBusy}
              >
                {bulkBusy ? "Working…" : "Send now"}
              </Button>
              <Button
                variant="secondary"
                onClick={() => setShowSchedule((v) => !v)}
                disabled={bulkBusy}
              >
                Schedule send
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
          {showSchedule && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-700 pt-3">
              <span className="text-slate-300">Send via Outlook at</span>
              <input
                type="datetime-local"
                value={scheduleAt}
                onChange={(e) => setScheduleAt(e.target.value)}
                className="rounded-md border border-slate-600 bg-slate-800 px-2 py-1 text-sm text-white"
              />
              <Button
                onClick={() => bulkSchedule(selectedPeople)}
                disabled={bulkBusy}
              >
                {bulkBusy ? "Scheduling…" : `Schedule ${selected.size}`}
              </Button>
              <span className="text-xs text-slate-400">
                Outlook delivers at the chosen time, even if this app is closed.
              </span>
            </div>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        metrics.sent === 0 ? (
          <Card className="p-10 text-center">
            <p className="text-sm font-medium text-slate-800">No conversations yet</p>
            <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
              This page lights up after you send email. Review and ship your first
              touches from <strong>Drafts</strong>, then come back here to check
              replies and draft follow-ups.
            </p>
            <Link
              to="/emails"
              className="mt-4 inline-block text-sm font-medium text-blue-700 underline hover:text-blue-900"
            >
              Go to Drafts →
            </Link>
          </Card>
        ) : (
          <Card className="p-10 text-center text-sm text-slate-500">
            No one here yet
            {view === "awaiting" && silence > 0
              ? ` who's been silent for ${silence}+ days.`
              : "."}
          </Card>
        )
      ) : (
        <Card className="divide-y divide-slate-100 p-0">
          {rows.map((p) => {
            const isOpen = expanded.has(p.contactId);
            const preview = p.previewDraft;
            return (
              <div key={p.contactId}>
                <div className="flex items-center gap-3 px-5 py-3.5 transition hover:bg-slate-50">
                  <input
                    type="checkbox"
                    className="shrink-0"
                    checked={selected.has(p.contactId)}
                    disabled={!p.pendingDraft}
                    onChange={() => toggleSelect(p.contactId)}
                    title={
                      p.pendingDraft
                        ? "Select for bulk send / schedule"
                        : "No unsent draft to send"
                    }
                  />
                  <button
                    type="button"
                    onClick={() => navigate(`/prospects/${p.contactId}`)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium text-slate-900">
                        {p.name}
                      </span>
                      {p.sentCount > 1 && (
                        <span className="text-xs text-slate-400">
                          · {p.sentCount} sent
                        </span>
                      )}
                      {p.opened && p.bucket === "awaiting" && (
                        <span className="text-xs font-medium text-emerald-600">
                          · opened
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-slate-500">
                      {[p.title, p.company].filter(Boolean).join(" · ") || "—"}
                    </div>
                    {p.replied && p.replyPreview && (
                      <div className="mt-1 truncate text-xs text-emerald-700">
                        ↩ {p.replyPreview}
                      </div>
                    )}
                  </button>
                  {preview && (
                    <button
                      type="button"
                      onClick={() => toggleExpand(p.contactId)}
                      className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50"
                    >
                      {isOpen ? "Hide" : "Preview"}
                    </button>
                  )}
                  <Badge tone={p.status.tone}>{p.status.label}</Badge>
                  <div className="w-20 shrink-0 text-right text-xs text-slate-400">
                    {relativeTime(
                      p.bucket === "replied" ? p.lastReplyAt : p.lastSentAt
                    ) || relativeTime(p.lastActivityAt)}
                  </div>
                </div>

                {isOpen && preview && (
                  <div className="border-t border-slate-100 bg-slate-50 px-5 py-4">
                    <div className="rounded-lg border border-slate-200 bg-white p-4">
                      <div className="mb-1 text-xs text-slate-400">
                        To: {preview.contact_email || "email not revealed"}
                      </div>
                      <div className="mb-2 font-medium text-slate-900">
                        {preview.subject}
                      </div>
                      <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                        {preview.body}
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {p.pendingDraft ? (
                        <>
                          <Button
                            onClick={() => bulkSendNow([p])}
                            disabled={bulkBusy || !p.canSend}
                            title={
                              p.canSend ? undefined : "Reveal the prospect's email first"
                            }
                          >
                            Send now
                          </Button>
                          <Button
                            variant="secondary"
                            onClick={() => {
                              setSelected(new Set([p.contactId]));
                              setShowSchedule(true);
                            }}
                            disabled={bulkBusy || !p.canSend}
                          >
                            Schedule…
                          </Button>
                        </>
                      ) : p.scheduledDraft ? (
                        <>
                          <span className="text-sm text-purple-700">
                            Scheduled for{" "}
                            {p.scheduledDraft.scheduled_at
                              ? new Date(
                                  p.scheduledDraft.scheduled_at
                                ).toLocaleString()
                              : "soon"}
                            {p.scheduledDraft.outlook_scheduled
                              ? " · via Outlook"
                              : " · in-app"}
                          </span>
                          <Button
                            variant="secondary"
                            onClick={async () => {
                              try {
                                await unscheduleEmail(p.scheduledDraft!.id);
                                await qc.invalidateQueries({ queryKey: ["emails"] });
                                setNote("Schedule cancelled.");
                              } catch {
                                setNote(
                                  "Could not cancel the scheduled send — it may already be sending."
                                );
                              }
                            }}
                          >
                            Cancel schedule
                          </Button>
                        </>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => navigate(`/prospects/${p.contactId}`)}
                        className="text-sm font-medium text-blue-700 underline hover:text-blue-900"
                      >
                        Open full conversation
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </Card>
      )}
    </div>
  );
}
