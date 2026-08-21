import axios from "axios";
import type {
  AgentConfig,
  AgentCopyVariantsResponse,
  AgentPlan,
  AgentPlaybook,
  AgentRun,
  AgentVariantsResponse,
  AnalyticsOut,
  AnalyticsQuery,
  BulkCampaign,
  BulkCampaignDetail,
  BulkLookup,
  BulkRecipient,
  CampaignCreatePayload,
  CampaignBulkSend,
  CampaignDashboard,
  CampaignDetail,
  CampaignList,
  CampaignProspects,
  CampaignSummary,
  CampaignUpdatePayload,
  Call,
  CallConfig,
  DashboardStats,
  DiscoveryRun,
  EmailDraft,
  IngestSummary,
  IndexFileResult,
  FollowerRow,
  FollowersProgress,
  FollowersStatus,
  LinkedInAccount,
  LinkedInAccountsResponse,
  LinkedInInviteStats,
  LinkedInMessage,
  LinkedInSendProgress,
  Mailbox,
  OptimizationState,
  OptimizationUpdate,
  Organization,
  Page,
  ProviderHealth,
  Principal,
  PrincipalDocument,
  Prospect,
  RelevanceInsight,
  SearchDefinition,
  UploadSummary,
} from "../types";

// Dev: Vite proxies "/" to the API. Azure web app: set VITE_API_BASE_URL to the API host.
const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") || "/";
export const api = axios.create({ baseURL: apiBase, timeout: 30_000 });

// --- Stats ---
export const getStats = () =>
  api.get<DashboardStats>("/api/stats").then((r) => r.data);

// --- Analytics ---
export const getAnalytics = (query: AnalyticsQuery = {}) => {
  const params = new URLSearchParams();
  // days=0 is meaningful ("all time"), so check for undefined rather than falsy.
  if (query.days !== undefined) params.set("days", String(query.days));
  if (query.start) params.set("start", query.start);
  if (query.end) params.set("end", query.end);
  if (query.principal_id !== undefined)
    params.set("principal_id", String(query.principal_id));
  if (query.campaign_id !== undefined)
    params.set("campaign_id", String(query.campaign_id));
  const qs = params.toString();
  return api
    .get<AnalyticsOut>(`/api/analytics${qs ? `?${qs}` : ""}`)
    .then((r) => r.data);
};

// --- Pipeline optimization ---
export const getOptimization = () =>
  api.get<OptimizationState>("/api/optimization").then((r) => r.data);
export const updateOptimization = (payload: OptimizationUpdate) =>
  api.put<OptimizationState>("/api/optimization", payload).then((r) => r.data);

export const getProviderHealth = (probe = false) =>
  api
    .get<ProviderHealth>("/api/provider-health", { params: { probe } })
    .then((r) => r.data);
export const resetPipeline = () =>
  api
    .post<{ deleted: Record<string, number>; message: string }>(
      "/api/reset-pipeline?confirm=true"
    )
    .then((r) => r.data);

// --- Principals ---
export interface PrincipalPayload {
  name: string;
  headline?: string;
  linkedin_url: string;
  phone: string;
  email_signature?: string | null;
  outreach_mailbox_id?: string | null;
  document_focus?: string;
  bio?: string;
  background?: string;
  focus_areas?: string[];
  target_sectors?: string[];
  investment_themes?: string[];
  acquisition_themes?: string[];
  target_titles?: string[];
  target_seniorities?: string[];
  geographies?: string[];
  opportunity_types?: string[];
  value_props?: string[];
  is_active?: boolean;
}
export const listPrincipals = (params: Record<string, unknown> = {}) =>
  api.get<Page<Principal>>("/api/principals", { params }).then((r) => r.data);
export const getPrincipal = (id: number) =>
  api.get<Principal>(`/api/principals/${id}`).then((r) => r.data);
export const createPrincipal = (payload: PrincipalPayload) =>
  api.post<Principal>("/api/principals", payload).then((r) => r.data);
export const updatePrincipal = (id: number, payload: PrincipalPayload) =>
  api.put<Principal>(`/api/principals/${id}`, payload).then((r) => r.data);
export const deletePrincipal = (id: number) =>
  api.delete(`/api/principals/${id}`).then((r) => r.data);

// --- Principal context documents ---
export const listPrincipalDocuments = (id: number) =>
  api
    .get<PrincipalDocument[]>(`/api/principals/${id}/documents`)
    .then((r) => r.data);
export const uploadPrincipalDocuments = (id: number, files: File[]) => {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  return api
    .post<UploadSummary>(`/api/principals/${id}/documents/upload`, form, {
      timeout: 120000,
    })
    .then((r) => r.data);
};
export const indexPrincipalDocument = (
  id: number,
  filename: string,
  force = false
) =>
  api
    .post<IndexFileResult>(
      `/api/principals/${id}/documents/index-file`,
      {},
      { params: { filename, force }, timeout: 180000 }
    )
    .then((r) => r.data);
export const ingestPrincipalDocuments = (id: number, force = false) =>
  api
    .post<IngestSummary>(
      `/api/principals/${id}/documents/ingest`,
      {},
      { params: { force }, timeout: 600000 }
    )
    .then((r) => r.data);
export const deletePrincipalDocument = (id: number, documentId: number) =>
  api
    .delete(`/api/principals/${id}/documents/${documentId}`)
    .then((r) => r.data);

export interface PrincipalDossier {
  documents_total: number;
  documents_usable: number;
  proof_points_total: number;
  proof_points_unique: number;
  themes: string[];
  top_proof_points: string[];
  documents: {
    id: number;
    filename: string;
    doc_type?: string | null;
    status: string;
    relevance_score?: number | null;
    relevance_note?: string | null;
    summary?: string | null;
    key_facts: string[];
    themes: string[];
    indexed_at?: string | null;
  }[];
  used_in: string[];
}
export const getPrincipalDossier = (id: number) =>
  api.get<PrincipalDossier>(`/api/principals/${id}/dossier`).then((r) => r.data);

// --- Search definitions ---
export const listSearchDefinitions = (params: Record<string, unknown> = {}) =>
  api
    .get<Page<SearchDefinition>>("/api/search-definitions", { params })
    .then((r) => r.data);
export const createSearchDefinition = (payload: Partial<SearchDefinition>) =>
  api
    .post<SearchDefinition>("/api/search-definitions", payload)
    .then((r) => r.data);

// --- Discovery ---
export interface DiscoveryRunPayload {
  principal_id: number;
  search_definition_id?: number;
  industries?: string[];
  company_types?: string[];
  healthcare_sectors?: string[];
  geographies?: string[];
  titles?: string[];
  seniorities?: string[];
  keywords?: string[];
  themes?: string[];
  employee_min?: number;
  employee_max?: number;
  org_limit?: number;
  people_limit?: number;
  people_first?: boolean;
  generate_insights?: boolean;
  organization_job_titles?: string[];
  contact_email_status?: string[];
  organization_domains?: string[];
  auto_expand_to_target?: boolean;
  auto_process?: boolean;
  /** Plain-language goal from AI-assisted setup — drives relevance research. */
  search_goal?: string;
  /** Skip anyone missing an email or LinkedIn URL; keep searching until the
   * requested count of COMPLETE prospects is found. */
  require_email_and_linkedin?: boolean;
}
// Discovery now runs in the background and returns immediately (202) with a
// pending run; poll getDiscoveryRun(id) until status is completed/failed.
export const runDiscovery = (payload: DiscoveryRunPayload) =>
  api
    .post<DiscoveryRun>("/api/discovery/run", payload, { timeout: 60000 })
    .then((r) => r.data);
export const listDiscoveryRuns = (params: Record<string, unknown> = {}) =>
  api.get<Page<DiscoveryRun>>("/api/discovery/runs", { params }).then((r) => r.data);
export const getDiscoveryRun = (id: number) =>
  api.get<DiscoveryRun>(`/api/discovery/runs/${id}`).then((r) => r.data);

// Run-level bulk jobs (all return immediately; poll the run for job_* progress).
export const revealRunEmails = (id: number) =>
  api.post<DiscoveryRun>(`/api/discovery/runs/${id}/reveal`).then((r) => r.data);
export const approveRunProspects = (id: number, contactIds?: number[]) =>
  api
    .post<DiscoveryRun>(`/api/discovery/runs/${id}/approve`, {
      contact_ids: contactIds ?? null,
    })
    .then((r) => r.data);
export const draftRunEmails = (id: number, outreachGoal?: string, principalId?: number) =>
  api
    .post<DiscoveryRun>(`/api/discovery/runs/${id}/draft-emails`, {
      outreach_goal: outreachGoal ?? null,
      principal_id: principalId ?? null,
    })
    .then((r) => r.data);
export const pipelineRunProspects = (id: number, contactIds?: number[], outreachGoal?: string) =>
  api
    .post<DiscoveryRun>(`/api/discovery/runs/${id}/pipeline`, {
      contact_ids: contactIds ?? null,
      outreach_goal: outreachGoal ?? null,
    })
    .then((r) => r.data);
export const sendRunEmails = (id: number) =>
  api.post<DiscoveryRun>(`/api/discovery/runs/${id}/send-emails`).then((r) => r.data);
export const sendRunLinkedin = (id: number) =>
  api.post<DiscoveryRun>(`/api/discovery/runs/${id}/send-linkedin`).then((r) => r.data);
export const cancelRunJob = (id: number) =>
  api.post<DiscoveryRun>(`/api/discovery/runs/${id}/cancel-job`).then((r) => r.data);
export const deleteDiscoveryRun = (id: number) =>
  api
    .delete<{ run_id: number; deleted: Record<string, number>; message: string }>(
      `/api/discovery/runs/${id}`
    )
    .then((r) => r.data);

// --- Organizations ---
export const listOrganizations = (params: Record<string, unknown> = {}) =>
  api.get<Page<Organization>>("/api/organizations", { params }).then((r) => r.data);
export const getOrganization = (id: number) =>
  api.get<Organization>(`/api/organizations/${id}`).then((r) => r.data);
export const getOrganizationInsights = (id: number) =>
  api
    .get<RelevanceInsight[]>(`/api/organizations/${id}/insights`)
    .then((r) => r.data);
export const enrichOrganization = (id: number, maxContacts = 5) =>
  api
    .post<Prospect[]>(
      `/api/organizations/${id}/enrich`,
      { max_contacts: maxContacts },
      { timeout: 120000 }
    )
    .then((r) => r.data);

// --- Prospects ---
export interface ProspectFilters {
  company_id?: number;
  discovery_run_id?: number;
  campaign_id?: number;
  role_category?: string;
  status?: string;
  approved?: boolean;
  min_relevance?: number;
  researched?: boolean;
  has_email_and_linkedin?: boolean;
  search?: string;
  sort?: string;
  limit?: number;
  offset?: number;
}
export const listProspects = (params: ProspectFilters = {}) =>
  api.get<Page<Prospect>>("/api/prospects", { params }).then((r) => r.data);
export const getProspect = (id: number) =>
  api.get<Prospect>(`/api/prospects/${id}`).then((r) => r.data);
export const getProspectInsights = (id: number) =>
  api.get<RelevanceInsight[]>(`/api/prospects/${id}/insights`).then((r) => r.data);
export const revealProspect = (id: number) =>
  api
    .post<Prospect>(`/api/prospects/${id}/reveal`, {}, { timeout: 120000 })
    .then((r) => r.data);
export const researchProspect = (id: number) =>
  api
    .post<Prospect>(`/api/prospects/${id}/research`, {}, { timeout: 180000 })
    .then((r) => r.data);
export const setProspectApproval = (id: number, approved: boolean) =>
  api
    .post<Prospect>(`/api/prospects/${id}/approval`, {
      approved_for_outreach: approved,
    })
    .then((r) => r.data);
export const setProspectStatus = (id: number, status: string) =>
  api.post<Prospect>(`/api/prospects/${id}/status`, { status }).then((r) => r.data);

// --- Insights ---
export const listInsights = (params: Record<string, unknown> = {}) =>
  api.get<Page<RelevanceInsight>>("/api/insights", { params }).then((r) => r.data);
export const generateInsight = (payload: {
  principal_id: number;
  contact_id?: number;
  company_id?: number;
}) =>
  api.post<RelevanceInsight>("/api/insights/generate", payload).then((r) => r.data);

export interface BatchInsightSummary {
  total: number;
  researched: number;
  skipped: number;
  failed: number;
  qualified: number;
  auto_rejected: number;
  errors: string[];
}

export const batchGenerateInsights = (payload: {
  principal_id: number;
  discovery_run_id?: number;
  contact_ids?: number[];
  skip_existing?: boolean;
  auto_reject_below?: number;
}) =>
  api
    .post<BatchInsightSummary>("/api/insights/batch-generate", payload, {
      timeout: 600000,
    })
    .then((r) => r.data);

export interface BatchRevealSummary {
  total: number;
  revealed: number;
  skipped: number;
  failed: number;
  errors: string[];
}

export const batchRevealProspects = (payload: {
  discovery_run_id?: number;
  contact_ids?: number[];
}) =>
  api
    .post<BatchRevealSummary>("/api/prospects/batch-reveal", payload, {
      timeout: 600000,
    })
    .then((r) => r.data);

export interface DiscoveryProcessSummary {
  run_id: number;
  research: BatchInsightSummary;
  reveal: BatchRevealSummary;
}

export const processDiscoveryRun = (runId: number) =>
  api
    .post<DiscoveryProcessSummary>(`/api/discovery/runs/${runId}/process`, {}, {
      timeout: 600000,
    })
    .then((r) => r.data);

// --- Emails ---
export interface EmailFilters {
  status?: string;
  contact_id?: number;
  principal_id?: number;
  campaign_id?: number;
  bulk_campaign_id?: number;
  discovery_run_id?: number;
  limit?: number;
  offset?: number;
}
export const listEmails = (params: EmailFilters = {}) =>
  api.get<Page<EmailDraft>>("/api/emails", { params }).then((r) => r.data);
export const generateEmail = (payload: {
  principal_id: number;
  contact_id: number;
  insight_id?: number;
  regenerate?: boolean;
}) => api.post<EmailDraft>("/api/emails/generate", payload).then((r) => r.data);
export const regenerateEmail = (draftId: number) =>
  api
    .post<EmailDraft>(`/api/emails/${draftId}/regenerate`, {}, { timeout: 120000 })
    .then((r) => r.data);
export interface RegenerateRunResult {
  discovery_run_id: number;
  candidates: number;
  regenerated: number;
  errors: string[];
  provider_warnings?: string[];
}
export interface GenerateRunResult {
  discovery_run_id: number;
  candidates: number;
  generated: number;
  skipped: number;
  errors: string[];
  provider_warnings?: string[];
}
export const generateRunDrafts = (payload: {
  discovery_run_id: number;
  principal_id?: number;
  outreach_goal?: string;
}) =>
  api
    .post<GenerateRunResult>("/api/emails/generate-run", payload, {
      timeout: 600000,
    })
    .then((r) => r.data);
export const regenerateRunDrafts = (payload: {
  discovery_run_id: number;
  principal_id?: number;
  only_statuses?: string[];
}) =>
  api
    .post<RegenerateRunResult>("/api/emails/regenerate-run", payload, {
      timeout: 600000,
    })
    .then((r) => r.data);
export const deleteEmail = (id: number) =>
  api.delete(`/api/emails/${id}`).then(() => undefined);
export const updateEmail = (
  id: number,
  payload: { subject?: string; body?: string; from_mailbox?: string | null }
) => api.patch<EmailDraft>(`/api/emails/${id}`, payload).then((r) => r.data);
export const listMailboxes = () =>
  api
    .get<{ mailboxes: Mailbox[] }>("/api/emails/mailboxes")
    .then((r) => r.data.mailboxes);

// --- Bulk email campaigns ---
export const listBulkCampaigns = () =>
  api
    .get<{ items: BulkCampaign[] }>("/api/bulk-emails")
    .then((r) => r.data.items);
export const createBulkCampaign = (payload: { name: string; mailbox_id: string }) =>
  api.post<BulkCampaignDetail>("/api/bulk-emails", payload).then((r) => r.data);
export const getBulkCampaign = (id: number) =>
  api.get<BulkCampaignDetail>(`/api/bulk-emails/${id}`).then((r) => r.data);
export const updateBulkCampaign = (
  id: number,
  payload: {
    name?: string;
    mailbox_id?: string;
    purpose?: string | null;
    signature?: string | null;
  }
) => api.patch<BulkCampaignDetail>(`/api/bulk-emails/${id}`, payload).then((r) => r.data);
export const sendBulkChat = (id: number, message: string) =>
  api
    .post<BulkCampaignDetail>(
      `/api/bulk-emails/${id}/chat`,
      { message },
      { timeout: 300000 }
    )
    .then((r) => r.data);
export const startBulkDrafting = (id: number, regenerate = false) =>
  api
    .post<BulkCampaignDetail>(`/api/bulk-emails/${id}/draft`, { regenerate })
    .then((r) => r.data);
export const startBulkSending = (id: number, draftIds?: number[]) =>
  api
    .post<BulkCampaignDetail>(`/api/bulk-emails/${id}/send`, {
      draft_ids: draftIds ?? null,
    })
    .then((r) => r.data);
export const cancelBulkJob = (id: number) =>
  api.post<BulkCampaignDetail>(`/api/bulk-emails/${id}/cancel`).then((r) => r.data);
export const listBulkRecipients = (id: number) =>
  api.get<BulkRecipient[]>(`/api/bulk-emails/${id}/recipients`).then((r) => r.data);
export const startBulkLookup = (id: number, retryFailed = false) =>
  api
    .post<BulkCampaignDetail>(`/api/bulk-emails/${id}/lookup`, {
      retry_failed: retryFailed,
    })
    .then((r) => r.data);
export const listBulkLookups = (id: number) =>
  api.get<BulkLookup[]>(`/api/bulk-emails/${id}/lookups`).then((r) => r.data);
export const acceptBulkLookups = (id: number, lookupIds: number[]) =>
  api
    .post<BulkCampaignDetail>(`/api/bulk-emails/${id}/lookups/accept`, {
      lookup_ids: lookupIds,
    })
    .then((r) => r.data);
export const rejectBulkLookups = (id: number, lookupIds: number[]) =>
  api
    .post<BulkCampaignDetail>(`/api/bulk-emails/${id}/lookups/reject`, {
      lookup_ids: lookupIds,
    })
    .then((r) => r.data);
export const setBulkLookupEmail = (id: number, lookupId: number, email: string) =>
  api
    .patch<BulkCampaignDetail>(`/api/bulk-emails/${id}/lookups/${lookupId}`, { email })
    .then((r) => r.data);
export const removeBulkRecipient = (id: number, contactId: number) =>
  api.delete(`/api/bulk-emails/${id}/recipients/${contactId}`).then(() => undefined);
export const deleteBulkCampaign = (id: number) =>
  api.delete(`/api/bulk-emails/${id}`).then(() => undefined);

// --- LinkedIn outreach ---
export type LinkedInFilters = {
  status?: string;
  contact_id?: number;
  principal_id?: number;
  discovery_run_id?: number;
  limit?: number;
  offset?: number;
};
export const getLinkedInAccount = () =>
  api.get<LinkedInAccount>("/api/linkedin/account").then((r) => r.data);
export const listLinkedInAccounts = () =>
  api.get<LinkedInAccountsResponse>("/api/linkedin/accounts").then((r) => r.data);
/** Label a sending account by hand. An empty name clears the label. */
export const setLinkedInAccountName = (accountId: string, name: string) =>
  api
    .put<{ known_names: Record<string, { name: string; manual: boolean }> }>(
      "/api/linkedin/account-names",
      { account_id: accountId, name }
    )
    .then((r) => r.data);
export const createLinkedInConnectLink = (name?: string) =>
  api
    .post<{ url: string }>("/api/linkedin/connect-link", { name }, { timeout: 60000 })
    .then((r) => r.data);
export const selectLinkedInAccount = (accountId: string) =>
  api
    .post<{ active_account_id: string }>("/api/linkedin/select-account", {
      account_id: accountId,
    })
    .then((r) => r.data);
export const listLinkedInMessages = (params: LinkedInFilters = {}) =>
  api.get<Page<LinkedInMessage>>("/api/linkedin", { params }).then((r) => r.data);
/** Invitation funnel (sent vs accepted). Deliberately ignores the status tab —
 * these are totals for the whole (optionally run-scoped) set. */
export const getLinkedInStats = (
  params: Omit<LinkedInFilters, "status" | "limit" | "offset"> = {}
) => api.get<LinkedInInviteStats>("/api/linkedin/stats", { params }).then((r) => r.data);
export const generateLinkedIn = (payload: {
  principal_id: number;
  contact_id: number;
  outreach_goal?: string;
  regenerate?: boolean;
}) =>
  api
    .post<LinkedInMessage>("/api/linkedin/generate", payload, { timeout: 120000 })
    .then((r) => r.data);
/**
 * Start LinkedIn drafting for a run's approved prospects in the BACKGROUND.
 *
 * Replaces the inline /linkedin/generate-run call, which held the request open
 * for one Claude call per prospect: the browser gave up long before it finished
 * and the server was killed before its single end-of-loop commit, so a large run
 * reliably produced nothing. Progress lands on the run's job_* fields — poll
 * getDiscoveryRun(id) the way the Drafts page does.
 */
export const draftRunLinkedIn = (
  id: number,
  outreachGoal?: string,
  principalId?: number
) =>
  api
    .post<DiscoveryRun>(`/api/discovery/runs/${id}/draft-linkedin`, {
      outreach_goal: outreachGoal ?? null,
      principal_id: principalId ?? null,
    })
    .then((r) => r.data);
export const updateLinkedIn = (
  id: number,
  payload: { body?: string; invitation_note?: string }
) => api.patch<LinkedInMessage>(`/api/linkedin/${id}`, payload).then((r) => r.data);
export const setLinkedInStatus = (id: number, status: string) =>
  api
    .post<LinkedInMessage>(`/api/linkedin/${id}/status`, { status })
    .then((r) => r.data);
/** `accountId` is the account picked in THIS tab, so a single send goes from the
 *  same account as the rest of the tab even if another tab switched accounts. */
export const sendLinkedIn = (id: number, accountId?: string) =>
  api
    .post<LinkedInMessage>(
      `/api/linkedin/${id}/send`,
      {},
      { params: accountId ? { account_id: accountId } : undefined, timeout: 120000 }
    )
    .then((r) => r.data);
export const replyLinkedIn = (id: number, body: string) =>
  api
    .post<LinkedInMessage>(`/api/linkedin/${id}/reply`, { body }, { timeout: 120000 })
    .then((r) => r.data);
export const deleteLinkedIn = (id: number) =>
  api.delete(`/api/linkedin/${id}`).then(() => undefined);
export interface LinkedInSendOpenResult {
  matched: number;
  queued: number;
  held: number;
  cap: number;
  sent_today: number;
}
/** Approve + send all open (draft/approved) LinkedIn messages, paced + capped, in
 *  the background. Optional runId scopes to one discovery run. */
/** `accountId` is the account picked in THIS tab. Sending it explicitly is what
 *  lets two tabs drive two accounts at once, and pins the batch so another tab
 *  switching accounts cannot redirect it mid-run. */
export const sendOpenLinkedIn = (discoveryRunId?: number, accountId?: string) =>
  api
    .post<LinkedInSendOpenResult>(
      "/api/linkedin/send-open",
      { discovery_run_id: discoveryRunId, account_id: accountId },
      { timeout: 60000 }
    )
    .then((r) => r.data);
/** Live progress of the bulk send for ONE account, so a tab watching Taha's
 *  batch never sees Usama's numbers. */
export const getLinkedInSendProgress = (accountId?: string) =>
  api
    .get<LinkedInSendProgress>("/api/linkedin/send-progress", {
      params: accountId ? { account_id: accountId } : undefined,
    })
    .then((r) => r.data);
/** Halt the running bulk send for ONE account, after the message in flight.
 *  Other accounts' batches keep running. */
export const stopLinkedInSend = (accountId?: string) =>
  api
    .post<{ stopped: boolean; account_id?: string | null; message: string }>(
      "/api/linkedin/stop-send",
      {},
      { params: accountId ? { account_id: accountId } : undefined }
    )
    .then((r) => r.data);
export const checkLinkedInUpdates = () =>
  api
    .post<{ started: boolean; supported: boolean; message: string }>(
      "/api/linkedin/check-updates",
      {},
      { timeout: 60000 }
    )
    .then((r) => r.data);
export interface LinkedInScanProgress {
  status: string; // starting | running | done | idle
  total: number;
  done: number;
  accepted: number;
  replied: number;
}
export const getLinkedInScanProgress = () =>
  api.get<LinkedInScanProgress>("/api/linkedin/scan-progress").then((r) => r.data);

// --- Followers LinkedIn ---
// A separate lane from the prospect-driven LinkedIn calls above. It reuses the
// account picker (listLinkedInAccounts / selectLinkedInAccount) so there is one
// notion of "the active account" app-wide.

/** Header state: connection status, account list, and counts for one message. */
export const getFollowersStatus = (message?: string) =>
  api
    .get<FollowersStatus>("/api/linkedin-followers/status", {
      params: { message: message || undefined },
    })
    .then((r) => r.data);

/** Live progress of the running sync/draft/send job (poll while running). */
export const getFollowersProgress = () =>
  api.get<FollowersProgress>("/api/linkedin-followers/progress").then((r) => r.data);

export interface FollowerFilters {
  /** The exact message text; hashed server-side into the campaign key. */
  message?: string;
  /** draft | approved | sent | replied | pending */
  status?: string;
  limit?: number;
  offset?: number;
}
export const listFollowers = (params: FollowerFilters = {}) =>
  api
    .get<Page<FollowerRow>>("/api/linkedin-followers", { params })
    .then((r) => r.data);

/** Refresh the follower roster from LinkedIn, in the background. */
export const syncFollowers = () =>
  api
    .post<{ started: boolean; account_id?: string; message: string }>(
      "/api/linkedin-followers/sync",
      {},
      { timeout: 60000 }
    )
    .then((r) => r.data);

export interface FollowerJobStart {
  started: boolean;
  candidates?: number;
  matched?: number;
  campaign_key: string;
  message: string;
}
/** Draft DMs for followers not yet drafted for this message (background).
 *  The message is used verbatim with `Hi <first name>,` prepended — no model
 *  call. `limit` caps how many followers enter this campaign in one go. */
export const draftAllFollowers = (
  message: string,
  principalId: number,
  limit?: number
) =>
  api
    .post<FollowerJobStart>(
      "/api/linkedin-followers/draft-all",
      {
        message,
        principal_id: principalId,
        limit: limit && limit > 0 ? limit : null,
      },
      { timeout: 60000 }
    )
    .then((r) => r.data);

export const approveAllFollowers = (message: string) =>
  api
    .post<{ approved: number; campaign_key: string }>(
      "/api/linkedin-followers/approve-all",
      { message },
      { timeout: 60000 }
    )
    .then((r) => r.data);

/** Approve + send every open DM for this message — paced, capped, checkpointed. */
export const sendAllFollowers = (message: string) =>
  api
    .post<FollowerJobStart>(
      "/api/linkedin-followers/send-all",
      { message },
      { timeout: 60000 }
    )
    .then((r) => r.data);

export const stopFollowersJob = () =>
  api
    .post<{ stopped: boolean; message: string }>("/api/linkedin-followers/stop")
    .then((r) => r.data);
export const setEmailStatus = (id: number, status: string) =>
  api.post<EmailDraft>(`/api/emails/${id}/status`, { status }).then((r) => r.data);
export const sendEmail = (id: number) =>
  api.post<EmailDraft>(`/api/emails/${id}/send`).then((r) => r.data);
export const scheduleEmail = (id: number, scheduledAt: string) =>
  api
    .post<EmailDraft>(`/api/emails/${id}/schedule`, { scheduled_at: scheduledAt })
    .then((r) => r.data);
export const unscheduleEmail = (id: number) =>
  api.post<EmailDraft>(`/api/emails/${id}/unschedule`).then((r) => r.data);
export interface CheckRepliesResult {
  checked: number;
  replied: number;
  supported: boolean;
  error?: string | null;
  message?: string;
}
export const checkEmailReplies = () =>
  api
    .post<CheckRepliesResult>(`/api/emails/check-replies`, {}, { timeout: 120000 })
    .then((r) => r.data);

export interface FollowupResult {
  created: number;
  candidates: number;
  skipped_pending: number;
  days: number;
  drafts: EmailDraft[];
}
export const generateFollowups = (params: {
  days: number;
  limit?: number;
  approve?: boolean;
  principal_id?: number;
}) =>
  api
    .post<FollowupResult>(`/api/emails/followups/generate`, params, {
      timeout: 180000,
    })
    .then((r) => r.data);
export const createFollowup = (draftId: number) =>
  api.post<EmailDraft>(`/api/emails/${draftId}/followup`).then((r) => r.data);
export const draftContextualReply = (draftId: number) =>
  api
    .post<EmailDraft>(`/api/emails/${draftId}/draft-reply`, undefined, {
      timeout: 120000,
    })
    .then((r) => r.data);
export const replyToEmail = (draftId: number, body: string) =>
  api
    .post<EmailDraft>(`/api/emails/${draftId}/reply`, { body }, { timeout: 120000 })
    .then((r) => r.data);

// --- Agent ---
export const planAgentSearch = (payload: {
  objective_prompt: string;
  principal_id?: number;
  clarifying_answers?: Record<string, string>;
}) =>
  api.post<AgentPlan>("/api/agent/plan", payload).then((r) => r.data);

export const listAgentPlaybooks = (params: Record<string, unknown> = {}) =>
  api.get<Page<AgentPlaybook>>("/api/agent/playbooks", { params }).then((r) => r.data);

export const saveAgentPlaybook = (payload: {
  name: string;
  objective_prompt: string;
  clarifying_answers?: Record<string, string>;
  criteria: Record<string, unknown>;
  set_active?: boolean;
  principal_id?: number;
}) =>
  api.post<AgentPlaybook>("/api/agent/playbooks", payload).then((r) => r.data);

export const deleteAgentPlaybook = (id: number) =>
  api.delete(`/api/agent/playbooks/${id}`).then(() => undefined);

export const getAgentConfig = (principalId?: number) =>
  api
    .get<AgentConfig>("/api/agent/config", {
      params: principalId ? { principal_id: principalId } : {},
    })
    .then((r) => r.data);
export const updateAgentConfig = (
  payload: Partial<AgentConfig>,
  principalId?: number
) =>
  api
    .put<AgentConfig>("/api/agent/config", payload, {
      params: principalId ? { principal_id: principalId } : {},
    })
    .then((r) => r.data);
export const runAgentNow = (payload?: { principal_id?: number; playbook_id?: number }) =>
  api
    .post<AgentRun>("/api/agent/run", payload ?? {})
    .then((r) => r.data);
export const listAgentRuns = (params: Record<string, unknown> = {}) =>
  api.get<Page<AgentRun>>("/api/agent/runs", { params }).then((r) => r.data);
export const getAgentRun = (id: number) =>
  api.get<AgentRun>(`/api/agent/runs/${id}`).then((r) => r.data);
export const getCampaignDashboard = (principalId?: number, days = 14) =>
  api
    .get<CampaignDashboard>("/api/agent/dashboard", {
      params: {
        ...(principalId ? { principal_id: principalId } : {}),
        days,
      },
    })
    .then((r) => r.data);
// --- Campaigns (multi-campaign) ---
export const listCampaigns = (days = 14) =>
  api
    .get<CampaignList>("/api/campaigns", { params: { days } })
    .then((r) => r.data);
export const getCampaign = (id: number, days = 14) =>
  api
    .get<CampaignDetail>(`/api/campaigns/${id}`, { params: { days } })
    .then((r) => r.data);
export const getCampaignProspects = (id: number) =>
  api
    .get<CampaignProspects>(`/api/campaigns/${id}/prospects`)
    .then((r) => r.data);
export const createCampaign = (payload: CampaignCreatePayload) =>
  api
    .post<CampaignSummary>("/api/campaigns", payload, { timeout: 120000 })
    .then((r) => r.data);
export const updateCampaign = (id: number, payload: CampaignUpdatePayload) =>
  api.put<CampaignDetail>(`/api/campaigns/${id}`, payload).then((r) => r.data);
export const runCampaign = (id: number, resume = false, skipDiscovery = false) =>
  api
    .post<CampaignDetail>(`/api/campaigns/${id}/run`, null, {
      params: resume
        ? { resume: true, ...(skipDiscovery ? { skip_discovery: true } : {}) }
        : {},
    })
    .then((r) => r.data);
export const cancelCampaignRun = (id: number) =>
  api.post<CampaignDetail>(`/api/campaigns/${id}/cancel`).then((r) => r.data);
export const pauseCampaign = (id: number, keepScheduled = false) =>
  api
    .post<CampaignDetail>(`/api/campaigns/${id}/pause`, null, {
      params: keepScheduled ? { keep_scheduled: true } : {},
    })
    .then((r) => r.data);
export const resumeCampaign = (id: number) =>
  api.post<CampaignDetail>(`/api/campaigns/${id}/resume`).then((r) => r.data);
export const scheduleApprovedEmails = (id: number) =>
  api
    .post<CampaignDetail>(`/api/campaigns/${id}/schedule-approved`)
    .then((r) => r.data);
export const deleteCampaign = (id: number) =>
  api.delete(`/api/campaigns/${id}`).then(() => undefined);
// Bulk "Approve & send all" for a campaign. The loop runs on the server, so it
// keeps going once started — poll `getCampaignDraftSend` for progress.
export const startCampaignDraftSend = (id: number) =>
  api
    .post<CampaignBulkSend>(`/api/campaigns/${id}/send-drafts`)
    .then((r) => r.data);
export const getCampaignDraftSend = (id: number) =>
  api
    .get<CampaignBulkSend | null>(`/api/campaigns/${id}/send-drafts`)
    .then((r) => r.data);
export const cancelCampaignDraftSend = (id: number) =>
  api
    .post<CampaignBulkSend>(`/api/campaigns/${id}/send-drafts/cancel`)
    .then((r) => r.data);
export const listAgentVariants = (principalId?: number) =>
  api
    .get<AgentVariantsResponse>("/api/agent/variants", {
      params: principalId ? { principal_id: principalId } : {},
    })
    .then((r) => r.data);
export const regenerateAgentVariants = (principalId?: number) =>
  api
    .post<AgentVariantsResponse>(
      "/api/agent/variants/regenerate",
      {},
      { params: principalId ? { principal_id: principalId } : {} }
    )
    .then((r) => r.data);
export const listAgentCopyVariants = (principalId?: number) =>
  api
    .get<AgentCopyVariantsResponse>("/api/agent/copy-variants", {
      params: principalId ? { principal_id: principalId } : {},
    })
    .then((r) => r.data);
export const regenerateAgentCopyVariants = (principalId?: number) =>
  api
    .post<AgentCopyVariantsResponse>(
      "/api/agent/copy-variants/regenerate",
      {},
      { params: principalId ? { principal_id: principalId } : {} }
    )
    .then((r) => r.data);

// --- Calls ---
export const listCalls = (params: Record<string, unknown> = {}) =>
  api.get<Page<Call>>("/api/calls", { params }).then((r) => r.data);
export const generateCall = (payload: {
  principal_id: number;
  contact_id: number;
  insight_id?: number;
}) => api.post<Call>("/api/calls/generate", payload).then((r) => r.data);
export const setCallStatus = (id: number, status: string) =>
  api.post<Call>(`/api/calls/${id}/status`, { status }).then((r) => r.data);
export const placeCall = (id: number) =>
  api.post<Call>(`/api/calls/${id}/place`).then((r) => r.data);
export const getCallConfig = () =>
  api.get<CallConfig>("/api/calls/config").then((r) => r.data);
