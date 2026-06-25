import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import {
  getAgentConfig,
  deleteDiscoveryRun,
  listDiscoveryRuns,
  listPrincipals,
  planAgentSearch,
  resetPipeline,
  runDiscovery,
  type DiscoveryRunPayload,
} from "../api/client";
import { MultiSelectDropdown } from "../components/MultiSelectDropdown";
import {
  BOARD_JOB_TITLE_OPTIONS,
  COMPANY_TYPE_OPTIONS,
  CONTACT_EMAIL_STATUS_OPTIONS,
  DEFAULT_BOARD_JOB_TITLES,
  DEFAULT_DISCOVERY_TITLES,
  DEFAULT_GEOGRAPHIES,
  GEOGRAPHY_OPTIONS,
  GEOGRAPHY_SUGGESTIONS,
  INDUSTRY_OPTIONS,
  SENIORITY_OPTIONS,
  THEME_OPTIONS,
  TITLE_OPTIONS,
} from "../constants/discoveryOptions";
import WorkflowSteps from "../components/WorkflowSteps";
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

function applyPlanCriteria(
  c: Record<string, unknown>,
  setters: {
    setTitles: (v: string[]) => void;
    setSeniorities: (v: string[]) => void;
    setIndustries: (v: string[]) => void;
    setCompanyTypes: (v: string[]) => void;
    setGeographies: (v: string[]) => void;
    setKeywords: (v: string[]) => void;
    setThemes: (v: string[]) => void;
    setOrganizationJobTitles: (v: string[]) => void;
    setContactEmailStatus: (v: string[]) => void;
    setOrganizationDomains: (v: string[]) => void;
    setEmployeeMin: (v: string) => void;
    setEmployeeMax: (v: string) => void;
    setPeopleLimit: (v: string) => void;
  }
) {
  if (Array.isArray(c.titles)) setters.setTitles(c.titles as string[]);
  if (Array.isArray(c.seniorities)) setters.setSeniorities(c.seniorities as string[]);
  if (Array.isArray(c.industries)) setters.setIndustries(c.industries as string[]);
  if (Array.isArray(c.company_types)) setters.setCompanyTypes(c.company_types as string[]);
  if (Array.isArray(c.geographies)) setters.setGeographies(c.geographies as string[]);
  if (Array.isArray(c.keywords)) setters.setKeywords(c.keywords as string[]);
  if (Array.isArray(c.themes)) setters.setThemes(c.themes as string[]);
  if (Array.isArray(c.organization_job_titles))
    setters.setOrganizationJobTitles(c.organization_job_titles as string[]);
  if (Array.isArray(c.contact_email_status))
    setters.setContactEmailStatus(c.contact_email_status as string[]);
  if (Array.isArray(c.organization_domains))
    setters.setOrganizationDomains(c.organization_domains as string[]);
  if (c.employee_min != null) setters.setEmployeeMin(String(c.employee_min));
  if (c.employee_max != null) setters.setEmployeeMax(String(c.employee_max));
  if (c.people_limit != null) setters.setPeopleLimit(String(c.people_limit));
}

export default function Discover() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });
  const { data: runs, isLoading } = useQuery({
    queryKey: ["discovery-runs"],
    queryFn: () => listDiscoveryRuns({ limit: 10 }),
  });

  const [principalId, setPrincipalId] = useState<number | "">("");
  const [objective, setObjective] = useState("");
  const [plan, setPlan] = useState<AgentPlan | null>(null);
  const [planAnswers, setPlanAnswers] = useState<Record<string, string>>({});
  const [planNote, setPlanNote] = useState<string | null>(null);

  const [industries, setIndustries] = useState<string[]>(["Healthcare", "Healthcare Services"]);
  const [companyTypes, setCompanyTypes] = useState<string[]>([
    "private_equity",
    "operating_company",
  ]);
  const [geographies, setGeographies] = useState<string[]>(DEFAULT_GEOGRAPHIES);
  const [titles, setTitles] = useState<string[]>(DEFAULT_DISCOVERY_TITLES);
  const [seniorities, setSeniorities] = useState<string[]>([]);
  const [contactEmailStatus, setContactEmailStatus] = useState<string[]>([]);
  const [organizationDomains, setOrganizationDomains] = useState<string[]>([]);
  const [keywords, setKeywords] = useState<string[]>([]);
  const [themes, setThemes] = useState<string[]>([]);
  const [organizationJobTitles, setOrganizationJobTitles] = useState<string[]>(
    DEFAULT_BOARD_JOB_TITLES
  );
  const [employeeMin, setEmployeeMin] = useState("");
  const [employeeMax, setEmployeeMax] = useState("");
  const [peopleLimit, setPeopleLimit] = useState("100");

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
  }, [agentConfig, principalId]);

  const planSetters = {
    setTitles,
    setSeniorities,
    setIndustries,
    setCompanyTypes,
    setGeographies,
    setKeywords,
    setThemes,
    setOrganizationJobTitles,
    setContactEmailStatus,
    setOrganizationDomains,
    setEmployeeMin,
    setEmployeeMax,
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
      const payload: DiscoveryRunPayload = {
        principal_id: Number(principalId),
        industries,
        company_types: companyTypes,
        geographies,
        titles,
        seniorities,
        contact_email_status:
          contactEmailStatus.length > 0 ? contactEmailStatus : undefined,
        organization_domains:
          organizationDomains.length > 0 ? organizationDomains : undefined,
        keywords,
        themes,
        organization_job_titles:
          organizationJobTitles.length > 0 ? organizationJobTitles : undefined,
        employee_min: employeeMin.trim() ? Number(employeeMin) : undefined,
        employee_max: employeeMax.trim() ? Number(employeeMax) : undefined,
        people_limit: peopleLimit.trim() ? Number(peopleLimit) : 100,
        people_first: true,
        auto_expand_to_target: true,
        auto_process: true,
        search_goal:
          objective.trim() ||
          (plan?.rationale && !plan.questions.length ? plan.rationale : undefined),
      };
      return runDiscovery(payload);
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["discovery-runs"] });
      qc.invalidateQueries({ queryKey: ["prospects"] });
      qc.invalidateQueries({ queryKey: ["organizations"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["emails"] });
      setPlanNote(
        `Run #${result.id} complete — ${result.people_imported ?? 0} prospects imported` +
          (result.insights_generated != null
            ? `, ${result.insights_generated} researched`
            : "") +
          ". Opening Prospects…"
      );
      navigate(`/prospects?run=${result.id}`);
    },
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
                Describe your goal in plain English. Claude suggests titles, industries,
                company types, geographies, keywords, and themes — then fills the filter
                fields below. You can edit everything before running. First click may ask
                2–3 clarifying questions; click Plan again to apply.
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
              <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  People — who to reach
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                  <MultiSelectDropdown
                    label="Titles"
                    hint="Job titles the person holds. Apollo expands to similar titles automatically. Add custom titles for niche roles."
                    selected={titles}
                    onChange={setTitles}
                    options={TITLE_OPTIONS}
                    placeholder="Search titles or add custom…"
                  />
                  <MultiSelectDropdown
                    label="Seniorities"
                    hint="All 11 Apollo seniority levels (complete API enum). Optional — leave empty for broadest search."
                    selected={seniorities}
                    onChange={setSeniorities}
                    options={SENIORITY_OPTIONS}
                    allowCustom={false}
                    placeholder="Select seniority levels…"
                  />
                  <MultiSelectDropdown
                    label="Email status"
                    hint="Apollo enum only — Verified, Likely to engage, or Unverified. Optional filter."
                    selected={contactEmailStatus}
                    onChange={setContactEmailStatus}
                    options={CONTACT_EMAIL_STATUS_OPTIONS}
                    allowCustom={false}
                    placeholder="Filter by email status…"
                  />
                </div>
              </div>

              <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-800">
                  Active job postings — optional boost
                </div>
                <p className="mb-3 text-xs text-amber-900">
                  When set, people whose employer has a matching <strong>active job posting</strong>{" "}
                  (e.g. Independent Director) are ranked first — but everyone matching your titles
                  and industries is still included. Companies without open postings appear normally.
                </p>
                <MultiSelectDropdown
                  label="Employer job posting titles"
                  hint="Open roles at the person's current employer (e.g. Independent Director, Director of Pharmacy)."
                  selected={organizationJobTitles}
                  onChange={setOrganizationJobTitles}
                  options={BOARD_JOB_TITLE_OPTIONS}
                  placeholder="Search or add posting titles…"
                />
              </div>

              <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Organizations — where they work
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                  <MultiSelectDropdown
                    label="Industries"
                    hint="Broad keyword tags — pick from the list or type any industry. Prefer 1-3 wide buckets."
                    selected={industries}
                    onChange={setIndustries}
                    options={INDUSTRY_OPTIONS}
                    placeholder="Search industries or type custom…"
                  />
                  <MultiSelectDropdown
                    label="Company types"
                    hint="Keyword tags for employer type — select presets or type custom (e.g. search fund, MSO)."
                    selected={companyTypes}
                    onChange={setCompanyTypes}
                    options={COMPANY_TYPE_OPTIONS}
                    placeholder="Search company types or type custom…"
                  />
                  <MultiSelectDropdown
                    label="Employer domains"
                    hint="Optional — limit to specific employers by domain (e.g. shorecp.com). Leave empty for broad discovery."
                    selected={organizationDomains}
                    onChange={setOrganizationDomains}
                    placeholder="e.g. vistria.com, revelstokecp.com…"
                  />
                  <MultiSelectDropdown
                    label="Geographies"
                    hint="Where the person is based (not employer HQ). US default; all 50 states listed. Type any city, state, or country."
                    selected={geographies}
                    onChange={setGeographies}
                    options={GEOGRAPHY_OPTIONS}
                    suggestions={GEOGRAPHY_SUGGESTIONS}
                    placeholder="Search locations or type custom…"
                  />
                </div>
              </div>

              <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
                <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Signals — keywords & themes
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                  <MultiSelectDropdown
                    label="Keywords"
                    hint="Free-text terms matched against company profiles (e.g. roll-up, formulary)."
                    selected={keywords}
                    onChange={setKeywords}
                    placeholder="e.g. roll-up, consolidation…"
                  />
                  <MultiSelectDropdown
                    label="Investment / acquisition themes"
                    hint="Strategic angles — used as search keywords and to ground AI relevance scoring."
                    selected={themes}
                    onChange={setThemes}
                    options={THEME_OPTIONS}
                    placeholder="Search themes or add custom…"
                  />
                </div>
              </div>

              <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Company size & result cap
                </div>
                <p className="mb-3 text-xs text-slate-400">
                  Employee min filters out very small companies (e.g. fewer than 10 people).
                  Leave max empty for the broadest net. Use employee max to focus on mid-market or smaller companies.
                </p>
                <div className="flex flex-wrap items-end gap-4">
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">Employee min</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      className="mt-1 w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      value={employeeMin}
                      placeholder="optional"
                      onChange={(e) => setEmployeeMin(e.target.value.replace(/\D/g, ""))}
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-600">Employee max</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      className="mt-1 w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      value={employeeMax}
                      placeholder="optional"
                      onChange={(e) => setEmployeeMax(e.target.value.replace(/\D/g, ""))}
                    />
                  </label>
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
                disabled={!principalId || run.isPending}
              >
                {run.isPending ? "Discovering…" : "Run discovery"}
              </Button>
              <span className="text-xs text-slate-400">
                {titles.length} title(s) · {industries.length} industry filter(s)
                {organizationJobTitles.length > 0 &&
                  ` · ${organizationJobTitles.length} job posting signal(s)`}
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
            {run.isSuccess && run.data && run.data.status === "failed" && (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                <div className="font-medium">Discovery failed</div>
                <p className="mt-1">
                  {run.data.error_message ||
                    "Apollo returned an error. Check APOLLO_API_KEY in backend .env."}
                </p>
              </div>
            )}
            {run.isSuccess && run.data && run.data.status !== "failed" && (
              <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-700">
                <div className="font-medium">
                  Run complete ({run.data.status}) via {run.data.provider}
                </div>
                {run.data.provider === "stub" && (
                  <p className="mt-2 text-amber-800">
                    This run used the offline stub provider (mock data), not Apollo.
                    Set <code className="text-xs">DISCOVERY_PROVIDER=apollo</code> in backend{" "}
                    <code className="text-xs">.env</code>, confirm{" "}
                    <code className="text-xs">APOLLO_API_KEY</code> is set, and restart the
                    backend.
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-2">
                  <Badge tone="blue">{run.data.people_imported ?? 0} prospects</Badge>
                  <Badge>{run.data.duplicates ?? 0} duplicates skipped</Badge>
                  {(run.data.people_imported ?? 0) <
                    Number(
                      (run.data.criteria as { people_limit?: number } | undefined)
                        ?.people_limit ?? peopleLimit
                    ) && (
                    <Badge tone="amber">Below target count</Badge>
                  )}
                </div>
                {(run.data.criteria as { expansion_summary?: string } | undefined)
                  ?.expansion_summary && (
                  <p className="mt-2 text-xs text-slate-600">
                    {(run.data.criteria as { expansion_summary: string }).expansion_summary}
                  </p>
                )}
                {(run.data.criteria as { shortfall_note?: string } | undefined)
                  ?.shortfall_note &&
                  !(run.data.criteria as { expansion_summary?: string }).expansion_summary && (
                    <p className="mt-2 text-xs text-amber-700">
                      {(run.data.criteria as { shortfall_note: string }).shortfall_note}
                    </p>
                  )}
                {(run.data.people_imported ?? 0) === 0 && (
                  <p className="mt-2 text-amber-700">
                    Apollo returned no matches. Try fewer industry filters (one broad bucket),
                    widen the employee range, or verify your API key is valid.
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-4">
                  <Link
                    to={`/prospects?run=${run.data.id}`}
                    className="font-medium text-slate-900 underline"
                  >
                    Review {run.data.people_imported ?? 0} prospects →
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
