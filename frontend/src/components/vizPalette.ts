/**
 * Chart palette and series shape.
 *
 * Separate from `charts.tsx` because a module that exports components must export
 * only components for fast refresh to work; these are values and types.
 *
 * The three series colours are a validated categorical set for a white surface:
 * they clear the lightness band, the chroma floor, colour-blind separation on all
 * pairs, and the normal-vision floor. Assign them in order and key them to the
 * series identity — a filter that removes a series must never repaint the ones
 * that remain, or a reader who learned "sent is blue" is misled. Aqua sits below
 * 3:1 against white, which is why every chart here also ships a table view.
 */
export const VIZ = {
  series: ["#2a78d6", "#eb6834", "#1baf7a"],
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  muted: "#898781",
  secondary: "#52514e",
  surface: "#ffffff",
} as const;

export type Series = {
  /** Stable identity — the colour follows this, not the row order. */
  key: string;
  label: string;
  color: string;
  values: number[];
};

export type BarRow = {
  key: string;
  label: string;
  value: number;
  /** Secondary detail, surfaced on hover and in the table view. */
  note?: string;
};
