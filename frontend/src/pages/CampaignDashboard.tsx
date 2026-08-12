import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelCampaignDraftSend,
  cancelCampaignRun,
  pauseCampaign,
  resumeCampaign,
  scheduleApprovedEmails,
  deleteCampaign,
  getCampaign,
  getCampaignDraftSend,
  getCampaignProspects,
  listAgentCopyVariants,
  listAgentVariants,
  listEmails,
  runCampaign,
  sendEmail,
  setEmailStatus,
  startCampaignDraftSend,
  updateCampaign,
} from "../api/client";
import { Badge, Button, Card, Loading, ScoreBar } from "../components/ui";
import { usePersistedState } from "../hooks/usePersistedState";
import { apiErrorMessage } from "../utils/apiError";
import { relativeTime } from "../utils/time";
import type {
  CampaignBulkSend,
  CampaignDetail,
  CampaignProspect,
  CampaignRunSnapshot,
  EmailDraft,
} from "../types";

type Tone = "green" | "blue" | "amber" | "slate" | "purple";

const inputCls =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100";

const FUNNEL = [
  { key: "discovered", label: "Find", desc: "Found on Apollo" },
  { key: "qualified", label: "Score", desc: "Passed AI research" },
  { key: "drafted", label: "Draft", desc: "Emails written" },
  { key: "sent", label: "Send", desc: "Emails sent" },
  { key: "followups_sent", label: "Follow up", desc: "Follow-ups sent" },
] as const;

function Stat({ label, value, sub, accent = "slate" }: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "violet" | "emerald" | "sky" | "amber" | "slate";
}) {
  const bg: Record<string, string> = {
    violet: "from-violet-500/10 to-violet-600/5 border-violet-200/60",
    emerald: "from-emerald-500/10 to-emerald-600/5 border-emerald-200/60",
    sky: "from-sky-500/10 to-sky-600/5 border-sky-200/60",
    amber: "from-amber-500/10 to-amber-600/5 border-amber-200/60",
    slate: "from-slate-500/5 to-slate-600/5 border-slate-200",
  };
  return (
    <div className={`rounded-2xl border bg-gradient-to-br px-4 py-3 ${bg[accent]}`}>
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

function emailTone(status?: string | null): Tone {
  switch (status) {
    case "replied":
      return "green";
    case "sent":
      return "blue";
    case "scheduled":
    case "approved":
      return "purple";
    case "draft":
      return "slate";
    default:
      return "slate";
  }
}

function pipelineTone(status?: string | null): Tone {
  if (!status) return "slate";
  const s = status.toLowerCase();
  if (s.includes("draft")) return "purple";
  if (s.includes("qualif")) return "green";
  if (s.includes("reject") || s.includes("below")) return "amber";
  if (s.includes("sent") || s.includes("schedul")) return "blue";
  return "slate";
}

/** Label, counts and a bar — one row of the run panel's stage progress. */
function ProgressRow({
  label,
  done,
  total,
  unit,
  sub,
  tone = "sky",
}: {
  label: string;
  done: number;
  total: number;
  unit: string;
  sub?: string;
  tone?: "sky" | "violet";
}) {
  // Floor, not round: 199 of 200 must not read as 100% complete when one was
  // skipped. Only an actually-finished stage shows 100.
  const pct = total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
  return (
    <div className="mt-2 max-w-sm">
      <div className="flex items-center justify-between text-[11px] text-slate-600">
        <span className="font-medium">{label}</span>
        <span className="tabular-nums">
          {done}/{total} {unit} · {pct}%
        </span>
      </div>
      <div
        className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
      >
        <div
          className={`h-full rounded-full transition-all ${
            tone === "violet" ? "bg-violet-500" : "bg-sky-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {sub ? (
        <p className="mt-1 text-[11px] text-slate-500 tabular-nums">{sub}</p>
      ) : null}
    </div>
  );
}

/** How far relevance research has got through the people this run found.
 *
 *  "Scored" is qualified + rejected, not qualified alone: a prospect rejected for
 *  a low score was still researched, so counting only the qualified ones
 *  understates the work — and on a run where most fall below the threshold it
 *  would read as almost no progress while every one of them had been scored.
 *
 *  Rendered for finished runs too, where it is the final split rather than
 *  progress: a run that was cancelled or interrupted part-way shows exactly how
 *  much of its research actually happened. */
function RelevanceProgress({ run }: { run: CampaignRunSnapshot }) {
  const found = run.discovered ?? 0;
  if (found <= 0) return null;
  const qualified = run.qualified ?? 0;
  const rejected = run.rejected ?? 0;
  const scored = Math.min(found, qualified + rejected);
  return (
    <ProgressRow
      label="Relevance research"
      done={scored}
      total={found}
      unit="scored"
      sub={
        rejected > 0
          ? `${qualified} qualified · ${rejected} below the threshold`
          : undefined
      }
    />
  );
}

/** How many emails have been written for the prospects this run qualified.
 *
 *  Qualified is the denominator because that is the draft stage's work list — it
 *  writes one email per qualified prospect (``orchestrator`` drafts from
 *  ``qualified_ids``). Discovered would be wrong: nobody drafts for a prospect
 *  the research rejected.
 *
 *  Once the run is finished any shortfall is a skip, not pending work, so it is
 *  named as such — a prospect whose email could not be revealed never gets a
 *  draft, and that is otherwise only visible in the run's error list. */
function DraftProgress({ run, live }: { run: CampaignRunSnapshot; live?: boolean }) {
  const qualified = run.qualified ?? 0;
  if (qualified <= 0) return null;
  const drafted = Math.min(qualified, run.drafted ?? 0);
  const missing = qualified - drafted;
  return (
    <ProgressRow
      label="Drafts written"
      done={drafted}
      total={qualified}
      unit="drafts"
      tone="violet"
      sub={
        !live && missing > 0
          ? `${missing} skipped — see the issues above`
          : undefined
      }
    />
  );
}

function RunProgress({
  run,
  live,
  campaignId,
}: {
  run: CampaignRunSnapshot;
  live?: boolean;
  campaignId: number;
}) {
  const stages = run.stages ?? [];
  const people = run.people ?? [];
  const errors = run.errors ?? [];
  const stageOrder = ["discovery", "qualify", "draft", "send", "followup"];
  const currentStage =
    live && stages.length > 0 ? stages[stages.length - 1]?.stage : run.status;

  return (
    <div
      className={`rounded-2xl border shadow-sm ${
        live ? "border-sky-200 bg-sky-50/50" : "border-slate-200 bg-white"
      }`}
    >
      <div className="border-b border-inherit px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-900">
            {live ? "Run in progress" : run.status === "failed" ? "Last run (interrupted)" : "Last run"}
            {live && (
              <span className="ml-2 inline-flex items-center gap-1 text-xs font-normal text-sky-700">
                <span className="h-2 w-2 animate-pulse rounded-full bg-sky-500" />
                Working…
              </span>
            )}
            {!live && run.status === "failed" && run.error_message?.includes("Interrupted") && (
              <span className="ml-2 text-xs font-normal text-amber-700">needs Continue</span>
            )}
          </h2>
          <span className="text-xs text-slate-500">
            Run #{run.id}
            {run.started_at ? ` · started ${relativeTime(run.started_at)}` : ""}
          </span>
        </div>
        {live && currentStage && (
          <p className="mt-1 text-xs text-sky-800">
            Current step: <strong>{String(currentStage)}</strong>
            {run.discovered ? ` · ${run.discovered} found so far` : ""}
            {run.qualified ? ` · ${run.qualified} scored` : ""}
            {run.drafted ? ` · ${run.drafted} drafted` : ""}
          </p>
        )}
        <RelevanceProgress run={run} />
        <DraftProgress run={run} live={live} />
      </div>

      {stages.length > 0 && (
        <div className="flex flex-wrap gap-2 border-b border-inherit px-5 py-3">
          {stageOrder.map((name) => {
            const hit = stages.find((s) => s.stage === name);
            return (
              <span
                key={name}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium capitalize ${
                  hit
                    ? "bg-violet-100 text-violet-800"
                    : live
                      ? "bg-slate-100 text-slate-400"
                      : "hidden"
                }`}
              >
                {name}
                {hit && name === "discovery" && hit.imported != null
                  ? ` (${hit.imported})`
                  : ""}
                {hit && name === "qualify" && hit.qualified != null
                  ? ` (${hit.qualified})`
                  : ""}
                {hit && name === "draft" && hit.drafted != null ? ` (${hit.drafted})` : ""}
              </span>
            );
          })}
        </div>
      )}

      {errors.length > 0 && (
        <div className="border-b border-rose-200 bg-rose-50 px-5 py-3">
          <p className="text-xs font-semibold text-rose-800">
            {errors.length} issue{errors.length === 1 ? "" : "s"} during this run
          </p>
          <ul className="mt-1 list-inside list-disc text-xs text-rose-700">
            {errors.slice(0, 3).map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {people.length > 0 && (
        <div className="max-h-64 overflow-y-auto px-5 py-3">
          <p className="mb-2 text-xs font-medium text-slate-500">
            People in this run ({people.length})
          </p>
          <ul className="space-y-1.5">
            {people.slice(0, 50).map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2 text-xs">
                <Link
                  to={`/prospects/${p.id}?campaign=${campaignId}`}
                  className="min-w-0 truncate text-violet-700 hover:underline"
                >
                  {p.name}
                  {p.title ? <span className="text-slate-500"> · {p.title}</span> : null}
                </Link>
                <Badge tone={pipelineTone(p.status)}>{p.status}</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function pct(rate: number): string {
  return `${Math.round((rate || 0) * 100)}%`;
}

function LearningPanel({ principalId }: { principalId: number }) {
  const { data: search } = useQuery({
    queryKey: ["agent-variants", principalId],
    queryFn: () => listAgentVariants(principalId),
    refetchInterval: 30000,
  });
  const { data: copy } = useQuery({
    queryKey: ["agent-copy-variants", principalId],
    queryFn: () => listAgentCopyVariants(principalId),
    refetchInterval: 30000,
  });

  const searchVariants = search?.variants ?? [];
  const copyVariants = copy?.copy_variants ?? [];
  if (!searchVariants.length && !copyVariants.length) return null;

  const best = <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">leading</span>;
  const bestSearchId = searchVariants
    .filter((v) => v.sent > 0)
    .sort((a, b) => b.reply_rate - a.reply_rate)[0]?.id;
  const bestCopyId = copyVariants
    .filter((v) => v.sent > 0)
    .sort((a, b) => b.reply_rate - a.reply_rate)[0]?.id;

  return (
    <Card className="mt-6">
      <h2 className="text-sm font-semibold text-slate-900">What's working (auto-optimizing)</h2>
      <p className="mt-1 text-xs text-slate-500">
        The agent A/B tests who it targets and how it writes, and shifts toward whatever earns replies.
        Stats build up over the first couple of weeks of sending.
      </p>

      <div className="mt-4 grid gap-5 md:grid-cols-2">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Who we target (search)
          </h3>
          <ul className="mt-2 space-y-1.5">
            {searchVariants.map((v) => (
              <li key={v.id} className="flex items-center justify-between text-xs">
                <span className="min-w-0 truncate text-slate-700">
                  {v.label}
                  {v.id === bestSearchId ? best : null}
                  {!v.is_active ? (
                    <span className="ml-1 text-slate-400">(retired)</span>
                  ) : null}
                </span>
                <span className="shrink-0 tabular-nums text-slate-500">
                  {v.replied}/{v.sent} · {pct(v.reply_rate)}
                </span>
              </li>
            ))}
            {!searchVariants.length ? (
              <li className="text-xs text-slate-400">No search variants yet.</li>
            ) : null}
          </ul>
        </div>

        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            How we write (copy)
          </h3>
          <ul className="mt-2 space-y-1.5">
            {copyVariants.map((v) => (
              <li key={v.id} className="flex items-center justify-between text-xs">
                <span className="min-w-0 truncate text-slate-700">
                  {v.label}
                  {v.id === bestCopyId ? best : null}
                  {!v.is_active ? (
                    <span className="ml-1 text-slate-400">(retired)</span>
                  ) : null}
                </span>
                <span className="shrink-0 tabular-nums text-slate-500">
                  {v.replied}/{v.sent} · {pct(v.reply_rate)}
                </span>
              </li>
            ))}
            {!copyVariants.length ? (
              <li className="text-xs text-slate-400">No copy variants yet.</li>
            ) : null}
          </ul>
        </div>
      </div>
    </Card>
  );
}

function ProspectRow({ p, campaignId }: { p: CampaignProspect; campaignId: number }) {
  return (
    <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50/50">
      <td className="px-4 py-3">
        <Link
          to={`/prospects/${p.contact_id}?campaign=${campaignId}`}
          className="font-medium text-violet-700 hover:underline"
        >
          {p.name}
        </Link>
        <p className="text-xs text-slate-500">
          {[p.title, p.company_name].filter(Boolean).join(" · ") || "—"}
        </p>
      </td>
      <td className="px-4 py-3">
        <ScoreBar value={p.relevance_score ?? null} />
      </td>
      <td className="px-4 py-3">
        {p.email_status ? (
          <Badge tone={emailTone(p.email_status)}>{p.email_status}</Badge>
        ) : p.pipeline_status ? (
          <Badge tone={pipelineTone(p.pipeline_status)}>{p.pipeline_status}</Badge>
        ) : (
          <span className="text-xs text-slate-400">not contacted</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-slate-500">
        {p.replied_at
          ? `replied ${relativeTime(p.replied_at)}`
          : p.sent_at
            ? `sent ${relativeTime(p.sent_at)}`
            : "—"}
      </td>
    </tr>
  );
}

function EditPanel({
  campaign,
  onClose,
}: {
  campaign: CampaignDetail;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(campaign.name);
  const [objective, setObjective] = useState(campaign.objective ?? "");
  const [discoverTarget, setDiscoverTarget] = useState(campaign.discover_target || 50);
  const [mailboxCap, setMailboxCap] = useState(campaign.mailbox_daily_cap || 50);
  const [qualifyMin, setQualifyMin] = useState(campaign.qualify_min ?? 0);
  const [requireEmailAndLinkedin, setRequireEmailAndLinkedin] = useState(
    campaign.require_email_and_linkedin ?? false
  );
  const [autoSend, setAutoSend] = useState(campaign.auto_send);
  const [autoSchedule, setAutoSchedule] = useState(campaign.auto_schedule);

  const save = useMutation({
    mutationFn: () =>
      updateCampaign(campaign.id, {
        name: name.trim() || campaign.name,
        objective_prompt: objective.trim() || undefined,
        discover_target: discoverTarget,
        mailbox_daily_cap: mailboxCap,
        qualify_min: qualifyMin,
        require_email_and_linkedin: requireEmailAndLinkedin,
        auto_send: autoSend,
        auto_schedule: autoSchedule,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      onClose();
    },
  });

  return (
    <Card className="p-5">
      <h3 className="text-sm font-semibold text-slate-900">Edit campaign</h3>
      <div className="mt-4 space-y-4">
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Campaign name</span>
          <input className={`${inputCls} mt-1.5`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Goal</span>
          <textarea
            rows={3}
            className={`${inputCls} mt-1.5`}
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs font-medium text-slate-600">People to find / run</span>
            <input
              type="number"
              className={`${inputCls} mt-1.5`}
              value={discoverTarget}
              onChange={(e) => setDiscoverTarget(Number(e.target.value))}
            />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-slate-600">
              Shared mailbox cap / day
            </span>
            <input
              type="number"
              className={`${inputCls} mt-1.5`}
              value={mailboxCap}
              onChange={(e) => setMailboxCap(Number(e.target.value))}
            />
            <span className="mt-1 block text-[11px] text-slate-400">
              Across all of {campaign.principal_name}&apos;s campaigns.
            </span>
          </label>
        </div>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">
            Minimum relevance score to qualify (0-100)
          </span>
          <input
            type="number"
            min={0}
            max={100}
            className={`${inputCls} mt-1.5`}
            value={qualifyMin}
            onChange={(e) => setQualifyMin(Number(e.target.value))}
          />
          <span className="mt-1 block text-[11px] text-slate-400">
            Lower this to qualify more of the people found each run (fewer get rejected), at
            the cost of some being a weaker fit.
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
          <input
            type="checkbox"
            className="mt-0.5 rounded border-slate-300"
            checked={requireEmailAndLinkedin}
            onChange={(e) => setRequireEmailAndLinkedin(e.target.checked)}
          />
          <span className="text-sm text-slate-800">
            <span className="font-medium">Only find people with both email + LinkedIn</span>
            <span className="block text-xs text-slate-500">
              Discards anyone missing either during discovery, so fewer get rejected later for
              having no reachable email.
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
          <input
            type="checkbox"
            className="mt-0.5 rounded border-slate-300"
            checked={autoSend}
            onChange={(e) => setAutoSend(e.target.checked)}
          />
          <span className="text-sm text-slate-800">
            <span className="font-medium">Autopilot — send without manual approval</span>
            <span className="block text-xs text-slate-500">
              Off = review mode: every email is drafted and waits for your approval on this page.
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
          <input
            type="checkbox"
            className="mt-0.5 rounded border-slate-300"
            checked={autoSchedule}
            onChange={(e) => setAutoSchedule(e.target.checked)}
          />
          <span className="text-sm text-slate-800">
            <span className="font-medium">Let the AI choose send times</span>
            <span className="block text-xs text-slate-500">
              Sends in each recipient's local business hours and A/B tests time-of-day.
            </span>
          </span>
        </label>
      </div>
      {save.isError && (
        <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          {apiErrorMessage(save.error, "Could not save changes. Please try again.")}
        </div>
      )}
      <div className="mt-5 flex gap-2">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save changes"}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}

/** Slim progress bar — same markup as the LinkedIn page's acceptance bar.
 *
 *  Tracks ``done`` (drafts attempted) rather than ``sent``, so the bar keeps
 *  moving through a draft that failed instead of appearing to stall on it. */
function BulkSendBar({
  done,
  total,
  tone = "amber",
}: {
  done: number;
  total: number;
  tone?: "amber" | "rose";
}) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div
      className="mt-1.5 h-2 w-full max-w-sm overflow-hidden rounded-full bg-amber-200/70"
      role="progressbar"
      aria-label="Approve and send all progress"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={done}
    >
      <div
        className={`h-full rounded-full transition-all ${
          tone === "rose" ? "bg-rose-500" : "bg-amber-600"
        }`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

/** Outcome of the last server-side bulk send, in the panel header.
 *
 *  Shown rather than announced through a toast so it survives a page refresh —
 *  the whole point of moving the loop server-side is that the browser no longer
 *  has to be present for it. */
function BulkSendStatus({ bulk }: { bulk: CampaignBulkSend }) {
  if (bulk.status === "running") {
    return (
      <div>
        <p className="mt-1 text-xs font-medium text-amber-900">
          Sending on the server — {bulk.done} of {bulk.total} done
          {bulk.failed > 0 ? `, ${bulk.failed} failed` : ""}.
          {bulk.cancel_requested ? " Stopping…" : " You can close this page."}
        </p>
        <BulkSendBar done={bulk.done} total={bulk.total} />
      </div>
    );
  }
  if (bulk.status === "interrupted") {
    return (
      <div>
        <p className="mt-1 text-xs font-medium text-rose-700">
          {bulk.error} Sent {bulk.sent} of {bulk.total} before it stopped.
        </p>
        <BulkSendBar done={bulk.done} total={bulk.total} tone="rose" />
      </div>
    );
  }
  const label = bulk.status === "cancelled" ? "Stopped" : "Finished";
  return (
    <p className="mt-1 text-xs text-amber-800/80">
      {label}: {bulk.sent} sent
      {bulk.failed > 0 ? `, ${bulk.failed} failed` : ""} of {bulk.total}.
      {bulk.errors.length > 0 ? ` ${bulk.errors.slice(0, 2).join("; ")}` : ""}
    </p>
  );
}

function PendingApproval({
  campaign,
  onChanged,
}: {
  campaign: CampaignDetail;
  onChanged: (msg: string) => void;
}) {
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  // The bulk send runs on the server, so this only watches it. Poll while it is
  // working; stop polling once it settles.
  const { data: bulk } = useQuery({
    queryKey: ["campaign", campaign.id, "bulk-send"],
    queryFn: () => getCampaignDraftSend(campaign.id),
    refetchInterval: (q) =>
      (q.state.data as CampaignBulkSend | null)?.status === "running" ? 3000 : false,
  });
  const bulkRunning = bulk?.status === "running";

  const { data: drafts, isLoading } = useQuery({
    queryKey: ["campaign", campaign.id, "drafts"],
    queryFn: () =>
      listEmails({ campaign_id: campaign.id, status: "draft", limit: 100 }),
    // Also poll while a bulk send is working, so the list empties as it goes.
    refetchInterval: campaign.status === "running" || bulkRunning ? 6000 : false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
    qc.invalidateQueries({ queryKey: ["campaign", campaign.id, "drafts"] });
    qc.invalidateQueries({ queryKey: ["campaign", campaign.id, "prospects"] });
  };

  const approveAndSend = async (d: EmailDraft) => {
    setBusyId(d.id);
    try {
      await setEmailStatus(d.id, "approved");
      await sendEmail(d.id);
      onChanged(`Sent to ${d.contact_name ?? "prospect"}.`);
      refresh();
    } catch (e) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not send.";
      onChanged(String(msg));
    } finally {
      setBusyId(null);
    }
  };

  // Hands the whole batch to the server and returns. The loop used to run here —
  // two requests per draft — so it only ever covered the 100 drafts this panel had
  // loaded, and closing the tab stopped it partway with no way to tell how far it
  // got. The server works through every draft in the campaign instead, and the
  // poll above reports progress.
  const sendAll = async () => {
    const waiting = drafts?.total ?? 0;
    if (!waiting) return;
    if (!window.confirm(`Approve and send all ${waiting} drafts now?`)) return;
    setStarting(true);
    try {
      await startCampaignDraftSend(campaign.id);
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id, "bulk-send"] });
      onChanged(`Sending ${waiting} drafts — this keeps going if you leave the page.`);
    } catch (e) {
      onChanged(apiErrorMessage(e, "Could not start sending."));
    } finally {
      setStarting(false);
    }
  };

  const stopSending = async () => {
    if (!window.confirm("Stop sending? Emails not yet sent stay as drafts.")) return;
    try {
      await cancelCampaignDraftSend(campaign.id);
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id, "bulk-send"] });
      onChanged("Stopping after the email currently going out.");
    } catch (e) {
      onChanged(apiErrorMessage(e, "Could not stop sending."));
    }
  };

  if (campaign.auto_send) return null;

  // ``total`` is every draft waiting in this campaign, not just the page loaded
  // below — the old ``items.length`` read "100 waiting" while 199 were queued.
  const count = drafts?.total ?? campaign.pending_drafts;

  return (
    <div className="mt-6 rounded-2xl border border-amber-200 bg-amber-50/60 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-200/70 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-amber-900">
            Review before sending
            {count ? <span className="ml-1 text-amber-700">({count} waiting)</span> : null}
          </h2>
          <p className="mt-0.5 text-xs text-amber-800/80">
            This campaign drafts every email and waits for your approval — nothing is sent
            automatically. Approve them here, or switch to Autopilot in Edit once you're happy.
          </p>
          {drafts && drafts.total > drafts.items.length ? (
            <p className="mt-0.5 text-xs text-amber-800/80">
              Listing the first {drafts.items.length} — &ldquo;Approve &amp; send all&rdquo;
              covers all {drafts.total}.
            </p>
          ) : null}
          {bulk ? <BulkSendStatus bulk={bulk} /> : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {bulkRunning ? (
            <>
              <Button variant="ghost" onClick={stopSending}>
                Stop sending
              </Button>
              <Button disabled>
                Sending… {bulk?.done ?? 0}/{bulk?.total ?? 0}
              </Button>
            </>
          ) : (
            count > 0 && (
              <Button onClick={sendAll} disabled={starting}>
                {starting
                  ? "Starting…"
                  : bulk?.status === "interrupted"
                    ? "Continue sending"
                    : "Approve & send all"}
              </Button>
            )
          )}
        </div>
      </div>
      {isLoading ? (
        <Loading />
      ) : !drafts || drafts.items.length === 0 ? (
        <div className="px-5 py-8 text-center text-sm text-amber-800/70">
          No drafts waiting. Run the campaign to generate emails for review.
        </div>
      ) : (
        <ul className="divide-y divide-amber-200/60">
          {drafts.items.map((d) => (
            <li key={d.id} className="px-5 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-900">
                    {d.contact_name ?? "Prospect"}
                    {d.contact_title ? (
                      <span className="font-normal text-slate-500"> · {d.contact_title}</span>
                    ) : null}
                  </p>
                  <p className="mt-0.5 text-xs font-medium text-slate-700">{d.subject}</p>
                  <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-slate-600">
                    {d.body}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {d.contact_id && (
                    <Link to={`/prospects/${d.contact_id}?campaign=${campaign.id}`}>
                      <Button variant="secondary">View</Button>
                    </Link>
                  )}
                  <Button
                    onClick={() => approveAndSend(d)}
                    disabled={busyId === d.id || bulkRunning || starting}
                  >
                    {busyId === d.id ? "Sending…" : "Approve & send"}
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CampaignDashboard() {
  const { id } = useParams();
  const campaignId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  // Persisted (not plain useState) so navigating to another module and back
  // reopens the edit panel exactly as it was left — React Router unmounts
  // this whole component on route change, which would otherwise wipe it.
  // Keyed per campaign so switching between campaigns doesn't leak state.
  const [editing, setEditing] = usePersistedState(
    `campaign:${campaignId}:editing`,
    false
  );
  const [pauseOpen, setPauseOpen] = useState(false);
  const [toast, setToast] = useState<{ msg: string; isError: boolean } | null>(null);
  const toastTimer = useRef<number | null>(null);
  const scrollRestored = useRef(false);

  const clearToastTimer = () => {
    if (toastTimer.current !== null) {
      window.clearTimeout(toastTimer.current);
      toastTimer.current = null;
    }
  };

  // Success messages fade; failures do NOT. A run that refuses to start (paused,
  // already in progress, backend error) used to say so for four seconds and then
  // erase itself, which is why "Run now" read as a dead button — the reason was
  // on screen but gone before it was noticed. Errors now wait to be dismissed.
  const notify = (msg: string) => {
    clearToastTimer();
    setToast({ msg, isError: false });
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  };

  const notifyError = (msg: string) => {
    clearToastTimer();
    setToast({ msg, isError: true });
  };

  useEffect(() => clearToastTimer, []);

  const { data: campaign, isLoading, isError } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => getCampaign(campaignId, 14),
    enabled: Number.isFinite(campaignId),
    refetchInterval: (q) => {
      const status = (q.state.data as CampaignDetail | undefined)?.status;
      return status === "running" ? 3000 : false;
    },
  });

  // Same "reopen where they left off" behavior for scroll position: save it
  // continuously while on the page, restore it once the content has actually
  // rendered (restoring against the loading skeleton would land in the wrong
  // place since the page is still short).
  const scrollKey = `campaign:${campaignId}:scrollY`;
  useEffect(() => {
    const onScroll = () => {
      sessionStorage.setItem(scrollKey, String(window.scrollY));
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [scrollKey]);

  useEffect(() => {
    if (scrollRestored.current || isLoading || !campaign) return;
    scrollRestored.current = true;
    const saved = Number(sessionStorage.getItem(scrollKey) ?? 0);
    if (saved > 0) {
      requestAnimationFrame(() => window.scrollTo(0, saved));
    }
  }, [isLoading, campaign, scrollKey]);

  const { data: prospects } = useQuery({
    queryKey: ["campaign", campaignId, "prospects"],
    queryFn: () => getCampaignProspects(campaignId),
    enabled: Number.isFinite(campaignId),
    refetchInterval: () => (campaign?.status === "running" ? 4000 : false),
  });

  // Refresh prospects when a run finishes.
  useEffect(() => {
    if (campaign?.last_run_at) {
      qc.invalidateQueries({ queryKey: ["campaign", campaignId, "prospects"] });
      qc.invalidateQueries({ queryKey: ["campaign", campaignId, "drafts"] });
    }
  }, [campaign?.last_run_at, campaignId, qc]);

  const run = useMutation({
    mutationFn: (opts?: { resume?: boolean; skipDiscovery?: boolean }) =>
      runCampaign(campaignId, opts?.resume === true, opts?.skipDiscovery === true),
    onSuccess: (_d, opts) => {
      notify(
        opts?.resume && opts?.skipDiscovery
          ? "Continuing — finishing the existing backlog, no new prospects this time."
          : opts?.resume
            ? "Continuing — picking up where the last run left off."
            : "Run started — watch the funnel update below."
      );
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not start run.";
      notifyError(String(msg));
    },
  });

  const stopRun = useMutation({
    mutationFn: () => cancelCampaignRun(campaignId),
    onSuccess: () => {
      notify(
        "Stopping — the run halts after the current step and queued emails were cancelled."
      );
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not stop the run.";
      notifyError(String(msg));
    },
  });

  const pause = useMutation({
    mutationFn: (keepScheduled: boolean) => pauseCampaign(campaignId, keepScheduled),
    onSuccess: (_d, keepScheduled) => {
      notify(
        keepScheduled
          ? "Paused — no new people will be found. Already-scheduled emails will still send."
          : "Paused — daily runs off and scheduled emails cancelled. Nothing sends until you resume."
      );
      setPauseOpen(false);
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not pause the campaign.";
      notifyError(String(msg));
      setPauseOpen(false);
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  const resume = useMutation({
    mutationFn: () => resumeCampaign(campaignId),
    onSuccess: (d) => {
      notify(
        d.scheduled_count > 0
          ? `Resumed — daily runs back on, ${d.scheduled_count} email(s) re-scheduled.`
          : "Resumed — daily runs are back on."
      );
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: () => notifyError("Could not resume the campaign."),
  });

  const scheduleApproved = useMutation({
    mutationFn: () => scheduleApprovedEmails(campaignId),
    onSuccess: (d) => {
      notify(
        `Queued ${d.scheduled_count} email(s) at AI-picked times across the next few days.`
      );
      qc.invalidateQueries({ queryKey: ["campaign", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e: unknown) => {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Could not schedule the approved emails.";
      notifyError(String(msg));
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteCampaign(campaignId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      navigate("/agent");
    },
  });

  if (isLoading) return <Loading />;
  if (isError || !campaign) {
    return (
      <Card className="mx-auto max-w-lg p-6 text-center text-sm text-rose-700">
        Could not load this campaign.{" "}
        <Link to="/agent" className="font-medium underline">
          Back to campaigns
        </Link>
      </Card>
    );
  }

  const t = campaign.totals;
  const running = campaign.status === "running";
  const paused = campaign.paused;
  const queued = campaign.scheduled_count ?? 0;
  const readyToQueue = campaign.approved_unscheduled ?? 0;
  const maxFunnel = Math.max(t.discovered, t.qualified, t.drafted, t.sent, 1);
  const interrupted = !!campaign.interrupted_run;
  // A run that broke in its background thread reported nothing at all: the POST
  // had already returned 202, so no error toast could fire, and the only failure
  // banner was gated on the word "Interrupted". Worse, a discovery crash is
  // caught and logged as a run "error" while the run still finishes as
  // *completed* with every counter at zero — so the page looked completely
  // unchanged. That is exactly what "Run now does nothing" was. Treat an
  // outright failure, or a run that errored and accomplished nothing, as one
  // thing worth saying out loud at the top of the page.
  const lastRun = campaign.last_run;
  const lastRunErrors = lastRun?.errors ?? [];
  const lastRunDidNothing =
    !!lastRun &&
    !(lastRun.discovered ?? 0) &&
    !(lastRun.drafted ?? 0) &&
    !(lastRun.sent ?? 0);
  const failedRun =
    !running &&
    !interrupted &&
    lastRun &&
    (lastRun.status === "failed" || (lastRunErrors.length > 0 && lastRunDidNothing))
      ? lastRun
      : null;
  const canContinue =
    !running &&
    !paused &&
    (interrupted ||
      (!!campaign.last_run &&
        (campaign.last_run.qualified ?? 0) > (campaign.last_run.drafted ?? 0)));

  return (
    <div className="pb-12">
      {toast && (
        <div
          role={toast.isError ? "alert" : "status"}
          className={`fixed bottom-6 right-6 z-50 flex max-w-sm items-start gap-3 rounded-xl border px-4 py-3 text-sm shadow-lg ${
            toast.isError
              ? "border-rose-300 bg-rose-50 text-rose-900"
              : "border-slate-200 bg-white text-slate-800"
          }`}
        >
          <span className="min-w-0">{toast.msg}</span>
          {toast.isError && (
            <button
              type="button"
              onClick={() => setToast(null)}
              aria-label="Dismiss"
              className="-mr-1 shrink-0 rounded px-1.5 text-rose-500 hover:bg-rose-100 hover:text-rose-800"
            >
              ✕
            </button>
          )}
        </div>
      )}

      <Link to="/agent" className="text-sm text-violet-700 hover:underline">
        ← All campaigns
      </Link>

      {/* Header */}
      <div className="mt-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wider text-violet-600">
              {campaign.principal_name}
            </p>
            <h1 className="mt-1 text-2xl font-semibold text-slate-900">{campaign.name}</h1>
            {campaign.objective && (
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-600">
                {campaign.objective}
              </p>
            )}
            <p className="mt-3 text-xs text-slate-500">
              Last run {relativeTime(campaign.last_run_at)} · finds up to{" "}
              {campaign.discover_target} people/run · shares a {campaign.mailbox_daily_cap}/day
              mailbox cap · {campaign.auto_schedule ? "AI-timed sends" : "fixed send window"}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <Badge
              tone={
                paused || !campaign.enabled
                  ? "red"
                  : running
                    ? "blue"
                    : "green"
              }
            >
              {paused || !campaign.enabled
                ? "Paused"
                : running
                  ? "Running now"
                  : "Runs daily"}
            </Badge>
            <Badge tone={campaign.auto_send ? "green" : "purple"}>
              {campaign.auto_send ? "Autopilot" : "Review before send"}
            </Badge>
          </div>
        </div>

        {paused && (
          <div className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-800 ring-1 ring-rose-200">
            <p className="font-semibold">This campaign is paused</p>
            <p className="mt-1 text-rose-700">
              Daily finding is off
              {queued > 0
                ? ` · ${queued} scheduled email(s) will still send`
                : " · no emails are queued to send"}
              . Resume when you want it working again.
            </p>
          </div>
        )}

        {interrupted && !paused && !running && (
          <div className="mt-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-950 ring-1 ring-amber-200">
            <p className="font-semibold">Previous run was interrupted</p>
            <p className="mt-1 text-amber-800">
              The server restarted while research was in progress
              {campaign.interrupted_run?.discovered
                ? ` (${campaign.interrupted_run.discovered} people found)`
                : ""}
              . Press <strong>Continue</strong> to finish researching and drafting
              those people.
            </p>
          </div>
        )}

        {failedRun && !paused && (
          <div className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-950 ring-1 ring-rose-200">
            <p className="font-semibold">
              {lastRunDidNothing
                ? "The last run did not get anywhere"
                : "Last run failed"}
            </p>
            <p className="mt-1 text-rose-800">
              It stopped
              {failedRun.discovered
                ? ` after finding ${failedRun.discovered} people`
                : " before it found anyone"}
              . Nothing already in this campaign was lost — you can safely run it
              again.
            </p>
            {(failedRun.error_message || lastRunErrors[0]) && (
              <p className="mt-2 break-words font-mono text-xs text-rose-700">
                {failedRun.error_message || lastRunErrors[0]}
              </p>
            )}
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
          {paused ? (
            <Button onClick={() => resume.mutate()} disabled={resume.isPending}>
              {resume.isPending ? "Resuming…" : "Resume"}
            </Button>
          ) : running ? (
            <Button
              variant="secondary"
              className="!text-rose-600 hover:!bg-rose-50"
              onClick={() => {
                if (
                  window.confirm(
                    "Stop the current run? Finding/research stops. Already-scheduled emails are cancelled too."
                  )
                ) {
                  stopRun.mutate();
                }
              }}
              disabled={stopRun.isPending}
            >
              {stopRun.isPending ? "Stopping…" : "Stop run"}
            </Button>
          ) : (
            <>
              <Button onClick={() => run.mutate({ resume: false })} disabled={run.isPending}>
                Run now
              </Button>
              {canContinue && (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => run.mutate({ resume: true })}
                    disabled={run.isPending}
                    title="Finish people from earlier runs who never got a draft, plus find new ones too"
                  >
                    Continue
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => run.mutate({ resume: true, skipDiscovery: true })}
                    disabled={run.isPending}
                    title="Finish people from earlier runs who never got a draft — skip finding new people"
                  >
                    Continue without new prospects
                  </Button>
                </>
              )}
            </>
          )}

          {!paused && readyToQueue > 0 && (
            <Button
              variant="secondary"
              onClick={() => scheduleApproved.mutate()}
              disabled={scheduleApproved.isPending}
              title="Give every approved email an AI-picked send time"
            >
              {scheduleApproved.isPending
                ? "Scheduling…"
                : `Schedule ${readyToQueue} approved`}
            </Button>
          )}

          {!paused && (
            <div className="relative">
              <Button
                variant="secondary"
                className="!text-rose-600 hover:!bg-rose-50"
                onClick={() => setPauseOpen((v) => !v)}
                disabled={pause.isPending}
              >
                {pause.isPending ? "Pausing…" : "Pause ▾"}
              </Button>
              {pauseOpen && (
                <>
                  <button
                    type="button"
                    className="fixed inset-0 z-10 cursor-default"
                    aria-label="Close pause menu"
                    onClick={() => setPauseOpen(false)}
                  />
                  <div className="absolute left-0 top-full z-20 mt-1 w-80 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg">
                    <button
                      type="button"
                      className="block w-full px-4 py-3 text-left hover:bg-rose-50"
                      onClick={() => pause.mutate(false)}
                    >
                      <span className="block text-sm font-semibold text-rose-800">
                        Pause everything
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        Stop finding people, turn off daily runs, and cancel
                        {queued > 0 ? ` ${queued} scheduled email(s)` : " any scheduled emails"}.
                      </span>
                    </button>
                    <button
                      type="button"
                      className="block w-full border-t border-slate-100 px-4 py-3 text-left hover:bg-slate-50"
                      onClick={() => pause.mutate(true)}
                    >
                      <span className="block text-sm font-semibold text-slate-800">
                        Pause finding, keep sending
                      </span>
                      <span className="mt-0.5 block text-xs text-slate-500">
                        No new people. Already-scheduled emails still go out.
                      </span>
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          <Button variant="secondary" onClick={() => setEditing((v) => !v)}>
            {editing ? "Close editor" : "Edit"}
          </Button>
          <Button
            variant="ghost"
            className="!text-rose-600 hover:!bg-rose-50"
            onClick={() => {
              if (
                window.confirm(
                  "Delete this campaign? Its prospects, runs, and email drafts are permanently removed."
                )
              ) {
                remove.mutate();
              }
            }}
            disabled={running}
          >
            Delete
          </Button>
        </div>
      </div>

      {/* Live run progress or last-run traceability */}
      {campaign.status === "running" && campaign.current_run && (
        <div className="mt-6">
          <RunProgress run={campaign.current_run} live campaignId={campaignId} />
        </div>
      )}
      {campaign.status !== "running" &&
        campaign.last_run &&
        ((campaign.last_run.people?.length ?? 0) > 0 ||
          (campaign.last_run.errors?.length ?? 0) > 0 ||
          interrupted) && (
          <div className="mt-6">
            <RunProgress run={campaign.last_run} campaignId={campaignId} />
          </div>
        )}

      {editing && (
        <div className="mt-6">
          <EditPanel campaign={campaign} onClose={() => setEditing(false)} />
        </div>
      )}

      {/* Drafts awaiting approval (review-before-send mode) */}
      <PendingApproval campaign={campaign} onChanged={notify} />

      {/* Self-optimizing A/B: who we target + how we write */}
      <LearningPanel principalId={campaign.principal_id} />

      {/* KPIs */}
      <div className="mt-6 grid gap-3 sm:grid-cols-4">
        <Stat label="Emails sent" value={t.sent} sub="Last 14 days" accent="sky" />
        <Stat label="Replies" value={t.replies} sub="People who wrote back" accent="emerald" />
        <Stat
          label="Reply rate"
          value={`${Math.round((campaign.reply_rate || 0) * 100)}%`}
          sub="Replies ÷ sent"
          accent="violet"
        />
        <Stat label="Qualified" value={t.qualified} sub="Passed AI research" accent="amber" />
      </div>

      {/* Funnel */}
      <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Pipeline (last 14 days)</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Find people → score them → draft emails → send → follow up.
        </p>
        <div className="mt-4 grid gap-2 sm:grid-cols-5">
          {FUNNEL.map((step, i) => {
            const value = (t as unknown as Record<string, number>)[step.key] ?? 0;
            return (
              <div key={step.key} className="rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-3">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-200 text-xs font-bold text-slate-600">
                    {i + 1}
                  </span>
                  <span className="text-sm font-semibold text-slate-900">{step.label}</span>
                </div>
                <p className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
                <p className="text-[11px] text-slate-500">{step.desc}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Daily activity */}
      {campaign.days.length > 0 && (
        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900">Daily activity</h2>
          <div className="mt-4 space-y-2">
            {campaign.days.slice(0, 14).map((d) => (
              <div key={d.date} className="grid grid-cols-[5.5rem_1fr_auto] items-center gap-3 text-sm">
                <span className="text-xs font-medium text-slate-500">{d.date}</span>
                <div className="flex h-6 items-center gap-1">
                  <div
                    className="h-2 rounded-full bg-violet-400/80"
                    style={{ width: `${(d.discovered / maxFunnel) * 100}%`, minWidth: d.discovered ? 4 : 0 }}
                    title={`${d.discovered} found`}
                  />
                  <div
                    className="h-2 rounded-full bg-sky-500"
                    style={{ width: `${(d.sent / maxFunnel) * 100}%`, minWidth: d.sent ? 4 : 0 }}
                    title={`${d.sent} sent`}
                  />
                  {d.replies > 0 && (
                    <span className="ml-1 text-xs font-medium text-emerald-600">+{d.replies} replies</span>
                  )}
                </div>
                <span className="text-xs tabular-nums text-slate-400">{d.sent} sent</span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-slate-400">Violet = discovered · Blue = sent</p>
        </div>
      )}

      {/* Prospects */}
      <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="text-sm font-semibold text-slate-900">
            Prospects {prospects ? <span className="text-slate-400">({prospects.total})</span> : null}
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Everyone this campaign surfaced. Click a name for full research, the email sent, and any
            replies.
          </p>
        </div>
        {!prospects ? (
          <Loading />
        ) : prospects.items.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-slate-400">
            No prospects yet. Click <strong>Run now</strong> to start finding people.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-3">Person</th>
                  <th className="px-4 py-3">Relevance</th>
                  <th className="px-4 py-3">Email</th>
                  <th className="px-4 py-3">Activity</th>
                </tr>
              </thead>
              <tbody>
                {prospects.items.map((p) => (
                  <ProspectRow key={p.contact_id} p={p} campaignId={campaignId} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
