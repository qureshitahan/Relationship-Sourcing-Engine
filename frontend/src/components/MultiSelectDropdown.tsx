import { useEffect, useId, useMemo, useRef, useState } from "react";

export interface SelectOption {
  value: string;
  label: string;
}

/**
 * LinkedIn-style multi-select: selected values appear as removable chips;
 * click the field to open a searchable dropdown of all preset options.
 */
export function MultiSelectDropdown({
  label,
  hint,
  selected,
  onChange,
  options = [],
  allowCustom = true,
  placeholder = "Search or select…",
  suggestions = [],
}: {
  label: string;
  hint?: string;
  selected: string[];
  onChange: (next: string[]) => void;
  options?: SelectOption[];
  allowCustom?: boolean;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  const selectedSet = useMemo(
    () => new Set(selected.map((s) => s.toLowerCase())),
    [selected]
  );
  const isSel = (v: string) => selectedSet.has(v.toLowerCase());

  const toggle = (v: string) => {
    if (isSel(v)) {
      onChange(selected.filter((s) => s.toLowerCase() !== v.toLowerCase()));
    } else {
      onChange([...selected, v]);
    }
  };

  const addCustom = (raw: string) => {
    const v = raw.trim().replace(/,$/, "").trim();
    if (!v || isSel(v)) return;
    onChange([...selected, v]);
    setQuery("");
  };

  const presetValues = useMemo(
    () => new Set(options.map((o) => o.value.toLowerCase())),
    [options]
  );
  const labelFor = (v: string) =>
    options.find((o) => o.value.toLowerCase() === v.toLowerCase())?.label ?? v;

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)
    );
  }, [options, query]);

  const suggestionPool = useMemo(() => {
    const fromOpts = options.map((o) => o.value);
    const merged = [...new Set([...fromOpts, ...suggestions])];
    const q = query.trim().toLowerCase();
    if (!q) return merged.filter((s) => !isSel(s)).slice(0, 12);
    return merged
      .filter((s) => s.toLowerCase().includes(q) && !isSel(s))
      .slice(0, 12);
  }, [options, suggestions, query, selectedSet]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
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

      {selected.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {selected.map((v) => (
            <span
              key={v}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${
                presetValues.has(v.toLowerCase())
                  ? "bg-slate-900 text-white ring-slate-900"
                  : "bg-blue-50 text-blue-700 ring-blue-600/20"
              }`}
            >
              {labelFor(v)}
              <button
                type="button"
                onClick={() => toggle(v)}
                className="opacity-70 transition hover:opacity-100"
                aria-label={`Remove ${labelFor(v)}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="relative mt-2">
        <input
          ref={inputRef}
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
          value={query}
          placeholder={selected.length ? "Add more…" : placeholder}
          list={suggestions.length > 0 && !open ? listId : undefined}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              if (allowCustom && query.trim()) {
                addCustom(query);
              } else if (filteredOptions.length === 1) {
                toggle(filteredOptions[0].value);
              }
            }
            if (e.key === "Escape") setOpen(false);
            if (e.key === "Backspace" && !query && selected.length > 0) {
              onChange(selected.slice(0, -1));
            }
          }}
          onBlur={() => {
            if (allowCustom && query.trim()) addCustom(query);
          }}
        />
        {suggestions.length > 0 && (
          <datalist id={listId}>
            {suggestions.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        )}

        {open && (options.length > 0 || allowCustom) && (
          <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
            {filteredOptions.length === 0 && !allowCustom && (
              <div className="px-3 py-2 text-xs text-slate-400">No matches</div>
            )}
            {filteredOptions.length === 0 && allowCustom && query.trim() && (
              <div className="px-3 py-2 text-xs text-slate-400">No preset matches</div>
            )}
            {filteredOptions.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-slate-50 ${
                  isSel(o.value) ? "bg-slate-50 font-medium text-slate-900" : "text-slate-700"
                }`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => toggle(o.value)}
              >
                <span
                  className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${
                    isSel(o.value)
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 bg-white"
                  }`}
                >
                  {isSel(o.value) ? "✓" : ""}
                </span>
                {o.label}
              </button>
            ))}
            {allowCustom &&
              query.trim() &&
              !options.some(
                (o) => o.value.toLowerCase() === query.trim().toLowerCase()
              ) && (
                <button
                  type="button"
                  className="flex w-full border-t border-slate-100 px-3 py-2 text-left text-sm text-blue-600 hover:bg-blue-50"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => addCustom(query)}
                >
                  Add &ldquo;{query.trim()}&rdquo;
                </button>
              )}
            {!query &&
              allowCustom &&
              suggestionPool.map((s) => (
                <button
                  key={`sug-${s}`}
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-500 hover:bg-slate-50"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => toggle(s)}
                >
                  <span className="h-4 w-4 shrink-0 rounded border border-slate-200" />
                  {labelFor(s)}
                </button>
              ))}
            {allowCustom && (
              <div className="sticky bottom-0 border-t border-slate-100 bg-slate-50 px-3 py-2 text-[11px] text-slate-500">
                {query.trim()
                  ? "Press Enter to add a custom value"
                  : "Type any value and press Enter to add it"}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
