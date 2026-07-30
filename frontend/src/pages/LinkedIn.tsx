import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  checkLinkedInUpdates,
  createLinkedInConnectLink,
  deleteLinkedIn,
  generateRunLinkedIn,
  listDiscoveryRuns,
  listLinkedInAccounts,
  listLinkedInMessages,
  replyLinkedIn,
  selectLinkedInAccount,
  sendLinkedIn,
  setLinkedInStatus,
  updateLinkedIn,
} from "../api/client";
import type { LinkedInMessage } from "../types";
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
  { key: "invite_sent", label: "Invited" },
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
] as const;

function distanceLabel(d?: string | null): string | null {
  if (!d) return null;
  const map: Record<string, string> = {
    FIRST_DEGREE: "1st",
    DISTANCE_1: "1st",
    SECOND_DEGREE: "2nd",
    DISTANCE_2: "2nd",
    THIRD_DEGREE: "3rd",
    DISTANCE_3: "3rd",
  };
  return map[d.toUpperCase()] ?? d.replace(/_/g, " ").toLowerCase();
}

function MessageCard({ msg }: { msg: LinkedInMessage }) {
  const qc = useQueryClient();
  const [body, setBody] = useState(msg.body);
  const [note, setNote] = useState(msg.invitation_note ?? "");
  const [replyText, setReplyText] = useState("");
  const [showReply, setShowReply] = useState(false);

  useEffect(() => {
    setBody(msg.body);
    setNote(msg.invitation_note ?? "");
  }, [msg.id, msg.body, msg.invitation_note]);

  const editable = msg.status === "draft" || msg.status === "approved";
  const dirty = body !== msg.body || note !== (msg.invitation_note ?? "");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["linkedin"] });
  const save = useMutation({
    mutationFn: () => updateLinkedIn(msg.id, { body, invitation_note: note }),
    onSuccess: invalidate,
  });
  const status = useMutation({
    mutationFn: (s: string) => setLinkedInStatus(msg.id, s),
    onSuccess: invalidate,
  });
  const send = useMutation({
    mutationFn: () => sendLinkedIn(msg.id),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => deleteLinkedIn(msg.id),
    onSuccess: invalidate,
  });
  const reply = useMutation({
    mutationFn: () => replyLinkedIn(msg.id, replyText),
    onSuccess: () => {
      setReplyText("");
      setShowReply(false);
      invalidate();
    },
  });

  const dist = distanceLabel(msg.network_distance);

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-slate-50/80 px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-slate-900">
              {msg.contact_name ?? `Prospect #${msg.contact_id}`}
            </span>
            <StatusBadge status={msg.status} />
            {dist && <Badge tone={dist === "1st" ? "green" : "slate"}>{dist} degree</Badge>}
          </div>
          <div className="mt-0.5 text-sm text-slate-500">
            {msg.contact_title && <span>{msg.contact_title}</span>}
            {msg.contact_title && msg.company_name && <span> · </span>}
            {msg.company_name && <span>{msg.company_name}</span>}
          </div>
        </div>
        {msg.linkedin_url && (
          <a
            href={msg.linkedin_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium text-blue-700 hover:underline"
          >
            View profile →
          </a>
        )}
      </div>

      {msg.status === "invite_sent" && (
        <div className="border-b border-amber-100 bg-amber-50 px-5 py-3 text-sm text-amber-900">
          <span className="font-semibold">Connection invitation sent.</span> The
          message below will send automatically once they accept.
          {msg.invitation_sent_at && (
            <span className="ml-2 text-xs text-amber-700">
              {new Date(msg.invitation_sent_at).toLocaleString()}
            </span>
          )}
        </div>
      )}
      {msg.status === "replied" && (
        <div className="border-b border-emerald-100 bg-emerald-50 px-5 py-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
            Reply received
            {msg.replied_at && (
              <span className="ml-2 font-normal normal-case text-emerald-600">
                {new Date(msg.replied_at).toLocaleString()}
              </span>
            )}
          </div>
          {msg.reply_snippet && (
            <p className="mt-1 text-sm text-emerald-900">"{msg.reply_snippet}"</p>
          )}
        </div>
      )}
      {msg.error && (
        <div className="border-b border-rose-100 bg-rose-50 px-5 py-2 text-xs text-rose-700">
          {msg.error}
        </div>
      )}

      <div className="space-y-3 px-5 py-3">
        {(msg.status === "draft" || msg.status === "approved" || msg.status === "invite_sent") && (
          <div>
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Connection note (used if not yet connected · ≤300 chars)
            </label>
            <textarea
              value={note}
              disabled={!editable}
              maxLength={300}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-1 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:border-slate-400 focus:outline-none disabled:bg-slate-50"
            />
            <p className="mt-0.5 text-right text-[11px] text-slate-400">{note.length}/300</p>
          </div>
        )}
        <div>
          <label className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Message (sent once connected)
          </label>
          <textarea
            value={body}
            disabled={!editable}
            onChange={(e) => setBody(e.target.value)}
            rows={5}
            className="mt-1 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm leading-relaxed text-slate-800 focus:border-slate-400 focus:outline-none disabled:bg-slate-50"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/50 px-5 py-3">
        <span className="text-xs text-slate-400">Sending via LinkedIn (Unipile)</span>
        <div className="flex flex-wrap gap-2">
          {dirty && editable && (
            <Button variant="secondary" onClick={() => save.mutate()} disabled={save.isPending}>
              Save edits
            </Button>
          )}
          {msg.status === "draft" && (
            <>
              <Button
                variant="danger"
                onClick={() => window.confirm("Delete this message?") && remove.mutate()}
                disabled={remove.isPending}
              >
                Delete
              </Button>
              <Button onClick={() => status.mutate("approved")}>Approve to send</Button>
            </>
          )}
          {msg.status === "approved" && (
            <>
              <Button variant="ghost" onClick={() => status.mutate("draft")}>
                Back to draft
              </Button>
              <Button onClick={() => send.mutate()} disabled={send.isPending}>
                {send.isPending ? "Sending…" : "Send now"}
              </Button>
            </>
          )}
          {(msg.status === "sent" || msg.status === "replied") && msg.provider_chat_id && (
            <Button variant="secondary" onClick={() => setShowReply((s) => !s)}>
              Reply
            </Button>
          )}
        </div>
        {send.isError && (
          <p className="w-full text-right text-xs text-rose-600">
            {(send.error as { response?: { data?: { detail?: string } } })?.response?.data
              ?.detail ||
              (send.error as Error)?.message ||
              "Send failed"}
          </p>
        )}
        {showReply && (
          <div className="mt-2 w-full border-t border-slate-100 pt-3">
            <textarea
              value={replyText}
              onChange={(e) => setReplyText(e.target.value)}
              rows={3}
              placeholder="Write a follow-up in this LinkedIn chat…"
              className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm"
            />
            <div className="mt-2 flex justify-end">
              <Button
                onClick={() => reply.mutate()}
                disabled={reply.isPending || !replyText.trim()}
              >
                {reply.isPending ? "Sending…" : "Send reply"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export default function LinkedIn() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run") ? Number(searchParams.get("run")) : undefined;
  const [statusFilter, setStatusFilter] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const { data: accountsData } = useQuery({
    queryKey: ["linkedin-accounts"],
    queryFn: listLinkedInAccounts,
  });
  const { data: runs } = useQuery({
    queryKey: ["discovery-runs"],
    queryFn: () => listDiscoveryRuns({ limit: 25 }),
  });
  const { data, isLoading } = useQuery({
    queryKey: ["linkedin", statusFilter, runId],
    queryFn: () =>
      listLinkedInMessages({
        limit: 500,
        ...(statusFilter ? { status: statusFilter } : {}),
        ...(runId ? { discovery_run_id: runId } : {}),
      }),
  });

  const draftRun = useMutation({
    mutationFn: () => generateRunLinkedIn({ discovery_run_id: runId! }),
    onSuccess: (res) => {
      setNote(
        `Drafted ${res.generated} LinkedIn message(s)` +
          (res.skipped ? ` (${res.skipped} skipped)` : "") +
          (res.errors.length ? `. ${res.errors.length} error(s).` : ".")
      );
      qc.invalidateQueries({ queryKey: ["linkedin"] });
    },
    onError: () => setNote("Could not draft messages — check backend logs."),
  });
  const poll = useMutation({
    mutationFn: checkLinkedInUpdates,
    onSuccess: (res) => {
      setNote(
        res.supported
          ? `Checked LinkedIn: ${res.accepted} accepted→sent, ${res.replied} new repl${
              res.replied === 1 ? "y" : "ies"
            }.`
          : "LinkedIn tracking is not configured (stub provider)."
      );
      qc.invalidateQueries({ queryKey: ["linkedin"] });
    },
  });

  const items = data?.items ?? [];
  const setRunFilter = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("run", value);
    else next.delete("run");
    setSearchParams(next);
  };

  const accounts = accountsData?.accounts ?? [];
  const activeId = accountsData?.active_account_id ?? null;
  const activeAccount = accounts.find((a) => a.id === activeId);
  const activeName = activeAccount?.name ?? null;
  const activeStatus = activeAccount?.status ?? (activeId ? "OK" : null);
  const banner = useMemo(() => {
    if (!accountsData) return null;
    if (accountsData.provider === "stub")
      return "LinkedIn is in test mode (stub provider) — nothing is actually sent.";
    return null;
  }, [accountsData]);

  // Switch the active sending account (Dalbir / Farah / …). Applies to every
  // send from here on — manual, run bulk, and the scheduler — until changed.
  const selectAccount = useMutation({
    mutationFn: (accountId: string) => selectLinkedInAccount(accountId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["linkedin-accounts"] });
      const name = (accountsData?.accounts ?? []).find(
        (a) => a.id === res.active_account_id
      )?.name;
      setNote(`Now sending LinkedIn DMs as ${name ?? res.active_account_id}.`);
    },
    onError: () => setNote("Could not switch LinkedIn account — please try again."),
  });
  // One-time: open Unipile's hosted login to connect another account (e.g. Farah).
  const connectAccount = useMutation({
    mutationFn: () => createLinkedInConnectLink("New LinkedIn account"),
    onSuccess: (res) => {
      if (res.url) window.open(res.url, "_blank", "noopener");
      setNote(
        "Opened Unipile to connect a LinkedIn account. Complete the login, then " +
          "click Refresh accounts — it will appear in the dropdown."
      );
    },
    onError: () =>
      setNote("Could not create a connect link — check the Unipile configuration."),
  });

  return (
    <div>
      <PageHeader
        title="LinkedIn"
        subtitle="Reach approved prospects on LinkedIn. Not connected? We send an invitation, then auto-send your message once they accept."
      />

      {banner && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          {banner}
        </div>
      )}
      {note && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {note}
        </div>
      )}

      {accountsData?.provider === "unipile" && (
        <Card className="mb-4 p-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-semibold text-slate-700">Send LinkedIn DMs as:</span>
            {accounts.length > 0 ? (
              <select
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                value={activeId ?? ""}
                disabled={selectAccount.isPending}
                onChange={(e) => {
                  const id = e.target.value;
                  if (id && id !== activeId) selectAccount.mutate(id);
                }}
              >
                {!activeId && <option value="">— Select an account —</option>}
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name ?? a.id}
                    {a.status && a.status !== "OK" ? ` (${a.status})` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-slate-900">{activeName ?? "Dalbir Bains"}</span>
            )}
            <Badge tone={activeStatus === "OK" ? "green" : "amber"}>
              {activeStatus === "OK" ? "Connected" : activeStatus || "Checking…"}
            </Badge>
            {selectAccount.isPending && (
              <span className="text-xs text-slate-400">Switching…</span>
            )}
            <div className="ml-auto flex items-center gap-3">
              <button
                type="button"
                className="text-xs font-medium text-slate-500 hover:underline"
                onClick={() =>
                  qc.invalidateQueries({ queryKey: ["linkedin-accounts"] })
                }
              >
                Refresh accounts
              </button>
              <button
                type="button"
                className="text-xs font-medium text-blue-700 hover:underline disabled:opacity-40"
                disabled={connectAccount.isPending}
                onClick={() => connectAccount.mutate()}
                title="Open Unipile's secure login to connect another LinkedIn account (e.g. Farah). Her password is entered on Unipile, never stored here."
              >
                + Connect account
              </button>
            </div>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Every LinkedIn send (manual, bulk run, and auto follow-ups) uses the
            account selected here. Switching does not affect messages already sent —
            their replies keep tracking under the account that sent them.
          </p>
        </Card>
      )}

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium text-slate-600">Discovery run</label>
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={runId ?? ""}
            onChange={(e) => setRunFilter(e.target.value)}
          >
            <option value="">All runs</option>
            {runs?.items.map((r) => (
              <option key={r.id} value={r.id}>
                Run #{r.id} · {r.people_imported ?? 0} prospects
              </option>
            ))}
          </select>
          {runId && (
            <Button onClick={() => draftRun.mutate()} disabled={draftRun.isPending}>
              {draftRun.isPending ? "Drafting…" : "Draft all approved"}
            </Button>
          )}
          <Button variant="secondary" onClick={() => poll.mutate()} disabled={poll.isPending}>
            {poll.isPending ? "Checking…" : "Check acceptances & replies"}
          </Button>
        </div>
      </Card>

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.key || "all"}
            type="button"
            onClick={() => setStatusFilter(tab.key)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              statusFilter === tab.key
                ? "bg-slate-900 text-white"
                : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            message={
              runId
                ? `No ${statusFilter || ""} LinkedIn messages for run #${runId}. Approve prospects (with a LinkedIn URL) and click "Draft all approved".`
                : "No LinkedIn messages yet. Pick a discovery run above and draft messages for approved prospects."
            }
          />
        </Card>
      ) : (
        <div className="space-y-4">
          {items.map((m) => (
            <MessageCard key={m.id} msg={m} />
          ))}
        </div>
      )}
    </div>
  );
}
