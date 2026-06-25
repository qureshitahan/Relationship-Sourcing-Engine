/** Resolve display + LLM outreach goal from a discovery run criteria snapshot. */
export function outreachGoalFromCriteria(
  criteria: Record<string, unknown> | null | undefined
): string | null {
  if (!criteria) return null;
  const goal = (criteria.search_goal as string | undefined)?.trim();
  if (goal) return goal;
  const titles = ((criteria.titles as string[]) || []).filter(Boolean).slice(0, 6);
  const industries = ((criteria.industries as string[]) || []).filter(Boolean).slice(0, 3);
  const geos = ((criteria.geographies as string[]) || []).filter(Boolean).slice(0, 2);
  if (titles.length === 0) return null;
  const geoPart = geos.length ? ` in ${geos.join(", ")}` : "";
  const indPart = industries.length ? ` at ${industries.join(", ")} companies` : "";
  return `Connect with people in roles like ${titles.join(", ")}${indPart}${geoPart}.`;
}
