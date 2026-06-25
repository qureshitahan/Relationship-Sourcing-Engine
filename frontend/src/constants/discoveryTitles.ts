import type { SelectOption } from "../components/MultiSelectDropdown";

/**
 * Common job titles for outreach — Apollo person_titles[] accepts any free text.
 * This list is a starting point; users and the AI planner can add any title.
 */
export const TITLE_OPTIONS: SelectOption[] = [
  // PE / investment
  { value: "Operating Partner", label: "Operating Partner" },
  { value: "Talent Partner", label: "Talent Partner" },
  { value: "Value Creation Partner", label: "Value Creation Partner" },
  { value: "Portfolio Operations", label: "Portfolio Operations" },
  { value: "Managing Partner", label: "Managing Partner" },
  { value: "General Partner", label: "General Partner" },
  { value: "Partner", label: "Partner" },
  { value: "Principal", label: "Principal (Investments)" },
  { value: "Vice President", label: "Vice President" },
  { value: "VP", label: "VP" },
  { value: "Director", label: "Director" },
  { value: "Associate", label: "Associate" },
  { value: "Analyst", label: "Analyst" },
  // Executive search / board advisory
  { value: "Head of Talent", label: "Head of Talent" },
  { value: "Head of Human Capital", label: "Head of Human Capital" },
  { value: "Board Practice", label: "Board Practice" },
  { value: "Board Search Consultant", label: "Board Search Consultant" },
  { value: "Executive Search Consultant", label: "Executive Search Consultant" },
  { value: "Managing Director", label: "Managing Director" },
  { value: "Senior Managing Director", label: "Senior Managing Director" },
  // Board / governance
  { value: "Independent Director", label: "Independent Director" },
  { value: "Non-Executive Director", label: "Non-Executive Director" },
  { value: "Lead Director", label: "Lead Director" },
  { value: "Audit Committee Chair", label: "Audit Committee Chair" },
  { value: "Compensation Committee Chair", label: "Compensation Committee Chair" },
  { value: "Nominating Committee Chair", label: "Nominating Committee Chair" },
  { value: "Board Chair", label: "Board Chair" },
  { value: "Board Member", label: "Board Member" },
  { value: "Chairman", label: "Chairman" },
  { value: "Chairwoman", label: "Chairwoman" },
  { value: "Trustee", label: "Trustee" },
  // C-suite
  { value: "CEO", label: "CEO" },
  { value: "Chief Executive Officer", label: "Chief Executive Officer" },
  { value: "CFO", label: "CFO" },
  { value: "Chief Financial Officer", label: "Chief Financial Officer" },
  { value: "COO", label: "COO" },
  { value: "Chief Operating Officer", label: "Chief Operating Officer" },
  { value: "CMO", label: "CMO" },
  { value: "Chief Medical Officer", label: "Chief Medical Officer" },
  { value: "Chief Pharmacy Officer", label: "Chief Pharmacy Officer" },
  { value: "Chief Nursing Officer", label: "Chief Nursing Officer" },
  { value: "Chief Information Officer", label: "Chief Information Officer" },
  { value: "Chief Strategy Officer", label: "Chief Strategy Officer" },
  { value: "Chief Commercial Officer", label: "Chief Commercial Officer" },
  { value: "President", label: "President" },
  { value: "Founder", label: "Founder" },
  { value: "Co-Founder", label: "Co-Founder" },
  { value: "Owner", label: "Owner" },
  // Pharma / medical affairs / market access
  { value: "VP Medical Affairs", label: "VP Medical Affairs" },
  { value: "Director Medical Affairs", label: "Director Medical Affairs" },
  { value: "Head of Medical Affairs", label: "Head of Medical Affairs" },
  { value: "Medical Affairs Director", label: "Medical Affairs Director" },
  { value: "VP Market Access", label: "VP Market Access" },
  { value: "Head of Market Access", label: "Head of Market Access" },
  { value: "Director Market Access", label: "Director Market Access" },
  { value: "VP Commercial", label: "VP Commercial" },
  { value: "Head of Commercial", label: "Head of Commercial" },
  // Pharmacy / formulary / P&T
  { value: "Director of Pharmacy", label: "Director of Pharmacy" },
  { value: "Director Pharmacy", label: "Director Pharmacy" },
  { value: "Pharmacy Director", label: "Pharmacy Director" },
  { value: "VP Pharmacy", label: "VP Pharmacy" },
  { value: "Formulary Manager", label: "Formulary Manager" },
  { value: "Pharmacy Manager", label: "Pharmacy Manager" },
  { value: "P&T Committee", label: "P&T Committee" },
  { value: "Pharmacy & Therapeutics", label: "Pharmacy & Therapeutics" },
  // Hospital / health system operations
  { value: "VP Operations", label: "VP Operations" },
  { value: "VP Clinical Operations", label: "VP Clinical Operations" },
  { value: "Administrator", label: "Administrator" },
  { value: "Practice Administrator", label: "Practice Administrator" },
  { value: "Department Chair", label: "Department Chair" },
  { value: "Medical Director", label: "Medical Director" },
];

export const BOARD_JOB_TITLE_OPTIONS: SelectOption[] = [
  { value: "Independent Director", label: "Independent Director" },
  { value: "Board Member", label: "Board Member" },
  { value: "Non-Executive Director", label: "Non-Executive Director" },
  { value: "Board Director", label: "Board Director" },
  { value: "Board Chair", label: "Board Chair" },
  { value: "Audit Committee", label: "Audit Committee" },
  { value: "Lead Director", label: "Lead Director" },
  { value: "Director", label: "Director (broad)" },
  { value: "Director of Pharmacy", label: "Director of Pharmacy" },
  { value: "Chief Medical Officer", label: "Chief Medical Officer" },
  { value: "Operating Partner", label: "Operating Partner" },
  { value: "Talent Partner", label: "Talent Partner" },
  { value: "VP Medical Affairs", label: "VP Medical Affairs" },
  { value: "Formulary Manager", label: "Formulary Manager" },
];

export const DEFAULT_DISCOVERY_TITLES = [
  "Operating Partner",
  "Talent Partner",
  "Managing Partner",
  "Board Search Consultant",
  "Independent Director",
  "Value Creation Partner",
];

export const DEFAULT_BOARD_JOB_TITLES = [
  "Independent Director",
  "Board Member",
  "Non-Executive Director",
];
