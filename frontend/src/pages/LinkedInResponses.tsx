import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  checkLinkedInUpdates,
  listLinkedInAccounts,
  listLinkedInMessages,
  replyLinkedIn,
} from "../api/client";
import type { LinkedInMessage } from "../types";
import { Badge, Button, Card, Loading, PageHeader } from "../components/ui";
import { LinkedInScanProgress } from "../components/LinkedInScanProgress";
import { usePersistedState } from "../hooks/usePersistedState";

function relativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (Number.isNaN(d)) return "—";
  if (d <= 0) return "today";
  if (d === 1) return "yesterday";
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function LinkedInResponses() {
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  const [search, setSearch] = usePersistedState("linkedin-responses:search", "");
  // While a background reply-scan runs, auto-refresh the list until this time.
  const [refreshUntil, setRefreshUntil] = useState(0);
  const [scanning, setScanning] = useState(false);
  // Which account's responses to show. Defaults to the active account below.
  const [accountFilter, setAccountFilter] = usePersistedState<string | null>(
    "linkedin-responses:accountFilter",
    null
  );
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({});
  const [openOriginal, setOpenOriginal] = useState<Set<number>>(new Set());

  const { data: accountsData } = useQuery({
    queryKey: ["linkedin-accounts"],
    queryFn: listLinkedInAccounts,
  });
  const { data, isLoading } = useQuery({
    queryKey: ["linkedin", "responses"],
    queryFn: () => listLinkedInMessages({ limit: 1000 }),
    refetchInterval: () => (Date.now() < refreshUntil ? 5000 : false),
  });

  const accounts = accountsData?.accounts ?? [];
  const isUnipile = accountsData?.provider === "unipile";
  // Effective account being viewed: explicit filter, else the active account.
  const effectiveAccount =
    accountFilter ?? accountsData?.active_account_id ?? null;
  // Messages with no stamped account predate per-account sending -> attribute
  // them to the env default account (historically the only sender).
  const accountOf = (m: LinkedInMessage): string | null =>
    m.from_account || accountsData?.default_account_id || null;
  const accountName = (id: string | null): string => {
    if (!id) return "—";
    return accounts.find((a) => a.id === id)?.name ?? id;
  };

  const poll = useMutation({
    mutationFn: checkLinkedInUpdates,
    onSuccess: (res) => {
      if (res.started) {
        setNote(null);
        setScanning(true);
        // Also refresh the list for a few minutes as replies land.
        setRefreshUntil(Date.now() + 180_000);
      } else {
        setNote(res.message);
      }
      qc.invalidateQueries({ queryKey: ["linkedin", "responses"] });
    },
    onError: () => setNote("Reply check failed — see backend logs."),
  });

  const reply = useMutation({
    mutationFn: ({ id, body }: { id: number; body: string }) => replyLinkedIn(id, body),
    onSuccess: (_res, vars) => {
      setNote("Reply sent in the LinkedIn thread.");
      setReplyDrafts((prev) => ({ ...prev, [vars.id]: "" }));
      qc.invalidateQueries({ queryKey: ["linkedin", "responses"] });
    },
    onError: () => setNote("Could not send the reply — the person may need to be connected, or try again."),
  });

  const all = data?.items ?? [];
  // Everyone who has replied, for the account being viewed.
  const responses = useMemo(() => {
    let list = all.filter(
      (m) => (m.status === "replied" || m.replied_at) && accountOf(m) === effectiveAccount
    );
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (m) =>
          (m.contact_name ?? "").toLowerCase().includes(q) ||
          (m.company_name ?? "").toLowerCase().includes(q)
      );
    }
    return [...list].sort(
      (a, b) =>
        +new Date(b.replied_at ?? b.created_at) - +new Date(a.replied_at ?? a.created_at)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, effectiveAccount, search, accountsData]);

  // Metrics for the selected account (sent DMs vs replies).
  const metrics = useMemo(() => {
    const mine = all.filter((m) => accountOf(m) === effectiveAccount);
    const sent = mine.filter((m) => m.sent_at || m.status === "sent" || m.status === "replied").length;
    const replied = mine.filter((m) => m.status === "replied" || m.replied_at).length;
    return { sent, replied, rate: sent ? Math.round((replied / sent) * 100) : 0 };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, effectiveAccount, accountsData]);

  if (isLoading) return <Loading />;

  return (
    <div>
      <PageHeader
        title="LinkedIn Responses"
        subtitle="People who replied to your LinkedIn DMs — read their response and reply personally, from the right account."
        actions={
          <Button
            variant="secondary"
            onClick={() => poll.mutate()}
            disabled={poll.isPending}
            title="Poll LinkedIn now for newly accepted invites and new replies"
          >
            {poll.isPending ? "Checking…" : "Check for replies now"}
          </Button>
        }
      />

      {note && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {note}
        </div>
      )}

      <LinkedInScanProgress
        active={scanning}
        onDone={(r) => {
          setScanning(false);
          setNote(
            `Reply check complete — ${r.replied} new repl${r.replied === 1 ? "y" : "ies"}, ` +
              `${r.accepted} invite${r.accepted === 1 ? "" : "s"} accepted.`
          );
          qc.invalidateQueries({ queryKey: ["linkedin", "responses"] });
        }}
      />

      {/* Account filter + metrics */}
      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-semibold text-slate-700">Show responses for:</span>
          {isUnipile && accounts.length > 0 ? (
            <select
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
              value={effectiveAccount ?? ""}
              onChange={(e) => setAccountFilter(e.target.value || null)}
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name ?? a.id}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-slate-900">{accountName(effectiveAccount)}</span>
          )}
          <div className="ml-auto flex items-center gap-4 text-xs text-slate-500">
            <span>
              <span className="font-semibold text-slate-900">{metrics.sent}</span> sent
            </span>
            <span>
              <span className="font-semibold text-emerald-700">{metrics.replied}</span> replied
            </span>
            <span>
              <span className="font-semibold text-slate-900">{metrics.rate}%</span> reply rate
            </span>
          </div>
        </div>
      </Card>

      <div className="mb-4">
        <input
          type="search"
          placeholder="Search by name or company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
      </div>

      {responses.length === 0 ? (
        <Card className="p-10 text-center">
          <p className="text-sm font-medium text-slate-800">No responses yet</p>
          <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
            When someone replies to a DM sent from{" "}
            <strong>{accountName(effectiveAccount)}</strong>, it shows up here. Send DMs from
            the <strong>LinkedIn</strong> page, then click{" "}
            <em>Check for replies now</em> to pull the latest.
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          {responses.map((m) => {
            const replyText = m.reply_body || m.reply_snippet || "(no text captured)";
            const draft = replyDrafts[m.id] ?? "";
            const canReply = Boolean(m.provider_chat_id);
            const showOriginal = openOriginal.has(m.id);
            return (
              <Card key={m.id} className="p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">
                        {m.contact_name || `Contact #${m.contact_id ?? "?"}`}
                      </span>
                      <Badge tone="green">Replied</Badge>
                      <span className="text-xs text-slate-400">
                        · via {accountName(accountOf(m))}
                      </span>
                    </div>
                    <div className="truncate text-xs text-slate-500">
                      {[m.contact_title, m.company_name].filter(Boolean).join(" · ") || "—"}
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-slate-400">{relativeTime(m.replied_at)}</span>
                    {m.linkedin_url && (
                      <a
                        href={m.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-blue-700 hover:underline"
                      >
                        View profile →
                      </a>
                    )}
                  </div>
                </div>

                {/* Their reply — the extracted response */}
                <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50/60 p-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
                    Their reply
                  </div>
                  <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                    {replyText}
                  </div>
                </div>

                {/* Your original DM, collapsible for context */}
                <button
                  type="button"
                  onClick={() =>
                    setOpenOriginal((prev) => {
                      const next = new Set(prev);
                      if (next.has(m.id)) next.delete(m.id);
                      else next.add(m.id);
                      return next;
                    })
                  }
                  className="mt-2 text-xs font-medium text-slate-500 hover:underline"
                >
                  {showOriginal ? "Hide your message" : "Show your message"}
                </button>
                {showOriginal && (
                  <div className="mt-2 whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                    {m.body}
                  </div>
                )}

                {/* Reply personally, in-thread, from the sending account */}
                <div className="mt-3 border-t border-slate-100 pt-3">
                  {canReply ? (
                    <>
                      <textarea
                        rows={3}
                        placeholder={`Write a personal reply (sends as ${accountName(accountOf(m))})…`}
                        value={draft}
                        onChange={(e) =>
                          setReplyDrafts((prev) => ({ ...prev, [m.id]: e.target.value }))
                        }
                        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      />
                      <div className="mt-2 flex items-center gap-2">
                        <Button
                          onClick={() => reply.mutate({ id: m.id, body: draft.trim() })}
                          disabled={!draft.trim() || reply.isPending}
                        >
                          {reply.isPending ? "Sending…" : "Send reply"}
                        </Button>
                        <span className="text-xs text-slate-400">
                          Sends in the LinkedIn thread from {accountName(accountOf(m))}.
                        </span>
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-slate-500">
                      No LinkedIn chat to reply in yet.{" "}
                      {m.linkedin_url && (
                        <a
                          href={m.linkedin_url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-blue-700 hover:underline"
                        >
                          Open in LinkedIn →
                        </a>
                      )}
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
