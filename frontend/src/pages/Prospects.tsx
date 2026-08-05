import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import {
  getDiscoveryRun,
  listDiscoveryRuns,
  listPrincipals,
  listProspects,
  setProspectApproval,
  setProspectStatus,
  type ProspectFilters,
} from "../api/client";
import type { DiscoveryRun, Prospect } from "../types";
import WorkflowSteps from "../components/WorkflowSteps";
import { serialMapForProspects } from "../utils/prospectReady";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  ScoreBar,
  StatusBadge,
  Table,
  Td,
  Th,
} from "../components/ui";

const ROLE_OPTIONS = [
  "",
  // Tier 1 gatekeepers
  "board_search_consultant",
  "talent_partner",
  "operating_partner",
  // Tier 2 board peers
  "audit_committee",
  "governance",
  "independent_director",
  "board_member",
  // Tier 3 referrers
  "investor",
  "ceo",
  "founder",
  "executive",
  "decision_maker",
];

function outreachTone(status?: string | null): "green" | "blue" | "amber" | "slate" {
  if (status === "replied") return "green";
  if (status === "sent") return "blue";
  if (status === "approved") return "amber";
  return "slate";
}

type Stage = "all" | "review" | "approved" | "contacted" | "replied" | "rejected";

const STAGES: { key: Stage; label: string; tone: "slate" | "blue" | "amber" | "green" | "rose" }[] = [
  { key: "all", label: "All", tone: "slate" },
  { key: "review", label: "To review", tone: "slate" },
  { key: "approved", label: "Approved", tone: "amber" },
  { key: "contacted", label: "Contacted", tone: "blue" },
  { key: "replied", label: "Replied", tone: "green" },
  { key: "rejected", label: "Not a fit", tone: "rose" },
];

/** Which exclusive pipeline stage a prospect currently sits in (furthest reached). */
function stageOf(p: Prospect): Exclude<Stage, "all"> {
  // Outreach progression beats rejection — if we emailed them, they're Contacted
  // even if later marked not a fit.
  if (p.outreach_status === "replied") return "replied";
  if (p.outreach_status === "sent") return "contacted";
  if (p.status === "rejected") return "rejected";
  if (p.approved_for_outreach) return "approved";
  return "review";
}

const STAGE_CHIP: Record<string, { on: string; off: string }> = {
  slate: {
    on: "bg-slate-900 text-white",
    off: "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50",
  },
  amber: {
    on: "bg-amber-500 text-white",
    off: "bg-white text-amber-700 ring-1 ring-amber-200 hover:bg-amber-50",
  },
  blue: {
    on: "bg-blue-600 text-white",
    off: "bg-white text-blue-700 ring-1 ring-blue-200 hover:bg-blue-50",
  },
  green: {
    on: "bg-emerald-600 text-white",
    off: "bg-white text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-50",
  },
  rose: {
    on: "bg-rose-600 text-white",
    off: "bg-white text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50",
  },
};

function buildExportUrl(filters: ProspectFilters): string {
  const params = new URLSearchParams();
  if (filters.discovery_run_id != null)
    params.set("discovery_run_id", String(filters.discovery_run_id));
  if (filters.role_category) params.set("role_category", filters.role_category);
  if (filters.status) params.set("status", filters.status);
  if (filters.approved != null) params.set("approved", String(filters.approved));
  if (filters.search) params.set("search", filters.search);
  if (filters.sort) params.set("sort", filters.sort);
  return `/api/prospects/export.csv?${params.toString()}`;
}

/** Human-readable summary of what a discovery run actually searched for. */
function criteriaSummary(
  run?: DiscoveryRun
): { label: string; values: string[] }[] {
  const c = (run?.criteria ?? {}) as Record<string, unknown>;
  const asList = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter(Boolean).map(String) : [];
  const out: { label: string; values: string[] }[] = [];
  const push = (label: string, v: unknown) => {
    const list = asList(v);
    if (list.length) out.push({ label, values: list });
  };
  push("Board job postings", c.organization_job_titles);
  push("Titles", c.titles);
  push("Seniorities", c.seniorities);
  push("Email status", c.contact_email_status);
  push("Employer domains", c.organization_domains);
  push("Industries", c.industries);
  push("Geographies", c.geographies);
  push("Themes", c.themes);
  push("Keywords", c.keywords);
  return out;
}

type BulkProgress = {
  label: string;
  total: number;
  done: number;
  ok: number;
  skipped: number;
  failed: number;
};

export default function Prospects() {
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();
  const runId = searchParams.get("run")
    ? Number(searchParams.get("run"))
    : undefined;
  const [filters, setFilters] = useState<ProspectFilters>({ sort: "relevance" });
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const effectiveFilters: ProspectFilters = {
    ...filters,
    discovery_run_id: runId,
  };

  const { data, isLoading } = useQuery({
    queryKey: ["prospects", effectiveFilters],
    // Load the whole run (backend caps at 1000) so a run of 375 isn't silently
    // truncated to 250 — otherwise the hidden prospects can never be
    // researched/revealed/approved/emailed from this page.
    queryFn: () => listProspects({ ...effectiveFilters, limit: 1000 }),
  });

  const { data: run } = useQuery({
    queryKey: ["discovery-run", runId],
    queryFn: () => getDiscoveryRun(runId!),
    enabled: !!runId,
  });

  const { data: runs } = useQuery({
    queryKey: ["discovery-runs"],
    queryFn: () => listDiscoveryRuns({ limit: 25 }),
  });

  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });

  const principalId = run?.principal_id ?? principals?.items[0]?.id;
  const principalName =
    principals?.items.find((p) => p.id === principalId)?.name ?? "Principal";

  const items = useMemo(() => data?.items ?? [], [data]);
  const serialById = useMemo(
    () => serialMapForProspects(items, runId),
    [items, runId]
  );

  const stage = (searchParams.get("stage") as Stage) || "all";

  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = {
      all: items.length,
      review: 0,
      approved: 0,
      contacted: 0,
      replied: 0,
      rejected: 0,
    };
    for (const p of items) counts[stageOf(p)] += 1;
    return counts;
  }, [items]);

  const visibleItems = useMemo(
    () => (stage === "all" ? items : items.filter((p) => stageOf(p) === stage)),
    [items, stage]
  );

  const summary = criteriaSummary(run);

  // A prospect needs (re-)research if it has no score OR its latest insight was a
  // stub fallback (real Claude research didn't actually run — score is bogus).
  const needsResearch = (p: { relevance_score?: number | null; insight_provider?: string | null }) =>
    p.relevance_score == null ||
    (p.insight_provider ?? "").includes("fallback");

  const unresearchedCount = items.filter(needsResearch).length;
  const revealedCount = items.filter((p) => Boolean(p.email)).length;
  const approvedCount = items.filter((p) => p.approved_for_outreach).length;

  // --- Selection -----------------------------------------------------------
  const selectedProspects = items.filter((p) => selected.has(p.id));
  const allVisibleSelected =
    visibleItems.length > 0 && visibleItems.every((p) => selected.has(p.id));

  const toggleOne = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAll = () =>
    setSelected((prev) => {
      if (visibleItems.length > 0 && visibleItems.every((p) => prev.has(p.id)))
        return new Set();
      return new Set(visibleItems.map((p) => p.id));
    });

  const clearSelection = () => setSelected(new Set());

  const setStage = (s: Stage) => {
    if (s === "all") searchParams.delete("stage");
    else searchParams.set("stage", s);
    setSearchParams(searchParams);
    setSelected(new Set());
  };

  // Live, parallel progress (shared by research + bulk actions).
  const [progress, setProgress] = useState<
    | (BulkProgress & { running: boolean; current: string[]; qualified?: number; rejected?: number })
    | null
  >(null);
  const cancelRef = useRef(false);

  const CONCURRENCY = 2;

  /** Generic bulk runner over the selected prospects. */
  async function runBulk(
    label: string,
    targets: Prospect[],
    perItem: (p: Prospect) => Promise<"ok" | "skipped">
  ) {
    if (targets.length === 0) return;
    cancelRef.current = false;
    setBatchMessage(null);
    setProgress({
      label,
      running: true,
      total: targets.length,
      done: 0,
      ok: 0,
      skipped: 0,
      failed: 0,
      current: [],
    });

    const queue = [...targets];
    let completed = 0;

    const worker = async () => {
      while (queue.length > 0 && !cancelRef.current) {
        const prospect = queue.shift();
        if (!prospect) break;
        setProgress((pr) =>
          pr ? { ...pr, current: [...pr.current, prospect.name] } : pr
        );
        try {
          const result = await perItem(prospect);
          setProgress((pr) =>
            pr
              ? {
                  ...pr,
                  ok: pr.ok + (result === "ok" ? 1 : 0),
                  skipped: pr.skipped + (result === "skipped" ? 1 : 0),
                }
              : pr
          );
        } catch {
          setProgress((pr) => (pr ? { ...pr, failed: pr.failed + 1 } : pr));
        } finally {
          completed += 1;
          setProgress((pr) =>
            pr
              ? {
                  ...pr,
                  done: completed,
                  current: pr.current.filter((n) => n !== prospect.name),
                }
              : pr
          );
          if (completed % 3 === 0) {
            qc.invalidateQueries({ queryKey: ["prospects"] });
          }
        }
      }
    };

    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, targets.length) }, worker)
    );

    await Promise.all([
      qc.invalidateQueries({ queryKey: ["prospects"] }),
      qc.invalidateQueries({ queryKey: ["emails"] }),
      qc.invalidateQueries({ queryKey: ["stats"] }),
    ]);
    setProgress((pr) => {
      if (pr) {
        setBatchMessage(
          `${label} done: ${pr.ok} succeeded` +
            (pr.skipped ? `, ${pr.skipped} skipped` : "") +
            (pr.failed ? `, ${pr.failed} failed` : "") +
            `.`
        );
      }
      return null;
    });
    clearSelection();
  }

  function bulkApprove() {
    // No client-side eligibility pre-filter: approval itself now triggers
    // research + reveal on the backend for anyone who needs it (see
    // prospects.py set_approval), so every selected prospect gets a real
    // attempt instead of silently being skipped for "not ready yet".
    const targets = selectedProspects.filter((p) => !p.approved_for_outreach);
    if (targets.length === 0) {
      setBatchMessage("Selected prospects are already approved.");
      return;
    }
    runBulk("Approve", targets, async (p) => {
      await setProspectApproval(p.id, true);
      return "ok";
    });
  }

  function bulkReject() {
    const targets = selectedProspects;
    if (
      !window.confirm(
        `Mark ${targets.length} prospect(s) as "Not a fit"? They'll move to the Not a fit stage (you can restore them later).`
      )
    )
      return;
    runBulk("Mark not a fit", targets, async (p) => {
      if (p.status === "rejected") return "skipped";
      await setProspectStatus(p.id, "rejected");
      return "ok";
    });
  }

  function bulkRestore() {
    runBulk("Restore to review", selectedProspects, async (p) => {
      if (p.status !== "rejected") return "skipped";
      await setProspectStatus(p.id, "review");
      return "ok";
    });
  }

  const setRunFilter = (value: string) => {
    if (value) searchParams.set("run", value);
    else searchParams.delete("run");
    setSearchParams(searchParams);
    clearSelection();
  };

  const clearRunFilter = () => setRunFilter("");

  const busy = Boolean(progress?.running);

  return (
    <div>
      <WorkflowSteps active={2} />
      <PageHeader
        title={runId ? `Prospects — Run #${runId}` : "Prospects"}
        subtitle={
          runId
            ? `${items.length} people · Principal: ${principalName} · Research, reveal, then approve who gets an email.`
            : "Pick a discovery run to review people, research them, and approve for outreach."
        }
        actions={
          runId && approvedCount > 0 ? (
            <Link to={`/emails?run=${runId}`}>
              <Button>Draft emails for {approvedCount} approved →</Button>
            </Link>
          ) : undefined
        }
      />

      {/* Pipeline stage bar */}
      <div className="mb-4 flex flex-wrap gap-2">
        {STAGES.map((s) => {
          const active = stage === s.key;
          const chip = STAGE_CHIP[s.tone];
          return (
            <button
              key={s.key}
              type="button"
              onClick={() => setStage(s.key)}
              className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                active ? chip.on : chip.off
              }`}
            >
              {s.label}
              <span
                className={`rounded-full px-1.5 text-xs ${
                  active ? "bg-white/25" : "bg-slate-100 text-slate-500"
                }`}
              >
                {stageCounts[s.key] ?? 0}
              </span>
            </button>
          );
        })}
      </div>

      {/* Run picker — categorize prospects by discovery run */}
      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium text-slate-600">
            Discovery run
          </label>
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={runId ?? ""}
            onChange={(e) => setRunFilter(e.target.value)}
          >
            <option value="">All runs</option>
            {runs?.items.map((r) => {
              const jobTitles = (r.criteria as Record<string, unknown> | null)
                ?.organization_job_titles as string[] | undefined;
              const tag =
                jobTitles && jobTitles.length
                  ? `${jobTitles.length} board job signal${jobTitles.length > 1 ? "s" : ""}`
                  : "broad search";
              return (
                <option key={r.id} value={r.id}>
                  Run #{r.id} · {r.people_imported ?? 0} prospects · {tag} ·{" "}
                  {new Date(r.created_at).toLocaleDateString()}
                </option>
              );
            })}
          </select>
          {runId && (
            <button
              type="button"
              onClick={clearRunFilter}
              className="text-sm font-medium text-blue-700 underline hover:text-blue-900"
            >
              Show all prospects
            </button>
          )}
        </div>

        {runId && summary.length > 0 && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Run #{runId} searched for
            </div>
            <div className="space-y-1.5">
              {summary.map((g) => (
                <div key={g.label} className="flex flex-wrap items-start gap-2 text-sm">
                  <span className="min-w-[120px] font-medium text-slate-500">
                    {g.label}
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    {g.values.map((v) => (
                      <Badge key={v} tone="slate">
                        {v}
                      </Badge>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      {runId && (
        <div className="mb-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-800">
            <span>
              Run <span className="font-semibold">#{runId}</span> ·{" "}
              {items.length} prospects · Researched {items.length - unresearchedCount}/
              {items.length} · Revealed {revealedCount}/{items.length} · Approved{" "}
              {approvedCount}
            </span>
          </div>
        </div>
      )}

      {progress?.running && (
        <div className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="font-medium text-slate-700">
              {progress.label}… {progress.done} / {progress.total}
            </span>
            <span className="text-slate-500">
              {progress.label.startsWith("Research")
                ? `${progress.qualified ?? 0} qualified · ${progress.rejected ?? 0} rejected`
                : `${progress.ok} ok · ${progress.skipped} skipped`}
              {progress.failed > 0 && ` · ${progress.failed} failed`}
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full bg-slate-900 transition-all"
              style={{
                width: `${Math.round(
                  (progress.done / Math.max(1, progress.total)) * 100
                )}%`,
              }}
            />
          </div>
          {progress.current.length > 0 && (
            <div className="mt-2 text-xs text-slate-400">
              In progress: {progress.current.join(", ")}
            </div>
          )}
        </div>
      )}

      {batchMessage && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
          {batchMessage}
        </div>
      )}

      {runId && unresearchedCount > 0 && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-900">
          <strong>{unresearchedCount} not yet researched.</strong> Select rows and click{" "}
          <em>Approve for outreach</em> — research and contact reveal happen automatically.
        </div>
      )}

      {runId && approvedCount > 0 && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900">
          {approvedCount} approved for outreach.{" "}
          <Link to={`/emails?run=${runId}`} className="font-medium underline">
            Go to Drafts → Run #{runId}
          </Link>{" "}
          to write and send emails.
        </div>
      )}

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap gap-3">
          <input
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            placeholder="Search name…"
            value={filters.search ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={filters.role_category ?? ""}
            onChange={(e) =>
              setFilters((f) => ({ ...f, role_category: e.target.value || undefined }))
            }
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {r ? r.replace(/_/g, " ") : "All roles"}
              </option>
            ))}
          </select>
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={filters.min_relevance ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                min_relevance: e.target.value ? Number(e.target.value) : undefined,
              }))
            }
          >
            <option value="">All relevance</option>
            <option value="50">Qualified only (≥50)</option>
            <option value="70">Strong fit only (≥70)</option>
          </select>
          <select
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            value={filters.sort ?? "relevance"}
            onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value }))}
          >
            <option value="relevance">Sort: relevance</option>
            <option value="usefulness">Sort: role fit</option>
            <option value="recent">Sort: recent</option>
          </select>
          <a
            href={buildExportUrl(effectiveFilters)}
            className="ml-auto inline-flex items-center rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Export CSV
          </a>
        </div>
      </Card>

      {/* Bulk action toolbar */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 mb-4 rounded-lg border border-slate-300 bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="font-medium">{selected.size} selected</span>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={bulkApprove} disabled={busy}>
              Approve for outreach
            </Button>
            {stage === "rejected" ? (
              <Button variant="secondary" onClick={bulkRestore} disabled={busy}>
                Restore to review
              </Button>
            ) : (
              <Button variant="danger" onClick={bulkReject} disabled={busy}>
                Not a fit
              </Button>
            )}
            <button
              type="button"
              onClick={clearSelection}
              className="px-2 text-slate-300 underline hover:text-white"
            >
              Clear
            </button>
          </div>
          </div>
        </div>
      )}

      <Card>
        {isLoading ? (
          <Loading />
        ) : items.length === 0 ? (
          <EmptyState message="No prospects yet. Run discovery to populate." />
        ) : visibleItems.length === 0 ? (
          <EmptyState message="No prospects in this stage. Pick another stage above." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th className="w-10">#</Th>
                <Th className="w-10">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAll}
                    aria-label="Select all"
                  />
                </Th>
                <Th>Name</Th>
                <Th>Title</Th>
                <Th>Role</Th>
                <Th>Email</Th>
                <Th>Links</Th>
                <Th>Relevance</Th>
                <Th>Outreach</Th>
                <Th>Status</Th>
                <Th>Approved</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleItems.map((p) => (
                <tr
                  key={p.id}
                  className={selected.has(p.id) ? "bg-blue-50" : "hover:bg-slate-50"}
                >
                  <Td className="text-slate-400 tabular-nums">
                    {serialById.get(p.id) ?? (p.discovery_run_id ? "…" : "—")}
                  </Td>
                  <Td>
                    <input
                      type="checkbox"
                      checked={selected.has(p.id)}
                      onChange={() => toggleOne(p.id)}
                      aria-label={`Select ${p.name}`}
                    />
                  </Td>
                  <Td>
                    <Link
                      to={`/prospects/${p.id}${runId ? `?run=${runId}` : ""}`}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {p.name}
                    </Link>
                  </Td>
                  <Td>{p.title ?? "—"}</Td>
                  <Td>
                    <div className="flex flex-wrap items-center gap-1">
                      {p.role_category ? (
                        <Badge tone="blue">{p.role_category.replace(/_/g, " ")}</Badge>
                      ) : (
                        "—"
                      )}
                      {p.rank_reason?.includes("active board") && (
                        <Badge tone="green">hiring board</Badge>
                      )}
                    </div>
                  </Td>
                  <Td>
                    {p.email ? (
                      <span className="text-xs text-slate-600">{p.email}</span>
                    ) : (
                      <Badge tone="amber">not revealed</Badge>
                    )}
                  </Td>
                  <Td>
                    {p.linkedin_url ? (
                      <a
                        href={p.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs font-medium text-blue-600 hover:underline"
                      >
                        LinkedIn ↗
                      </a>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </Td>
                  <Td>
                    {(p.insight_provider ?? "").includes("fallback") ? (
                      <Badge tone="amber">research failed — retry</Badge>
                    ) : p.relevance_score != null ? (
                      <ScoreBar value={p.relevance_score} />
                    ) : (
                      <Badge tone="slate">not researched</Badge>
                    )}
                  </Td>
                  <Td>
                    {p.outreach_status ? (
                      <Badge tone={outreachTone(p.outreach_status)}>
                        {p.outreach_status === "replied"
                          ? "replied ✓"
                          : p.outreach_status === "sent"
                          ? "contacted"
                          : p.outreach_status}
                      </Badge>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={p.status} />
                  </Td>
                  <Td>
                    {p.approved_for_outreach ? (
                      <Badge tone="green">Approved</Badge>
                    ) : (
                      <Badge>Pending</Badge>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {data && (
          <div className="border-t border-slate-100 px-4 py-2 text-xs text-slate-400">
            {stage === "all"
              ? `${data.total} prospect(s)`
              : `${visibleItems.length} in ${
                  STAGES.find((s) => s.key === stage)?.label ?? stage
                } · ${data.total} total`}
          </div>
        )}
      </Card>
    </div>
  );
}
