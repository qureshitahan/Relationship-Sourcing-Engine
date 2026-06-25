import { useId, useState } from "react";

export interface ChipOption {
  value: string;
  label: string;
}

/**
 * Multi-select built from clickable preset "chips" plus an optional free-text
 * field to add your own values. Selected values are returned as a string[].
 *
 * - Preset chips toggle on/off.
 * - Custom values (typed) appear as removable blue chips.
 * - Press Enter or comma to add a custom value.
 */
export function ChipSelect({
  label,
  hint,
  selected,
  onChange,
  options = [],
  allowCustom = true,
  placeholder = "Type and press Enter to add…",
  suggestions = [],
}: {
  label: string;
  hint?: string;
  selected: string[];
  onChange: (next: string[]) => void;
  options?: ChipOption[];
  allowCustom?: boolean;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState("");
  const listId = useId();

  const selectedSet = new Set(selected.map((s) => s.toLowerCase()));
  const isSel = (v: string) => selectedSet.has(v.toLowerCase());

  const toggle = (v: string) => {
    if (isSel(v)) {
      onChange(selected.filter((s) => s.toLowerCase() !== v.toLowerCase()));
    } else {
      onChange([...selected, v]);
    }
  };

  const addCustom = () => {
    const v = draft.trim().replace(/,$/, "").trim();
    if (!v) {
      setDraft("");
      return;
    }
    if (!isSel(v)) onChange([...selected, v]);
    setDraft("");
  };

  // Values the user typed that aren't part of the preset list.
  const presetValues = new Set(options.map((o) => o.value.toLowerCase()));
  const customSelected = selected.filter((s) => !presetValues.has(s.toLowerCase()));

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium text-slate-600">{label}</span>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-xs text-slate-400 transition hover:text-slate-600"
          >
            Clear
          </button>
        )}
      </div>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}

      {options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => toggle(o.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition ${
                isSel(o.value)
                  ? "bg-slate-900 text-white ring-slate-900"
                  : "bg-white text-slate-600 ring-slate-300 hover:bg-slate-50"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}

      {customSelected.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {customSelected.map((c) => (
            <span
              key={c}
              className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20"
            >
              {c}
              <button
                type="button"
                onClick={() => toggle(c)}
                className="text-blue-400 transition hover:text-blue-700"
                aria-label={`Remove ${c}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {allowCustom && (
        <>
          <input
            className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
            value={draft}
            placeholder={placeholder}
            list={suggestions.length > 0 ? listId : undefined}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                addCustom();
              }
            }}
            onBlur={addCustom}
          />
          {suggestions.length > 0 && (
            <datalist id={listId}>
              {suggestions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          )}
        </>
      )}
    </div>
  );
}
