import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  getAgentConfig,
  cancelRunJob,
  deleteDiscoveryRun,
  getDiscoveryRun,
  listDiscoveryRuns,
  listPrincipals,
  planAgentSearch,
  resetPipeline,
  revealRunEmails,
  runDiscovery,
  sendRunEmails,
  sendRunLinkedin,
  type DiscoveryRunPayload,
} from "../api/client";
import { MultiSelectDropdown } from "../components/MultiSelectDropdown";
import {
  DEFAULT_GEOGRAPHIES,
  GEOGRAPHY_OPTIONS,
  GEOGRAPHY_SUGGESTIONS,
  INDUSTRY_OPTIONS,
  SENIORITY_OPTIONS,
} from "../constants/discoveryOptions";
import WorkflowSteps from "../components/WorkflowSteps";
import { usePersistedState } from "../hooks/usePersistedState";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
  Table,
  Td,
  Th,
} from "../components/ui";
import type { AgentPlan } from "../types";

// Only the essential filters are kept in the UI (Industry, Seniorities,
// Location, Max prospects) — every other Apollo filter (titles, company
// types, keywords, themes, domains, employee range, job-posting signals) is
// intentionally omitted so discovery casts the widest possible net. The
// backend treats all of those as optional and simply skips the filter when
// absent (see apollo.py _build_people_search_payload), so leaving them out
// broadens matches instead of breaking anything.
function applyPlanCriteria(
  c: Record<string, unknown>,
  setters: {
    setSeniorities: (v: string[]) => void;
    setIndustries: (v: string[]) => void;
    setGeographies: (v: string[]) => void;
    setPeopleLimit: (v: string) => void;
  }
) {
  if (Array.isArray(c.seniorities)) setters.setSeniorities(c.seniorities as string[]);
  if (Array.isArray(c.industries)) setters.setIndustries(c.industries as string[]);
  if (Array.isArray(c.geographies)) setters.setGeographies(c.geographies as string[]);
  if (c.people_limit != null) setters.setPeopleLimit(String(c.people_limit));
}

export default function Discover() {
  const qc = useQueryClient();
  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });
  const { data: runs, isLoading } = useQuery({
    queryKey: ["discovery-runs"],
    queryFn: () => listDiscoveryRuns({ limit: 10 }),
    // Keep the list live while any run is discovering or running a bulk job.
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      const busy = items.some(
        (r) =>
          r.status === "pending" ||
          r.status === "running" ||
          r.job_status === "running"
      );
      return busy ? 2500 : false;
    },
  });

  // Persisted (sessionStorage) rather than plain useState: navigating to
  // another module and back should not wipe a filter set, an in-progress AI
  // plan, or the run being watched — React Router unmounts this component
  // on route change, which would otherwise reset all of it.
  const [principalId, setPrincipalId] = usePersistedState<number | "">(
    "discover:principalId",
    ""
  );
  const [objective, setObjective] = usePersistedState("discover:objective", "");
  const [plan, setPlan] = usePersistedState<AgentPlan | null>("discover:plan", null);
  const [planAnswers, setPlanAnswers] = usePersistedState<Record<string, string>>(
    "discover:planAnswers",
    {}
  );
  const [planNote, setPlanNote] = usePersistedState<string | null>(
    "discover:planNote",
    null
  );
  // Id of the run kicked off by the "Run discovery" button, polled until done.
  const [activeRunId, setActiveRunId] = usePersistedState<number | null>(
    "discover:activeRunId",
    null
  );
  const activeRun = useQuery({
    queryKey: ["discovery-run", activeRunId],
    queryFn: () => getDiscoveryRun(activeRunId as number),
    enabled: activeRunId != null,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      const js = query.state.data?.job_status;
      // Keep polling through the automatic post-import reveal pass (job_status
      // stays "running" briefly after status flips to "completed") so the
      // banner below can show live email/LinkedIn reveal progress.
      if (s !== "completed" && s !== "failed") return 2000;
      return js === "running" ? 2000 : false;
    },
  });

  const [industries, setIndustries] = usePersistedState<string[]>("discover:industries", [
    "Healthcare",
    "Healthcare Services",
  ]);
  const [geographies, setGeographies] = usePersistedState<string[]>(
    "discover:geographies",
    DEFAULT_GEOGRAPHIES
  );
  const [seniorities, setSeniorities] = usePersistedState<string[]>(
    "discover:seniorities",
    []
  );
  const [peopleLimit, setPeopleLimit] = usePersistedState("discover:peopleLimit", "100");

  const { data: agentConfig } = useQuery({
    queryKey: ["agent", "config", principalId],
    queryFn: () => getAgentConfig(Number(principalId)),
    enabled: principalId !== "",
  });

  useEffect(() => {
    if (!agentConfig || principalId === "") return;
    if (agentConfig.discover_target) {
      setPeopleLimit(String(agentConfig.discover_target));
    }
  }, [agentConfig, principalId, setPeopleLimit]);

  const planSetters = {
    setSeniorities,
    setIndustries,
    setGeographies,
    setPeopleLimit,
  };

  const planSearch = useMutation({
    mutationFn: () =>
      planAgentSearch({
        objective_prompt: objective,
        principal_id: principalId !== "" ? Number(principalId) : undefined,
        clarifying_answers: Object.keys(planAnswers).length ? planAnswers : undefined,
      }),
    onSuccess: (p) => {
      setPlan(p);
      applyPlanCriteria(p.criteria, planSetters);
      if (p.questions.length > 0) {
        const init: Record<string, string> = {};
        for (const q of p.questions) {
          init[q.id] = planAnswers[q.id] ?? q.suggested ?? "";
        }
        setPlanAnswers(init);
        setPlanNote("Answer the questions below, then click Plan again to fill the filters.");
      } else {
        setPlanNote(p.rationale || "Filters updated from your objective — review and edit below.");
      }
    },
    onError: (err) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setPlanNote(
        detail
          ? `Plan failed: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`
          : "Could not plan the search. Select a principal and ensure the backend is running."
      );
    },
  });

  const run = useMutation({
    mutationFn: () => {
      // Only the essential filters are sent — Industry, Seniorities, Location
      // (geographies), Max prospects. Every other Apollo filter is left unset
      // on purpose so discovery casts the widest net (see applyPlanCriteria
      // above for why omitting is safe rather than restrictive).
      const payload: DiscoveryRunPayload = {
        principal_id: Number(principalId),
        industries,
        geographies,
        seniorities,
        people_limit: peopleLimit.trim() ? Number(peopleLimit) : 100,
        people_first: true,
        auto_expand_to_target: true,
        // auto_process stays false: skip the slow per-prospect LLM research step.
        // Email + LinkedIn reveal now runs automatically server-side right after
        // import (see discovery_jobs.py _discovery_worker) regardless of this
        // flag, so prospects still come back with contact details filled in.
        auto_process: false,
        search_goal:
          objective.trim() ||
          (plan?.rationale && !plan.questions.length ? plan.rationale : undefined),
      };
      return runDiscovery(payload);
    },
    onSuccess: (result) => {
      // Discovery now runs in the background. We get back a pending run; poll it.
      qc.invalidateQueries({ queryKey: ["discovery-runs"] });
      setActiveRunId(result.id);
      setPlanNote(
        `Run #${result.id} started — discovering prospects in the background. ` +
          "You can keep working; progress shows below."
      );
    },
  });

  // When the polled run finishes, refresh the dependent views.
  useEffect(() => {
    const s = activeRun.data?.status;
    if (s === "completed" || s === "failed") {
      qc.invalidateQueries({ queryKey: ["discovery-runs"] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    }
  }, [activeRun.data?.status, qc]);

  // Run-level bulk jobs (draft/send emails, send LinkedIn) — all background.
  const invalidateRuns = () =>
    qc.invalidateQueries({ queryKey: ["discovery-runs"] });
  const runReveal = useMutation({
    mutationFn: (runId: number) => revealRunEmails(runId),
    onSuccess: invalidateRuns,
  });
  const runSendEmail = useMutation({
    mutationFn: (runId: number) => sendRunEmails(runId),
    onSuccess: invalidateRuns,
  });
  const runSendLinkedin = useMutation({
    mutationFn: (runId: number) => sendRunLinkedin(runId),
    onSuccess: invalidateRuns,
  });
  const runCancel = useMutation({
    mutationFn: (runId: number) => cancelRunJob(runId),
    onSuccess: invalidateRuns,
  });

  const clearHistory = useMutation({
    mutationFn: () => resetPipeline(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-runs"] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["emails"] });
    },
  });

  const deleteRun = useMutation({
    mutationFn: (runId: number) => deleteDiscoveryRun(runId),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["discovery-runs"] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["emails"] });
      setPlanNote(result.message);
    },
    onError: (err) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setPlanNote(
        detail
          ? `Could not delete run: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`
          : "Could not delete run."
      );
    },
  });

  const hasPrincipals = principals && principals.items.length > 0;

  return (
    <div>
      <WorkflowSteps active={1} />
      <PageHeader
        title="Discover"
        subtitle="Search Apollo for the right people by title, industry, and company type. Describe your objective for AI-assisted setup, or configure filters manually. Each plausible fit can get light per-person research before outreach."
      />

      <Card className="mb-6 p-5">
        {!hasPrincipals ? (
          <div className="text-sm text-slate-500">
            Create a{" "}
            <Link to="/principals" className="font-medium text-slate-900 underline">
              principal
            </Link>{" "}
            first — discovery and relevance scoring are always relative to a principal.
          </div>
        ) : (
          <>
            <label className="mb-6 block max-w-md">
              <span className="text-xs font-medium text-slate-600">Principal</span>
              <select
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                value={principalId}
                onChange={(e) =>
                  setPrincipalId(e.target.value ? Number(e.target.value) : "")
                }
              >
                <option value="">Select a principal…</option>
                {principals!.items.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <div className="mb-6 rounded-xl border border-indigo-200 bg-indigo-50/50 p-4">
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-800">
                AI-assisted setup (optional)
              </div>
              <p className="mb-3 text-xs text-indigo-900/80">
                Describe your goal in plain English. Claude suggests industries, seniorities,
                and locations — then fills the filter fields below. You can edit everything
                before running. First click may ask 2–3 clarifying questions; click Plan
                again to apply.
              </p>
              <textarea
                className="w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm focus:border-indigo-400 focus:outline-none"
                rows={3}
                placeholder="e.g. Find PE operating partners who place independent directors on healthcare boards in the US…"
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
              />
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Button
                  variant="secondary"
                  onClick={() => planSearch.mutate()}
                  disabled={
                    planSearch.isPending ||
                    objective.trim().length < 10 ||
                    principalId === ""
                  }
                >
                  {planSearch.isPending
                    ? "Planning…"
                    : plan?.questions.length
                      ? "Finalize plan"
                      : "Plan with AI"}
                </Button>
                {principalId === "" && (
                  <span className="text-xs text-indigo-700">Select a principal first.</span>
                )}
              </div>
              {planNote && (
                <p className="mt-2 text-xs text-indigo-800">{planNote}</p>
              )}
              {plan && plan.questions.length > 0 && (
                <div className="mt-4 space-y-3 border-t border-indigo-200 pt-4">
                  {plan.questions.map((q) => (
                    <label key={q.id} className="block">
                      <span className="text-xs font-medium text-indigo-900">{q.prompt}</span>
                      <input
                        className="mt-1 w-full rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-sm"
                        value={planAnswers[q.id] ?? ""}
                        placeholder={q.suggested}
                        onChange={(e) =>
                          setPlanAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                        }
                      />
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-6">
              {/* Deliberately minimal: only Industry, Seniorities, Location, and
                  Max prospects. Every other Apollo filter (titles, company types,
                  keywords, themes, domains, employee range, job-posting signals)
                  narrows the match pool, so it's left unset to maximize how many
                  prospects a run can return — see applyPlanCriteria above. */}
              <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Search filters
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                  <MultiSelectDropdown
                    label="Industry"
                    hint="Broad keyword tags — pick from the list or type any industry. Prefer 1-3 wide buckets for the most matches."
                    selected={industries}
                    onChange={setIndustries}
                    options={INDUSTRY_OPTIONS}
                    placeholder="Search industries or type custom…"
                  />
                  <MultiSelectDropdown
                    label="Seniorities"
                    hint="All 11 Apollo seniority levels. Optional — leave empty for the broadest search."
                    selected={seniorities}
                    onChange={setSeniorities}
                    options={SENIORITY_OPTIONS}
                    allowCustom={false}
                    placeholder="Select seniority levels…"
                  />
                  <MultiSelectDropdown
                    label="Location"
                    hint="Where the person is based (not employer HQ). US default; all 50 states listed. Type any city, state, or country."
                    selected={geographies}
                    onChange={setGeographies}
                    options={GEOGRAPHY_OPTIONS}
                    suggestions={GEOGRAPHY_SUGGESTIONS}
                    placeholder="Search locations or type custom…"
                  />
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">
                      Max prospects (people)
                    </span>
                    <input
                      type="text"
                      inputMode="numeric"
                      className="mt-1 w-32 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      value={peopleLimit}
                      placeholder="100"
                      onChange={(e) => setPeopleLimit(e.target.value.replace(/\D/g, ""))}
                    />
                  </label>
                </div>
              </div>
            </div>

            <p className="mt-4 text-xs text-slate-500">
              Discovery lists prospects only — research relevance scores on the{" "}
              <Link to="/prospects" className="font-medium text-slate-700 underline">
                Prospects
              </Link>{" "}
              page after reviewing who came back.
            </p>

            <div className="mt-4 flex items-center gap-3">
              <Button
                onClick={() => run.mutate()}
                disabled={
                  !principalId ||
                  run.isPending ||
                  activeRun.data?.status === "pending" ||
                  activeRun.data?.status === "running"
                }
              >
                {run.isPending ||
                activeRun.data?.status === "pending" ||
                activeRun.data?.status === "running"
                  ? "Discovering…"
                  : "Run discovery"}
              </Button>
              <span className="text-xs text-slate-400">
                {industries.length} industry filter(s)
                {seniorities.length > 0 && ` · ${seniorities.length} seniority filter(s)`}
              </span>
            </div>

            {run.isError && (
              <div className="mt-3 text-sm text-rose-600">
                Discovery failed:{" "}
                {(run.error as { response?: { data?: { detail?: string } } })?.response
                  ?.data?.detail ||
                  (run.error as Error)?.message ||
                  "Unknown error — check backend logs."}
              </div>
            )}
            {(activeRun.data?.provider_warnings?.length ?? 0) > 0 && (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                <div className="font-medium">Provider issue detected</div>
                <p className="mt-1">
                  {activeRun.data!.provider_warnings!.join(" ")}
                </p>
                <p className="mt-1 text-rose-700">
                  This is why prospects may be missing emails/LinkedIn URLs even
                  though discovery "completed" — it isn't caused by your filters.
                </p>
              </div>
            )}
            {(activeRun.data?.status === "pending" ||
              activeRun.data?.status === "running") && (
              <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
                <div className="font-medium">
                  Discovering prospects in the background…
                </div>
                <p className="mt-1 text-blue-800">
                  This can take several minutes for a large target count. You can leave
                  this page — the run keeps going and appears in Recent discovery runs.
                </p>
              </div>
            )}
            {activeRun.data?.status === "failed" && (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                <div className="font-medium">Discovery failed</div>
                <p className="mt-1">
                  {activeRun.data.error_message ||
                    "Apollo returned an error. Check APOLLO_API_KEY in backend .env."}
                </p>
              </div>
            )}
            {activeRun.data?.status === "completed" && (
              <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-700">
                <div className="font-medium">
                  Run #{activeRun.data.id} complete via {activeRun.data.provider}
                </div>
                {activeRun.data.provider === "stub" && (
                  <p className="mt-2 text-amber-800">
                    This run used the offline stub provider (mock data), not Apollo.
                    Set <code className="text-xs">DISCOVERY_PROVIDER=apollo</code> in backend{" "}
                    <code className="text-xs">.env</code>, confirm{" "}
                    <code className="text-xs">APOLLO_API_KEY</code> is set, and restart the
                    backend.
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-2">
                  <Badge tone="blue">{activeRun.data.people_imported ?? 0} prospects</Badge>
                  <Badge>{activeRun.data.duplicates ?? 0} duplicates skipped</Badge>
                  {(activeRun.data.people_imported ?? 0) <
                    Number(
                      (activeRun.data.criteria as { people_limit?: number } | undefined)
                        ?.people_limit ?? peopleLimit
                    ) && <Badge tone="amber">Below target count</Badge>}
                </div>
                {(activeRun.data.criteria as { expansion_summary?: string } | undefined)
                  ?.expansion_summary && (
                  <p className="mt-2 text-xs text-slate-600">
                    {
                      (activeRun.data.criteria as { expansion_summary: string })
                        .expansion_summary
                    }
                  </p>
                )}
                {(activeRun.data.people_imported ?? 0) === 0 && (
                  <p className="mt-2 text-amber-700">
                    Apollo returned no matches. Try fewer industry filters (one broad bucket),
                    widen the employee range, or verify your API key is valid.
                  </p>
                )}
                {activeRun.data.job_kind === "reveal" && (
                  <p className="mt-2 text-slate-600">
                    {activeRun.data.job_status === "running"
                      ? `Fetching emails & LinkedIn URLs… ${activeRun.data.job_done ?? 0}/${
                          activeRun.data.job_total ?? 0
                        }`
                      : "Emails & LinkedIn URLs fetched (best effort — Apollo doesn't have " +
                        "contact details for every prospect)."}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-4">
                  <Link
                    to={`/prospects?run=${activeRun.data.id}`}
                    className="font-medium text-slate-900 underline"
                  >
                    Review {activeRun.data.people_imported ?? 0} prospects →
                  </Link>
                </div>
              </div>
            )}
          </>
        )}
      </Card>

      <Card>
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <div className="text-sm font-semibold text-slate-700">Recent discovery runs</div>
          {runs && runs.items.length > 0 && (
            <Button
              variant="ghost"
              onClick={() => {
                if (
                  window.confirm(
                    "Clear all discovery run history, organizations, and any remaining prospects/drafts?"
                  )
                ) {
                  clearHistory.mutate();
                }
              }}
              disabled={clearHistory.isPending}
            >
              {clearHistory.isPending ? "Clearing…" : "Clear history"}
            </Button>
          )}
        </div>
        {isLoading ? (
          <Loading />
        ) : !runs || runs.items.length === 0 ? (
          <EmptyState message="No discovery runs yet." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Provider</Th>
                <Th>Prospects</Th>
                <Th>Researched</Th>
                <Th>Status</Th>
                <Th>Bulk outreach</Th>
                <Th>When</Th>
                <Th />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {runs.items.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50">
                  <Td>#{r.id}</Td>
                  <Td>{r.provider}</Td>
                  <Td>
                    <Link
                      to={`/prospects?run=${r.id}`}
                      className="text-blue-600 hover:underline"
                    >
                      {r.people_imported ?? 0}
                    </Link>
                  </Td>
                  <Td>{r.insights_generated ?? 0}</Td>
                  <Td>
                    <StatusBadge status={r.status} />
                  </Td>
                  <Td>
                    {r.job_status === "running" ? (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-600">
                          {r.job_kind === "reveal"
                            ? "Revealing"
                            : r.job_kind === "draft_email"
                            ? "Drafting"
                            : r.job_kind === "send_email"
                            ? "Sending email"
                            : r.job_kind === "send_linkedin"
                            ? "Sending LinkedIn"
                            : "Working"}{" "}
                          {r.job_done ?? 0}/{r.job_total ?? 0}
                        </span>
                        <button
                          type="button"
                          className="text-xs font-medium text-rose-600 hover:underline"
                          onClick={() => runCancel.mutate(r.id)}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-2 text-xs font-medium">
                        <button
                          type="button"
                          className="text-emerald-700 hover:underline disabled:opacity-40"
                          disabled={r.status !== "completed"}
                          title="Reveal email/phone for every prospect in this run (Apollo, background). Uses credits."
                          onClick={() => {
                            if (
                              window.confirm(
                                `Reveal email for all unrevealed prospects in run #${r.id}? ` +
                                  "Runs in the background and uses Apollo credits."
                              )
                            )
                              runReveal.mutate(r.id);
                          }}
                        >
                          Reveal emails
                        </button>
                        <button
                          type="button"
                          className="text-blue-700 hover:underline disabled:opacity-40"
                          disabled={r.status !== "completed"}
                          title="QUANTITY: email everyone in this run with a revealed address (drafts on the fly, no research/approval needed), paced"
                          onClick={() => {
                            if (
                              window.confirm(
                                `Send emails to EVERYONE in run #${r.id} who has a revealed ` +
                                  "email address? This is the quantity path — it drafts on the " +
                                  "fly and sends in the background (no research or approval " +
                                  "step). For the personalized/quality path, use the Prospects page."
                              )
                            )
                              runSendEmail.mutate(r.id);
                          }}
                        >
                          Send email
                        </button>
                        <button
                          type="button"
                          className="text-purple-700 hover:underline disabled:opacity-40"
                          disabled={r.status !== "completed"}
                          title="Approve + send all LinkedIn messages for this run (background, paced)"
                          onClick={() => {
                            if (
                              window.confirm(
                                `Send all LinkedIn messages for run #${r.id}? ` +
                                  "Paced to protect the account. Generate them on the LinkedIn page first."
                              )
                            )
                              runSendLinkedin.mutate(r.id);
                          }}
                        >
                          Send LinkedIn
                        </button>
                      </div>
                    )}
                    {r.job_status === "failed" && r.job_error && (
                      <div className="mt-1 max-w-[16rem] text-xs text-rose-600">
                        {r.job_error}
                      </div>
                    )}
                  </Td>
                  <Td>{new Date(r.created_at).toLocaleString()}</Td>
                  <Td className="space-x-2">
                    <Link
                      to={`/prospects?run=${r.id}`}
                      className="text-sm font-medium text-slate-900 hover:underline"
                    >
                      Prospects
                    </Link>
                    <button
                      type="button"
                      className="text-sm font-medium text-rose-600 hover:underline disabled:opacity-50"
                      disabled={deleteRun.isPending}
                      onClick={() => {
                        const n = r.people_imported ?? 0;
                        if (
                          window.confirm(
                            `Delete run #${r.id}? This removes ${n} prospect(s) from the database. ` +
                              "Those people can be found again in a new discovery search."
                          )
                        ) {
                          deleteRun.mutate(r.id);
                        }
                      }}
                    >
                      Delete
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
