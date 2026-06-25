import { useCallback, useEffect, useState } from "react";
import { getProviderHealth } from "../api/client";
import type { ProviderHealth } from "../types";

const POLL_MS = 60_000;

export default function ProviderHealthBanner() {
  const [health, setHealth] = useState<ProviderHealth | null>(null);
  const [dismissed, setDismissed] = useState(false);

  const load = useCallback(async (probe = false) => {
    try {
      const data = await getProviderHealth(probe);
      setHealth(data);
      if (data.has_blocking_issues) {
        setDismissed(false);
      }
    } catch {
      /* backend may be down — banner is best-effort */
    }
  }, []);

  useEffect(() => {
    void load(false);
    const id = window.setInterval(() => void load(false), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  if (!health?.has_blocking_issues || dismissed) {
    return null;
  }

  const warnings =
    health.warnings.length > 0
      ? health.warnings
      : health.providers
          .filter((p) => p.status !== "ok")
          .map((p) => p.message || `${p.label} issue (${p.status})`);

  return (
    <div
      className="mb-6 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-950"
      role="alert"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">API provider issue — results may be wrong or empty</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-rose-800">
            Discovery, email reveals, research, and draft regeneration depend on live Apollo and
            Anthropic credits. Stub fallbacks run silently without this banner once dismissed until
            the next error.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
