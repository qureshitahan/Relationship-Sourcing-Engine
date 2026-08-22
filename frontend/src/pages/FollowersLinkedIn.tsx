import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePersistedState } from "../hooks/usePersistedState";
import {
  approveAllFollowers,
  createLinkedInConnectLink,
  deleteLinkedIn,
  deleteLinkedInMessages,
  draftAllFollowers,
  getFollowersProgress,
  getFollowersStatus,
  listFollowers,
  listPrincipals,
  selectLinkedInAccount,
  sendAllFollowers,
  stopFollowersJob,
  syncFollowers,
} from "../api/client";
import type { FollowerRow, FollowersProgress, FollowerStats } from "../types";
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
  { key: "pending", label: "Not drafted" },
  { key: "draft", label: "Draft" },
  { key: "approved", label: "Approved" },
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
] as const;

/** Mirrors the backend's `first_name_of` exactly, so the preview is not a guess.
 *  First token only (LinkedIn names carry suffixes like "Jennie Reis, CPCC, ACC"),
 *  punctuation trimmed, and "there" when there is no usable name. */
function firstNameOf(name?: string | null): string {
  const raw = (name ?? "").trim();
  if (!raw) return "there";
  const first = raw.split(/\s+/)[0].replace(/^[,.;:]+|[,.;:]+$/g, "").trim();
  return first || "there";
}

const REACH_LABEL: Record<string, string> = {
  connected: "1st-degree DM",
  open_profile: "Open profile",
  inmail: "InMail",
};

/** Live bar for the running sync / draft / send job. */
function JobBar({ progress }: { progress: FollowersProgress }) {
  const { job, status, total, done } = progress;
  if (status !== "running" && status !== "failed") return null;
  if (status === "failed")
    return (
      <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-900">
        {progress.message ?? "The job failed — check backend logs."}
      </div>
    );

  // A sync has no known total until the first page lands, so it shows an
  // indeterminate label rather than a misleading 0%.
  const pct = total > 0 ? Math.floor((done / total) * 100) : null;
  const title =
    job === "sync"
      ? `Refreshing network — ${progress.imported} new so far`
      : job === "draft"
        ? `Writing DMs — ${progress.drafted} of ${total}`
        : `Sending DMs — ${progress.sent} of ${total}`;
  return (
    <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
      <div className="flex items-center justify-between text-sm font-medium text-blue-900">
        <span>{title}</span>
        <span>{pct === null ? "working…" : `${pct}%`}</span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-blue-100">
        <div
          className={`h-full rounded-full bg-blue-500 transition-all ${
            pct === null ? "w-1/4 animate-pulse" : ""
          }`}
          style={pct === null ? undefined : { width: `${pct}%` }}
        />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-blue-800">
        {job === "draft" && <span>{progress.drafted} created</span>}
        {job === "send" && <span>{progress.sent} sent</span>}
        {progress.skipped > 0 && <span>{progress.skipped} not reachable</span>}
        {progress.failed > 0 && <span>{progress.failed} failed</span>}
        {progress.stop_requested && <span className="font-semibold">stopping…</span>}
      </div>
    </div>
  );
}

/** How far through the whole follower roster you are — the "47 of 999" answer.
 *  Counted across every campaign, so it does not reset when the message changes. */
function RosterProgress({
  total,
  contacted,
  cap,
}: {
  total: number;
  contacted: number;
  cap: number;
}) {
  if (!total) return null;
  const remaining = Math.max(0, total - contacted);
  const pct = Math.floor((contacted / total) * 100);
  // At the daily cap, how many more days of sending the rest represents.
  const days = cap > 0 ? Math.ceil(remaining / cap) : null;
  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-sm font-semibold text-slate-900">
          {contacted} of {total} in your network contacted
        </div>
        <div className="text-xs text-slate-500">
          {remaining} still to reach
          {days !== null && remaining > 0
            ? ` · about ${days} more day${days === 1 ? "" : "s"} at ${cap}/day`
            : ""}
        </div>
      </div>
      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1 text-[11px] text-slate-500">
        {pct}% — counts everyone ever messaged from this account, under any message.
      </div>
    </div>
  );
}

/** The created / approved / sent counters, read from the database. */
function CountRow({ stats }: { stats: FollowerStats }) {
  const cells: { label: string; value: number; hint: string }[] = [
    { label: "In network", value: stats.followers_total, hint: "People in your synced roster" },
    { label: "Created", value: stats.all, hint: "DMs drafted for this message" },
    { label: "Approved", value: stats.approved, hint: "Approved, not yet sent" },
    { label: "Sent", value: stats.sent, hint: "Delivered DMs" },
    { label: "Replied", value: stats.replied, hint: "They wrote back" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {cells.map((c) => (
        <div
          key={c.label}
          title={c.hint}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2"
        >
          <div className="text-lg font-semibold text-slate-900">{c.value}</div>
          <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
            {c.label}
          </div>
        </div>
      ))}
    </div>
  );
}

function FollowerCard({
  row,
  busy,
  selected,
  onSelect,
}: {
  row: FollowerRow;
  busy: boolean;
  /** Selection for the bulk delete. Absent for rows that cannot be removed. */
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
}) {
  const qc = useQueryClient();
  // Reuses the existing LinkedIn delete endpoint, which refuses to delete a
  // sent/invited message — so a delivered DM can never be erased from the record.
  const remove = useMutation({
    mutationFn: () => deleteLinkedIn(row.message_id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["followers"] }),
  });
  // Only an unsent draft/approved DM can be removed, and never while a job is
  // running — the send worker already holds this row in memory.
  const deletable =
    !!row.message_id &&
    (row.message_status === "draft" || row.message_status === "approved");
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {onSelect && (
              <input
                type="checkbox"
                checked={!!selected}
                onChange={(e) => onSelect(e.target.checked)}
                aria-label={`Select ${row.name ?? "follower"}`}
                className="h-4 w-4 cursor-pointer rounded border-slate-300"
              />
            )}
            <span className="font-medium text-slate-900">{row.name ?? row.provider_id}</span>
            {row.message_status ? (
              <StatusBadge status={row.message_status} />
            ) : (
              <Badge tone="slate">not drafted</Badge>
            )}
            {row.reach && <Badge tone="blue">{REACH_LABEL[row.reach] ?? row.reach}</Badge>}
            {row.send_status === "skipped" && <Badge tone="amber">not reachable</Badge>}
            {row.send_status === "claimed" && <Badge tone="amber">needs review</Badge>}
          </div>
          {row.headline && (
            <div className="mt-0.5 truncate text-sm text-slate-600">{row.headline}</div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs">
          {row.profile_url && (
            <a
              href={row.profile_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-700 hover:underline"
            >
              Profile
            </a>
          )}
          {deletable && (
            <button
              type="button"
              onClick={() =>
                window.confirm(
                  `Delete the draft for ${row.name ?? "this person"}? ` +
                    "They become eligible again, so the next Draft all will write it fresh."
                ) && remove.mutate()
              }
              disabled={busy || remove.isPending}
              className="text-rose-700 hover:underline disabled:cursor-not-allowed disabled:text-slate-400"
              title={
                busy
                  ? "Wait for the running job to finish"
                  : "Remove this draft so it is not sent"
              }
            >
              {remove.isPending ? "Deleting…" : "Delete"}
            </button>
          )}
        </div>
      </div>
      {/* Always visible. The DM is the thing being reviewed before it is sent to
          a real person, so hiding it behind a toggle put the one detail that
          matters an extra click away. */}
      {row.body && (
        <pre className="mt-3 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-800">
          {row.body}
        </pre>
      )}
      {row.reply_snippet && (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
          <span className="font-medium">Reply:</span> {row.reply_snippet}
        </div>
      )}
      {row.error && (
        <div className="mt-2 text-xs text-rose-700" title={row.error}>
          {row.error}
        </div>
      )}
    </div>
  );
}

export default function FollowersLinkedIn() {
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  // Clicking the tab that is already open folds its list away, so a long page
  // of drafts can be collapsed without losing the tab. Deliberately NOT
  // persisted: returning to the page should always show the messages, never an
  // empty screen whose cause is a click from days ago.
  const [listHidden, setListHidden] = useState(false);
  const [statusFilter, setStatusFilter] = usePersistedState<string>(
    "followers:statusFilter",
    ""
  );
  // The message IS the campaign: its text decides which DMs belong together and
  // who has already been contacted, so it must survive a navigation or refresh.
  const [message, setMessage] = usePersistedState<string>("followers:message", "");
  // No principal picker any more. It used to matter when this module generated
  // copy in a principal's voice; the message is now sent verbatim, so the choice
  // could not change a single character of what goes out — it only decided which
  // name the record was filed under, which made "Send as" actively misleading
  // (the DM has always gone from the LinkedIn account selected above). The
  // principal is now derived from that account instead. See attributedPrincipalId.
  // Only the committed message drives queries. Typing must not silently re-key
  // the campaign on every keystroke.
  const [activeMessage, setActiveMessage] = usePersistedState<string>(
    "followers:activeMessage",
    ""
  );
  // How many to draft in one go. Defaults to the daily cap because that is all
  // that can actually be sent today; drafting the whole roster would just queue
  // hundreds of DMs that sit unsent and pin them to this campaign.
  const [draftLimit, setDraftLimit] = usePersistedState<string>(
    "followers:draftLimit",
    "50"
  );

  // Declared before the others because both of them key their polling off it.
  const { data: progress } = useQuery({
    queryKey: ["followers", "progress"],
    queryFn: getFollowersProgress,
    // Poll only while something is running, and fetch on mount so a reload
    // mid-job still shows the bar and offers Stop.
    refetchInterval: (q) =>
      (q.state.data as FollowersProgress | undefined)?.status === "running" ? 2000 : false,
  });
  const running = progress?.status === "running";

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["followers", "status", activeMessage],
    queryFn: () => getFollowersStatus(activeMessage || undefined),
    // The counters and tab counts live here. A mutation only reports that the
    // background job STARTED, so without polling the tiles kept showing
    // "0 created" after drafting had finished and the list below already
    // showed the drafts.
    refetchInterval: running ? 3000 : false,
  });
  const { data: principals } = useQuery({
    queryKey: ["principals", "active"],
    queryFn: () => listPrincipals({ active: true }),
  });

  // A job's last few results land after its final poll, so refresh once more on
  // the running -> finished edge. Without this the tiles can sit one tick behind
  // forever, since nothing polls them once the job is done.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (running) {
      wasRunning.current = true;
      return;
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      qc.invalidateQueries({ queryKey: ["followers"] });
    }
  }, [running, qc]);

  const { data: followers, isLoading } = useQuery({
    queryKey: ["followers", "list", activeMessage, statusFilter],
    queryFn: () =>
      listFollowers({
        limit: 500,
        ...(activeMessage ? { message: activeMessage } : {}),
        ...(statusFilter ? { status: statusFilter } : {}),
      }),
    enabled: !!status?.active_account_id,
    // While a job runs, keep the list and the counters moving in step with it.
    refetchInterval: running ? 4000 : false,
  });

  // Whenever a job finishes, pull the authoritative counts in once.
  const invalidate = () => qc.invalidateQueries({ queryKey: ["followers"] });

  const accounts = status?.accounts ?? [];
  const activeId = status?.active_account_id ?? null;
  const stats = status?.stats ?? null;
  // Which principal each draft is filed under. Derived from the connected
  // LinkedIn account by name, because that account is what actually sends — so
  // the record matches reality without anyone having to keep two dropdowns in
  // sync. Falls back to the first principal when no name matches; the list is
  // ordered by id, where a leftover test row often sits, so the resolved name is
  // shown next to the buttons rather than left invisible.
  const attributedPrincipal = useMemo(() => {
    const list = principals?.items ?? [];
    if (list.length === 0) return undefined;
    const accountName = (status?.active_account_name ?? "").trim().toLowerCase();
    const match = accountName
      ? list.find((p) => (p.name ?? "").trim().toLowerCase() === accountName)
      : undefined;
    return match ?? list[0];
  }, [principals, status?.active_account_name]);
  const resolvedPrincipalId = attributedPrincipal?.id;

  const banner = useMemo(() => {
    if (statusLoading || !status) return null;
    if (status.provider === "stub")
      return "LinkedIn is in test mode (stub provider) — the people listed are fake and nothing is actually sent.";
    if (!status.supports_followers)
      return "This provider cannot read your LinkedIn network. Connect an account via Unipile.";
    if (!activeId) return "Pick the connected LinkedIn account whose network you want to reach.";
    return null;
  }, [status, statusLoading, activeId]);

  const selectAccount = useMutation({
    mutationFn: (accountId: string) => selectLinkedInAccount(accountId),
    onSuccess: (res) => {
      // Shared with the LinkedIn page on purpose: one active account app-wide.
      qc.invalidateQueries({ queryKey: ["followers"] });
      qc.invalidateQueries({ queryKey: ["linkedin-accounts"] });
      const name = accounts.find((a) => a.id === res.active_account_id)?.name;
      setNote(`Now acting as ${name ?? res.active_account_id}.`);
    },
    onError: () => setNote("Could not switch LinkedIn account — please try again."),
  });

  const connectAccount = useMutation({
    mutationFn: () => createLinkedInConnectLink("New LinkedIn account"),
    onSuccess: (res) => {
      if (res.url) window.open(res.url, "_blank", "noopener");
      setNote(
        "Opened Unipile to connect a LinkedIn account. Finish the login, then refresh this page."
      );
    },
    onError: () => setNote("Could not create a connect link — check the Unipile configuration."),
  });

  const sync = useMutation({
    mutationFn: syncFollowers,
    onSuccess: (res) => {
      setNote(res.message);
      invalidate();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      setNote(String(detail ?? "Could not refresh followers."));
    },
  });

  const requireMessage = (): string | null => {
    const text = message.trim();
    if (!text) {
      setNote("Write your message first — it is the exact text that gets sent.");
      return null;
    }
    // Committing the message here is what starts (or resumes) a campaign.
    setActiveMessage(text);
    return text;
  };

  // Two controls, two meanings. The headline number is a TARGET: pressing it
  // again tops up to that many rather than doubling the batch, which is what
  // used to turn "50" into 100 on a second click. Append is the explicit
  // "give me this many more" — the old add-N behaviour, kept but named.
  const [appendCount, setAppendCount] = usePersistedState<string>(
    "followers:appendCount",
    ""
  );

  const draftAll = useMutation({
    mutationFn: (text: string) =>
      draftAllFollowers(
        text,
        resolvedPrincipalId!,
        undefined,
        Number(draftLimit) > 0 ? Number(draftLimit) : undefined
      ),
    onSuccess: (res) => {
      setNote(res.message);
      invalidate();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      setNote(String(detail ?? "Could not start drafting."));
    },
  });

  // Ids only, and intersected with what is on screen before anything is sent —
  // a tick that survived a tab change must never delete a row now out of view.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const appendDrafts = useMutation({
    mutationFn: (text: string) =>
      draftAllFollowers(
        text,
        resolvedPrincipalId!,
        Number(appendCount) > 0 ? Number(appendCount) : undefined
      ),
    onSuccess: (res) => {
      setNote(res.message);
      invalidate();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      setNote(String(detail ?? "Could not start drafting."));
    },
  });

  const approveAll = useMutation({
    mutationFn: (text: string) => approveAllFollowers(text),
    onSuccess: (res) => {
      setNote(
        res.approved
          ? `Approved ${res.approved} DM${res.approved === 1 ? "" : "s"}.`
          : "No drafts to approve."
      );
      invalidate();
    },
    onError: () => setNote("Could not approve the drafts."),
  });

  const sendAll = useMutation({
    mutationFn: (text: string) => sendAllFollowers(text),
    onSuccess: (res) => {
      setNote(res.message);
      invalidate();
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      setNote(String(detail ?? "Could not start sending."));
    },
  });

  const bulkDelete = useMutation({
    mutationFn: (ids: number[]) => deleteLinkedInMessages(ids),
    onSuccess: (res) => {
      setNote(
        `Deleted ${res.deleted} draft${res.deleted === 1 ? "" : "s"}.` +
          (res.skipped
            ? ` ${res.skipped} kept — already sent, so they stay as the record of that contact.`
            : "")
      );
      setSelectedIds(new Set());
      invalidate();
    },
    onError: () => setNote("Could not delete the selected drafts."),
  });

  const stop = useMutation({
    mutationFn: stopFollowersJob,
    onSuccess: (res) => {
      setNote(res.message);
      invalidate();
    },
    onError: () => setNote("Could not stop the job — check backend logs."),
  });

  const busy =
    running || sync.isPending || draftAll.isPending || sendAll.isPending || approveAll.isPending;
  const items = followers?.items ?? [];
  // Preview against a REAL follower from the current list, so the greeting shown
  // is the greeting that will actually be sent.
  const previewName = items[0]?.name ?? null;
  const previewFirstName = firstNameOf(previewName);

  return (
    <div>
      <PageHeader
        title="Followers LinkedIn"
        subtitle="Direct-message your LinkedIn network — your 1st-degree connections, who also follow you. Nobody outside your network is ever contacted from here, and once someone has been messaged they are never messaged again for the same message."
      />

      {banner && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          {banner}
        </div>
      )}
      {note && (
        <div
          className={`mb-4 rounded-lg border px-4 py-2 text-sm ${
            /could not|failed|enter an/i.test(note)
              ? "border-rose-200 bg-rose-50 text-rose-900"
              : "border-emerald-200 bg-emerald-50 text-emerald-800"
          }`}
        >
          {note}
        </div>
      )}

      {progress && <JobBar progress={progress} />}

      {/* Roster progress sits above everything: it is the one number that
          answers "how far through my followers am I", and unlike the campaign
          tiles below it does not reset when the message text changes. */}
      {activeId && (status?.followers_total ?? 0) > 0 && (
        <RosterProgress
          total={status?.followers_total ?? 0}
          contacted={status?.contacted_all_time ?? 0}
          cap={stats?.cap ?? 50}
        />
      )}

      {/* --- Account --- */}
      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              LinkedIn account
            </label>
            <select
              value={activeId ?? ""}
              onChange={(e) => e.target.value && selectAccount.mutate(e.target.value)}
              className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
              disabled={busy || accounts.length === 0}
            >
              <option value="">
                {accounts.length ? "Select an account…" : "No connected accounts"}
              </option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name ?? a.id}
                  {a.status && a.status !== "OK" ? ` · ${a.status}` : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="pb-2">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Connection
            </div>
            <div className="mt-1 flex items-center gap-2">
              {activeId ? (
                <Badge
                  tone={
                    (status?.active_account_status ?? "OK") === "OK" ? "green" : "amber"
                  }
                >
                  {(status?.active_account_status ?? "OK") === "OK"
                    ? "Connected"
                    : (status?.active_account_status as string)}
                </Badge>
              ) : (
                <Badge tone="red">Not connected</Badge>
              )}
              <span className="text-xs text-slate-500">
                {status?.provider === "stub" ? "test mode" : status?.provider}
              </span>
            </div>
          </div>
          <div className="pb-1 flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => sync.mutate()}
              disabled={busy || !activeId || !status?.supports_followers}
              title="Pull your latest connections from LinkedIn"
            >
              {sync.isPending || progress?.job === "sync" ? "Refreshing…" : "Refresh network"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => connectAccount.mutate()}
              disabled={connectAccount.isPending}
            >
              Connect another
            </Button>
          </div>
          {typeof status?.followers_total === "number" && (
            <div className="pb-2 text-sm text-slate-600">
              {status.followers_total} in network
              {status.followers_total === 1 ? "" : ""} synced
            </div>
          )}
        </div>
      </Card>

      {/* --- Message + actions --- */}
      <Card className="mb-4">
        <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Message
        </label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={6}
          placeholder={
            "Write or paste the exact message to send.\n\n" +
            "Don't include a greeting — “Hi <first name>,” is added for you."
          }
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          disabled={busy}
        />
        <p className="mt-1 text-xs text-slate-500">
          Sent exactly as written — nothing rewrites or personalises it. Only{" "}
          <span className="font-medium">Hi &lt;first name&gt;,</span> is added at the top. The
          text also identifies the campaign: change it and you start a new one, so the same
          followers become eligible again.
        </p>

        {message.trim() && (
          // Show the real thing, not a description of it, so there is no surprise
          // about what lands in someone's inbox.
          <div className="mt-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Preview{previewName ? ` — as ${previewName} will see it` : ""}
            </div>
            <pre className="mt-1 whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-800">
              {`Hi ${previewFirstName},\n\n${message.trim()}`}
            </pre>
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Draft how many
            </label>
            <input
              type="number"
              min={1}
              value={draftLimit}
              onChange={(e) => setDraftLimit(e.target.value)}
              className="mt-1 w-24 rounded-md border border-slate-300 px-3 py-2 text-sm"
              disabled={busy}
              title="How many followers to prepare this message for. Blank = all remaining. Only the daily cap can actually be sent today."
            />
          </div>
          <Button
            onClick={() => {
              const text = requireMessage();
              if (!text) return;
              if (!resolvedPrincipalId) {
                setNote(
                  "Add a principal on the Principals page first — drafts are filed against one."
                );
                return;
              }
              draftAll.mutate(text);
            }}
            disabled={busy || !activeId}
            title={
              Number(draftLimit) > 0
                ? `Bring this message up to ${Number(draftLimit)} drafts. Pressing it again does nothing until you raise the number or use Append.`
                : "Prepare your message for every follower who does not have it yet"
            }
          >
            {progress?.job === "draft" && running
              ? "Drafting…"
              : Number(draftLimit) > 0
                ? `Draft ${Number(draftLimit)}`
                : stats
                  ? `Draft all (${stats.eligible})`
                  : "Draft all"}
          </Button>

          {/* The explicit "more" control. Separate box so the target above keeps
              meaning a total — one number cannot mean both. */}
          <label className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-slate-500">Append</span>
            <input
              type="number"
              min={1}
              value={appendCount}
              placeholder="0"
              onChange={(e) => setAppendCount(e.target.value)}
              className="w-20 rounded-md border border-slate-300 px-2 py-2 text-sm"
              disabled={busy}
              title="Draft this many MORE, on top of what already exists."
            />
          </label>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (!text) return;
              if (!resolvedPrincipalId) {
                setNote(
                  "Add a principal on the Principals page first — drafts are filed against one."
                );
                return;
              }
              appendDrafts.mutate(text);
            }}
            disabled={busy || !activeId || !(Number(appendCount) > 0)}
            title="Draft this many more, on top of the ones already prepared"
          >
            {Number(appendCount) > 0 ? `Append ${Number(appendCount)}` : "Append"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (text) approveAll.mutate(text);
            }}
            disabled={busy || !activeId}
            title="Approve every draft for this message"
          >
            {stats ? `Approve all (${stats.draft})` : "Approve all"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (text) sendAll.mutate(text);
            }}
            disabled={busy || !activeId}
            title="Approve and send every open DM for this message, paced and capped"
          >
            {progress?.job === "send" && running
              ? "Sending…"
              : stats
                ? `Approve & send all (${stats.draft + stats.approved})`
                : "Approve & send all"}
          </Button>
          {stats && stats.draft + stats.approved > stats.remaining_today && (
            <span className="text-xs text-amber-700">
              only {stats.remaining_today} can go today
            </span>
          )}
          {running && (
            <Button
              variant="danger"
              onClick={() => stop.mutate()}
              disabled={stop.isPending || progress?.stop_requested}
            >
              {progress?.stop_requested ? "Stopping…" : "Stop"}
            </Button>
          )}
        </div>

        {/* Replaces the old "Send as" picker. Shown, not editable: it never
            changed the message or the sender, so a control invited the mistake
            of filing DMs under someone who did not send them. */}
        {attributedPrincipal && (
          <p className="mt-2 text-xs text-slate-500">
            Sent from <span className="font-medium text-slate-700">
              {status?.active_account_name ?? "the selected LinkedIn account"}
            </span>
            , recorded against{" "}
            <span className="font-medium text-slate-700">{attributedPrincipal.name}</span>.
          </p>
        )}

        {stats && (
          <div className="mt-4 space-y-3">
            <CountRow stats={stats} />
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
              <span>
                {stats.remaining_today} of {stats.cap} sends left today for this account
                {stats.sent_today > 0 ? ` (${stats.sent_today} used)` : ""}
              </span>
              <span>{stats.eligible} still to draft</span>
              {stats.contacted_ever > 0 && (
                <span title="Recorded in the checkpoint — these are skipped on every future run">
                  {stats.contacted_ever} already contacted with this message
                </span>
              )}
              {stats.not_reachable > 0 && (
                <span title="Not a connection, not an open profile, and no InMail available">
                  {stats.not_reachable} not reachable
                </span>
              )}
              {stats.needs_review > 0 && (
                <span className="font-medium text-amber-700" title="A send was interrupted and its outcome is unknown, so it is never retried automatically">
                  {stats.needs_review} needs review
                </span>
              )}
            </div>
          </div>
        )}
      </Card>

      {/* --- Tabs --- */}
      <div className="mb-3 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => {
          const count = stats
            ? tab.key === ""
              ? stats.all
              : tab.key === "pending"
                ? stats.eligible
                : (stats[tab.key as keyof FollowerStats] as number)
            : null;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                if (statusFilter === tab.key) {
                  setListHidden((v) => !v);
                } else {
                  setStatusFilter(tab.key);
                  setListHidden(false);
                }
              }}
              title={
                statusFilter === tab.key
                  ? listHidden
                    ? `Show ${tab.label.toLowerCase()} again`
                    : `Hide ${tab.label.toLowerCase()}`
                  : undefined
              }
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                statusFilter === tab.key
                  ? listHidden
                    ? "bg-slate-900 text-white opacity-60 ring-2 ring-slate-300"
                    : "bg-slate-900 text-white"
                  : "bg-white text-slate-700 ring-1 ring-inset ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {tab.label}
              {count !== null ? ` (${count})` : ""}
              {statusFilter === tab.key && listHidden ? " ▸" : ""}
            </button>
          );
        })}
      </div>

      {!activeId ? (
        <EmptyState message="Select a connected LinkedIn account to see its network." />
      ) : listHidden ? (
        <Card className="p-6 text-center text-sm text-slate-500">
          {items.length} row{items.length === 1 ? "" : "s"} hidden — click{" "}
          <b>{STATUS_TABS.find((t) => t.key === statusFilter)?.label ?? "All"}</b>{" "}
          again to show them.
        </Card>
      ) : isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState
          message={
            (status?.followers_total ?? 0) === 0
              ? 'Nothing synced yet — click "Refresh network".'
              : activeMessage
                ? `Nobody in this tab for this message.`
                : "Write your message and click Draft all to begin."
          }
        />
      ) : (
        <div className="space-y-3">
          {(() => {
            const deletable = items.filter(
              (r) =>
                !!r.message_id &&
                (r.message_status === "draft" || r.message_status === "approved")
            );
            const ids = new Set(deletable.map((r) => r.message_id as number));
            const chosen = [...selectedIds].filter((id) => ids.has(id));
            if (deletable.length === 0) return null;
            return (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5">
                <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={chosen.length === deletable.length}
                    onChange={(e) =>
                      setSelectedIds(
                        e.target.checked
                          ? new Set(deletable.map((r) => r.message_id as number))
                          : new Set()
                      )
                    }
                    className="h-4 w-4 cursor-pointer rounded border-slate-300"
                  />
                  Select all on this page ({deletable.length})
                </label>
                {chosen.length > 0 && (
                  <>
                    <span className="text-sm text-slate-500">{chosen.length} selected</span>
                    <Button
                      variant="danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete ${chosen.length} draft${chosen.length === 1 ? "" : "s"}?

` +
                              "They become eligible again, so the next Draft or Append will " +
                              "write them fresh. Nobody is removed from your network."
                          )
                        )
                          bulkDelete.mutate(chosen);
                      }}
                      disabled={bulkDelete.isPending || busy}
                    >
                      {bulkDelete.isPending
                        ? "Deleting…"
                        : `Delete selected (${chosen.length})`}
                    </Button>
                    <button
                      type="button"
                      onClick={() => setSelectedIds(new Set())}
                      className="text-xs font-medium text-blue-700 hover:underline"
                    >
                      Clear selection
                    </button>
                  </>
                )}
              </div>
            );
          })()}
          {items.map((row) => {
            const selectable =
              !!row.message_id &&
              (row.message_status === "draft" || row.message_status === "approved");
            return (
              <FollowerCard
                key={row.id}
                row={row}
                busy={busy}
                selected={selectable ? selectedIds.has(row.message_id as number) : undefined}
                onSelect={
                  selectable
                    ? (checked) =>
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          if (checked) next.add(row.message_id as number);
                          else next.delete(row.message_id as number);
                          return next;
                        })
                    : undefined
                }
              />
            );
          })}
          {followers && followers.total > items.length && (
            <div className="text-center text-xs text-slate-500">
              Showing {items.length} of {followers.total}. The bulk actions above cover all of
              them.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
