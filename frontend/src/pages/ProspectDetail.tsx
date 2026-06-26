import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  createFollowup,
  deleteEmail,
  generateCall,
  generateEmail,
  generateInsight,
  getOrganization,
  getProspect,
  getProspectInsights,
  listEmails,
  listPrincipals,
  listProspects,
  replyToEmail,
  revealProspect,
  sendEmail,
  setEmailStatus,
  setProspectApproval,
  updateEmail,
} from "../api/client";
import type { EmailDraft } from "../types";
import InsightCard from "../components/InsightCard";
import { Badge, Button, Card, Loading, PageHeader } from "../components/ui";

function Row({ label, value }: { label: string; value?: string | number | null }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-700">{value}</span>
    </div>
  );
}

function daysSince(iso?: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type Tone = "green" | "blue" | "amber" | "purple" | "slate";

/** Friendly status chip per message. */
function statusChip(status: string): { label: string; tone: Tone } {
  switch (status) {
    case "sent":
      return { label: "Sent", tone: "green" };
    case "replied":
      return { label: "Replied", tone: "green" };
    case "approved":
      return { label: "Approved", tone: "amber" };
    case "scheduled":
      return { label: "Scheduled", tone: "purple" };
    default:
      return { label: "Draft", tone: "slate" };
  }
}

type Engagement = { label: string; tone: Tone };

function engagementStatus(
  drafts: EmailDraft[],
  hasEmail: boolean,
  hasInsight: boolean
): Engagement {
  if (drafts.some((d) => d.status === "replied"))
    return { label: "Replied", tone: "green" };
  const sent = drafts
    .filter((d) => d.status === "sent" && d.sent_at)
    .sort((a, b) => +new Date(b.sent_at!) - +new Date(a.sent_at!));
  if (sent.length > 0) {
    const d = daysSince(sent[0].sent_at);
    return {
      label: d === 0 ? "Awaiting reply · today" : `Awaiting reply · ${d}d`,
      tone: d != null && d >= 4 ? "amber" : "blue",
    };
  }
  if (drafts.some((d) => d.status === "scheduled"))
    return { label: "Send scheduled", tone: "purple" };
  if (drafts.some((d) => d.status === "approved"))
    return { label: "Approved — ready to send", tone: "amber" };
  if (drafts.some((d) => d.status === "draft"))
    return { label: "Draft ready to send", tone: "slate" };
  if (hasEmail) return { label: "Ready to reach out", tone: "slate" };
  if (hasInsight) return { label: "Researched", tone: "slate" };
  return { label: "Not contacted", tone: "slate" };
}

const UNSENT = ["draft", "approved", "scheduled"];

/** One outbound message + (optional) reply, with inline actions. */
function MessageCard({
  draft,
  label,
  labelTone,
  contactName,
  showFollowup,
  onSend,
  onDelete,
  onSaveEdit,
  onFollowup,
  sending,
  deleting,
  saving,
  followingUp,
}: {
  draft: EmailDraft;
  label: string;
  labelTone: Tone;
  contactName: string;
  showFollowup: boolean;
  onSend: (d: EmailDraft) => void;
  onDelete: (id: number) => void;
  onSaveEdit: (id: number, subject: string, body: string) => void;
  onFollowup: (id: number) => void;
  sending: boolean;
  deleting: boolean;
  saving: boolean;
  followingUp: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);

  useEffect(() => {
    setSubject(draft.subject);
    setBody(draft.body);
  }, [draft.id, draft.subject, draft.body]);

  const chip = statusChip(draft.status);
  const isUnsent = UNSENT.includes(draft.status);
  const when =
    draft.status === "sent" || draft.status === "replied"
      ? `Sent ${fmtDate(draft.sent_at)}`
      : draft.status === "scheduled"
        ? `Scheduled ${fmtDate(draft.scheduled_at)}`
        : `Created ${fmtDate(draft.created_at)}`;

  return (
    <div className="rounded-xl border border-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Badge tone={labelTone}>{label}</Badge>
          <Badge tone={chip.tone}>{chip.label}</Badge>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          {(draft.status === "sent" || draft.status === "replied") &&
            (draft.open_count ?? 0) > 0 && (
              <span
                className="font-medium text-emerald-600"
                title={
                  draft.last_opened_at
                    ? `Last opened ${fmtDate(draft.last_opened_at)}`
                    : undefined
                }
              >
                Opened {draft.open_count}×
              </span>
            )}
          <span>{when}</span>
        </div>
      </div>

      <div className="px-4 py-3">
        {editing ? (
          <div className="space-y-2">
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium"
            />
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
        ) : (
          <>
            <div className="mb-1 text-sm font-semibold text-slate-800">
              {draft.subject || "(no subject)"}
            </div>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
              {draft.body}
            </p>
          </>
        )}
      </div>

      {draft.status === "replied" && (
        <div className="border-t border-emerald-100 bg-emerald-50/60 px-4 py-3">
          <div className="mb-1 flex items-center justify-between text-xs">
            <span className="font-semibold text-emerald-700">
              ↩ {contactName} replied
            </span>
            {draft.replied_at && (
              <span className="text-emerald-600">{fmtDate(draft.replied_at)}</span>
            )}
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-emerald-900">
            {draft.reply_body || draft.reply_snippet || "(reply detected)"}
          </p>
        </div>
      )}

      {/* Inline actions */}
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-4 py-2">
        {isUnsent && editing && (
          <>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                onSaveEdit(draft.id, subject, body);
                setEditing(false);
              }}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </>
        )}
        {isUnsent && !editing && (
          <>
            <Button variant="ghost" onClick={() => onDelete(draft.id)} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete"}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(true)}>
              Edit
            </Button>
            <Button onClick={() => onSend(draft)} disabled={sending}>
              {sending ? "Sending…" : "Approve & send"}
            </Button>
          </>
        )}
        {showFollowup && (
          <Button
            variant="secondary"
            onClick={() => onFollowup(draft.id)}
            disabled={followingUp}
          >
            {followingUp ? "Drafting follow-up…" : "Draft follow-up"}
          </Button>
        )}
      </div>
    </div>
  );
}

export default function ProspectDetail() {
  const { id } = useParams();
  const prospectId = Number(id);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get("run") ? Number(searchParams.get("run")) : undefined;
  const campaignId = searchParams.get("campaign")
    ? Number(searchParams.get("campaign"))
    : undefined;

  const { data: siblings } = useQuery({
    queryKey: ["prospect-siblings", runId, campaignId],
    queryFn: () => {
      if (campaignId) {
        return listProspects({ campaign_id: campaignId, sort: "relevance", limit: 200 });
      }
      if (runId) {
        return listProspects({ discovery_run_id: runId, sort: "relevance", limit: 200 });
      }
      return Promise.resolve({ items: [], total: 0, limit: 0, offset: 0 });
    },
    enabled: !!(runId || campaignId),
  });
  const siblingItems = siblings?.items ?? [];
  const currentIndex = siblingItems.findIndex((p) => p.id === prospectId);
  const prevProspect = currentIndex > 0 ? siblingItems[currentIndex - 1] : undefined;
  const nextProspect =
    currentIndex >= 0 && currentIndex < siblingItems.length - 1
      ? siblingItems[currentIndex + 1]
      : undefined;
  const querySuffix = campaignId
    ? `?campaign=${campaignId}`
    : runId
      ? `?run=${runId}`
      : "";
  const goTo = (pid: number) => navigate(`/prospects/${pid}${querySuffix}`);

  const { data: prospect, isLoading } = useQuery({
    queryKey: ["prospect", prospectId],
    queryFn: () => getProspect(prospectId),
  });
  const { data: insights } = useQuery({
    queryKey: ["prospect-insights", prospectId],
    queryFn: () => getProspectInsights(prospectId),
  });
  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });
  const { data: org } = useQuery({
    queryKey: ["organization", prospect?.company_id],
    queryFn: () => getOrganization(prospect!.company_id!),
    enabled: !!prospect?.company_id,
  });

  const [principalId, setPrincipalId] = useState<number | "">("");
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<"success" | "error">("success");
  const [replyText, setReplyText] = useState("");
  const [showCompose, setShowCompose] = useState(false);

  const effectivePrincipalId =
    principalId || insights?.[0]?.principal_id || principals?.items[0]?.id;

  const { data: emailDrafts } = useQuery({
    queryKey: ["emails", "prospect", prospectId, effectivePrincipalId],
    queryFn: () =>
      listEmails({
        contact_id: prospectId,
        principal_id: Number(effectivePrincipalId),
        limit: 25,
      }),
    enabled: !!effectivePrincipalId && !!prospectId,
  });

  const ok = (msg: string) => {
    setMessageTone("success");
    setMessage(msg);
  };
  const fail = (err: unknown, fallback: string) => {
    const detail =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      (err as Error)?.message;
    setMessageTone("error");
    setMessage(detail || fallback);
  };
  const invalidateEmails = () => {
    qc.invalidateQueries({ queryKey: ["emails"] });
    qc.invalidateQueries({ queryKey: ["emails", "prospect", prospectId] });
    qc.invalidateQueries({ queryKey: ["prospect", prospectId] });
  };

  const approve = useMutation({
    mutationFn: (approved: boolean) => setProspectApproval(prospectId, approved),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prospect", prospectId] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  const reveal = useMutation({
    mutationFn: () => revealProspect(prospectId),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ["prospect", prospectId] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
      ok(
        updated.email
          ? "Contact details revealed."
          : "Reveal requested. No work email found for this person."
      );
    },
    onError: (err) =>
      fail(err, "Reveal failed — check Apollo credits or approve this prospect first."),
  });

  const genInsight = useMutation({
    mutationFn: () =>
      generateInsight({
        principal_id: Number(effectivePrincipalId),
        contact_id: prospectId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prospect-insights", prospectId] });
      qc.invalidateQueries({ queryKey: ["prospect", prospectId] });
      ok("Strategic insight generated.");
    },
    onError: (err) => fail(err, "Insight generation failed — check ANTHROPIC_API_KEY."),
  });

  const genEmail = useMutation({
    mutationFn: (regenerate: boolean) =>
      generateEmail({
        principal_id: Number(effectivePrincipalId),
        contact_id: prospectId,
        insight_id: insights?.[0]?.id,
        regenerate,
      }),
    onSuccess: (_d, regenerate) => {
      invalidateEmails();
      ok(regenerate ? "New draft created." : "Outreach draft ready — review and send below.");
    },
    onError: (err) => fail(err, "Draft failed — check ANTHROPIC_API_KEY."),
  });

  const sendNow = useMutation({
    mutationFn: async (d: EmailDraft) => {
      if (d.status === "draft") await setEmailStatus(d.id, "approved");
      return sendEmail(d.id);
    },
    onSuccess: () => {
      invalidateEmails();
      ok("Email sent from the configured mailbox.");
    },
    onError: (err) => fail(err, "Send failed — reveal the email or check the provider."),
  });

  const editDraft = useMutation({
    mutationFn: (v: { id: number; subject: string; body: string }) =>
      updateEmail(v.id, { subject: v.subject, body: v.body }),
    onSuccess: () => {
      invalidateEmails();
      ok("Draft updated.");
    },
    onError: (err) => fail(err, "Could not save the draft."),
  });

  const removeDraft = useMutation({
    mutationFn: (id: number) => deleteEmail(id),
    onSuccess: () => {
      invalidateEmails();
      ok("Draft deleted.");
    },
    onError: (err) => fail(err, "Could not delete the draft."),
  });

  const reply = useMutation({
    mutationFn: (vars: { draftId: number; body: string }) =>
      replyToEmail(vars.draftId, vars.body),
    onSuccess: () => {
      invalidateEmails();
      setReplyText("");
      setShowCompose(false);
      ok("Message sent.");
    },
    onError: (err) => fail(err, "Send failed — check the email provider config."),
  });

  const followup = useMutation({
    mutationFn: (draftId: number) => createFollowup(draftId),
    onSuccess: () => {
      invalidateEmails();
      ok("Follow-up drafted — review and send it below.");
    },
    onError: (err) => fail(err, "Follow-up drafting failed."),
  });

  const genCall = useMutation({
    mutationFn: () =>
      generateCall({
        principal_id: Number(effectivePrincipalId),
        contact_id: prospectId,
        insight_id: insights?.[0]?.id,
      }),
    onSuccess: () => ok("Call script queued — see Call Queue."),
    onError: (err) => fail(err, "Could not queue a call script."),
  });

  if (isLoading || !prospect) return <Loading />;

  const hasInsight = Boolean(insights && insights.length > 0);
  const hasEmail = Boolean(prospect.email);

  // Conversation: oldest → newest, like an email thread.
  const thread = [...(emailDrafts?.items ?? [])].sort(
    (a, b) => +new Date(a.created_at) - +new Date(b.created_at)
  );
  const engagement = engagementStatus(thread, hasEmail, hasInsight);
  const principalName =
    principals?.items.find((p) => p.id === Number(effectivePrincipalId))?.name || "You";

  const hasPendingDraft = thread.some((d) => UNSENT.includes(d.status));
  const hasSent = thread.some((d) => d.status === "sent" || d.status === "replied");
  const lastMessage = thread[thread.length - 1];

  // Outreach step labels (initial vs follow-up #n) computed in send order.
  let outreachIdx = -1;
  const labelFor = (_d: EmailDraft): { label: string; tone: Tone } => {
    outreachIdx += 1;
    if (outreachIdx === 0) return { label: "Initial outreach", tone: "slate" };
    return { label: `Follow-up #${outreachIdx}`, tone: "blue" };
  };

  // Single, state-driven "what to do next".
  const nextStep: { text: string; btn?: string; onClick?: () => void; disabled?: boolean } =
    !hasInsight
      ? {
          text: "Start by researching why this person matters to your principal.",
          btn: genInsight.isPending ? "Researching…" : "Research this person",
          onClick: () => genInsight.mutate(),
          disabled: !effectivePrincipalId || genInsight.isPending,
        }
      : !hasEmail
        ? {
            text: "Reveal their work email to begin outreach.",
            btn: reveal.isPending ? "Revealing…" : "Reveal email",
            onClick: () => reveal.mutate(),
            disabled: reveal.isPending,
          }
        : thread.length === 0
          ? {
              text: "Draft a personalized outreach email.",
              btn: genEmail.isPending ? "Drafting…" : "Draft outreach email",
              onClick: () => genEmail.mutate(false),
              disabled: genEmail.isPending,
            }
          : hasPendingDraft
            ? { text: "You have a draft ready — review and send it in the conversation below." }
            : lastMessage?.status === "replied"
              ? { text: "They replied. Reply below to keep the conversation going." }
              : {
                  text: "Sent and awaiting a reply. Nudge them with a follow-up when ready.",
                  btn: followup.isPending ? "Drafting…" : "Draft follow-up",
                  onClick: () => lastMessage && followup.mutate(lastMessage.id),
                  disabled: followup.isPending || !lastMessage,
                };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Link
          to={campaignId ? `/campaigns/${campaignId}` : `/prospects${querySuffix}`}
          className="text-sm font-medium text-slate-500 hover:text-slate-800"
        >
          ← Back to {campaignId ? "campaign" : "prospects"}
          {runId && !campaignId ? ` (run #${runId})` : ""}
        </Link>
        {siblingItems.length > 1 && (
          <div className="flex items-center gap-2">
            {currentIndex >= 0 && (
              <span className="text-xs text-slate-400">
                {currentIndex + 1} of {siblingItems.length}
              </span>
            )}
            <Button
              variant="secondary"
              onClick={() => prevProspect && goTo(prevProspect.id)}
              disabled={!prevProspect}
            >
              ← Previous
            </Button>
            <Button
              variant="secondary"
              onClick={() => nextProspect && goTo(nextProspect.id)}
              disabled={!nextProspect}
            >
              Next →
            </Button>
          </div>
        )}
      </div>

      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            {prospect.name}
            <Badge tone={engagement.tone}>{engagement.label}</Badge>
          </span>
        }
        subtitle={[prospect.title, org?.name].filter(Boolean).join(" · ") || undefined}
        actions={
          prospect.approved_for_outreach ? (
            <Button variant="secondary" onClick={() => approve.mutate(false)}>
              Revoke approval
            </Button>
          ) : (
            <Button onClick={() => approve.mutate(true)}>Approve for outreach</Button>
          )
        }
      />

      {message && (
        <div
          className={`mb-4 rounded-lg px-4 py-2 text-sm ${
            messageTone === "error"
              ? "bg-rose-50 text-rose-800"
              : "bg-emerald-50 text-emerald-700"
          }`}
        >
          {message}
        </div>
      )}

      {/* One clear next step */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
        <span className="text-sm text-slate-600">{nextStep.text}</span>
        {nextStep.btn && (
          <Button onClick={nextStep.onClick} disabled={nextStep.disabled}>
            {nextStep.btn}
          </Button>
        )}
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <div className="space-y-6 md:col-span-2">
          {/* Conversation */}
          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold text-slate-700">Conversation</div>
              {hasSent && (
                <button
                  onClick={() => setShowCompose((s) => !s)}
                  className="text-xs font-medium text-blue-600 hover:underline"
                >
                  {showCompose ? "Close" : "Write a message"}
                </button>
              )}
            </div>

            {thread.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-400">
                No emails yet. Use the step above to draft the first outreach.
              </p>
            ) : (
              <div className="space-y-3">
                {thread.map((d) => {
                  const lab = labelFor(d);
                  return (
                    <MessageCard
                      key={d.id}
                      draft={d}
                      label={lab.label}
                      labelTone={lab.tone}
                      contactName={prospect.name}
                      showFollowup={
                        !hasPendingDraft &&
                        d.id === lastMessage?.id &&
                        d.status === "sent"
                      }
                      onSend={(dr) => {
                        if (
                          window.confirm(`Send this email to ${prospect.name} now?`)
                        )
                          sendNow.mutate(dr);
                      }}
                      onDelete={(id) => {
                        if (window.confirm("Delete this draft?")) removeDraft.mutate(id);
                      }}
                      onSaveEdit={(id, subject, body) =>
                        editDraft.mutate({ id, subject, body })
                      }
                      onFollowup={(id) => followup.mutate(id)}
                      sending={sendNow.isPending && sendNow.variables?.id === d.id}
                      deleting={removeDraft.isPending && removeDraft.variables === d.id}
                      saving={editDraft.isPending && editDraft.variables?.id === d.id}
                      followingUp={followup.isPending && followup.variables === d.id}
                    />
                  );
                })}
              </div>
            )}

            {/* Free-form compose / reply */}
            {hasEmail && (showCompose || lastMessage?.status === "replied") && (
              <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {lastMessage?.status === "replied"
                    ? `Reply to ${prospect.name}`
                    : `Message ${prospect.name}`}
                </div>
                <textarea
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  rows={4}
                  placeholder={`Sends from ${principalName}'s Outlook and is logged here.`}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <div className="mt-2 flex justify-end">
                  <Button
                    onClick={() =>
                      lastMessage &&
                      reply.mutate({ draftId: lastMessage.id, body: replyText })
                    }
                    disabled={reply.isPending || !replyText.trim() || !lastMessage}
                  >
                    {reply.isPending ? "Sending…" : "Send"}
                  </Button>
                </div>
              </div>
            )}
          </Card>

          {/* Strategic relevance */}
          {hasInsight ? (
            insights!.map((ins) => <InsightCard key={ins.id} insight={ins} />)
          ) : (
            <Card className="p-8 text-center">
              <div className="text-sm font-medium text-slate-700">
                No strategic insight yet
              </div>
              <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
                Claude analyzes why this person is relevant — strategic connection,
                talking points, and opportunity type.
              </p>
              <Button
                className="mt-5"
                onClick={() => genInsight.mutate()}
                disabled={!effectivePrincipalId || genInsight.isPending}
              >
                {genInsight.isPending ? "Generating…" : "Generate strategic insight"}
              </Button>
            </Card>
          )}
        </div>

        {/* Right rail */}
        <div className="space-y-6">
          <Card className="p-5">
            <div className="mb-2 text-sm font-semibold text-slate-700">Prospect</div>
            <Row label="Role" value={prospect.role_category?.replace(/_/g, " ")} />
            <Row label="Seniority" value={prospect.seniority?.replace(/_/g, " ")} />
            <Row label="Relevance" value={prospect.relevance_score ?? undefined} />

            <div className="mt-3 border-t border-slate-100 pt-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Contact details
              </div>
              <div className="flex items-center justify-between py-1.5 text-sm">
                <span className="text-slate-400">Email</span>
                {prospect.email ? (
                  <a
                    href={`mailto:${prospect.email}`}
                    className="text-right text-blue-600 hover:underline"
                  >
                    {prospect.email}
                  </a>
                ) : (
                  <Badge tone="amber">not revealed</Badge>
                )}
              </div>
              <div className="flex items-center justify-between py-1.5 text-sm">
                <span className="text-slate-400">Phone</span>
                {prospect.phone ? (
                  <a
                    href={`tel:${prospect.phone}`}
                    className="text-right text-blue-600 hover:underline"
                  >
                    {prospect.phone}
                  </a>
                ) : prospect.phone_reveal_status === "pending" ? (
                  <Badge tone="blue">arriving via webhook</Badge>
                ) : (
                  <Badge tone="amber">not revealed</Badge>
                )}
              </div>
              {prospect.location && <Row label="Location" value={prospect.location} />}
              <div className="flex items-center justify-between py-1.5 text-sm">
                <span className="text-slate-400">LinkedIn</span>
                {prospect.linkedin_url ? (
                  <a
                    href={prospect.linkedin_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-right text-blue-600 hover:underline"
                  >
                    View profile ↗
                  </a>
                ) : (
                  <span className="text-slate-300">—</span>
                )}
              </div>
              {!prospect.email && (
                <Button
                  variant="secondary"
                  className="mt-3 w-full"
                  onClick={() => reveal.mutate()}
                  disabled={reveal.isPending}
                >
                  {reveal.isPending ? "Revealing…" : "Reveal email & phone"}
                </Button>
              )}
            </div>
          </Card>

          {org && (
            <Card className="p-5">
              <div className="mb-2 text-sm font-semibold text-slate-700">
                Organization
              </div>
              <Link
                to={`/organizations/${org.id}`}
                className="font-medium text-slate-900 hover:underline"
              >
                {org.name}
              </Link>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {org.company_type && (
                  <Badge tone="purple">{org.company_type.replace(/_/g, " ")}</Badge>
                )}
                {org.industry && <Badge>{org.industry}</Badge>}
              </div>
              <Row label="Employees" value={org.employee_count ?? undefined} />
              <Row label="HQ" value={org.headquarters} />
            </Card>
          )}

          {/* Compact more-actions */}
          <Card className="p-5">
            <div className="mb-3 text-sm font-semibold text-slate-700">More actions</div>
            {principals && principals.items.length > 1 && (
              <label className="mb-3 block">
                <span className="text-xs font-medium text-slate-500">Principal</span>
                <select
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  value={effectivePrincipalId ?? ""}
                  onChange={(e) =>
                    setPrincipalId(e.target.value ? Number(e.target.value) : "")
                  }
                >
                  {principals.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="flex flex-col gap-2">
              <Button
                variant="secondary"
                onClick={() => genInsight.mutate()}
                disabled={!effectivePrincipalId || genInsight.isPending}
              >
                {genInsight.isPending ? "Refreshing…" : "Refresh insight"}
              </Button>
              {hasEmail && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    if (
                      !hasPendingDraft ||
                      window.confirm(
                        "A draft already exists. Create a new outreach draft anyway?"
                      )
                    )
                      genEmail.mutate(true);
                  }}
                  disabled={!effectivePrincipalId || genEmail.isPending || !hasInsight}
                  title={!hasInsight ? "Generate an insight first" : undefined}
                >
                  {genEmail.isPending ? "Drafting…" : "Regenerate outreach draft"}
                </Button>
              )}
              <Button
                variant="secondary"
                onClick={() => genCall.mutate()}
                disabled={!effectivePrincipalId || genCall.isPending}
              >
                Queue call script
              </Button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
