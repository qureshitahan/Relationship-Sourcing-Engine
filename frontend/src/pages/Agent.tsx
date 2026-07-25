import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { listCampaigns, listPrincipals } from "../api/client";
import { Badge, Button, Loading } from "../components/ui";
import type { CampaignSummary } from "../types";

type Tone = "green" | "blue" | "amber" | "slate";

const STATUS_TONE: Record<CampaignSummary["status"], Tone> = {
  running: "blue",
  ready: "green",
  draft: "amber",
};

function relativeTime(iso?: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function statusLabel(c: CampaignSummary): string {
  if (c.status === "running") return "Running now";
  if (c.paused) return "Paused";
  if (c.status === "draft") return "Needs setup";
  return c.enabled ? "Runs daily" : "Ready";
}

function CampaignCard({
  campaign,
  onOpen,
}: {
  campaign: CampaignSummary;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full flex-col rounded-2xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:border-violet-300 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="min-w-0 flex-1 truncate text-base font-semibold text-slate-900 group-hover:text-violet-700">
          {campaign.name}
        </h3>
        <Badge tone={STATUS_TONE[campaign.status]}>{statusLabel(campaign)}</Badge>
      </div>

      {campaign.objective_preview ? (
        <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-slate-500">
          {campaign.objective_preview}
        </p>
      ) : (
        <p className="mt-2 text-sm italic text-slate-400">
          No goal yet — open to describe who to reach.
        </p>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500">
        {campaign.status === "running" ? (
          <span className="flex items-center gap-1.5 font-medium text-sky-700">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-500" />
            {campaign.current_run_discovered ?? 0} found · {campaign.current_run_sent ?? 0} sent
          </span>
        ) : (
          <span>
            <span className="font-semibold text-slate-700">{campaign.totals_sent_14d}</span> sent ·{" "}
            <span className="font-semibold text-emerald-600">{campaign.totals_replies_14d}</span>{" "}
            replied <span className="text-slate-400">(14d)</span>
          </span>
        )}
        <span>Last run {relativeTime(campaign.last_run_at)}</span>
      </div>
    </button>
  );
}

export default function Agent() {
  const navigate = useNavigate();

  const { data: principals } = useQuery({
    queryKey: ["principals", "active"],
    queryFn: () => listPrincipals({ active: true }),
  });

  const { data: campaignList, isLoading } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => listCampaigns(14),
    refetchInterval: (q) => {
      const count = (q.state.data as { running_count?: number } | undefined)?.running_count;
      return count && count > 0 ? 5000 : 30000;
    },
  });

  const campaigns = campaignList?.items ?? [];

  // Group campaigns by principal, preserving principal order.
  const groups = useMemo(() => {
    const byPrincipal = new Map<number, { name: string; items: CampaignSummary[] }>();
    for (const p of principals?.items ?? []) {
      byPrincipal.set(p.id, { name: p.name, items: [] });
    }
    for (const c of campaigns) {
      if (!byPrincipal.has(c.principal_id)) {
        byPrincipal.set(c.principal_id, { name: c.principal_name, items: [] });
      }
      byPrincipal.get(c.principal_id)!.items.push(c);
    }
    return Array.from(byPrincipal.entries())
      .map(([principalId, g]) => ({ principalId, ...g }))
      .filter((g) => g.items.length > 0);
  }, [principals, campaigns]);

  const hasPrincipals = (principals?.items.length ?? 0) > 0;

  if (!hasPrincipals && !isLoading) {
    return (
      <div className="mx-auto max-w-lg py-16 text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-100 text-2xl">
          ◎
        </div>
        <h1 className="mt-4 text-xl font-semibold text-slate-900">Campaigns</h1>
        <p className="mt-2 text-sm text-slate-500">
          Add a principal first — campaigns run outreach on their behalf.
        </p>
        <Link to="/principals" className="mt-6 inline-block">
          <Button>Go to Principals</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="pb-12">
      {/* Hero */}
      <div className="relative -mx-4 mb-8 overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-violet-950 px-6 py-7 text-white sm:-mx-6 sm:px-8">
        <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-violet-500/20 blur-3xl" />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-violet-300/90">
              Autonomous outreach
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Campaigns</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-300">
              Each campaign targets one audience for one principal — it finds people, researches
              them, writes short emails, and sends follow-ups. A principal can run several
              campaigns at once (they share one daily send limit to protect the mailbox).
            </p>
          </div>
          <Button
            className="!bg-violet-600 !text-white hover:!bg-violet-500"
            onClick={() => navigate("/campaigns/new")}
          >
            + New campaign
          </Button>
        </div>
      </div>

      {isLoading ? (
        <Loading />
      ) : campaigns.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <h2 className="text-lg font-semibold text-slate-900">No campaigns yet</h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
            Create your first campaign. You&apos;ll describe a goal in plain language, the AI asks a
            few clarifying questions, then it starts finding and emailing people.
          </p>
          <Button className="mt-6" onClick={() => navigate("/campaigns/new")}>
            + Create a campaign
          </Button>
        </div>
      ) : (
        <div className="space-y-10">
          {groups.map((group) => (
            <section key={group.principalId}>
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">
                  {group.name}
                  <span className="ml-2 text-slate-400">
                    ({group.items.length} campaign{group.items.length === 1 ? "" : "s"})
                  </span>
                </h2>
                <button
                  type="button"
                  onClick={() => navigate(`/campaigns/new?principal=${group.principalId}`)}
                  className="text-xs font-medium text-violet-700 hover:underline"
                >
                  + Add campaign for {group.name.split(" ")[0]}
                </button>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {group.items.map((c) => (
                  <CampaignCard
                    key={c.id}
                    campaign={c}
                    onOpen={() => navigate(`/campaigns/${c.id}`)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
