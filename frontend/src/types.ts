// Types mirroring the backend Pydantic schemas (app/schemas/entities.py).

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** Progress of a campaign's server-side "Approve & send all".
 *  "interrupted" = a restart killed the worker; unsent drafts are still drafts,
 *  so starting again continues from where it stopped. */
export interface CampaignBulkSend {
  status: "running" | "done" | "cancelled" | "interrupted";
  total: number;
  done: number;
  sent: number;
  failed: number;
  errors: string[];
  cancel_requested: boolean;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface Principal {
  id: number;
  name: string;
  headline?: string | null;
  linkedin_url?: string | null;
  phone?: string | null;
  email_signature?: string | null;
  outreach_mailbox_id?: string | null;
  objective?: string | null;
  document_focus?: string | null;
  bio?: string | null;
  background?: string | null;
  focus_areas?: string[] | null;
  target_sectors?: string[] | null;
  investment_themes?: string[] | null;
  acquisition_themes?: string[] | null;
  target_titles?: string[] | null;
  target_seniorities?: string[] | null;
  geographies?: string[] | null;
  opportunity_types?: string[] | null;
  value_props?: string[] | null;
  is_active: boolean;
  created_at: string;
}

export interface PrincipalDocument {
  id: number;
  filename: string;
  doc_type?: string | null;
  status: string;
  char_count?: number | null;
  summary?: string | null;
  key_facts: string[];
  themes: string[];
  relevance_score?: number | null;
  relevance_note?: string | null;
  indexed_by?: string | null;
  indexed_at?: string | null;
}

export interface UploadSummary {
  folder: string;
  uploaded: string[];
  rejected: { file: string; reason: string }[];
  message?: string;
}

export interface IndexFileResult {
  file: string;
  action: string;
  relevance_score?: number;
  status?: string;
  proof_points?: number;
  error?: string;
}

export interface IngestSummary {
  folder: string;
  indexed: number;
  updated: number;
  skipped: number;
  failed: number;
  files: {
    file: string;
    action: string;
    error?: string;
    relevance_score?: number;
    status?: string;
    proof_points?: number;
  }[];
  uploaded?: string[];
  rejected?: { file: string; reason: string }[];
  error?: string;
}

export interface SearchDefinition {
  id: number;
  principal_id: number;
  name: string;
  industries?: string[] | null;
  company_types?: string[] | null;
  healthcare_sectors?: string[] | null;
  geographies?: string[] | null;
  titles?: string[] | null;
  seniorities?: string[] | null;
  keywords?: string[] | null;
  themes?: string[] | null;
  employee_min?: number | null;
  employee_max?: number | null;
  created_at: string;
}

export interface Organization {
  id: number;
  name: string;
  domain?: string | null;
  website?: string | null;
  linkedin_url?: string | null;
  industry?: string | null;
  employee_count?: number | null;
  headquarters?: string | null;
  phone?: string | null;
  funding?: string | null;
  revenue?: string | null;
  company_type?: string | null;
  sectors?: string[] | null;
  themes?: string[] | null;
  signals?: string[] | null;
  discovery_run_id?: number | null;
  enrichment_status: string;
  enrichment_source?: string | null;
  do_not_contact: boolean;
  created_at: string;
}

export interface Prospect {
  id: number;
  company_id?: number | null;
  name: string;
  title?: string | null;
  role_category?: string | null;
  seniority?: string | null;
  email?: string | null;
  email_status?: string | null;
  phone?: string | null;
  phone_reveal_status?: string | null;
  linkedin_url?: string | null;
  location?: string | null;
  source?: string | null;
  discovery_run_id?: number | null;
  confidence_score?: number | null;
  usefulness_score?: number | null;
  relevance_score?: number | null;
  rank_reason?: string | null;
  status: string;
  approved_for_outreach: boolean;
  do_not_contact: boolean;
  created_at: string;
  outreach_status?: string | null;
  insight_provider?: string | null;
}

export interface SourcedBullet {
  text: string;
  source_url?: string | null;
  source_title?: string | null;
  source_date?: string | null;
}

export interface RelevanceInsight {
  id: number;
  principal_id: number;
  contact_id?: number | null;
  company_id?: number | null;
  relevance_score: number;
  why_relevant?: string | null;
  why_speak_with_principal?: string | null;
  strategic_connection?: string | null;
  common_ground?: string | null;
  relevant_experience?: string | null;
  signals?: string[] | null;
  talking_points?: string[] | null;
  snapshot?: string | null;
  key_facts?: (string | SourcedBullet)[] | null;
  sources?: { title?: string | null; url: string }[] | null;
  identity_verified?: boolean;
  identity_warnings?: string[] | null;
  opportunity_type?: string | null;
  generated_by?: string | null;
  created_at: string;
}

export interface EmailDraft {
  id: number;
  principal_id?: number | null;
  bulk_campaign_id?: number | null;
  company_id?: number | null;
  contact_id?: number | null;
  insight_id?: number | null;
  subject: string;
  body: string;
  status: string;
  provider?: string | null;
  provider_message_id?: string | null;
  approved_by?: string | null;
  created_at: string;
  scheduled_at?: string | null;
  outlook_scheduled?: boolean;
  sent_at?: string | null;
  replied_at?: string | null;
  reply_snippet?: string | null;
  reply_body?: string | null;
  last_reply_check_at?: string | null;
  open_count?: number;
  first_opened_at?: string | null;
  last_opened_at?: string | null;
  principal_name?: string | null;
  from_email?: string | null;
  from_mailbox?: string | null;
  from_name?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_title?: string | null;
  company_name?: string | null;
  discovery_run_id?: number | null;
}

export interface Mailbox {
  id: string;
  label: string;
  from_email: string;
  from_name?: string;
  provider: string;
}

// --- Pipeline optimization (current vs cost-optimized mode) ---
export interface OptimizationCapability {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
  /** True when turning this on can change how drafts read. */
  affects_quality: boolean;
}

export interface OptimizationState {
  enabled: boolean;
  research_model: string;
  draft_model: string;
  capabilities: OptimizationCapability[];
}

export interface OptimizationUpdate {
  enabled?: boolean;
  capabilities?: Record<string, boolean>;
  draft_model?: string;
}

// --- Bulk email campaigns (pasted recipient list + chat brief) ---
export interface BulkCampaign {
  id: number;
  name: string;
  mailbox_id?: string | null;
  mailbox_label?: string | null;
  from_email?: string | null;
  from_name?: string | null;
  /** collecting | looking_up | drafting | ready | sending | sent */
  status: string;
  purpose?: string | null;
  signature?: string | null;
  recipients: number;
  drafted: number;
  approved: number;
  sent: number;
  replied: number;
  /** Pasted people with no address: still to search / proposed / total unresolved. */
  lookup_pending: number;
  lookup_found: number;
  needs_email: number;
  progress_total: number;
  progress_done: number;
  last_error?: string | null;
  created_at: string;
}

export interface BulkChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta?: Record<string, unknown> | null;
  created_at: string;
}

export interface BulkCampaignDetail extends BulkCampaign {
  messages: BulkChatMessage[];
  recipients_pending_draft: number;
}

export interface BulkRecipient {
  contact_id: number;
  name: string;
  email?: string | null;
  title?: string | null;
  company_name?: string | null;
  notes?: string | null;
  draft_id?: number | null;
  draft_status?: string | null;
  subject?: string | null;
  sent_at?: string | null;
  replied_at?: string | null;
  reply_snippet?: string | null;
  open_count: number;
}

export interface BulkLookupEvidence {
  title: string;
  url: string;
}

export interface BulkLookup {
  id: number;
  contact_id: number;
  /** pending | found | not_found | ambiguous | accepted | rejected | error */
  status: string;
  /** The person exactly as they were pasted. */
  name: string;
  source_text?: string | null;
  title?: string | null;
  company_name?: string | null;
  /** Who the web search decided they are. */
  resolved_name?: string | null;
  resolved_title?: string | null;
  resolved_org?: string | null;
  resolved_domain?: string | null;
  linkedin_url?: string | null;
  location?: string | null;
  confidence?: number | null;
  reason?: string | null;
  evidence?: BulkLookupEvidence[] | null;
  /** The proposed address, not yet on the contact. */
  email?: string | null;
  /** verified | likely | guessed | provided | ... */
  email_status?: string | null;
  manual: boolean;
  error?: string | null;
  created_at: string;
}

export interface LinkedInMessage {
  id: number;
  principal_id?: number | null;
  campaign_id?: number | null;
  company_id?: number | null;
  contact_id?: number | null;
  insight_id?: number | null;
  body: string;
  invitation_note?: string | null;
  status: string;
  provider?: string | null;
  from_account?: string | null;
  network_distance?: string | null;
  connected?: boolean;
  public_identifier?: string | null;
  provider_chat_id?: string | null;
  approved_by?: string | null;
  created_at: string;
  invitation_sent_at?: string | null;
  sent_at?: string | null;
  replied_at?: string | null;
  reply_snippet?: string | null;
  reply_body?: string | null;
  last_reply_check_at?: string | null;
  last_status_check_at?: string | null;
  error?: string | null;
  principal_name?: string | null;
  contact_name?: string | null;
  contact_title?: string | null;
  company_name?: string | null;
  linkedin_url?: string | null;
  discovery_run_id?: number | null;
}

export interface LinkedInAccount {
  provider: string;
  configured: boolean;
  account_id?: string | null;
}

export interface LinkedInConnectedAccount {
  id: string;
  name?: string | null;
  type?: string | null;
  status?: string | null;
}

export interface LinkedInAccountsResponse {
  provider: string;
  active_account_id?: string | null;
  default_account_id?: string | null;
  accounts: LinkedInConnectedAccount[];
}

/** Live state of the LinkedIn bulk approve+send job (drives the Stop button). */
export interface LinkedInSendProgress {
  status: "idle" | "running" | "done" | "stopped";
  total: number;
  done: number;
  sent: number;
  failed: number;
  stop_requested: boolean;
}

// --- Followers LinkedIn ---

/** One follower of a connected account, joined to its state for one message. */
export interface FollowerRow {
  id: number;
  account_id: string;
  provider_id: string;
  name?: string | null;
  headline?: string | null;
  profile_url?: string | null;
  picture_url?: string | null;
  /** Null until a DM has been drafted for this outreach goal. */
  message_id?: number | null;
  message_status?: string | null;
  body?: string | null;
  /** Checkpoint state: claimed | sent | failed | skipped. */
  send_status?: string | null;
  /** How it was delivered: connected | open_profile | inmail. */
  reach?: string | null;
  sent_at?: string | null;
  error?: string | null;
  replied_at?: string | null;
  reply_snippet?: string | null;
}

/** DB-derived counts for one follower campaign — never reset by a refresh. */
export interface FollowerStats {
  followers_total: number;
  /** Followers with no draft yet for this message. */
  eligible: number;
  all: number;
  draft: number;
  approved: number;
  sent: number;
  replied: number;
  /** Checkpoint truth: how many were ever successfully DM'd for this message. */
  contacted_ever: number;
  /** Neither connected, nor an open profile, nor InMail-able. */
  not_reachable: number;
  /** Claims left by a worker that died mid-send; never auto-retried. */
  needs_review: number;
  cap: number;
  sent_today: number;
  remaining_today: number;
}

export interface FollowersStatus {
  provider: string;
  configured: boolean;
  supports_followers: boolean;
  active_account_id?: string | null;
  active_account_name?: string | null;
  active_account_status?: string | null;
  default_account_id?: string | null;
  accounts: LinkedInConnectedAccount[];
  followers_total?: number;
  campaign_key?: string | null;
  stats?: FollowerStats | null;
}

/** Live state of the running sync / draft / send job. */
export interface FollowersProgress {
  job: "sync" | "draft" | "send" | null;
  status: "idle" | "running" | "done" | "stopped" | "failed";
  total: number;
  done: number;
  drafted: number;
  approved: number;
  sent: number;
  skipped: number;
  failed: number;
  imported: number;
  stop_requested: boolean;
  message?: string | null;
  campaign_key?: string | null;
}

/** Connection-invitation funnel shown above the LinkedIn message list. */
export interface LinkedInInviteStats {
  invites_sent: number;
  invites_accepted: number;
  invites_pending: number;
  /** Accepted / sent as a percentage (0-100). */
  acceptance_rate: number;
}

export interface Call {
  id: number;
  principal_id?: number | null;
  company_id?: number | null;
  contact_id?: number | null;
  insight_id?: number | null;
  phone_number?: string | null;
  script?: string | null;
  status: string;
  transcript?: string | null;
  outcome_notes?: string | null;
  human_handoff_needed: boolean;
  meeting_requested: boolean;
  provider?: string | null;
  provider_call_id?: string | null;
  placed_at?: string | null;
  created_at: string;
  principal_name?: string | null;
  contact_name?: string | null;
  contact_title?: string | null;
  company_name?: string | null;
}

export interface CallConfig {
  voice_provider: string;
  vapi_configured: boolean;
  webhook_configured: boolean;
  webhook_url_hint: string;
}

export interface DiscoveryRun {
  id: number;
  principal_id?: number | null;
  search_definition_id?: number | null;
  provider: string;
  criteria?: Record<string, unknown> | null;
  status: string;
  organizations_found?: number | null;
  organizations_imported?: number | null;
  people_found?: number | null;
  people_imported?: number | null;
  duplicates?: number | null;
  insights_generated?: number | null;
  error_message?: string | null;
  requested_by?: string | null;
  created_at: string;
  provider_warnings?: string[];
  // Background bulk-job progress (draft/send emails, send LinkedIn) for this run.
  job_kind?: string | null;
  job_status?: string | null;
  job_total?: number | null;
  job_done?: number | null;
  job_sent?: number | null;
  job_error?: string | null;
  job_cancel_requested?: boolean | null;
}

export interface AgentRun {
  id: number;
  principal_id?: number | null;
  discovery_run_id?: number | null;
  playbook_id?: number | null;
  status: string;
  trigger: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
  discovered: number;
  duplicates: number;
  qualified: number;
  rejected: number;
  drafted: number;
  sent: number;
  followups_drafted: number;
  followups_sent: number;
  summary?: {
    playbook_name?: string;
    objective?: string;
    criteria?: Record<string, unknown>;
    people?: {
      id: number;
      name: string;
      title?: string | null;
      company?: string | null;
      score?: number | null;
      status: string;
    }[];
    stages?: { stage: string; people?: string[]; [k: string]: unknown }[];
    errors?: string[];
  } | null;
  created_at: string;
}

export interface AgentPlaybook {
  id: number;
  principal_id: number;
  name: string;
  objective_prompt: string;
  clarifying_answers?: Record<string, string> | null;
  criteria: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentPlan {
  questions: { id: string; prompt: string; suggested?: string }[];
  criteria: Record<string, unknown>;
  rationale?: string | null;
}

export interface AgentVariant {
  id: number;
  label: string;
  axis?: string | null;
  criteria: Record<string, unknown>;
  rationale?: string | null;
  is_active: boolean;
  runs: number;
  discovered: number;
  drafted: number;
  sent: number;
  opened: number;
  replied: number;
  reply_rate: number;
  open_rate: number;
}

export interface AgentVariantsResponse {
  playbook_id: number | null;
  playbook_name?: string;
  variants: AgentVariant[];
}

export interface AgentCopyVariant {
  id: number;
  label: string;
  style: Record<string, unknown>;
  rationale?: string | null;
  is_active: boolean;
  drafted: number;
  sent: number;
  opened: number;
  replied: number;
  reply_rate: number;
  open_rate: number;
}

export interface AgentCopyVariantsResponse {
  playbook_id: number | null;
  playbook_name?: string;
  copy_variants: AgentCopyVariant[];
}

export interface AgentConfig {
  id: number;
  principal_id: number;
  enabled: boolean;
  run_hour_utc: number;
  playbook_id?: number | null;
  search_definition_id?: number | null;
  mode: string;
  discover_target: number;
  qualify_min: number;
  auto_reject_below: number;
  sanity_min: number;
  draft_batch_size: number;
  auto_send: boolean;
  daily_send_cap: number;
  followup_enabled: boolean;
  followup_days: number;
  max_followups: number;
  followup_cap: number;
  followup_schedule_days: number[];
  timezone: string;
  run_hour_local: number;
  send_window_start_local: number;
  send_window_end_local: number;
  digest_recipients: string[];
  last_run_at?: string | null;
}

export interface CampaignDayStats {
  date: string;
  discovered: number;
  qualified: number;
  rejected: number;
  drafted: number;
  sent: number;
  followups_sent: number;
  replies: number;
  variant_labels?: string[];
  runs: {
    id: number;
    status: string;
    started_at?: string | null;
    discovered?: number;
    qualified?: number;
    sent?: number;
    variant_label?: string | null;
    objective?: string | null;
  }[];
}

export interface CampaignDashboard {
  principal_id: number;
  days: CampaignDayStats[];
  totals: {
    discovered: number;
    qualified: number;
    sent: number;
    followups_sent: number;
    replies: number;
    runs: number;
  };
}

export interface CampaignSummary {
  id: number;
  name: string;
  principal_id: number;
  principal_name: string;
  playbook_id?: number | null;
  playbook_name?: string | null;
  objective_preview?: string | null;
  enabled: boolean;
  paused: boolean;
  status: "running" | "paused" | "ready" | "draft";
  current_run_id?: number | null;
  current_run_discovered?: number | null;
  current_run_sent?: number | null;
  last_run_at?: string | null;
  totals_sent_14d: number;
  totals_replies_14d: number;
  /** Prior run was killed by a restart and still needs Continue. */
  needs_continue?: boolean;
  interrupted_discovered?: number;
}

export interface CampaignList {
  items: CampaignSummary[];
  running_count: number;
}

export interface CampaignDetail {
  id: number;
  name: string;
  principal_id: number;
  principal_name: string;
  enabled: boolean;
  paused: boolean;
  scheduled_count: number;
  approved_unscheduled: number;
  status: "running" | "paused" | "ready" | "draft";
  objective?: string | null;
  playbook_id?: number | null;
  playbook_name?: string | null;
  criteria: Record<string, unknown>;
  mailbox_daily_cap: number;
  discover_target: number;
  qualify_min: number;
  auto_reject_below: number;
  require_email_and_linkedin: boolean;
  auto_send: boolean;
  auto_schedule: boolean;
  pending_drafts: number;
  last_run_at?: string | null;
  current_run_id?: number | null;
  totals: {
    discovered: number;
    qualified: number;
    rejected: number;
    drafted: number;
    sent: number;
    followups_sent: number;
    replies: number;
    runs: number;
  };
  reply_rate: number;
  days: CampaignDayStats[];
  last_run?: CampaignRunSnapshot | null;
  current_run?: CampaignRunSnapshot | null;
  /** Prior run killed by a server restart — prompt the operator to Continue. */
  interrupted_run?: CampaignRunSnapshot | null;
}

export interface CampaignRunSnapshot {
  id: number;
  status: string;
  trigger: string;
  /** The DiscoveryRun this run imported into. Its id is unrelated to `id` above,
   *  and it is the number the LinkedIn and Prospects run pickers show. */
  discovery_run_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  discovered: number;
  qualified: number;
  rejected: number;
  drafted: number;
  sent: number;
  followups_sent: number;
  error_message?: string | null;
  stages?: { stage: string; [key: string]: unknown }[];
  people?: {
    id: number;
    name: string;
    title?: string | null;
    company?: string | null;
    score?: number | null;
    status: string;
  }[];
  errors?: string[];
}

export interface CampaignProspect {
  contact_id: number;
  name: string;
  title?: string | null;
  company_name?: string | null;
  email?: string | null;
  location?: string | null;
  status: string;
  pipeline_status?: string | null;
  relevance_score?: number | null;
  email_status?: string | null;
  email_subject?: string | null;
  sent_at?: string | null;
  replied_at?: string | null;
  reply_snippet?: string | null;
  last_email_id?: number | null;
}

export interface CampaignProspects {
  campaign_id: number;
  items: CampaignProspect[];
  total: number;
}

export interface CampaignCreatePayload {
  principal_id: number;
  name: string;
  objective_prompt: string;
  clarifying_answers?: Record<string, string>;
  criteria?: Record<string, unknown>;
  discover_target?: number;
  mailbox_daily_cap?: number;
  qualify_min?: number;
  auto_reject_below?: number;
  require_email_and_linkedin?: boolean;
  auto_send?: boolean;
  auto_schedule?: boolean;
  followup_enabled?: boolean;
  followup_schedule_days?: number[];
  timezone?: string;
  run_hour_local?: number;
  send_window_start_local?: number;
  send_window_end_local?: number;
  digest_recipients?: string[];
  enabled?: boolean;
  run_now?: boolean;
}

export interface CampaignUpdatePayload {
  name?: string;
  objective_prompt?: string;
  clarifying_answers?: Record<string, string>;
  criteria?: Record<string, unknown>;
  enabled?: boolean;
  discover_target?: number;
  mailbox_daily_cap?: number;
  qualify_min?: number;
  auto_reject_below?: number;
  require_email_and_linkedin?: boolean;
  auto_send?: boolean;
  auto_schedule?: boolean;
  followup_enabled?: boolean;
  followup_schedule_days?: number[];
  timezone?: string;
  run_hour_local?: number;
  send_window_start_local?: number;
  send_window_end_local?: number;
  digest_recipients?: string[];
}

export interface DashboardStats {
  principals_total: number;
  organizations_total: number;
  prospects_total: number;
  prospects_by_status: Record<string, number>;
  prospects_by_role: Record<string, number>;
  insights_total: number;
  high_relevance_prospects: number;
  email_drafts_total: number;
  calls_total: number;
  discovery_runs_total: number;
  prospects_approved: number;
  prospects_researched: number;
  emails_sent: number;
  emails_opened: number;
  emails_replied: number;
  open_rate: number;
  reply_rate: number;
  emails_by_status: Record<string, number>;
}

export interface ProviderStatus {
  provider: string;
  label: string;
  configured: boolean;
  expected: boolean;
  status: string;
  message?: string | null;
  using_stub: boolean;
  last_error_at?: string | null;
  last_success_at?: string | null;
  extra?: Record<string, unknown> | null;
}

export interface ProviderHealth {
  providers: ProviderStatus[];
  has_blocking_issues: boolean;
  warnings: string[];
}
