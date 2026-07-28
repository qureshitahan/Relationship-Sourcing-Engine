import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getOptimization, updateOptimization } from "../api/client";
import type { OptimizationCapability, OptimizationUpdate } from "../types";
import { Badge, Card } from "./ui";

/** Question-mark bubble that reveals an explanation on hover. */
function InfoTip({ children }: { children: React.ReactNode }) {
  return (
    <span className="group relative inline-flex">
      <span
        tabIndex={0}
        className="flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-slate-300 text-[10px] font-semibold text-slate-500 transition hover:border-slate-400 hover:text-slate-700"
        aria-label="What the optimized pipeline changes"
      >
        i
      </span>
      <span className="pointer-events-none invisible absolute left-1/2 top-6 z-30 w-80 -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-900 p-3 text-xs leading-relaxed text-slate-100 opacity-0 shadow-xl transition group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        {children}
      </span>
    </span>
  );
}

function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:opacity-50 ${
        checked ? "bg-emerald-600" : "bg-slate-300"
      }`}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
          checked ? "left-[22px]" : "left-0.5"
        }`}
      />
    </button>
  );
}

function CapabilityRow({
  cap,
  disabled,
  onToggle,
}: {
  cap: OptimizationCapability;
  disabled: boolean;
  onToggle: (key: string, next: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-slate-200 py-3 first:border-t-0">
      <div>
        <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
          {cap.label}
          {cap.affects_quality && <Badge tone="amber">May change drafts</Badge>}
        </div>
        <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-slate-500">
          {cap.description}
        </p>
      </div>
      <Switch
        checked={cap.enabled}
        disabled={disabled}
        label={cap.label}
        onChange={(next) => onToggle(cap.key, next)}
      />
    </div>
  );
}

/**
 * Home-page switch between the pipeline that is known to produce good research
 * and drafts, and the cost-optimized one. Everything is reversible: turning the
 * master switch off restores the original behaviour immediately.
 */
export default function PipelineModeCard() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["optimization"],
    queryFn: getOptimization,
  });

  const save = useMutation({
    mutationFn: (payload: OptimizationUpdate) => updateOptimization(payload),
    onSuccess: (next) => qc.setQueryData(["optimization"], next),
  });

  if (!data) return null;

  const on = data.enabled;
  const busy = save.isPending;
  const risky = data.capabilities.filter((c) => c.affects_quality && c.enabled);

  const toggleCapability = (key: string, next: boolean) => {
    const cap = data.capabilities.find((c) => c.key === key);
    if (
      next &&
      cap?.affects_quality &&
      !window.confirm(
        `"${cap.label}" is the one setting that can change how your emails read.\n\n` +
          "Turn it on, generate a batch of drafts, and compare them against your " +
          "current ones before leaving it on. Continue?"
      )
    ) {
      return;
    }
    save.mutate({ capabilities: { [key]: next } });
  };

  return (
    <Card className="mb-6 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-slate-900">Pipeline mode</h2>
            <InfoTip>
              <span className="mb-1 block font-semibold">
                Optimized pipeline
              </span>
              Cuts API spend without changing how research or drafts are written:
              <span className="mt-1 block">
                • Prompt caching — reuses the cached instructions across prospects
              </span>
              <span className="block">
                • Skips paid research for prospects whose free fit score is already
                too low to qualify
              </span>
              <span className="block">
                • Reuses research from the last 30 days instead of paying twice for
                the same person
              </span>
              <span className="block">
                • One web search when the LinkedIn URL is already known, the full
                budget when it isn't
              </span>
              <span className="mt-1 block">
                A cheaper writing model is available too, but it is off by default
                because it is the only setting that can change how drafts read.
              </span>
              <span className="mt-1 block text-slate-300">
                Switch back any time — the current pipeline resumes immediately.
              </span>
            </InfoTip>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {on ? (
              <>
                Optimized. Research on{" "}
                <span className="font-medium text-slate-700">
                  {data.research_model}
                </span>
                , writing on{" "}
                <span className="font-medium text-slate-700">
                  {data.draft_model}
                </span>
                .
              </>
            ) : (
              <>
                Current system — the settings your existing research and drafts were
                produced with. Nothing is optimized.
              </>
            )}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          <span
            className={`text-xs font-medium ${
              on ? "text-emerald-700" : "text-slate-500"
            }`}
          >
            {on ? "Optimized" : "Current"}
          </span>
          <Switch
            checked={on}
            disabled={busy}
            label="Use the optimized pipeline"
            onChange={(next) => save.mutate({ enabled: next })}
          />
        </div>
      </div>

      {on && risky.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {risky.map((c) => c.label).join(", ")} can change how drafts read. Compare
          a batch against your current emails before relying on it.
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mt-3 text-xs font-medium text-blue-700 hover:underline"
      >
        {open ? "Hide details" : "What changes?"}
      </button>

      {open && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-1">
          {!on && (
            <p className="border-b border-slate-200 py-3 text-xs text-slate-500">
              These apply once the optimized pipeline is switched on.
            </p>
          )}
          {data.capabilities.map((cap) => (
            <CapabilityRow
              key={cap.key}
              cap={cap}
              disabled={busy || !on}
              onToggle={toggleCapability}
            />
          ))}
        </div>
      )}
    </Card>
  );
}
