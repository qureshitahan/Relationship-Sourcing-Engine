/** Parsing for the API's timestamps, which are UTC but unmarked. */

/**
 * Parse an API timestamp as UTC.
 *
 * The backend stamps rows with ``datetime.utcnow()`` and serialises with
 * ``.isoformat()``, which yields "2026-08-06T18:15:39" — UTC, but with no
 * trailing "Z". `new Date()` reads a suffix-less date-time as LOCAL time, so
 * every timestamp was displayed shifted by the viewer's UTC offset: a run
 * started seconds ago read "started 5h ago" in Pakistan (UTC+5).
 *
 * Timestamps that already carry a zone ("Z" or "+05:00") are left alone.
 */
export function parseApiDate(iso: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasZone ? iso : `${iso}Z`);
}

/** "just now" / "12m ago" / "3h ago" / "2d ago", or "never" when absent. */
export function relativeTime(iso?: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - parseApiDate(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  // Small negative skews (clock drift between server and browser) read as now.
  const m = Math.floor(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
