import type { SelectOption } from "../components/MultiSelectDropdown";

/**
 * Ready-made choices for the Classic Search LinkedIn filters.
 *
 * Two different kinds live here, because LinkedIn treats them differently:
 *
 * `JOB_TITLE_OPTIONS` are plain text. LinkedIn's search accepts any string, so
 * these are only a starting point — anything typed is just as valid.
 *
 * `LOCATION_PRESETS` / `INDUSTRY_PRESETS` are NOT values. LinkedIn takes ids for
 * those, and the ids differ between classic and Sales Navigator, so a hard-coded
 * id would be wrong half the time. They are search TERMS: clicking one looks it
 * up through the same route the type-ahead uses and adds whatever id comes back
 * for the mode you are in. That keeps one click convenient and still correct.
 */

/** Operations / executive titles, ordered from most senior. */
export const JOB_TITLE_OPTIONS: SelectOption[] = [
  // C-suite
  { value: "Chief Executive Officer", label: "Chief Executive Officer (CEO)" },
  { value: "Chief Operating Officer", label: "Chief Operating Officer (COO)" },
  { value: "Chief Financial Officer", label: "Chief Financial Officer (CFO)" },
  { value: "Chief Medical Officer", label: "Chief Medical Officer (CMO)" },
  { value: "Chief Technology Officer", label: "Chief Technology Officer (CTO)" },
  { value: "Chief Information Officer", label: "Chief Information Officer (CIO)" },
  { value: "Chief Digital Officer", label: "Chief Digital Officer" },
  { value: "Chief Quality Officer", label: "Chief Quality Officer" },
  { value: "Chief Commercial Officer", label: "Chief Commercial Officer" },
  { value: "Chief Transformation Officer", label: "Chief Transformation Officer" },
  // Owner / board
  { value: "Founder", label: "Founder" },
  { value: "Co-Founder", label: "Co-Founder" },
  { value: "President", label: "President" },
  { value: "Managing Director", label: "Managing Director" },
  { value: "Board Member", label: "Board Member" },
  { value: "General Manager", label: "General Manager" },
  // Operations
  { value: "VP Operations", label: "VP Operations" },
  { value: "Vice President of Operations", label: "Vice President of Operations" },
  { value: "Head of Operations", label: "Head of Operations" },
  { value: "Director of Operations", label: "Director of Operations" },
  { value: "Operations Manager", label: "Operations Manager" },
  { value: "Head of Manufacturing", label: "Head of Manufacturing" },
  { value: "Director of Manufacturing", label: "Director of Manufacturing" },
  { value: "Plant Manager", label: "Plant Manager" },
  { value: "Site Director", label: "Site Director" },
  // Quality / regulatory / supply chain — the pharma operations spine
  { value: "Head of Quality", label: "Head of Quality" },
  { value: "Director of Quality", label: "Director of Quality" },
  { value: "VP Quality Assurance", label: "VP Quality Assurance" },
  { value: "Head of Regulatory Affairs", label: "Head of Regulatory Affairs" },
  { value: "VP Supply Chain", label: "VP Supply Chain" },
  { value: "Head of Supply Chain", label: "Head of Supply Chain" },
  { value: "Director of Supply Chain", label: "Director of Supply Chain" },
  // Clinical / R&D
  { value: "Chief Scientific Officer", label: "Chief Scientific Officer" },
  { value: "VP Clinical Operations", label: "VP Clinical Operations" },
  { value: "Head of Clinical Operations", label: "Head of Clinical Operations" },
  { value: "VP Research and Development", label: "VP Research and Development" },
  // Data / AI, for the AI-led offer
  { value: "Head of Data", label: "Head of Data" },
  { value: "Head of AI", label: "Head of AI" },
  { value: "VP Engineering", label: "VP Engineering" },
  { value: "Head of Digital Transformation", label: "Head of Digital Transformation" },
];

/** Common places, looked up per mode rather than stored as ids. */
export const LOCATION_PRESETS: string[] = [
  "United States",
  "Canada",
  "United Kingdom",
  "European Union",
  "Ireland",
  "Germany",
  "Switzerland",
  "India",
  "Australia",
  "United Arab Emirates",
  "California, United States",
  "New York, United States",
  "New Jersey, United States",
  "Massachusetts, United States",
  "Texas, United States",
];

/** Common industries, looked up per mode rather than stored as ids. */
export const INDUSTRY_PRESETS: string[] = [
  "Pharmaceutical Manufacturing",
  "Biotechnology Research",
  "Hospitals and Health Care",
  "Medical Equipment Manufacturing",
  "Medical Practices",
  "Research Services",
  "Wellness and Fitness Services",
  "Retail Pharmacies",
  "Insurance",
  "Software Development",
];
