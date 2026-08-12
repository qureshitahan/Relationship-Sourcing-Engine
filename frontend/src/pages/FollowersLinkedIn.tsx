import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePersistedState } from "../hooks/usePersistedState";
import {
  approveAllFollowers,
  createLinkedInConnectLink,
  deleteLinkedIn,
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
      ? `Refreshing followers — ${progress.imported} new so far`
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

/** The created / approved / sent counters, read from the database. */
function CountRow({ stats }: { stats: FollowerStats }) {
  const cells: { label: string; value: number; hint: string }[] = [
    { label: "Followers", value: stats.followers_total, hint: "In your synced roster" },
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

function FollowerCard({ row, busy }: { row: FollowerRow; busy: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
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
          {row.body && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="text-slate-600 hover:underline"
            >
              {open ? "Hide DM" : "View DM"}
            </button>
          )}
          {deletable && (
            <button
              type="button"
              onClick={() =>
                window.confirm(
                  `Delete the draft for ${row.name ?? "this follower"}? ` +
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
      {open && row.body && (
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
  const [statusFilter, setStatusFilter] = usePersistedState<string>(
    "followers:statusFilter",
    ""
  );
  // The message IS the campaign: its text decides which DMs belong together and
  // who has already been contacted, so it must survive a navigation or refresh.
  const [message, setMessage] = usePersistedState<string>("followers:message", "");
  const [principalId, setPrincipalId] = usePersistedState<string>(
    "followers:principalId",
    ""
  );
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
  // Default to the principal whose name matches the connected LinkedIn account —
  // the DM goes out from that account, so writing it in anyone else's voice is
  // almost never intended. Falls back to the first principal only if no name
  // matches (the list is ordered by id, where a leftover test row often sits).
  const defaultPrincipalId = useMemo(() => {
    const list = principals?.items ?? [];
    if (list.length === 0) return undefined;
    const accountName = (status?.active_account_name ?? "").trim().toLowerCase();
    const match = accountName
      ? list.find((p) => (p.name ?? "").trim().toLowerCase() === accountName)
      : undefined;
    return (match ?? list[0]).id;
  }, [principals, status?.active_account_name]);
  const resolvedPrincipalId = principalId ? Number(principalId) : defaultPrincipalId;

  const banner = useMemo(() => {
    if (statusLoading || !status) return null;
    if (status.provider === "stub")
      return "LinkedIn is in test mode (stub provider) — followers are fake and nothing is actually sent.";
    if (!status.supports_followers)
      return "This provider cannot read your followers. Connect a LinkedIn account via Unipile.";
    if (!activeId) return "Pick the connected LinkedIn account whose followers you want to reach.";
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

  const draftAll = useMutation({
    mutationFn: (text: string) =>
      draftAllFollowers(text, resolvedPrincipalId!, Number(draftLimit) || undefined),
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
        subtitle="Direct-message the people who already follow your LinkedIn account. Followers only — nobody else is ever contacted from here, and a follower who has been messaged is never messaged again for the same message."
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
              title="Pull the latest follower list from LinkedIn"
            >
              {sync.isPending || progress?.job === "sync" ? "Refreshing…" : "Refresh followers"}
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
              {status.followers_total} follower
              {status.followers_total === 1 ? "" : "s"} synced
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
              Send as
            </label>
            <select
              // Show the resolved default until the user picks explicitly, so the
              // dropdown never displays someone other than who will actually send.
              value={principalId || (resolvedPrincipalId ? String(resolvedPrincipalId) : "")}
              onChange={(e) => setPrincipalId(e.target.value)}
              className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
              disabled={busy}
            >
              {(principals?.items ?? []).map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
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
                setNote("Add a principal first — the DM is written in their voice.");
                return;
              }
              draftAll.mutate(text);
            }}
            disabled={busy || !activeId}
            title="Prepare your message for every follower who does not have it yet"
          >
            {progress?.job === "draft" && running ? "Drafting…" : "Draft all"}
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
            Approve all
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
            {progress?.job === "send" && running ? "Sending…" : "Approve & send all"}
          </Button>
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

        {stats && (
          <div className="mt-4 space-y-3">
            <CountRow stats={stats} />
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
              <span>
                {stats.remaining_today} of {stats.cap} sends left today for this account
                {stats.sent_today > 0 ? ` (${stats.sent_today} used)` : ""}
              </span>
              <span>{stats.eligible} follower(s) still to draft</span>
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
              onClick={() => setStatusFilter(tab.key)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                statusFilter === tab.key
                  ? "bg-slate-900 text-white"
                  : "bg-white text-slate-700 ring-1 ring-inset ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {tab.label}
              {count !== null ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>

      {!activeId ? (
        <EmptyState message="Select a connected LinkedIn account to see its followers." />
      ) : isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState
          message={
            (status?.followers_total ?? 0) === 0
              ? 'No followers synced yet — click "Refresh followers".'
              : activeMessage
                ? `No followers in this tab for this message.`
                : "Write your message and click Draft all to begin."
          }
        />
      ) : (
        <div className="space-y-3">
          {items.map((row) => (
            <FollowerCard key={row.id} row={row} busy={busy} />
          ))}
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
