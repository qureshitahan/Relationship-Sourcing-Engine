import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { getLinkedInScanProgress } from "../api/client";

/**
 * Live progress bar for the LinkedIn "Check for replies" background scan.
 * Polls /linkedin/scan-progress while `active`, and calls `onDone` once when the
 * scan finishes. Render it near the top of the LinkedIn / Responses pages.
 */
export function LinkedInScanProgress({
  active,
  onDone,
}: {
  active: boolean;
  onDone: (r: { accepted: number; replied: number }) => void;
}) {
  const firedRef = useRef(false);
  const { data } = useQuery({
    queryKey: ["linkedin-scan-progress"],
    queryFn: getLinkedInScanProgress,
    enabled: active,
    refetchInterval: active ? 1500 : false,
  });

  useEffect(() => {
    if (!active) {
      firedRef.current = false;
      return;
    }
    if (data?.status === "done" && !firedRef.current) {
      firedRef.current = true;
      onDone({ accepted: data.accepted ?? 0, replied: data.replied ?? 0 });
    }
  }, [active, data, onDone]);

  if (!active) return null;

  const total = data?.total ?? 0;
  const done = data?.done ?? 0;
  const starting = !data || data.status === "starting" || total === 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const replied = data?.replied ?? 0;
  const accepted = data?.accepted ?? 0;

  return (
    <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-4">
      <div className="mb-2 flex items-center justify-between text-sm text-blue-900">
        <span className="font-medium">
          {starting
            ? "Starting reply check…"
            : `Checking replies… ${done} / ${total} messages`}
        </span>
        {!starting && <span className="tabular-nums text-blue-700">{pct}%</span>}
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-blue-100">
        <div
          className="h-full rounded-full bg-blue-600 transition-all duration-500"
          style={{ width: `${starting ? 6 : pct}%` }}
        />
      </div>
      {!starting && (replied > 0 || accepted > 0) && (
        <div className="mt-2 text-xs text-blue-700">
          {replied} new repl{replied === 1 ? "y" : "ies"} · {accepted} invite
          {accepted === 1 ? "" : "s"} accepted so far
        </div>
      )}
      <p className="mt-2 text-[11px] text-blue-600">
        Runs in the background — you can keep working; replies appear as they're found.
      </p>
    </div>
  );
}
