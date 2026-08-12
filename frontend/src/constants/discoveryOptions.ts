import type { SelectOption } from "../components/MultiSelectDropdown";

export {
  COUNTRY_OPTIONS,
  DEFAULT_GEOGRAPHIES,
  GEOGRAPHY_OPTIONS,
  GEOGRAPHY_SUGGESTIONS,
  US_CITY_SUGGESTIONS,
  US_STATE_OPTIONS,
} from "./discoveryGeographies";

export { INDUSTRY_OPTIONS } from "./discoveryIndustries";

export {
  BOARD_JOB_TITLE_OPTIONS,
  DEFAULT_BOARD_JOB_TITLES,
  DEFAULT_DISCOVERY_TITLES,
  TITLE_OPTIONS,
} from "./discoveryTitles";

/** CEO is a TITLE in Apollo, not a seniority — ``person_seniorities`` accepts only
 *  the 11 values below, and "ceo" is not one of them, so sending it would leave a
 *  filter that looks applied and does nothing. It still belongs in the seniority
 *  dropdown because that is where people look for it, so it lives there as a
 *  pseudo-value and ``splitSeniorityFilters`` turns it into a title filter on the
 *  way out. ``c_suite`` is the nearest real seniority but it also returns CFO/COO/
 *  CTO, which is not what picking "CEO" means. */
export const CEO_SENIORITY_VALUE = "ceo";
export const CEO_TITLES = ["CEO", "Chief Executive Officer"];

/** Split a seniority selection into what Apollo accepts and the titles that its
 *  pseudo-values stand for. Anything not a pseudo-value passes through untouched,
 *  so the existing 11 seniorities behave exactly as before. */
export function splitSeniorityFilters(selected: string[]): {
  seniorities: string[];
  titles: string[];
} {
  return {
    seniorities: selected.filter((value) => value !== CEO_SENIORITY_VALUE),
    titles: selected.includes(CEO_SENIORITY_VALUE) ? [...CEO_TITLES] : [],
  };
}

/** Apollo person_seniorities[] — complete 11-value enum (no others accepted by
 *  API) — plus "CEO", which is a title and is translated out before sending. */
export const SENIORITY_OPTIONS: SelectOption[] = [
  { value: "owner", label: "Owner" },
  { value: "founder", label: "Founder" },
  { value: CEO_SENIORITY_VALUE, label: "CEO" },
  { value: "c_suite", label: "C-Suite" },
  { value: "partner", label: "Partner" },
  { value: "vp", label: "VP" },
  { value: "head", label: "Head" },
  { value: "director", label: "Director" },
  { value: "manager", label: "Manager" },
  { value: "senior", label: "Senior (IC)" },
  { value: "entry", label: "Entry level" },
  { value: "intern", label: "Intern" },
];

/** Apollo contact_email_status[] — omit unavailable (no reachable email). */
export const CONTACT_EMAIL_STATUS_OPTIONS: SelectOption[] = [
  { value: "verified", label: "Verified" },
  { value: "likely to engage", label: "Likely to engage" },
  { value: "unverified", label: "Unverified (may exist)" },
];

/**
 * Company-type keyword tags (not an Apollo enum — sent as q_organization_keyword_tags).
 * Underscores become spaces on the backend. Users can add any custom type.
 */
export const COMPANY_TYPE_OPTIONS: SelectOption[] = [
  { value: "operating_company", label: "Operating Company" },
  { value: "private_equity", label: "Private Equity" },
  { value: "venture_capital", label: "Venture Capital" },
  { value: "growth_equity", label: "Growth Equity" },
  { value: "family_office", label: "Family Office" },
  { value: "hedge_fund", label: "Hedge Fund" },
  { value: "investment_bank", label: "Investment Bank" },
  { value: "asset_management", label: "Asset Management" },
  { value: "advisory", label: "Advisory / Consulting" },
  { value: "management_consulting", label: "Management Consulting" },
  { value: "executive_search", label: "Executive Search" },
  { value: "staffing_recruiting", label: "Staffing & Recruiting" },
  { value: "health_system", label: "Health System" },
  { value: "hospital", label: "Hospital" },
  { value: "academic_medical_center", label: "Academic Medical Center" },
  { value: "physician_practice", label: "Physician Practice / MSO" },
  { value: "nonprofit", label: "Nonprofit" },
  { value: "payer", label: "Payer / Insurance" },
  { value: "pharmacy_benefit_manager", label: "Pharmacy Benefit Manager (PBM)" },
  { value: "pharma", label: "Pharma" },
  { value: "biotech", label: "Biotech" },
  { value: "medical_device", label: "Medical Device" },
  { value: "cro", label: "CRO / Clinical Research" },
  { value: "government", label: "Government" },
  { value: "law_firm", label: "Law Firm" },
  { value: "accounting_firm", label: "Accounting Firm" },
  { value: "real_estate", label: "Real Estate / REIT" },
  { value: "technology", label: "Technology / Software" },
];

/** Investment / acquisition / outreach themes — free-form keywords for search + AI scoring. */
export const THEME_OPTIONS: SelectOption[] = [
  { value: "platform consolidation", label: "Platform consolidation" },
  { value: "roll-up", label: "Roll-up" },
  { value: "buy-and-build", label: "Buy-and-build" },
  { value: "carve-out", label: "Carve-out" },
  { value: "add-on acquisition", label: "Add-on acquisition" },
  { value: "M&A", label: "M&A" },
  { value: "divestiture", label: "Divestiture" },
  { value: "capital raise", label: "Capital raise" },
  { value: "growth equity", label: "Growth equity" },
  { value: "expansion", label: "Expansion" },
  { value: "leadership change", label: "Leadership change" },
  { value: "succession planning", label: "Succession planning" },
  { value: "board refresh", label: "Board refresh" },
  { value: "IPO preparation", label: "IPO preparation" },
  { value: "formulary access", label: "Formulary / market access" },
  { value: "clinical adoption", label: "Clinical adoption" },
  { value: "market access", label: "Market access" },
  { value: "pipeline partnership", label: "Pipeline partnership" },
  { value: "licensing deal", label: "Licensing deal" },
  { value: "value-based care", label: "Value-based care" },
  { value: "digital health", label: "Digital health" },
  { value: "telehealth", label: "Telehealth" },
  { value: "value creation", label: "Value creation" },
  { value: "operational turnaround", label: "Operational turnaround" },
];
