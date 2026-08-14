/**
 * Chart primitives, hand-built in SVG.
 *
 * No charting dependency: the app ships three chart shapes, and a library would
 * be more bytes and more API surface than the shapes themselves.
 *
 * The specs below are deliberate, not taste — thin marks, hairline solid grid,
 * a 2px surface ring on markers, a legend whenever there are two or more series,
 * labels only at the endpoints, and a table view beside every chart so no value
 * is reachable only by hovering. The palette is a validated categorical set
 * (blue → orange → aqua, assigned in fixed order and keyed to the series, so a
 * filter that drops a series never repaints the survivors).
 *
 * The app has no dark mode, so these commit to the light surface and paint every
 * colour explicitly rather than inheriting.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { VIZ } from "./vizPalette";
import type { BarRow, Series } from "./vizPalette";

/** Container width, so stroke widths stay true instead of being scaled by a viewBox. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0;
      setWidth(next);
    });
    observer.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Round a maximum up to a clean axis top, so ticks land on readable numbers. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * magnitude;
}

function fmt(n: number): string {
  return n.toLocaleString();
}

function shortDate(iso: string): string {
  // Dates arrive as plain YYYY-MM-DD; split rather than parse, so no timezone
  // shift can move a bucket to the previous day.
  const [, m, d] = iso.split("-");
  if (!m || !d) return iso;
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${d.replace(/^0/, "")} ${months[Number(m) - 1] ?? m}`;
}

/** Toggle between a chart and its table twin. */
function ViewToggle({
  showTable,
  onToggle,
}: {
  showTable: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="text-xs font-medium text-blue-700 hover:underline"
    >
      {showTable ? "Show chart" : "Show table"}
    </button>
  );
}

function Legend({ series }: { series: Series[] }) {
  // One series needs no legend — the title already names what is plotted.
  if (series.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-0.5 w-4 rounded-full"
            style={{ backgroundColor: s.color }}
          />
          <span className="text-xs text-slate-600">{s.label}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * Multi-series line chart over time, with a crosshair + tooltip.
 *
 * One y-axis always: two measures of different scale get two charts, never a
 * second axis, whose alignment would invent a correlation the data does not have.
 */
export function TrendChart({
  title,
  subtitle,
  labels,
  series,
  height = 220,
  emptyMessage = "No activity in this range.",
}: {
  title: string;
  subtitle?: string;
  labels: string[];
  series: Series[];
  height?: number;
  emptyMessage?: string;
}) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const pad = { top: 14, right: 58, bottom: 26, left: 46 };
  const plotW = Math.max(0, width - pad.left - pad.right);
  const plotH = Math.max(0, height - pad.top - pad.bottom);

  const max = useMemo(
    () => niceMax(Math.max(0, ...series.flatMap((s) => s.values))),
    [series]
  );

  const x = useCallback(
    (i: number) =>
      pad.left + (labels.length <= 1 ? plotW / 2 : (plotW * i) / (labels.length - 1)),
    [labels.length, plotW, pad.left]
  );
  const y = useCallback(
    (v: number) => pad.top + plotH - (plotH * v) / max,
    [plotH, max, pad.top]
  );

  const ticks = useMemo(() => {
    const count = 4;
    return Array.from({ length: count + 1 }, (_, i) => (max / count) * i);
  }, [max]);

  // Show at most ~6 x labels so they never collide.
  const xLabelEvery = Math.max(1, Math.ceil(labels.length / 6));

  const hasData = labels.length > 0 && series.some((s) => s.values.some((v) => v > 0));

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!labels.length || plotW <= 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left - pad.left;
    const ratio = labels.length <= 1 ? 0 : px / plotW;
    const i = Math.round(ratio * (labels.length - 1));
    setHover(Math.min(labels.length - 1, Math.max(0, i)));
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-1 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-800">{title}</div>
          {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
        </div>
        <ViewToggle showTable={showTable} onToggle={() => setShowTable((v) => !v)} />
      </div>

      <div className="mb-3 mt-2">
        <Legend series={series} />
      </div>

      {showTable ? (
        <TrendTable labels={labels} series={series} />
      ) : !hasData ? (
        <p className="py-10 text-center text-sm text-slate-400">{emptyMessage}</p>
      ) : (
        <div ref={box} className="relative">
          <svg
            width={width || 0}
            height={height}
            role="img"
            aria-label={title}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            {/* Gridlines: solid hairlines, one step off the surface. */}
            {ticks.map((t) => (
              <g key={t}>
                <line
                  x1={pad.left}
                  x2={pad.left + plotW}
                  y1={y(t)}
                  y2={y(t)}
                  stroke={t === 0 ? VIZ.axis : VIZ.grid}
                  strokeWidth={1}
                />
                <text
                  x={pad.left - 8}
                  y={y(t) + 3.5}
                  textAnchor="end"
                  fontSize={10}
                  fill={VIZ.muted}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {fmt(Math.round(t))}
                </text>
              </g>
            ))}

            {labels.map((label, i) =>
              i % xLabelEvery === 0 || i === labels.length - 1 ? (
                <text
                  key={label}
                  x={x(i)}
                  y={height - 8}
                  textAnchor="middle"
                  fontSize={10}
                  fill={VIZ.muted}
                >
                  {shortDate(label)}
                </text>
              ) : null
            )}

            {hover !== null && (
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={pad.top}
                y2={pad.top + plotH}
                stroke={VIZ.axis}
                strokeWidth={1}
              />
            )}

            {series.map((s) => (
              <polyline
                key={s.key}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
              />
            ))}

            {/* End markers carry a 2px surface ring so overlaps stay legible. */}
            {series.map((s) => {
              const last = s.values.length - 1;
              if (last < 0) return null;
              return (
                <circle
                  key={s.key}
                  cx={x(last)}
                  cy={y(s.values[last])}
                  r={4}
                  fill={s.color}
                  stroke={VIZ.surface}
                  strokeWidth={2}
                />
              );
            })}

            {/* Direct labels at the endpoint only — never a number on every point. */}
            {series.map((s) => {
              const last = s.values.length - 1;
              if (last < 0 || !s.values[last]) return null;
              return (
                <text
                  key={s.key}
                  x={x(last) + 9}
                  y={y(s.values[last]) + 3.5}
                  fontSize={10}
                  fill={VIZ.secondary}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {fmt(s.values[last])}
                </text>
              );
            })}

            {hover !== null &&
              series.map((s) => (
                <circle
                  key={s.key}
                  cx={x(hover)}
                  cy={y(s.values[hover] ?? 0)}
                  r={4}
                  fill={s.color}
                  stroke={VIZ.surface}
                  strokeWidth={2}
                />
              ))}
          </svg>

          {hover !== null && (
            <div
              className="pointer-events-none absolute z-10 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg"
              style={{
                left: Math.min(Math.max(x(hover) + 12, 0), Math.max(width - 150, 0)),
                top: pad.top,
              }}
            >
              <div className="mb-1 font-medium text-slate-700">
                {shortDate(labels[hover])}
              </div>
              {series.map((s) => (
                <div key={s.key} className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: s.color }}
                  />
                  <span className="text-slate-500">{s.label}</span>
                  <span className="ml-auto pl-3 font-medium tabular-nums text-slate-800">
                    {fmt(s.values[hover] ?? 0)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TrendTable({ labels, series }: { labels: string[]; series: Series[] }) {
  if (!labels.length)
    return <p className="py-6 text-center text-sm text-slate-400">Nothing to show.</p>;
  return (
    <div className="max-h-72 overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-white">
          <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
            <th className="py-2 pr-3 text-left font-medium">Date</th>
            {series.map((s) => (
              <th key={s.key} className="px-3 py-2 text-right font-medium">
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((label, i) => (
            <tr key={label} className="border-b border-slate-100 last:border-0">
              <td className="py-1.5 pr-3 text-slate-600">{shortDate(label)}</td>
              {series.map((s) => (
                <td
                  key={s.key}
                  className="px-3 py-1.5 text-right tabular-nums text-slate-700"
                >
                  {fmt(s.values[i] ?? 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Horizontal bar path: square at the baseline, 4px rounded at the data end. */
function barPath(x0: number, y0: number, w: number, h: number, r = 4): string {
  const radius = Math.min(r, w, h / 2);
  if (w <= radius) return `M${x0},${y0} h${w} v${h} h${-w} Z`;
  const x1 = x0 + w;
  return [
    `M${x0},${y0}`,
    `H${x1 - radius}`,
    `A${radius},${radius} 0 0 1 ${x1},${y0 + radius}`,
    `V${y0 + h - radius}`,
    `A${radius},${radius} 0 0 1 ${x1 - radius},${y0 + h}`,
    `H${x0}`,
    "Z",
  ].join(" ");
}

/**
 * Ranked horizontal bars — one measure, one colour.
 *
 * Horizontal because the labels are campaign and person names: long text reads
 * straight instead of being rotated. A single series, so no legend and no
 * value-ramp: colouring each bar darker-where-bigger would double-encode the
 * length the bar already shows.
 */
export function BarList({
  title,
  subtitle,
  rows,
  valueLabel = "Sent",
  color = VIZ.series[0],
  max: maxOverride,
  emptyMessage = "Nothing here yet.",
}: {
  title: string;
  subtitle?: string;
  rows: BarRow[];
  valueLabel?: string;
  color?: string;
  max?: number;
  emptyMessage?: string;
}) {
  const [showTable, setShowTable] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [track, trackW] = useWidth<HTMLDivElement>();
  const max = niceMax(maxOverride ?? Math.max(0, ...rows.map((r) => r.value)));

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-800">{title}</div>
          {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
        </div>
        <ViewToggle showTable={showTable} onToggle={() => setShowTable((v) => !v)} />
      </div>

      {rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-400">{emptyMessage}</p>
      ) : showTable ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
              <th className="py-2 pr-3 text-left font-medium">Name</th>
              <th className="px-3 py-2 text-right font-medium">{valueLabel}</th>
              <th className="py-2 pl-3 text-right font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-slate-100 last:border-0">
                <td className="py-1.5 pr-3 text-slate-700">{r.label}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-800">
                  {fmt(r.value)}
                </td>
                <td className="py-1.5 pl-3 text-right text-slate-500">{r.note ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div ref={track} className="space-y-2.5">
          {rows.map((r) => {
            // Pixel widths, not percentages: the rounded data end is drawn as a
            // path, which needs a real length. A value above zero always gets a
            // visible sliver so "1" never renders as nothing.
            const full = Math.max(0, trackW);
            const w = max && full ? Math.max((r.value / max) * full, r.value > 0 ? 3 : 0) : 0;
            return (
              <div
                key={r.key}
                // Hit area spans the whole row, so it clears the 24px minimum
                // rather than requiring a landing on a 12px bar.
                className="cursor-default py-0.5"
                onMouseEnter={() => setHover(r.key)}
                onMouseLeave={() => setHover(null)}
              >
                <div className="mb-1 flex items-baseline justify-between gap-3">
                  <span className="truncate text-xs text-slate-600" title={r.label}>
                    {r.label}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-slate-500">
                    {hover === r.key && r.note ? r.note : fmt(r.value)}
                  </span>
                </div>
                <svg
                  width={full || 0}
                  height={12}
                  role="img"
                  aria-label={`${r.label}: ${fmt(r.value)}${r.note ? `, ${r.note}` : ""}`}
                >
                  <rect x={0} y={0} width={full} height={12} fill="#f4f4f2" rx={2} />
                  {w > 0 && <path d={barPath(0, 0, w, 12)} fill={color} />}
                </svg>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Label · value · optional sub-line. The number IS the chart. */
export function StatTile({
  label,
  value,
  sub,
  tone = "slate",
}: {
  label: string;
  value: number | string;
  sub?: string;
  tone?: "slate" | "blue" | "green" | "amber";
}) {
  const tones: Record<string, string> = {
    slate: "text-slate-900",
    blue: "text-blue-700",
    green: "text-emerald-700",
    amber: "text-amber-700",
  };
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold ${tones[tone]}`}>
        {typeof value === "number" ? fmt(value) : value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}
