import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePersistedState } from "../hooks/usePersistedState";
import { useAccountScopedState } from "../hooks/useAccountScopedState";
import {
  approveAllSearchLeads,
  createLinkedInConnectLink,
  draftAllSearchLeads,
  generateSearchCopy,
  getSearchParameters,
  getSearchProgress,
  getSearchStatus,
  listPrincipals,
  listSearchLeads,
  runLinkedInSearch,
  selectLinkedInAccount,
  sendAllSearchLeads,
  stopSearchJob,
} from "../api/client";
import type {
  SearchFilters,
  SearchLeadRow,
  SearchParameterOption,
  SearchProgress,
  SearchStats,
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
import { MultiSelectDropdown } from "../components/MultiSelectDropdown";
import {
  INDUSTRY_PRESETS,
  JOB_TITLE_OPTIONS,
  LOCATION_PRESETS,
} from "../constants/linkedinSearchOptions";

/** Tabs over the leads list. `pending` is "found, but no message written yet". */
const STATUS_TABS = [
  { key: "", label: "All" },
  { key: "pending", label: "Not drafted" },
  { key: "draft", label: "Draft" },
  { key: "approved", label: "Approved" },
  { key: "invite_sent", label: "Invited" },
  { key: "sent", label: "Sent" },
  { key: "replied", label: "Replied" },
] as const;

/** LinkedIn's own seniority buckets, spelled the way its search expects. */
const SENIORITY = [
  "Owner",
  "Partner",
  "CXO",
  "Vice President",
  "Director",
  "Manager",
  "Senior",
  "Entry",
] as const;

/** LinkedIn's own headcount bands. */
const HEADCOUNT = [
  "1-10",
  "11-50",
  "51-200",
  "201-500",
  "501-1000",
  "1001-5000",
  "5001-10000",
  "10001+",
] as const;

const DEGREES = [
  { value: 1, label: "1st" },
  { value: 2, label: "2nd" },
  { value: 3, label: "3rd" },
] as const;

/** Mirrors the backend's `first_name_of` exactly, so the preview is not a guess. */
function firstNameOf(name?: string | null): string {
  const token = (name ?? "").trim().split(" ")[0]?.replace(/,+$/, "") ?? "";
  const cleaned = token.replace(/[^A-Za-z\-']/g, "");
  return cleaned || "there";
}

/** A checkbox row that toggles one value in and out of a string/number list. */
function ChipGroup<T extends string | number>({
  options,
  selected,
  onChange,
  disabled,
}: {
  options: readonly { value: T; label: string }[];
  selected: T[];
  onChange: (next: T[]) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const on = selected.includes(opt.value);
        return (
          <button
            key={String(opt.value)}
            type="button"
            disabled={disabled}
            onClick={() =>
              onChange(
                on
                  ? selected.filter((v) => v !== opt.value)
                  : [...selected, opt.value]
              )
            }
            className={`rounded-full border px-2.5 py-1 text-xs font-medium disabled:opacity-50 ${
              on
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-300 bg-white text-slate-600 hover:border-slate-400"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * A filter LinkedIn will not accept as free text.
 *
 * Location and industry are ids on LinkedIn's side. Typing "Toronto" into a
 * plain text box would look like it worked and then quietly match nothing, so
 * this resolves what you type into real ids and only sends the ones you picked.
 */
function IdPicker({
  label,
  kind,
  accountId,
  selected,
  onChange,
  disabled,
  presets = [],
}: {
  label: string;
  kind: string;
  accountId?: string;
  selected: SearchParameterOption[];
  onChange: (next: SearchParameterOption[]) => void;
  disabled?: boolean;
  /** Common values offered as one-click chips. Held as SEARCH TERMS, not ids:
   *  the id for "United States" differs between classic and Sales Navigator, so
   *  a click resolves it in whichever mode is active instead of storing one. */
  presets?: string[];
}) {
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);

  const addPreset = async (preset: string) => {
    setResolving(preset);
    try {
      const options = await getSearchParameters(kind, preset, accountId);
      // Prefer an exact title match; LinkedIn often returns narrower entries
      // first ("California, United States" ahead of "United States").
      const exact = options.find(
        (o) => o.title.toLowerCase() === preset.toLowerCase()
      );
      const pick = exact ?? options[0];
      if (pick && !selected.some((sel) => sel.id === pick.id)) {
        onChange([...selected, pick]);
      }
    } catch {
      // A lookup that fails leaves the filter untouched rather than adding a
      // value LinkedIn would ignore.
    } finally {
      setResolving(null);
    }
  };
  const { data: options, isFetching } = useQuery({
    queryKey: ["linkedin-search", "parameters", kind, term, accountId],
    queryFn: () => getSearchParameters(kind, term, accountId),
    // Only ask once there is something to resolve; LinkedIn returns nothing
    // useful for one or two characters anyway.
    enabled: term.trim().length >= 2 && open,
  });

  return (
    <div className="relative">
      <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </label>
      {selected.length > 0 && (
        <div className="mb-1 mt-1 flex flex-wrap gap-1">
          {selected.map((opt) => (
            <span
              key={opt.id}
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
            >
              {opt.title}
              <button
                type="button"
                className="text-slate-400 hover:text-slate-700"
                onClick={() => onChange(selected.filter((s) => s.id !== opt.id))}
                disabled={disabled}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        value={term}
        onChange={(e) => {
          setTerm(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        // A blur that fires before the click would close the list first and eat
        // the selection, so closing is deferred a tick.
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        placeholder="Type to search…"
        disabled={disabled}
        className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      {presets.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {presets
            .filter((p) => !selected.some((sel) => sel.title === p))
            .slice(0, 8)
            .map((preset) => (
              <button
                key={preset}
                type="button"
                disabled={disabled || resolving !== null}
                onClick={() => addPreset(preset)}
                className="rounded-full border border-dashed border-slate-300 px-2 py-0.5 text-[11px] text-slate-500 hover:border-slate-400 hover:text-slate-700 disabled:opacity-50"
              >
                {resolving === preset ? "adding…" : `+ ${preset}`}
              </button>
            ))}
        </div>
      )}
      {open && term.trim().length >= 2 && (
        <div className="absolute z-20 mt-1 max-h-48 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {isFetching && (
            <div className="px-3 py-2 text-xs text-slate-500">Looking up…</div>
          )}
          {!isFetching && (options ?? []).length === 0 && (
            <div className="px-3 py-2 text-xs text-slate-500">
              Nothing matched — LinkedIn only accepts values from its own list.
            </div>
          )}
          {(options ?? []).map((opt) => (
            <button
              key={opt.id}
              type="button"
              className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50"
              onClick={() => {
                if (!selected.some((s) => s.id === opt.id)) {
                  onChange([...selected, opt]);
                }
                setTerm("");
              }}
            >
              {opt.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ProgressBar({ progress }: { progress: SearchProgress }) {
  const total = Math.max(0, progress.total);
  const done = Math.max(0, progress.done);
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const label =
    progress.job === "search"
      ? "Searching LinkedIn…"
      : progress.job === "draft"
        ? "Writing messages…"
        : "Reaching people…";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-slate-800">{label}</span>
        <span className="text-slate-500">
          {progress.job === "search"
            ? `${progress.imported} found`
            : `${done} of ${total}`}
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-emerald-500 transition-all"
          style={{ width: `${progress.job === "search" ? 100 : pct}%` }}
        />
      </div>
      {progress.message && (
        <div className="mt-1 text-[11px] text-slate-500">{progress.message}</div>
      )}
    </div>
  );
}

function CountRow({ stats }: { stats: SearchStats }) {
  // Every cell counts PEOPLE, not message rows — the same lesson the followers
  // page had to learn, where counting rows made Sent read 597 for 503 people.
  const cells = [
    { label: "Found", value: stats.leads_total, hint: "People this search stored" },
    { label: "Created", value: stats.all, hint: "People this message is drafted for" },
    { label: "Approved", value: stats.approved, hint: "Approved, not yet sent" },
    { label: "Invited", value: stats.invite_sent, hint: "Connection request sent" },
    { label: "Sent", value: stats.sent, hint: "Message delivered" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {cells.map((c) => (
        <div
          key={c.label}
          title={c.hint}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2"
        >
          <div className="text-lg font-semibold text-slate-900">{c.value}</div>
          <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
            {c.label}
          </div>
        </div>
      ))}
    </div>
  );
}

function LeadRow({ row }: { row: SearchLeadRow }) {
  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="px-3 py-2">
        <div className="text-sm font-medium text-slate-900">
          {row.profile_url ? (
            <a
              className="hover:underline"
              href={row.profile_url}
              target="_blank"
              rel="noreferrer"
            >
              {row.name ?? row.provider_id}
            </a>
          ) : (
            row.name ?? row.provider_id
          )}
        </div>
        <div className="text-xs text-slate-500">{row.headline}</div>
        <div className="mt-0.5 text-[11px] text-slate-400">
          {[row.job_title, row.company, row.location].filter(Boolean).join(" · ")}
        </div>
      </td>
      <td className="px-3 py-2">
        {row.network_distance && (
          <Badge tone={row.network_distance === "1" ? "green" : "slate"}>
            {row.network_distance === "1"
              ? "1st — direct message"
              : `${row.network_distance}${
                  row.network_distance === "2" ? "nd" : "rd"
                } — invitation`}
          </Badge>
        )}
      </td>
      <td className="px-3 py-2">
        {row.message_status ? <StatusBadge status={row.message_status} /> : null}
        {row.send_status === "claimed" && <Badge tone="amber">needs review</Badge>}
        {row.error && (
          <div className="mt-1 max-w-xs text-[11px] text-rose-600">{row.error}</div>
        )}
      </td>
      <td className="px-3 py-2">
        <pre className="max-w-md whitespace-pre-wrap text-xs text-slate-600">
          {row.body}
        </pre>
      </td>
    </tr>
  );
}

export default function ClassicSearchLinkedIn() {
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);
  // The last job's result is a DB row, so it survives a refresh and sits there
  // until the next job overwrites it — a failed search kept showing its error
  // long after it stopped being true. Dismissal is keyed by the summary text,
  // not a boolean: closing one run's result must not hide the next one's.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = usePersistedState<string>(
    "search:statusFilter",
    ""
  );

  // The account THIS browser tab is working with. The server keeps ONE active
  // account row shared by every tab, so without pinning it here, someone picking
  // a different account on the LinkedIn page would silently move this page onto
  // their account. Empty = follow the server's choice, which is the same
  // behaviour as having no pin at all. Same approach as the LinkedIn and
  // Followers pages.
  const [tabAccountId, setTabAccountId] = usePersistedState<string>(
    "search:accountId",
    ""
  );

  // Everything below belongs to ONE account. The server already scopes leads and
  // counts by account; without scoping the boxes too, switching left the previous
  // account's filters and message sitting above the new account's (empty)
  // results, which reads as a fault rather than as a switch. Empty until the
  // account is known — nothing can be typed before then, because the page renders
  // a spinner until status loads.
  const scopeId = tabAccountId;

  // --- filters -----------------------------------------------------------
  const [api, setApi] = useAccountScopedState<string>("api", scopeId, "classic");
  const [keywords, setKeywords] = useAccountScopedState<string>("keywords", scopeId, "");
  const [jobTitles, setJobTitles] = useAccountScopedState<string[]>(
    "jobTitles",
    scopeId,
    []
  );
  const [seniority, setSeniority] = useAccountScopedState<string[]>("seniority", scopeId, []);
  const [headcount, setHeadcount] = useAccountScopedState<string[]>("headcount", scopeId, []);
  const [degrees, setDegrees] = useAccountScopedState<number[]>("degrees", scopeId, []);
  const [industry, setIndustry] = useAccountScopedState<SearchParameterOption[]>(
    "industry",
    scopeId,
    []
  );
  const [location, setLocation] = useAccountScopedState<SearchParameterOption[]>(
    "location",
    scopeId,
    []
  );

  // --- message -----------------------------------------------------------
  // The message IS the campaign: its text decides which people belong together
  // and who has already been contacted, so it must survive a refresh.
  const [message, setMessage] = useAccountScopedState<string>("message", scopeId, "");
  const [activeMessage, setActiveMessage] = useAccountScopedState<string>(
    "activeMessage",
    scopeId,
    ""
  );
  const [inviteNote, setInviteNote] = useAccountScopedState<string>("inviteNote", scopeId, "");
  // What the copy is drafted FROM. Kept separately from the two boxes, and never
  // sent to anybody — only the boxes below are transmitted.
  const [goal, setGoal] = useAccountScopedState<string>("goal", scopeId, "");
  const [draftLimit, setDraftLimit] = usePersistedState<string>("search:draftLimit", "50");
  const [appendCount, setAppendCount] = usePersistedState<string>("search:append", "");

  const salesNav = api === "sales_navigator";

  /** Location and industry ids belong to whichever API resolved them, so a mode
   *  change drops them rather than quietly sending ids the other API does not
   *  know. Seniority and headcount are kept: they are plain labels, and classic
   *  simply does not send them. */
  const lastApi = useRef(api);
  useEffect(() => {
    if (lastApi.current === api) return;
    lastApi.current = api;
    setLocation([]);
    setIndustry([]);
  }, [api, setLocation, setIndustry]);

  /** Exactly what gets hashed into the search key, server-side and here. */
  const filters: SearchFilters = useMemo(() => {
    const out: SearchFilters = {};
    if (keywords.trim()) out.keywords = keywords.trim();
    if (jobTitles.length) out.job_titles = jobTitles;
    if (salesNav && seniority.length) out.seniority = seniority;
    if (salesNav && headcount.length) out.company_headcount = headcount;
    if (degrees.length) out.network_distance = degrees;
    if (industry.length) out.industry = industry.map((o) => o.id);
    if (location.length) out.location = location.map((o) => o.id);
    return out;
  }, [keywords, jobTitles, seniority, headcount, degrees, industry, location, salesNav]);

  const hasFilters = Object.keys(filters).length > 0;

  // Declared first because the other queries key their polling off it.
  const { data: progress } = useQuery({
    queryKey: ["linkedin-search", "progress"],
    queryFn: getSearchProgress,
    refetchInterval: (q) =>
      (q.state.data as SearchProgress | undefined)?.status === "running" ? 2000 : false,
  });
  const running = progress?.status === "running";

  // The search key the server assigned to the last run, so the counts and the
  // list scope to the filters actually searched rather than to whatever is
  // currently typed in the boxes.
  const [searchKey, setSearchKey] = useAccountScopedState<string>("key", scopeId, "");

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["linkedin-search", "status", activeMessage, tabAccountId, searchKey],
    queryFn: () =>
      getSearchStatus({
        message: activeMessage || undefined,
        accountId: tabAccountId || undefined,
        searchKey: searchKey || undefined,
      }),
    // A mutation only reports that the background job STARTED, so without
    // polling the tiles would sit at zero while the job filled the database.
    refetchInterval: running ? 3000 : false,
  });
  const { data: principals } = useQuery({
    queryKey: ["principals", "active"],
    queryFn: () => listPrincipals({ active: true }),
  });

  // A job's last few results land after its final poll, so refresh once more on
  // the running -> finished edge; nothing polls the tiles once it is done.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (running) {
      wasRunning.current = true;
      return;
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      qc.invalidateQueries({ queryKey: ["linkedin-search"] });
    }
  }, [running, qc]);

  const { data: leads, isLoading } = useQuery({
    queryKey: [
      "linkedin-search",
      "list",
      activeMessage,
      statusFilter,
      tabAccountId,
      searchKey,
    ],
    queryFn: () =>
      listSearchLeads({
        limit: 500,
        ...(activeMessage ? { message: activeMessage } : {}),
        ...(statusFilter ? { status: statusFilter } : {}),
        ...(tabAccountId ? { account_id: tabAccountId } : {}),
        ...(searchKey ? { search_key: searchKey } : {}),
      }),
    enabled: !!status?.active_account_id,
    refetchInterval: running ? 4000 : false,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["linkedin-search"] });

  const accounts = status?.accounts ?? [];
  const activeId = status?.active_account_id ?? null;
  useEffect(() => {
    // Pin whatever the server had selected the first time it is known. Without
    // this the page's state would sit under a placeholder key until someone
    // picked an account by hand, and would not be per-account at all.
    if (!tabAccountId && activeId) setTabAccountId(activeId);
  }, [tabAccountId, activeId, setTabAccountId]);
  useEffect(() => {
    // A pin naming an account that is no longer connected clears itself, rather
    // than leaving the page pointed at nothing.
    const list = status?.accounts ?? [];
    if (tabAccountId && list.length > 0 && !list.some((a) => a.id === tabAccountId)) {
      setTabAccountId("");
    }
  }, [tabAccountId, status, setTabAccountId]);
  const stats = status?.stats ?? null;

  // Which principal each draft is filed under. Derived from the connected
  // LinkedIn account by name, because that account is what actually sends.
  const attributedPrincipal = useMemo(() => {
    const list = principals?.items ?? [];
    if (list.length === 0) return undefined;
    const accountName = (status?.active_account_name ?? "").trim().toLowerCase();
    const match = accountName
      ? list.find((p) => (p.name ?? "").trim().toLowerCase() === accountName)
      : undefined;
    return match ?? list[0];
  }, [principals, status?.active_account_name]);

  const previewName = leads?.items?.[0]?.name ?? null;
  // Read from the server, never hard-coded: this page used to say 300 while the
  // backend trimmed at 200, so a long note lost its tail with no warning.
  const noteMax = status?.invite_note_max_chars ?? 200;
  const noteLength = inviteNote.trim().length;

  const selectAccount = useMutation({
    mutationFn: (id: string) => selectLinkedInAccount(id),
    onSuccess: (_d, id) => {
      setTabAccountId(id);
      invalidate();
    },
  });

  // Its own connect, rather than sending people to the Followers page for it.
  // Reconnecting is part of THIS page's troubleshooting: a Sales Navigator seat
  // added after the account was linked is invisible to the old session, and the
  // only fix is to link it again.
  const connectAccount = useMutation({
    mutationFn: (label: string) => createLinkedInConnectLink(label),
    onSuccess: (res) => {
      if (res.url) window.open(res.url, "_blank", "noopener");
      setNote(
        "Opened LinkedIn in a new tab. Finish the login there, then come back " +
          "and refresh this page."
      );
    },
    onError: () =>
      setNote("Could not create a connect link — check the Unipile configuration."),
  });

  const search = useMutation({
    mutationFn: () =>
      runLinkedInSearch({
        filters,
        api,
        accountId: tabAccountId || undefined,
      }),
    onSuccess: (data) => {
      if (data.search_key) setSearchKey(data.search_key);
      setNote(data.message);
      invalidate();
    },
    onError: () => setNote("Could not start the search."),
  });

  const hasCopy = Boolean(message.trim() || inviteNote.trim());

  const generate = useMutation({
    // `fresh` throws away what is in the boxes; the default hands it to the
    // model as "not this" so a re-roll actually reads differently.
    mutationFn: ({ fresh }: { fresh: boolean }) =>
      generateSearchCopy({
        goal,
        jobTitles: jobTitles.length ? jobTitles : undefined,
        keywords: keywords.trim() || undefined,
        avoidNote: fresh ? undefined : inviteNote,
        avoidMessage: fresh ? undefined : message,
      }),
    onSuccess: (data) => {
      // Straight into the boxes, where it can be read and edited. Committing the
      // campaign is still a separate press, so nothing is keyed to this text
      // until the user decides it is right.
      setMessage(data.message);
      setInviteNote(data.invitation_note);
      setNote(
        data.note_trimmed
          ? "Drafted. The note came back long and was trimmed to fit — read it before sending."
          : "Drafted below. Read both, edit anything, then draft for your leads."
      );
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      setNote(detail ?? "Could not draft the copy.");
    },
  });

  const requireMessage = (): string | null => {
    const text = message.trim();
    if (!text) {
      setNote("Write the message first — it is what gets sent.");
      return null;
    }
    // Committing the text is what keys the campaign; typing must not re-key it
    // on every keystroke.
    setActiveMessage(text);
    return text;
  };

  const draft = useMutation({
    mutationFn: ({ text, append }: { text: string; append?: number }) =>
      draftAllSearchLeads({
        filters,
        message: text,
        principalId: attributedPrincipal?.id as number,
        invitationNote: inviteNote.trim() || undefined,
        accountId: tabAccountId || undefined,
        ...(append ? { limit: append } : {}),
        ...(!append && Number(draftLimit) > 0 ? { target: Number(draftLimit) } : {}),
      }),
    onSuccess: (data) => {
      setNote(data.message);
      invalidate();
    },
    onError: () => setNote("Could not start drafting."),
  });

  const approve = useMutation({
    mutationFn: (text: string) =>
      approveAllSearchLeads({
        filters,
        message: text,
        accountId: tabAccountId || undefined,
      }),
    onSuccess: (data) => {
      setNote(`Approved ${data.approved} message(s).`);
      invalidate();
    },
    onError: () => setNote("Could not approve."),
  });

  const send = useMutation({
    mutationFn: (text: string) =>
      sendAllSearchLeads({
        filters,
        message: text,
        accountId: tabAccountId || undefined,
      }),
    onSuccess: (data) => {
      setNote(data.message);
      invalidate();
    },
    onError: () => setNote("Could not start sending."),
  });

  const stop = useMutation({
    mutationFn: stopSearchJob,
    onSuccess: (data) => {
      setNote(data.message);
      invalidate();
    },
  });

  const busy =
    running ||
    search.isPending ||
    draft.isPending ||
    approve.isPending ||
    send.isPending;

  if (statusLoading) return <Loading />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Classic Search LinkedIn"
        subtitle={
          "Find people with LinkedIn's own search instead of Apollo, then reach " +
          "them the way the LinkedIn tab already does — a direct message if you " +
          "are already connected, otherwise a connection request carrying your " +
          "note. Nothing sends until you press Send."
        }
      />

      {note && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-900">
          {note}
        </div>
      )}

      {progress && running && <ProgressBar progress={progress} />}
      {progress && !running && progress.message && dismissed !== progress.message && (
        <div className="flex items-start justify-between gap-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
          <span>{progress.message}</span>
          <button
            type="button"
            onClick={() => setDismissed(progress.message ?? null)}
            className="shrink-0 text-xs font-medium underline-offset-2 hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* --- Account --- */}
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              LinkedIn account
            </label>
            <select
              value={activeId ?? ""}
              onChange={(e) => e.target.value && selectAccount.mutate(e.target.value)}
              className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
              disabled={busy}
            >
              <option value="">Select an account…</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name ?? a.id}
                </option>
              ))}
            </select>
          </div>
          <div className="text-xs text-slate-500">
            {status?.supports_search ? (
              <Badge tone="green">Connected</Badge>
            ) : (
              <Badge tone="amber">
                This account cannot search — connect it below first
              </Badge>
            )}
          </div>
          <Button
            variant="secondary"
            onClick={() =>
              connectAccount.mutate(
                status?.active_account_name
                  ? `Reconnect ${status.active_account_name}`
                  : "Reconnect LinkedIn account"
              )
            }
            disabled={busy || connectAccount.isPending || !activeId}
            title="Link this same LinkedIn account again — use it when a subscription was added after it was first connected"
          >
            {connectAccount.isPending ? "Opening…" : "Reconnect this account"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => connectAccount.mutate("New LinkedIn account")}
            disabled={busy || connectAccount.isPending}
            title="Link a different LinkedIn account"
          >
            Connect another
          </Button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Sales Navigator has to be on the account at the moment it is linked. If
          the seat was added later, LinkedIn refuses the search
          (&ldquo;feature not subscribed&rdquo;) until the account is reconnected
          — open Sales Navigator in the same browser first, then press Reconnect.
        </p>
      </Card>

      {/* --- Search --- */}
      <Card>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">1. Find people</h2>
          <div className="flex gap-1.5">
            {(["classic", "sales_navigator"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                disabled={busy}
                onClick={() => setApi(mode)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                  api === mode
                    ? "bg-slate-900 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {mode === "classic" ? "Classic search" : "Sales Navigator"}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Sales Navigator supports more filters and needs that subscription on the
          selected account. Classic search works on any connected account.
        </p>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Keywords
            </label>
            <input
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              disabled={busy}
              placeholder="e.g. healthcare AI"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </div>
          <MultiSelectDropdown
            label="Job titles"
            selected={jobTitles}
            onChange={setJobTitles}
            options={JOB_TITLE_OPTIONS}
            placeholder="Pick from the list, or type any title…"
            hint={
              salesNav
                ? "Anyone holding any of these titles matches. The list is a starting point — type anything."
                : "Classic search takes one title box, so several are joined with OR. Type anything."
            }
          />
          {/* The id namespaces differ per API — classic resolves LOCATION and
              INDUSTRY, Sales Navigator resolves REGION and SALES_INDUSTRY — so
              the picker asks for the right type and the selections are cleared
              when the mode changes. Reusing an id across modes finds nobody. */}
          <IdPicker
            label="Location"
            kind={salesNav ? "REGION" : "LOCATION"}
            accountId={tabAccountId || activeId || undefined}
            selected={location}
            onChange={setLocation}
            disabled={busy}
            presets={LOCATION_PRESETS}
          />
          <IdPicker
            label="Industry"
            kind={salesNav ? "SALES_INDUSTRY" : "INDUSTRY"}
            accountId={tabAccountId || activeId || undefined}
            selected={industry}
            onChange={setIndustry}
            disabled={busy}
            presets={INDUSTRY_PRESETS}
          />
        </div>

        <div className="mt-3 space-y-3">
          {/* LinkedIn's classic search has no seniority or headcount filter at
              all. Showing them as usable there would have been a lie: the
              request is rejected outright rather than ignoring the field. */}
          <div className={salesNav ? "" : "opacity-50"}>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
              Seniority
              {!salesNav && (
                <span className="ml-1 normal-case text-slate-400">
                  — Sales Navigator only
                </span>
              )}
            </div>
            <ChipGroup
              options={SENIORITY.map((s) => ({ value: s, label: s }))}
              selected={seniority}
              onChange={setSeniority}
              disabled={busy || !salesNav}
            />
          </div>
          <div className={salesNav ? "" : "opacity-50"}>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
              Company headcount
              {!salesNav && (
                <span className="ml-1 normal-case text-slate-400">
                  — Sales Navigator only
                </span>
              )}
            </div>
            <ChipGroup
              options={HEADCOUNT.map((h) => ({ value: h, label: h }))}
              selected={headcount}
              onChange={setHeadcount}
              disabled={busy || !salesNav}
            />
          </div>
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
              Connection degree
            </div>
            <ChipGroup
              options={DEGREES.map((d) => ({ value: d.value, label: d.label }))}
              selected={degrees}
              onChange={setDegrees}
              disabled={busy}
            />
            <p className="mt-1 text-[11px] text-slate-500">
              Leave empty for every degree. 1st-degree people get a direct
              message; everyone else gets a connection request first.
            </p>
          </div>
        </div>

        {/* No "how many pages" control. One press brings the 50 LinkedIn returns
            per page, which is also the most that can be messaged in a day, and
            the next press continues from where this one stopped. */}
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <Button
            onClick={() => search.mutate()}
            disabled={busy || !activeId || !hasFilters}
            title={
              hasFilters
                ? "Bring in the next 50 people matching these filters"
                : "Fill in at least one filter first"
            }
          >
            {progress?.job === "search" && running
              ? "Searching…"
              : "Search LinkedIn"}
          </Button>
          <span className="text-xs text-slate-500">
            Brings 50 at a time — the most that can be messaged in a day. Press
            it again for the next 50.
          </span>
          {stats && (
            <span className="text-xs text-slate-500">
              {stats.leads_total} stored for these filters
            </span>
          )}
        </div>
      </Card>

      {/* --- Message --- */}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900">2. Write the message</h2>

        {/* Optional shortcut. Describe the campaign and Claude fills the two
            boxes below; they stay fully editable, and only what is in them is
            ever sent. Leave this empty and write the boxes yourself. */}
        <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Campaign goal{" "}
            <span className="normal-case text-slate-400">
              (optional — draft the copy for me)
            </span>
          </label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={4}
            disabled={busy || generate.isPending}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="Who you want to reach, what you are offering, who you are. e.g. Book 15-minute intro calls with pharmacy owners and operations leaders about automating prescription intake, refills and prior auth…"
          />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() => generate.mutate({ fresh: !hasCopy })}
              disabled={busy || generate.isPending || !goal.trim()}
              title={
                hasCopy
                  ? "Write a different version — the current one is shown to the model as what NOT to repeat"
                  : "Write the invitation note and the message from this goal"
              }
            >
              {generate.isPending
                ? "Drafting…"
                : hasCopy
                  ? "Regenerate"
                  : "Draft with AI"}
            </Button>
            {hasCopy && (
              <Button
                variant="secondary"
                onClick={() => {
                  setMessage("");
                  setInviteNote("");
                  setNote("Cleared. Write them yourself, or draft again.");
                }}
                disabled={busy || generate.isPending}
                title="Empty both boxes"
              >
                Clear copy
              </Button>
            )}
            <span className="text-[11px] text-slate-500">
              {hasCopy
                ? "Regenerate writes a different version — it will not repeat what is below."
                : "Fills the two boxes below — both stay editable, and nothing is sent until you press Send."}
            </span>
          </div>
        </div>

        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={5}
          disabled={busy}
          className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          placeholder="Sent exactly as written, with only 'Hi <first name>,' added at the top."
        />
        <p className="mt-1 text-xs text-slate-500">
          Sent exactly as written — nothing rewrites or personalises it. The text
          also identifies the campaign: change it and you start a new one, so the
          same people become eligible again.
        </p>

        <div className="mt-3">
          <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
            Invitation note{" "}
            <span className="normal-case text-slate-400">
              (optional, max {noteMax} characters)
            </span>
          </label>
          <textarea
            value={inviteNote}
            onChange={(e) => setInviteNote(e.target.value)}
            rows={2}
            disabled={busy}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="Carried by the connection request. Blank = the message above, trimmed."
          />
          {/* Anything past the limit is cut server-side without an error, so the
              count is shown rather than left to be discovered in a sent invite. */}
          <div className="mt-1 flex justify-between text-[11px]">
            <span className="text-slate-500">
              LinkedIn caps the note on a connection request. Whatever is blank
              here falls back to the message above, trimmed to the same length.
            </span>
            <span
              className={
                noteLength > noteMax ? "font-medium text-rose-600" : "text-slate-400"
              }
            >
              {noteLength}/{noteMax}
              {noteLength > noteMax
                ? ` — last ${noteLength - noteMax} will be cut`
                : ""}
            </span>
          </div>
        </div>

        {message.trim() && (
          <div className="mt-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Preview{previewName ? ` — as ${previewName} will see it` : ""}
            </div>
            <pre className="mt-1 whitespace-pre-wrap rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-800">
              {`Hi ${firstNameOf(previewName)},\n\n${message.trim()}`}
            </pre>
          </div>
        )}
      </Card>

      {/* --- Draft / approve / send --- */}
      <Card>
        <h2 className="text-sm font-semibold text-slate-900">
          3. Draft, approve, send
        </h2>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">
              Draft how many{" "}
              <span className="normal-case text-slate-400">(total)</span>
            </label>
            <input
              type="number"
              min={1}
              value={draftLimit}
              onChange={(e) => setDraftLimit(e.target.value)}
              disabled={busy}
              className="mt-1 w-24 rounded-md border border-slate-300 px-3 py-2 text-sm"
              title="How many people to prepare this message for, in total. Blank = everyone found."
            />
          </div>
          <Button
            onClick={() => {
              const text = requireMessage();
              if (!text) return;
              if (!attributedPrincipal) {
                setNote(
                  "Add a principal on the Principals page first — drafts are filed against one."
                );
                return;
              }
              draft.mutate({ text });
            }}
            disabled={busy || !activeId || !hasFilters}
          >
            {progress?.job === "draft" && running
              ? "Drafting…"
              : Number(draftLimit) > 0
                ? `Draft ${Number(draftLimit)}`
                : stats
                  ? `Draft all (${stats.eligible})`
                  : "Draft all"}
          </Button>

          {/* The explicit "more" control, separate so the box above keeps
              meaning a total — one number cannot mean both. */}
          <label className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-slate-500">Append</span>
            <input
              type="number"
              min={1}
              value={appendCount}
              placeholder="0"
              onChange={(e) => setAppendCount(e.target.value)}
              disabled={busy}
              className="w-20 rounded-md border border-slate-300 px-2 py-2 text-sm"
              title="Draft this many MORE, on top of what already exists."
            />
          </label>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (!text) return;
              if (!attributedPrincipal) {
                setNote("Add a principal on the Principals page first.");
                return;
              }
              draft.mutate({ text, append: Number(appendCount) });
            }}
            disabled={busy || !activeId || !(Number(appendCount) > 0)}
          >
            {Number(appendCount) > 0 ? `Append ${Number(appendCount)}` : "Append"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (text) approve.mutate(text);
            }}
            disabled={busy || !activeId}
          >
            {stats ? `Approve all (${stats.draft})` : "Approve all"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              const text = requireMessage();
              if (text) send.mutate(text);
            }}
            disabled={busy || !activeId}
            title="Approve and send everything open for this message, paced and capped"
          >
            {progress?.job === "send" && running
              ? "Sending…"
              : stats
                ? `Approve & send all (${stats.draft + stats.approved})`
                : "Approve & send all"}
          </Button>
          {stats && stats.draft + stats.approved > stats.remaining_today && (
            <span className="text-xs text-amber-700">
              only {stats.remaining_today} can go today
            </span>
          )}
          {running && (
            <Button
              variant="danger"
              onClick={() => stop.mutate()}
              disabled={stop.isPending || progress?.stop_requested}
            >
              {progress?.stop_requested ? "Stopping…" : "Stop"}
            </Button>
          )}
        </div>

        {/* The number above is a target TOTAL, which reads like "add this many"
            right up until it quietly does nothing. Say the arithmetic out loud
            rather than leaving it to a tooltip. */}
        {Number(draftLimit) > 0 && stats && (
          <p className="mt-2 text-xs text-slate-500">
            &ldquo;Draft {Number(draftLimit)}&rdquo; means finish with{" "}
            {Number(draftLimit)} in total for this message, not {Number(draftLimit)}{" "}
            more.{" "}
            {stats.all >= Number(draftLimit)
              ? `You already have ${stats.all}, so it will do nothing — use Append to add more on top.`
              : `You have ${stats.all}, so it will draft ${
                  Number(draftLimit) - stats.all
                } more.`}
          </p>
        )}

        {attributedPrincipal && (
          <p className="mt-2 text-xs text-slate-500">
            Sent from{" "}
            <span className="font-medium text-slate-700">
              {status?.active_account_name ?? "the selected LinkedIn account"}
            </span>
            , recorded against{" "}
            <span className="font-medium text-slate-700">
              {attributedPrincipal.name}
            </span>
            .
          </p>
        )}

        {stats && (
          <div className="mt-3 space-y-3">
            <CountRow stats={stats} />
            <div className="flex flex-wrap gap-4 text-xs text-slate-500">
              <span>
                {stats.remaining_today} of {stats.cap} sends left today for this
                account
                {stats.sent_today > 0 ? ` (${stats.sent_today} used)` : ""}
              </span>
              <span>{stats.eligible} still to draft</span>
              {stats.contacted_ever > 0 && (
                <span>{stats.contacted_ever} already contacted with this message</span>
              )}
              {stats.needs_review > 0 && (
                <span className="text-amber-700">{stats.needs_review} needs review</span>
              )}
            </div>
          </div>
        )}
      </Card>

      {/* --- Tabs + list --- */}
      <div className="mb-3 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => {
          const count = stats
            ? tab.key === ""
              ? stats.all
              : tab.key === "pending"
                ? stats.eligible
                : (stats[tab.key as keyof SearchStats] as number)
            : null;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setStatusFilter(tab.key)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                statusFilter === tab.key
                  ? "bg-slate-900 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {tab.label}
              {count != null ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>

      <Card>
        {isLoading ? (
          <Loading />
        ) : (leads?.items ?? []).length === 0 ? (
          <EmptyState
            message={
              hasFilters
                ? 'Nothing here yet — press "Search LinkedIn" to find people.'
                : "Fill in at least one filter above, then search."
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-500">
                  <th className="px-3 py-2">Person</th>
                  <th className="px-3 py-2">How they get reached</th>
                  <th className="px-3 py-2">State</th>
                  <th className="px-3 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {(leads?.items ?? []).map((row) => (
                  <LeadRow key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
