import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelBulkJob,
  deleteBulkCampaign,
  deleteEmail,
  getBulkCampaign,
  listBulkRecipients,
  listEmails,
  regenerateEmail,
  removeBulkRecipient,
  sendBulkChat,
  sendEmail,
  setEmailStatus,
  startBulkDrafting,
  startBulkSending,
  updateBulkCampaign,
  updateEmail,
} from "../api/client";
import type { BulkCampaignDetail, BulkRecipient, EmailDraft } from "../types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
} from "../components/ui";

const BUSY_STATUSES = ["drafting", "sending"];
const inputCls =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none";

export default function BulkCampaignPage() {
  const { id } = useParams();
  const campaignId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"drafts" | "people">("drafts");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [note, setNote] = useState("");

  const { data: campaign, isLoading } = useQuery({
    queryKey: ["bulk-campaign", campaignId],
    queryFn: () => getBulkCampaign(campaignId),
    enabled: Number.isFinite(campaignId),
    refetchInterval: (q) => {
      const data = q.state.data as BulkCampaignDetail | undefined;
      return data && BUSY_STATUSES.includes(data.status) ? 2000 : false;
    },
  });
  const busy = !!campaign && BUSY_STATUSES.includes(campaign.status);

  const { data: drafts } = useQuery({
    queryKey: ["bulk-drafts", campaignId],
    queryFn: () => listEmails({ bulk_campaign_id: campaignId, limit: 1000 }),
    enabled: Number.isFinite(campaignId),
    refetchInterval: busy ? 2000 : false,
  });
  const { data: recipients } = useQuery({
    queryKey: ["bulk-recipients", campaignId],
    queryFn: () => listBulkRecipients(campaignId),
    enabled: Number.isFinite(campaignId),
    refetchInterval: busy ? 3000 : false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["bulk-campaign", campaignId] });
    qc.invalidateQueries({ queryKey: ["bulk-drafts", campaignId] });
    qc.invalidateQueries({ queryKey: ["bulk-recipients", campaignId] });
  };

  // The campaign poll stops the moment the job finishes, which can leave the
  // draft and recipient lists one tick behind. Pull them once more on the way out.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      qc.invalidateQueries({ queryKey: ["bulk-drafts", campaignId] });
      qc.invalidateQueries({ queryKey: ["bulk-recipients", campaignId] });
    }
    wasBusy.current = busy;
  }, [busy, campaignId, qc]);

  const draftAll = useMutation({
    mutationFn: () => startBulkDrafting(campaignId),
    onSuccess: () => {
      setNote("");
      refresh();
    },
    onError: (e) => setNote(apiError(e, "Could not start drafting.")),
  });
  const sendAll = useMutation({
    mutationFn: (draftIds?: number[]) => startBulkSending(campaignId, draftIds),
    onSuccess: () => {
      setSelected(new Set());
      setNote("");
      refresh();
    },
    onError: (e) => setNote(apiError(e, "Could not start sending.")),
  });
  const cancel = useMutation({
    mutationFn: () => cancelBulkJob(campaignId),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => deleteBulkCampaign(campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["bulk-campaigns"] });
      navigate("/bulk");
    },
  });

  const items = useMemo(
    () => [...(drafts?.items ?? [])].sort((a, b) => a.id - b.id),
    [drafts]
  );
  const reviewable = items.filter((d) => d.status === "draft" || d.status === "approved");

  if (!Number.isFinite(campaignId)) return <EmptyState message="Unknown campaign." />;
  if (isLoading || !campaign) return <Loading />;

  const toggle = (draftId: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(draftId)) next.delete(draftId);
      else next.add(draftId);
      return next;
    });
  };

  const confirmSend = (draftIds?: number[]) => {
    const count = draftIds ? draftIds.length : reviewable.length;
    if (!count) return;
    const ok = window.confirm(
      `Send ${count} email${count === 1 ? "" : "s"} from ${campaign.from_email}? ` +
        "They go out one at a time, a couple of seconds apart."
    );
    if (ok) sendAll.mutate(draftIds);
  };

  return (
    <div>
      <Link to="/bulk" className="text-sm text-slate-500 hover:text-slate-800">
        ← All bulk campaigns
      </Link>

      <div className="mt-3">
        <PageHeader
          title={campaign.name}
          subtitle={`Sending as ${campaign.from_name ?? campaign.mailbox_label ?? ""} <${
            campaign.from_email ?? ""
          }>`}
          actions={
            <>
              {busy ? (
                <Button variant="secondary" onClick={() => cancel.mutate()}>
                  Stop
                </Button>
              ) : (
                <>
                  {campaign.recipients_pending_draft > 0 && (
                    <Button onClick={() => draftAll.mutate()} disabled={draftAll.isPending}>
                      Draft {campaign.recipients_pending_draft} email
                      {campaign.recipients_pending_draft === 1 ? "" : "s"}
                    </Button>
                  )}
                  {reviewable.length > 0 && (
                    <Button onClick={() => confirmSend()} disabled={sendAll.isPending}>
                      Approve &amp; send all ({reviewable.length})
                    </Button>
                  )}
                </>
              )}
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm(`Delete "${campaign.name}" and its drafts?`))
                    remove.mutate();
                }}
              >
                Delete
              </Button>
            </>
          }
        />
      </div>

      {busy && <ProgressBar campaign={campaign} />}
      {note && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          {note}
        </div>
      )}
      {campaign.last_error && !busy && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {campaign.last_error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <ChatPanel campaign={campaign} onChanged={refresh} />
          <BriefCard campaign={campaign} onChanged={refresh} />
        </div>

        <div className="lg:col-span-3">
          <div className="mb-4 grid grid-cols-4 gap-3">
            <Tile label="People" value={campaign.recipients} />
            <Tile label="To review" value={reviewable.length} />
            <Tile label="Sent" value={campaign.sent} />
            <Tile label="Replied" value={campaign.replied} tone="green" />
          </div>

          <div className="mb-4 flex gap-2">
            <TabButton active={tab === "drafts"} onClick={() => setTab("drafts")}>
              Drafts ({items.length})
            </TabButton>
            <TabButton active={tab === "people"} onClick={() => setTab("people")}>
              People ({campaign.recipients})
            </TabButton>
          </div>

          {tab === "drafts" ? (
            <>
              {selected.size > 0 && (
                <div className="sticky top-2 z-10 mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-300 bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">
                  <span className="font-medium">{selected.size} selected</span>
                  <div className="flex gap-2">
                    <Button variant="secondary" onClick={() => setSelected(new Set())}>
                      Clear
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => confirmSend([...selected])}
                      disabled={sendAll.isPending}
                    >
                      Approve &amp; send selected
                    </Button>
                  </div>
                </div>
              )}
              {items.length === 0 ? (
                <Card>
                  <EmptyState
                    message={
                      campaign.recipients === 0
                        ? "Paste your list of people into the chat to get started."
                        : "No drafts yet. Tell the assistant what to say, then draft the emails."
                    }
                  />
                </Card>
              ) : (
                <div className="space-y-4">
                  {items.map((draft) => (
                    <DraftCard
                      key={draft.id}
                      draft={draft}
                      selected={selected.has(draft.id)}
                      onToggle={toggle}
                      onChanged={refresh}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <RecipientsTable
              campaignId={campaignId}
              recipients={recipients ?? []}
              onChanged={refresh}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// --- Chat -------------------------------------------------------------------

function ChatPanel({
  campaign,
  onChanged,
}: {
  campaign: BulkCampaignDetail;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const send = useMutation({
    mutationFn: (text: string) => sendBulkChat(campaign.id, text),
    onSuccess: (updated) => {
      qc.setQueryData(["bulk-campaign", campaign.id], updated);
      setMessage("");
      setError("");
      onChanged();
    },
    onError: (e) => setError(apiError(e, "The assistant could not read that message.")),
  });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [campaign.messages.length, send.isPending]);

  const submit = () => {
    const text = message.trim();
    if (text && !send.isPending) send.mutate(text);
  };

  return (
    <Card className="flex h-[32rem] flex-col overflow-hidden">
      <div className="border-b border-slate-100 bg-slate-50/80 px-4 py-3">
        <div className="text-sm font-semibold text-slate-900">Campaign assistant</div>
        <div className="text-xs text-slate-500">
          Paste your people, then say what the email should do.
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {campaign.messages.length === 0 && (
          <div className="rounded-lg bg-slate-100 px-3 py-2 text-sm leading-relaxed text-slate-700">
            Paste the people you want to email, straight out of your spreadsheet. Each row
            needs an email address; names, titles, companies and any notes are used to
            personalize. Then tell me what you want to say and I'll draft one email per
            person for you to review.
          </div>
        )}
        {campaign.messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-slate-900 px-3 py-2 text-sm text-white">
                {truncate(m.content)}
              </div>
            </div>
          ) : (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[90%] whitespace-pre-wrap break-words rounded-lg bg-slate-100 px-3 py-2 text-sm leading-relaxed text-slate-700">
                {m.content}
              </div>
            </div>
          )
        )}
        {send.isPending && (
          <div className="text-xs text-slate-400">Reading your message…</div>
        )}
        <div ref={endRef} />
      </div>

      {error && <p className="px-4 pb-2 text-xs text-rose-600">{error}</p>}

      <div className="border-t border-slate-100 p-3">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          rows={4}
          placeholder="Paste your list here, or write the purpose of these emails…"
          className={`${inputCls} resize-y`}
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[11px] text-slate-400">⌘/Ctrl + Enter to send</span>
          <Button onClick={submit} disabled={send.isPending || !message.trim()}>
            {send.isPending ? "Working…" : "Send"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function BriefCard({
  campaign,
  onChanged,
}: {
  campaign: BulkCampaignDetail;
  onChanged: () => void;
}) {
  const [purpose, setPurpose] = useState(campaign.purpose ?? "");
  const [signature, setSignature] = useState(campaign.signature ?? "");
  const [open, setOpen] = useState(false);

  // Re-seed the fields whenever the assistant rewrites the brief on the server.
  const [saved, setSaved] = useState({
    purpose: campaign.purpose,
    signature: campaign.signature,
  });
  if (saved.purpose !== campaign.purpose || saved.signature !== campaign.signature) {
    setSaved({ purpose: campaign.purpose, signature: campaign.signature });
    setPurpose(campaign.purpose ?? "");
    setSignature(campaign.signature ?? "");
  }

  const save = useMutation({
    mutationFn: () =>
      updateBulkCampaign(campaign.id, { purpose, signature: signature || null }),
    onSuccess: onChanged,
  });
  const dirty =
    purpose !== (campaign.purpose ?? "") || signature !== (campaign.signature ?? "");

  return (
    <Card className="mt-4 p-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-sm font-semibold text-slate-900">
          Brief {campaign.purpose ? "" : "· not set yet"}
        </span>
        <span className="text-xs text-slate-400">{open ? "Hide" : "Edit"}</span>
      </button>
      {!open ? (
        <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-500">
          {campaign.purpose ?? "Tell the assistant what these emails should say."}
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
              What every email should say
            </label>
            <textarea
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              rows={5}
              className={`${inputCls} resize-y leading-relaxed`}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-400">
              Sign-off
            </label>
            <textarea
              value={signature}
              onChange={(e) => setSignature(e.target.value)}
              rows={3}
              placeholder={`Thanks,\n${campaign.from_name ?? ""}`}
              className={`${inputCls} resize-y`}
            />
          </div>
          <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving…" : "Save brief"}
          </Button>
          <p className="text-xs text-slate-400">
            Changes apply to emails drafted from now on. Use Regenerate on a draft to
            rewrite it with the new brief.
          </p>
        </div>
      )}
    </Card>
  );
}

// --- Draft review -----------------------------------------------------------

function DraftCard({
  draft,
  selected,
  onToggle,
  onChanged,
}: {
  draft: EmailDraft;
  selected: boolean;
  onToggle: (id: number) => void;
  onChanged: () => void;
}) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [error, setError] = useState("");

  // Pick up server-side rewrites (regenerate, signature changes) without
  // clobbering edits the user is making right now.
  const [saved, setSaved] = useState({ subject: draft.subject, body: draft.body });
  if (saved.subject !== draft.subject || saved.body !== draft.body) {
    setSaved({ subject: draft.subject, body: draft.body });
    setSubject(draft.subject);
    setBody(draft.body);
  }

  const locked = draft.status === "sent" || draft.status === "replied";
  const dirty = subject !== draft.subject || body !== draft.body;

  const save = useMutation({
    mutationFn: () => updateEmail(draft.id, { subject, body }),
    onSuccess: onChanged,
  });
  const regenerate = useMutation({
    mutationFn: () => regenerateEmail(draft.id),
    onSuccess: onChanged,
    onError: (e) => setError(apiError(e, "Could not rewrite this email.")),
  });
  const discard = useMutation({
    mutationFn: () => deleteEmail(draft.id),
    onSuccess: onChanged,
  });
  const approveAndSend = useMutation({
    mutationFn: async () => {
      if (draft.status === "draft") await setEmailStatus(draft.id, "approved");
      return sendEmail(draft.id);
    },
    onSuccess: onChanged,
    onError: (e) => setError(apiError(e, "Could not send this email.")),
  });

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
        <div className="flex items-start gap-3">
          {!locked && (
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggle(draft.id)}
              className="mt-1"
              aria-label={`Select email to ${draft.contact_name ?? draft.contact_id}`}
            />
          )}
          <div>
            <div className="flex flex-wrap items-center gap-2">
              {draft.contact_id ? (
                <Link
                  to={`/prospects/${draft.contact_id}`}
                  className="text-sm font-semibold text-slate-900 hover:underline"
                >
                  {draft.contact_name ?? `Recipient #${draft.contact_id}`}
                </Link>
              ) : (
                <span className="text-sm font-semibold text-slate-900">Recipient</span>
              )}
              <StatusBadge status={draft.status} />
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              {draft.contact_email}
              {draft.contact_title && ` · ${draft.contact_title}`}
              {draft.company_name && ` · ${draft.company_name}`}
            </div>
          </div>
        </div>
        {draft.sent_at && (
          <span className="text-xs text-slate-400">
            Sent {new Date(draft.sent_at).toLocaleString()}
          </span>
        )}
      </div>

      {draft.status === "replied" && (
        <div className="border-b border-emerald-100 bg-emerald-50 px-4 py-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-emerald-700">
            Replied
            {draft.replied_at && (
              <span className="ml-2 font-normal normal-case text-emerald-600">
                {new Date(draft.replied_at).toLocaleString()}
              </span>
            )}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-sm text-emerald-900">
            {draft.reply_body || draft.reply_snippet}
          </p>
        </div>
      )}

      <div className="space-y-2 px-4 py-3">
        <input
          value={subject}
          disabled={locked}
          onChange={(e) => setSubject(e.target.value)}
          className={`${inputCls} font-medium`}
          placeholder="Subject"
        />
        <textarea
          value={body}
          disabled={locked}
          onChange={(e) => setBody(e.target.value)}
          rows={7}
          className={`${inputCls} resize-y leading-relaxed`}
        />
      </div>

      {error && <p className="px-4 pb-2 text-xs text-rose-600">{error}</p>}

      {!locked && (
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 bg-slate-50/50 px-4 py-3">
          {dirty && (
            <Button variant="secondary" onClick={() => save.mutate()}>
              Save edits
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={() => regenerate.mutate()}
            disabled={regenerate.isPending}
          >
            {regenerate.isPending ? "Rewriting…" : "Regenerate"}
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (window.confirm("Delete this draft?")) discard.mutate();
            }}
          >
            Delete
          </Button>
          <Button
            onClick={() => approveAndSend.mutate()}
            disabled={approveAndSend.isPending || !draft.contact_email}
          >
            {approveAndSend.isPending ? "Sending…" : "Approve & send"}
          </Button>
        </div>
      )}
    </Card>
  );
}

// --- People tracker ---------------------------------------------------------

function RecipientsTable({
  campaignId,
  recipients,
  onChanged,
}: {
  campaignId: number;
  recipients: BulkRecipient[];
  onChanged: () => void;
}) {
  const drop = useMutation({
    mutationFn: (contactId: number) => removeBulkRecipient(campaignId, contactId),
    onSuccess: onChanged,
  });

  if (!recipients.length) {
    return (
      <Card>
        <EmptyState message="No one on this list yet. Paste your people into the chat." />
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="divide-y divide-slate-100">
        {recipients.map((r) => (
          <div key={r.contact_id} className="flex items-start justify-between gap-4 px-4 py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/prospects/${r.contact_id}`}
                  className="text-sm font-medium text-slate-900 hover:underline"
                >
                  {r.name}
                </Link>
                {r.draft_status ? (
                  <StatusBadge status={r.draft_status} />
                ) : (
                  <Badge tone="slate">no email yet</Badge>
                )}
                {r.open_count > 0 && <Badge tone="blue">opened</Badge>}
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-500">
                {r.email}
                {r.title && ` · ${r.title}`}
                {r.company_name && ` · ${r.company_name}`}
              </div>
              {r.notes && (
                <div className="mt-1 text-xs italic text-slate-400">{r.notes}</div>
              )}
              {r.subject && (
                <div className="mt-1 truncate text-xs text-slate-600">“{r.subject}”</div>
              )}
              {r.reply_snippet && (
                <div className="mt-1 line-clamp-2 rounded bg-emerald-50 px-2 py-1 text-xs text-emerald-800">
                  {r.reply_snippet}
                </div>
              )}
            </div>
            <div className="flex flex-shrink-0 flex-col items-end gap-1">
              <span className="text-xs text-slate-400">
                {r.replied_at
                  ? `Replied ${new Date(r.replied_at).toLocaleDateString()}`
                  : r.sent_at
                    ? `Sent ${new Date(r.sent_at).toLocaleDateString()}`
                    : ""}
              </span>
              {!r.sent_at && (
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove ${r.name} from this campaign?`))
                      drop.mutate(r.contact_id);
                  }}
                  className="text-xs text-slate-400 hover:text-rose-600"
                >
                  Remove
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// --- Small pieces -----------------------------------------------------------

function ProgressBar({ campaign }: { campaign: BulkCampaignDetail }) {
  const total = campaign.progress_total || 0;
  const done = campaign.progress_done || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="flex items-center justify-between text-sm text-slate-700">
        <span className="font-medium">
          {campaign.status === "drafting" ? "Writing drafts" : "Sending emails"}
          {total ? ` · ${done} of ${total}` : "…"}
        </span>
        <span className="text-xs text-slate-400">{pct}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full bg-slate-900 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  tone = "slate",
}: {
  label: string;
  value: number;
  tone?: "slate" | "green";
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div
        className={`text-lg font-semibold tabular-nums ${
          tone === "green" ? "text-emerald-600" : "text-slate-900"
        }`}
      >
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

function truncate(text: string, limit = 900): string {
  if (text.length <= limit) return text;
  const omitted = text.length - limit;
  return `${text.slice(0, limit)}…\n[${omitted} more characters pasted]`;
}

function apiError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data
    ?.detail;
  return typeof detail === "string" ? detail : fallback;
}
