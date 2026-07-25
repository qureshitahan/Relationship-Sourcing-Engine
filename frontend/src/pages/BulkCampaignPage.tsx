import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acceptBulkLookups,
  cancelBulkJob,
  deleteBulkCampaign,
  deleteEmail,
  getBulkCampaign,
  listBulkLookups,
  listBulkRecipients,
  listEmails,
  regenerateEmail,
  rejectBulkLookups,
  removeBulkRecipient,
  sendBulkChat,
  sendEmail,
  setBulkLookupEmail,
  setEmailStatus,
  startBulkDrafting,
  startBulkLookup,
  startBulkSending,
  updateBulkCampaign,
  updateEmail,
} from "../api/client";
import type {
  BulkCampaignDetail,
  BulkLookup,
  BulkRecipient,
  EmailDraft,
} from "../types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
} from "../components/ui";

const BUSY_STATUSES = ["drafting", "sending", "looking_up"];
type Tone = "green" | "red" | "amber" | "blue" | "slate" | "purple";
const inputCls =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none";

export default function BulkCampaignPage() {
  const { id } = useParams();
  const campaignId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"drafts" | "people" | "lookup">("drafts");
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
  const { data: lookups } = useQuery({
    queryKey: ["bulk-lookups", campaignId],
    queryFn: () => listBulkLookups(campaignId),
    enabled: Number.isFinite(campaignId),
    refetchInterval: busy ? 3000 : false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["bulk-campaign", campaignId] });
    qc.invalidateQueries({ queryKey: ["bulk-drafts", campaignId] });
    qc.invalidateQueries({ queryKey: ["bulk-recipients", campaignId] });
    qc.invalidateQueries({ queryKey: ["bulk-lookups", campaignId] });
  };

  // The campaign poll stops the moment the job finishes, which can leave the
  // draft and recipient lists one tick behind. Pull them once more on the way out.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      qc.invalidateQueries({ queryKey: ["bulk-drafts", campaignId] });
      qc.invalidateQueries({ queryKey: ["bulk-recipients", campaignId] });
      qc.invalidateQueries({ queryKey: ["bulk-lookups", campaignId] });
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
  const lookup = useMutation({
    mutationFn: (retryFailed: boolean) => startBulkLookup(campaignId, retryFailed),
    onSuccess: () => {
      setNote("");
      setTab("lookup");
      refresh();
    },
    onError: (e) => setNote(apiError(e, "Could not start the search.")),
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
                  {campaign.lookup_pending > 0 && (
                    <Button
                      variant="secondary"
                      onClick={() => lookup.mutate(false)}
                      disabled={lookup.isPending}
                    >
                      Find {campaign.lookup_pending} missing email
                      {campaign.lookup_pending === 1 ? "" : "s"}
                    </Button>
                  )}
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
          <div className="mb-4 grid grid-cols-5 gap-3">
            <Tile label="People" value={campaign.recipients} />
            <Tile
              label="No address"
              value={campaign.needs_email}
              tone={campaign.lookup_found > 0 ? "amber" : undefined}
            />
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
            {(lookups?.length ?? 0) > 0 && (
              <TabButton active={tab === "lookup"} onClick={() => setTab("lookup")}>
                Needs email ({campaign.needs_email})
              </TabButton>
            )}
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
          ) : tab === "people" ? (
            <RecipientsTable
              campaignId={campaignId}
              recipients={recipients ?? []}
              onChanged={refresh}
            />
          ) : (
            <LookupQueue
              campaignId={campaignId}
              lookups={lookups ?? []}
              busy={busy}
              onChanged={refresh}
              onRetry={() => lookup.mutate(true)}
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
          Name or paste your people, then say what the email should do. Missing addresses
          can be searched for.
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {campaign.messages.length === 0 && (
          <div className="rounded-lg bg-slate-100 px-3 py-2 text-sm leading-relaxed text-slate-700">
            Paste the people you want to email, straight out of your spreadsheet or a
            roster — or just tell me who, like "email Dana Cole at Mercy Health". Names,
            titles, companies and any notes are used to personalize. Anyone without an
            address goes into a list I can search the web for, and you approve each address
            I find. Then tell me what you want to say and I'll draft one email per person
            for you to review.
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
          placeholder="Paste your list, name who to email, or write the purpose of these emails…"
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
          <EmptyState message="No one on this list yet. Paste or name your people in the chat." />
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

// --- Missing address review -------------------------------------------------

/** Apollo's own verdict on an address, and how much we trust it. */
const EMAIL_STATUS_TONE: Record<string, Tone> = {
  verified: "green",
  likely: "blue",
  provided: "slate",
  guessed: "amber",
};
/** Addresses Apollo isn't sure about are never pre-selected. */
const isUnsure = (lookup: BulkLookup) =>
  (lookup.email_status ?? "").toLowerCase() === "guessed";

function LookupQueue({
  campaignId,
  lookups,
  busy,
  onChanged,
  onRetry,
}: {
  campaignId: number;
  lookups: BulkLookup[];
  busy: boolean;
  onChanged: () => void;
  onRetry: () => void;
}) {
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");

  const found = lookups.filter((l) => l.status === "found");
  const unresolved = lookups.filter((l) =>
    ["pending", "not_found", "ambiguous", "error"].includes(l.status)
  );
  const settled = lookups.filter((l) => ["accepted", "rejected"].includes(l.status));

  const accept = useMutation({
    mutationFn: (ids: number[]) => acceptBulkLookups(campaignId, ids),
    onSuccess: () => {
      setPicked(new Set());
      setError("");
      onChanged();
    },
    onError: (e) => setError(apiError(e, "Could not add those addresses.")),
  });
  const reject = useMutation({
    mutationFn: (ids: number[]) => rejectBulkLookups(campaignId, ids),
    onSuccess: () => {
      setPicked(new Set());
      setError("");
      onChanged();
    },
    onError: (e) => setError(apiError(e, "Could not dismiss those people.")),
  });

  const toggle = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectConfident = () =>
    setPicked(new Set(found.filter((l) => !isUnsure(l)).map((l) => l.id)));

  if (!lookups.length) {
    return (
      <Card>
        <EmptyState message="Everyone you pasted came with an email address." />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs leading-relaxed text-slate-500">
            These people came in without an address. Each match below was found by
            searching the web and then a contact database — check it against the sources
            before you accept it. Nothing is emailed until you do.
          </p>
          {!busy && unresolved.some((l) => l.status !== "pending") && (
            <Button variant="secondary" onClick={onRetry}>
              Search again for the misses
            </Button>
          )}
        </div>
      </Card>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      {found.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-300 bg-slate-900 px-4 py-3 text-sm text-white">
          <span className="font-medium">
            {picked.size
              ? `${picked.size} selected`
              : `${found.length} address${found.length === 1 ? "" : "es"} to review`}
          </span>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={selectConfident}>
              Select confident matches
            </Button>
            {picked.size > 0 && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => reject.mutate([...picked])}
                  disabled={reject.isPending}
                >
                  Dismiss
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => accept.mutate([...picked])}
                  disabled={accept.isPending}
                >
                  Add {picked.size} to the campaign
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {[...found, ...unresolved, ...settled].map((lookup) => (
        <LookupCard
          key={lookup.id}
          campaignId={campaignId}
          lookup={lookup}
          picked={picked.has(lookup.id)}
          onToggle={toggle}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

function LookupCard({
  campaignId,
  lookup,
  picked,
  onToggle,
  onChanged,
}: {
  campaignId: number;
  lookup: BulkLookup;
  picked: boolean;
  onToggle: (id: number) => void;
  onChanged: () => void;
}) {
  const [manual, setManual] = useState("");
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: () => setBulkLookupEmail(campaignId, lookup.id, manual.trim()),
    onSuccess: () => {
      setManual("");
      setError("");
      onChanged();
    },
    onError: (e) => setError(apiError(e, "Could not save that address.")),
  });

  const hasEmail = lookup.status === "found";
  const settled = lookup.status === "accepted" || lookup.status === "rejected";
  const confidence =
    lookup.confidence != null ? Math.round(lookup.confidence * 100) : null;

  return (
    <Card className={`overflow-hidden ${settled ? "opacity-60" : ""}`}>
      <div className="flex items-start gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
        {hasEmail && (
          <input
            type="checkbox"
            checked={picked}
            onChange={() => onToggle(lookup.id)}
            className="mt-1"
            aria-label={`Select ${lookup.name}`}
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/prospects/${lookup.contact_id}`}
              className="text-sm font-semibold text-slate-900 hover:underline"
            >
              {lookup.name}
            </Link>
            <LookupBadge status={lookup.status} />
            {lookup.email_status && (
              <Badge tone={EMAIL_STATUS_TONE[lookup.email_status] ?? "slate"}>
                {lookup.email_status}
              </Badge>
            )}
            {confidence != null && lookup.status !== "pending" && (
              <span className="text-xs text-slate-400">{confidence}% sure</span>
            )}
          </div>
          {lookup.source_text && (
            <div className="mt-1 text-xs italic text-slate-400">
              From: {lookup.source_text}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-2 px-4 py-3">
        {hasEmail && (
          <div className="font-medium text-slate-900">{lookup.email}</div>
        )}
        {(lookup.resolved_org || lookup.resolved_title || lookup.location) && (
          <div className="text-xs text-slate-600">
            Identified as {lookup.resolved_name ?? lookup.name}
            {lookup.resolved_title && `, ${lookup.resolved_title}`}
            {lookup.resolved_org && ` at ${lookup.resolved_org}`}
            {lookup.location && ` · ${lookup.location}`}
          </div>
        )}
        {lookup.reason && <div className="text-xs text-slate-500">{lookup.reason}</div>}
        {lookup.error && <div className="text-xs text-rose-600">{lookup.error}</div>}

        {(lookup.linkedin_url || (lookup.evidence?.length ?? 0) > 0) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {lookup.linkedin_url && (
              <a
                href={lookup.linkedin_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 hover:underline"
              >
                LinkedIn profile
              </a>
            )}
            {(lookup.evidence ?? []).map((source) => (
              <a
                key={source.url}
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="max-w-[16rem] truncate text-slate-500 hover:text-slate-800 hover:underline"
                title={source.title}
              >
                {source.title}
              </a>
            ))}
          </div>
        )}

        {!hasEmail && !settled && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <input
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              placeholder="Type their address if you know it"
              className={`${inputCls} max-w-xs`}
            />
            <Button
              variant="secondary"
              onClick={() => save.mutate()}
              disabled={!manual.includes("@") || save.isPending}
            >
              Add
            </Button>
          </div>
        )}
        {error && <p className="text-xs text-rose-600">{error}</p>}
      </div>
    </Card>
  );
}

function LookupBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; tone: Tone }> = {
    pending: { label: "not searched yet", tone: "slate" },
    found: { label: "address found", tone: "blue" },
    not_found: { label: "no address found", tone: "slate" },
    ambiguous: { label: "could not identify", tone: "amber" },
    accepted: { label: "added", tone: "green" },
    rejected: { label: "dismissed", tone: "slate" },
    error: { label: "search failed", tone: "red" },
  };
  const item = map[status] ?? { label: status, tone: "slate" as Tone };
  return <Badge tone={item.tone}>{item.label}</Badge>;
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
          {campaign.status === "looking_up"
            ? "Searching for missing addresses"
            : campaign.status === "drafting"
              ? "Writing drafts"
              : "Sending emails"}
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
  tone?: "slate" | "green" | "amber";
}) {
  const valueCls =
    tone === "green"
      ? "text-emerald-600"
      : tone === "amber"
        ? "text-amber-600"
        : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div className={`text-lg font-semibold tabular-nums ${valueCls}`}>
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
