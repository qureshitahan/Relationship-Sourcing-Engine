import type { SelectOption } from "../components/MultiSelectDropdown";

/** All 50 US states + DC — Apollo accepts free-text state names. */
export const US_STATE_OPTIONS: SelectOption[] = [
  "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
  "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
  "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
  "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
  "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
  "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
  "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
  "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
  "Washington", "West Virginia", "Wisconsin", "Wyoming",
].map((s) => ({ value: s, label: s }));

export const COUNTRY_OPTIONS: SelectOption[] = [
  { value: "United States", label: "United States" },
  { value: "US", label: "US" },
  { value: "USA", label: "USA" },
  { value: "Canada", label: "Canada" },
  { value: "United Kingdom", label: "United Kingdom" },
  { value: "Ireland", label: "Ireland" },
  { value: "Germany", label: "Germany" },
  { value: "France", label: "France" },
  { value: "Switzerland", label: "Switzerland" },
  { value: "Netherlands", label: "Netherlands" },
  { value: "Australia", label: "Australia" },
  { value: "Singapore", label: "Singapore" },
  { value: "India", label: "India" },
  { value: "Israel", label: "Israel" },
  { value: "Japan", label: "Japan" },
];

export const US_CITY_SUGGESTIONS: string[] = [
  "New York City", "Los Angeles", "Chicago", "Houston", "Phoenix",
  "Philadelphia", "San Antonio", "San Diego", "Dallas", "Austin",
  "Jacksonville", "San Jose", "Fort Worth", "Columbus", "Charlotte",
  "Indianapolis", "San Francisco", "Seattle", "Denver", "Boston",
  "Nashville", "Detroit", "Portland", "Las Vegas", "Miami",
  "Atlanta", "Minneapolis", "Tampa", "Baltimore", "St. Louis",
  "Salt Lake City", "Raleigh", "Pittsburgh", "Cincinnati", "Kansas City",
  "San Francisco Bay Area", "Palo Alto", "Silicon Valley", "Washington DC",
  "Northern Virginia", "Research Triangle", "Cambridge",
];

export const GEOGRAPHY_OPTIONS: SelectOption[] = [
  ...COUNTRY_OPTIONS,
  ...US_STATE_OPTIONS,
];

export const GEOGRAPHY_SUGGESTIONS: string[] = [
  ...COUNTRY_OPTIONS.map((c) => c.value),
  ...US_CITY_SUGGESTIONS,
];

export const DEFAULT_GEOGRAPHIES = ["United States"];
