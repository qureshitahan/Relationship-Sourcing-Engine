import type { SelectOption } from "../components/MultiSelectDropdown";

/**
 * Industry keyword tags aligned with LinkedIn / Sales Navigator taxonomy.
 * Apollo has no fixed industry enum — these are sent as q_organization_keyword_tags.
 * Prefer broad selections; users can type any custom industry.
 */
export const INDUSTRY_OPTIONS: SelectOption[] = [
  // Healthcare & life sciences (broad first)
  { value: "Healthcare", label: "Healthcare (broad)" },
  { value: "Healthcare Services", label: "Healthcare Services" },
  { value: "Hospital & Health Care", label: "Hospital & Health Care" },
  { value: "Hospitals and Health Care", label: "Hospitals and Health Care" },
  { value: "Health Care", label: "Health Care" },
  { value: "Medical Practice", label: "Medical Practice" },
  { value: "Medical Devices", label: "Medical Devices" },
  { value: "Medical Equipment Manufacturing", label: "Medical Equipment Manufacturing" },
  { value: "Pharmaceuticals", label: "Pharmaceuticals" },
  { value: "Biotechnology", label: "Biotechnology" },
  { value: "Biotechnology Research", label: "Biotechnology Research" },
  { value: "Mental Health Care", label: "Mental Health Care" },
  { value: "Health, Wellness & Fitness", label: "Health, Wellness & Fitness" },
  { value: "Wellness & Fitness Services", label: "Wellness & Fitness Services" },
  { value: "Veterinary Services", label: "Veterinary Services" },
  { value: "Research", label: "Research" },
  { value: "Clinical Research", label: "Clinical Research" },
  { value: "Alternative Medicine", label: "Alternative Medicine" },
  { value: "Home Health Care Services", label: "Home Health Care Services" },
  { value: "Nursing Homes and Residential Care", label: "Nursing Homes and Residential Care" },
  // Finance & investment
  { value: "Financial Services", label: "Financial Services" },
  { value: "Investment Management", label: "Investment Management" },
  { value: "Investment Banking", label: "Investment Banking" },
  { value: "Venture Capital & Private Equity", label: "Venture Capital & Private Equity" },
  { value: "Private Equity", label: "Private Equity" },
  { value: "Venture Capital", label: "Venture Capital" },
  { value: "Banking", label: "Banking" },
  { value: "Capital Markets", label: "Capital Markets" },
  { value: "Insurance", label: "Insurance" },
  { value: "Accounting", label: "Accounting" },
  { value: "FinTech", label: "FinTech" },
  { value: "Holding Companies", label: "Holding Companies" },
  // Professional & business services
  { value: "Management Consulting", label: "Management Consulting" },
  { value: "Business Consulting and Services", label: "Business Consulting and Services" },
  { value: "Staffing & Recruiting", label: "Staffing & Recruiting" },
  { value: "Executive Search", label: "Executive Search" },
  { value: "Legal Services", label: "Legal Services" },
  { value: "Human Resources Services", label: "Human Resources Services" },
  // Other common employer types
  { value: "Nonprofit", label: "Nonprofit" },
  { value: "Non-profit Organization Management", label: "Non-profit Organization Management" },
  { value: "Government Administration", label: "Government Administration" },
  { value: "Higher Education", label: "Higher Education" },
  { value: "Education", label: "Education" },
  { value: "Information Technology & Services", label: "Information Technology & Services" },
  { value: "Software Development", label: "Software Development" },
  { value: "Real Estate", label: "Real Estate" },
  { value: "Retail", label: "Retail" },
  { value: "Manufacturing", label: "Manufacturing" },
  { value: "Consumer Services", label: "Consumer Services" },
];
