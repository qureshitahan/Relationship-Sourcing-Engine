/**
 * Clears the Discover page's saved form state when that page is refreshed.
 *
 * Discover keeps its principal, objective, AI plan, and filters in
 * sessionStorage (see hooks/usePersistedState) so leaving the module and coming
 * back does not wipe a filter set that took effort to build. Refreshing is a
 * different intent: the page should start clean.
 *
 * Client-side navigation never reloads the document, so running this once at
 * boot resets refresh only — clicking "1. Discover" in the sidebar still keeps
 * whatever was set. The path check keeps a refresh on some other module from
 * quietly wiping Discover's filters.
 */
const DISCOVER_STATE_PREFIX = "discover:";
const DISCOVER_PATH = "/discover";

export function clearDiscoverStateOnReload(): void {
  try {
    if (window.location.pathname.replace(/\/+$/, "") !== DISCOVER_PATH) return;
    // Collect first: removing while iterating shifts the remaining indices.
    const stale: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key?.startsWith(DISCOVER_STATE_PREFIX)) stale.push(key);
    }
    for (const key of stale) sessionStorage.removeItem(key);
  } catch {
    // Storage unavailable (private mode) — nothing was persisted anyway.
  }
}
