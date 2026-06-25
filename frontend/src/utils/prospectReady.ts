import type { Prospect } from "../types";

/** True when live Claude research did not complete (stub fallback or no score). */
export function researchFailed(p: Prospect): boolean {
  if (p.relevance_score == null) return true;
  return (p.insight_provider ?? "").toLowerCase().includes("fallback");
}

/** Whether this prospect is eligible for outreach email drafting. */
export function canDraftProspect(p: Prospect): boolean {
  return (
    Boolean(p.approved_for_outreach) &&
    Boolean(p.email?.trim()) &&
    !researchFailed(p)
  );
}

export function draftBlockers(p: Prospect): string[] {
  const blockers: string[] = [];
  if (!p.email?.trim()) blockers.push("email not revealed");
  if (p.relevance_score == null) blockers.push("not researched");
  if (researchFailed(p)) blockers.push("research failed");
  return blockers;
}

/** Serial numbers within each discovery run (1-based). */
export function serialMapForProspects(
  items: Prospect[],
  runFilter?: number
): Map<number, string> {
  const byRun = new Map<number, Prospect[]>();
  for (const p of items) {
    if (!p.discovery_run_id) continue;
    const list = byRun.get(p.discovery_run_id) ?? [];
    list.push(p);
    byRun.set(p.discovery_run_id, list);
  }
  const out = new Map<number, string>();
  for (const [runId, prospects] of byRun) {
    prospects
      .slice()
      .sort((a, b) => a.id - b.id)
      .forEach((p, i) => {
        const n = String(i + 1);
        out.set(p.id, runFilter ? n : `${runId}.${n}`);
      });
  }
  return out;
}
