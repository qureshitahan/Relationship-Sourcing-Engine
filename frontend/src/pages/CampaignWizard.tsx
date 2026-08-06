import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { isAxiosError } from "axios";

import { createCampaign, listPrincipals, planAgentSearch } from "../api/client";
import { ChipSelect } from "../components/ChipSelect";
import { usePersistedState } from "../hooks/usePersistedState";
import {
  COMPANY_TYPE_OPTIONS,
  INDUSTRY_OPTIONS,
  SENIORITY_OPTIONS,
  TITLE_OPTIONS,
} from "../constants/discoveryOptions";
import { Badge, Button, Card, Loading } from "../components/ui";
import type { AgentPlan } from "../types";

const inputCls =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100";

const STEPS = ["Principal", "Goal", "Questions", "Audience", "Settings", "Launch"] as const;

/** Human-readable reason from an API error, so failures name the actual problem
 *  (missing prerequisite, validation issue, server fault) instead of a generic line. */
function apiErrorMessage(e: unknown, fallback: string): string {
  if (isAxiosError(e)) {
    if (!e.response) return "Cannot reach the server — check your connection and try again.";
    const detail: unknown = e.response.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
      // FastAPI validation errors: [{loc: ["body", "field"], msg: "..."}]
      const first = detail[0] as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(first.loc)
        ? first.loc.filter((p) => p !== "body").join(".")
        : "";
      if (first.msg) return field ? `${field}: ${first.msg}` : first.msg;
    }
    if (e.response.status >= 500)
      return `${fallback} The server hit an internal error (${e.response.status}) — the campaign may still have been created; check All campaigns before retrying.`;
  }
  return fallback;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      {hint && <span className="ml-1 text-xs text-slate-400">— {hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

export default function CampaignWizard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const presetPrincipal = Number(searchParams.get("principal")) || null;

  // Persisted to sessionStorage (not just useState): navigating away mid-wizard
  // (e.g. to check LinkedIn) fully unmounts this page, and a plain useState
  // would lose everything typed/selected so far. Cleared on successful create.
  const [step, setStep, clearStep] = usePersistedState("campaign-wizard:step", 0);
  const [maxStep, setMaxStep, clearMaxStep] = usePersistedState("campaign-wizard:maxStep", 0);
  const [principalId, setPrincipalId, clearPrincipalId] = usePersistedState<number | null>(
    "campaign-wizard:principalId",
    presetPrincipal
  );
  const [objective, setObjective, clearObjective] = usePersistedState("campaign-wizard:objective", "");
  const [plan, setPlan, clearPlan] = usePersistedState<AgentPlan | null>("campaign-wizard:plan", null);
  const [answers, setAnswers, clearAnswers] = usePersistedState<Record<string, string>>(
    "campaign-wizard:answers",
    {}
  );

  const [titles, setTitles, clearTitles] = usePersistedState<string[]>("campaign-wizard:titles", []);
  const [seniorities, setSeniorities, clearSeniorities] = usePersistedState<string[]>(
    "campaign-wizard:seniorities",
    []
  );
  const [industries, setIndustries, clearIndustries] = usePersistedState<string[]>(
    "campaign-wizard:industries",
    ["Healthcare Services"]
  );
  const [companyTypes, setCompanyTypes, clearCompanyTypes] = usePersistedState<string[]>(
    "campaign-wizard:companyTypes",
    ["private_equity"]
  );

  const [peoplePerDay, setPeoplePerDay, clearPeoplePerDay] = usePersistedState(
    "campaign-wizard:peoplePerDay",
    50
  );
  const [mailboxCap, setMailboxCap, clearMailboxCap] = usePersistedState("campaign-wizard:mailboxCap", 50);
  // Default new campaigns to review-before-send so the first runs are safe to inspect.
  const [autoSend, setAutoSend, clearAutoSend] = usePersistedState("campaign-wizard:autoSend", false);
  const [autoSchedule, setAutoSchedule, clearAutoSchedule] = usePersistedState(
    "campaign-wizard:autoSchedule",
    true
  );
  const [followupEnabled, setFollowupEnabled, clearFollowupEnabled] = usePersistedState(
    "campaign-wizard:followupEnabled",
    true
  );
  const [runHour, setRunHour, clearRunHour] = usePersistedState("campaign-wizard:runHour", 9);
  const [timezone, setTimezone, clearTimezone] = usePersistedState(
    "campaign-wizard:timezone",
    "America/New_York"
  );
  const [digest, setDigest, clearDigest] = usePersistedState("campaign-wizard:digest", "");
  const [name, setName, clearName] = usePersistedState("campaign-wizard:name", "");
  const [enabled, setEnabled, clearEnabled] = usePersistedState("campaign-wizard:enabled", true);
  const [runNow, setRunNow, clearRunNow] = usePersistedState("campaign-wizard:runNow", true);
  const [error, setError] = useState<string | null>(null);

  // Wipe every persisted field once the campaign is actually created, so the
  // next visit to "New campaign" starts blank instead of reloading this draft.
  const clearWizardDraft = () => {
    clearStep();
    clearMaxStep();
    clearPrincipalId();
    clearObjective();
    clearPlan();
    clearAnswers();
    clearTitles();
    clearSeniorities();
    clearIndustries();
    clearCompanyTypes();
    clearPeoplePerDay();
    clearMailboxCap();
    clearAutoSend();
    clearAutoSchedule();
    clearFollowupEnabled();
    clearRunHour();
    clearTimezone();
    clearDigest();
    clearName();
    clearEnabled();
    clearRunNow();
  };

  const { data: principals, isLoading: principalsLoading } = useQuery({
    queryKey: ["principals", "active"],
    queryFn: () => listPrincipals({ active: true }),
  });

  useEffect(() => {
    if (principalId == null && principals?.items.length) {
      setPrincipalId(principals.items[0].id);
    }
  }, [principals, principalId, setPrincipalId]);

  // Remember the furthest step reached so the stepper can jump back to any
  // visited step to correct earlier answers.
  useEffect(() => {
    setMaxStep((m) => Math.max(m, step));
  }, [step, setMaxStep]);

  const principalName = useMemo(
    () => principals?.items.find((p) => p.id === principalId)?.name ?? "",
    [principals, principalId]
  );

  const planner = useMutation({
    mutationFn: () =>
      planAgentSearch({
        objective_prompt: objective,
        principal_id: principalId ?? undefined,
        clarifying_answers: Object.keys(answers).length ? answers : undefined,
      }),
    onSuccess: (p) => {
      setPlan(p);
      const c = p.criteria;
      if (Array.isArray(c.titles)) setTitles(c.titles as string[]);
      if (Array.isArray(c.seniorities)) setSeniorities(c.seniorities as string[]);
      if (Array.isArray(c.industries)) setIndustries(c.industries as string[]);
      if (Array.isArray(c.company_types)) setCompanyTypes(c.company_types as string[]);
      if (c.people_limit) setPeoplePerDay(Number(c.people_limit));
      if (p.questions.length > 0) {
        const init: Record<string, string> = { ...answers };
        for (const q of p.questions) init[q.id] = answers[q.id] ?? q.suggested ?? "";
        setAnswers(init);
        setStep(2);
      } else {
        setStep(3);
      }
    },
    onError: (e) => setError(apiErrorMessage(e, "Could not plan the search. Try again.")),
  });

  const create = useMutation({
    mutationFn: () =>
      createCampaign({
        principal_id: principalId!,
        name: name.trim() || `${principalName} outreach`,
        objective_prompt: objective.trim(),
        clarifying_answers: answers,
        criteria: {
          titles,
          seniorities,
          geographies: ["United States"],
          industries,
          company_types: companyTypes,
          employee_max: 5000,
          people_limit: peoplePerDay,
          search_goal: objective.trim(),
        },
        discover_target: peoplePerDay,
        mailbox_daily_cap: mailboxCap,
        auto_send: autoSend,
        auto_schedule: autoSchedule,
        followup_enabled: followupEnabled,
        timezone,
        run_hour_local: runHour,
        digest_recipients: digest
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        enabled,
        run_now: runNow,
      }),
    onSuccess: (c) => {
      clearWizardDraft();
      navigate(`/campaigns/${c.id}`);
    },
    onError: (e) => setError(apiErrorMessage(e, "Could not create the campaign.")),
  });

  const goalReady = objective.trim().length >= 10;
  const audienceReady = titles.length > 0;

  if (principalsLoading) return <Loading />;

  if (!principals?.items.length) {
    return (
      <div className="mx-auto max-w-lg py-16 text-center">
        <h1 className="text-xl font-semibold text-slate-900">New campaign</h1>
        <p className="mt-2 text-sm text-slate-500">Add a principal first.</p>
        <Link to="/principals" className="mt-6 inline-block">
          <Button>Go to Principals</Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl pb-12">
      <Link to="/agent" className="text-sm text-violet-700 hover:underline">
        ← All campaigns
      </Link>
      <h1 className="mt-3 text-2xl font-semibold text-slate-900">New campaign</h1>
      <p className="mt-1 text-sm text-slate-500">
        Describe who to reach and why. The AI asks clarifying questions so the objective is crystal
        clear before it starts emailing.
      </p>

      {/* Stepper — click any visited step to go back and correct it */}
      <div className="mt-6 flex flex-wrap gap-1.5">
        {STEPS.map((s, i) => {
          const visited = i <= maxStep;
          return (
            <button
              key={s}
              type="button"
              onClick={() => visited && setStep(i)}
              disabled={!visited}
              title={visited ? `Go to ${s}` : `Finish earlier steps first`}
              className={`flex-1 rounded-lg px-2 py-1.5 text-center text-[11px] font-medium transition ${
                step === i
                  ? "bg-violet-600 text-white"
                  : visited
                    ? "cursor-pointer bg-violet-100 text-violet-800 hover:bg-violet-200"
                    : "cursor-not-allowed bg-slate-100 text-slate-400"
              }`}
            >
              {i + 1}. {s}
            </button>
          );
        })}
      </div>
      {maxStep > 0 && (
        <p className="mt-1.5 text-center text-[11px] text-slate-400">
          Tip: click any step above to go back and edit it — your answers are kept.
        </p>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="mt-6">
        {/* STEP 0 — Principal */}
        {step === 0 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">Who is this campaign for?</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              The principal whose voice and credibility the emails use.
            </p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {principals.items.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setPrincipalId(p.id)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    principalId === p.id
                      ? "border-violet-400 bg-violet-50 ring-2 ring-violet-200"
                      : "border-slate-200 bg-white hover:border-violet-200"
                  }`}
                >
                  <p className="text-sm font-semibold text-slate-900">{p.name}</p>
                  {p.headline && <p className="mt-0.5 text-xs text-slate-500">{p.headline}</p>}
                </button>
              ))}
            </div>
            <div className="mt-5 flex justify-end">
              <Button onClick={() => setStep(1)} disabled={principalId == null}>
                Next
              </Button>
            </div>
          </Card>
        )}

        {/* STEP 1 — Goal */}
        {step === 1 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">
              What are you trying to accomplish?
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Be specific about who to reach and the purpose of the outreach.
            </p>
            <textarea
              rows={5}
              className={`${inputCls} mt-4`}
              placeholder="e.g. Reach hospital pharmacy directors in the US to introduce our 503B compounding line with a warm, consultative tone."
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
            />
            <div className="mt-4 flex items-center justify-between">
              <Button variant="secondary" onClick={() => setStep(0)}>
                Back
              </Button>
              <Button onClick={() => planner.mutate()} disabled={!goalReady || planner.isPending}>
                {planner.isPending ? "Thinking…" : "Plan with AI"}
              </Button>
            </div>
          </Card>
        )}

        {/* STEP 2 — Clarifying questions */}
        {step === 2 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">A few clarifying questions</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              These sharpen the search so we target the right people.
            </p>
            {plan && plan.questions.length > 0 ? (
              <div className="mt-4 space-y-4">
                {plan.questions.map((q, i) => (
                  <Field key={q.id} label={`${i + 1}. ${q.prompt}`}>
                    <input
                      className={inputCls}
                      value={answers[q.id] ?? ""}
                      placeholder={q.suggested}
                      onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })}
                    />
                  </Field>
                ))}
              </div>
            ) : (
              <p className="mt-4 text-sm text-slate-500">No more questions — continue.</p>
            )}
            <div className="mt-5 flex items-center justify-between">
              <Button variant="secondary" onClick={() => setStep(1)}>
                Back
              </Button>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setStep(3)}>
                  Skip
                </Button>
                <Button onClick={() => planner.mutate()} disabled={planner.isPending}>
                  {planner.isPending ? "Thinking…" : "Submit answers"}
                </Button>
              </div>
            </div>
          </Card>
        )}

        {/* STEP 3 — Audience */}
        {step === 3 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">Who should we find?</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              These filters go to Apollo. Cast a wide net — relevance scoring happens after.
            </p>
            {plan?.rationale && (
              <p className="mt-3 rounded-lg bg-violet-50 px-3 py-2 text-xs text-violet-900">
                {plan.rationale}
              </p>
            )}
            <div className="mt-4 space-y-5">
              <ChipSelect
                label="Job titles"
                options={TITLE_OPTIONS}
                selected={titles}
                onChange={setTitles}
                allowCustom
                placeholder="Add title…"
              />
              <ChipSelect
                label="Seniority"
                options={SENIORITY_OPTIONS}
                selected={seniorities}
                onChange={setSeniorities}
                allowCustom={false}
              />
              <ChipSelect
                label="Industries"
                options={INDUSTRY_OPTIONS}
                selected={industries}
                onChange={setIndustries}
                allowCustom
              />
              <ChipSelect
                label="Company type"
                options={COMPANY_TYPE_OPTIONS}
                selected={companyTypes}
                onChange={setCompanyTypes}
                allowCustom={false}
              />
            </div>
            <div className="mt-5 flex items-center justify-between">
              <Button variant="secondary" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button onClick={() => setStep(4)} disabled={!audienceReady}>
                Next
              </Button>
            </div>
          </Card>
        )}

        {/* STEP 4 — Settings */}
        {step === 4 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">How should it run?</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="People to find / run">
                <input
                  type="number"
                  className={inputCls}
                  value={peoplePerDay}
                  onChange={(e) => setPeoplePerDay(Number(e.target.value))}
                />
              </Field>
              <Field label="Daily email limit" hint="shared mailbox cap">
                <input
                  type="number"
                  className={inputCls}
                  value={mailboxCap}
                  onChange={(e) => setMailboxCap(Number(e.target.value))}
                />
              </Field>
            </div>
            <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-500">
              These are two different things. <b>People to find</b> is how many new prospects
              discovery surfaces each run. <b>Daily email limit</b> caps how many emails actually go
              out — it's <b>shared across all of {principalName || "this principal"}'s campaigns</b>{" "}
              to protect the mailbox from spam filters. Not everyone found gets emailed: some have no
              valid email and some are filtered out by the AI research step, so finding 50 usually
              means fewer than 50 sends.
            </p>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="Daily report inboxes" hint="optional">
                <input
                  className={inputCls}
                  placeholder="you@company.com"
                  value={digest}
                  onChange={(e) => setDigest(e.target.value)}
                />
              </Field>
            </div>

            <div className="mt-4 space-y-3">
              <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
                <input
                  type="checkbox"
                  className="mt-0.5 rounded border-slate-300"
                  checked={autoSchedule}
                  onChange={(e) => setAutoSchedule(e.target.checked)}
                />
                <span className="text-sm text-slate-800">
                  <span className="font-medium">Let the AI choose the best send time</span>
                  <span className="block text-xs text-slate-500">
                    Each email is sent during business hours in the recipient's own timezone (from
                    Apollo/LinkedIn), and the AI A/B tests morning vs. afternoon to learn what gets
                    replies. Recommended.
                  </span>
                </span>
              </label>
              {!autoSchedule && (
                <Field label="Fixed daily run hour">
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min={0}
                      max={23}
                      className={inputCls}
                      value={runHour}
                      onChange={(e) => setRunHour(Number(e.target.value))}
                    />
                    <select
                      className={inputCls}
                      value={timezone}
                      onChange={(e) => setTimezone(e.target.value)}
                    >
                      <option value="America/New_York">Eastern</option>
                      <option value="America/Chicago">Central</option>
                      <option value="America/Denver">Mountain</option>
                      <option value="America/Los_Angeles">Pacific</option>
                    </select>
                  </div>
                </Field>
              )}
            </div>

            <div className="mt-4">
              <span className="text-xs font-medium text-slate-600">Before emails go out</span>
              <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => setAutoSend(false)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    !autoSend
                      ? "border-violet-400 bg-violet-50 ring-2 ring-violet-200"
                      : "border-slate-200 bg-white hover:border-violet-200"
                  }`}
                >
                  <p className="text-sm font-semibold text-slate-900">Review before sending</p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    Drafts everything and waits for your approval. Recommended while you test.
                  </p>
                </button>
                <button
                  type="button"
                  onClick={() => setAutoSend(true)}
                  className={`rounded-xl border px-4 py-3 text-left transition ${
                    autoSend
                      ? "border-violet-400 bg-violet-50 ring-2 ring-violet-200"
                      : "border-slate-200 bg-white hover:border-violet-200"
                  }`}
                >
                  <p className="text-sm font-semibold text-slate-900">Autopilot</p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    Sends automatically once each prospect passes research. No manual step.
                  </p>
                </button>
              </div>
            </div>

            <div className="mt-4 space-y-3">
              <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
                <input
                  type="checkbox"
                  className="rounded border-slate-300"
                  checked={followupEnabled}
                  onChange={(e) => setFollowupEnabled(e.target.checked)}
                />
                <span className="text-sm font-medium text-slate-800">
                  Automatic follow-ups to people who go quiet
                </span>
              </label>
            </div>
            <div className="mt-5 flex items-center justify-between">
              <Button variant="secondary" onClick={() => setStep(3)}>
                Back
              </Button>
              <Button onClick={() => setStep(5)}>Next</Button>
            </div>
          </Card>
        )}

        {/* STEP 5 — Launch */}
        {step === 5 && (
          <Card className="p-5">
            <h2 className="text-sm font-semibold text-slate-900">Name & launch</h2>
            <div className="mt-4">
              <Field label="Campaign name" hint="appears on the card">
                <input
                  className={inputCls}
                  placeholder={`${principalName} outreach`}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
            </div>
            <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50/60 p-4 text-sm text-slate-600">
              <p>
                <span className="font-medium text-slate-800">{principalName}</span> ·{" "}
                {titles.slice(0, 3).join(", ")}
                {titles.length > 3 ? "…" : ""}
              </p>
              <p className="mt-1 text-xs">
                Finds {peoplePerDay} people/run · up to {mailboxCap} emails/day ·{" "}
                {autoSend ? "auto-sends" : "drafts for review"}
              </p>
            </div>
            <div className="mt-4 space-y-3">
              <label className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  className="rounded border-slate-300"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                <span className="text-sm text-slate-700">Run automatically every day</span>
              </label>
              <label className="flex cursor-pointer items-center gap-3">
                <input
                  type="checkbox"
                  className="rounded border-slate-300"
                  checked={runNow}
                  onChange={(e) => setRunNow(e.target.checked)}
                />
                <span className="text-sm text-slate-700">Run a first cycle now</span>
              </label>
            </div>
            <div className="mt-5 flex items-center justify-between">
              <Button variant="secondary" onClick={() => setStep(4)}>
                Back
              </Button>
              <Button onClick={() => create.mutate()} disabled={create.isPending || !audienceReady}>
                {create.isPending ? "Creating…" : "Create campaign"}
              </Button>
            </div>
            {runNow && (
              <p className="mt-3 text-center text-xs text-slate-400">
                <Badge tone="blue">Heads up</Badge> The first run can take a few minutes to find and
                research people.
              </p>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}
