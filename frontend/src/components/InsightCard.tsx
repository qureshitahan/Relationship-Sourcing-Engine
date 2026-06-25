import type { RelevanceInsight, SourcedBullet } from "../types";
import { Badge, Card, ScoreBar } from "./ui";

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Render **markdown bold** segments as <strong>. */
function FormattedText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={i} className="font-semibold text-slate-900">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

function normalizeBullet(item: string | SourcedBullet): SourcedBullet {
  if (typeof item === "string") return { text: item };
  return item;
}

function SourceLink({
  url,
  title,
  sourceDate,
}: {
  url: string;
  title?: string | null;
  sourceDate?: string | null;
}) {
  const label = title?.trim() || hostname(url);
  const stale = isSourceStale(sourceDate);
  return (
    <span className="ml-1.5 inline-flex flex-wrap items-center gap-1">
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-0.5 whitespace-nowrap text-xs font-medium text-blue-600 hover:underline"
        title={`${url}${sourceDate ? ` · ${sourceDate}` : ""}`}
      >
        {label} ↗
      </a>
      {sourceDate && (
        <span
          className={`text-[10px] ${stale ? "font-medium text-amber-700" : "text-slate-400"}`}
        >
          · {formatSourceDate(sourceDate)}
          {stale ? " · dated" : ""}
        </span>
      )}
    </span>
  );
}

/** True when source is older than 18 months (time-bound facts may be stale). */
function isSourceStale(sourceDate?: string | null): boolean {
  if (!sourceDate) return false;
  const d = parseSourceDate(sourceDate);
  if (!d) return false;
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - 18);
  return d < cutoff;
}

function parseSourceDate(raw: string): Date | null {
  const s = raw.trim();
  const m = s.match(/^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/);
  if (m) {
    const y = Number(m[1]);
    const mo = m[2] ? Number(m[2]) - 1 : 0;
    const day = m[3] ? Number(m[3]) : 1;
    return new Date(y, mo, day);
  }
  const parsed = Date.parse(s);
  return Number.isNaN(parsed) ? null : new Date(parsed);
}

function formatSourceDate(raw: string): string {
  const d = parseSourceDate(raw);
  if (!d) return raw;
  return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function BulletList({
  label,
  items,
  showSources = false,
}: {
  label: string;
  items: (string | SourcedBullet)[];
  showSources?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <ul className="mt-1 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-slate-700">
        {items.map((raw, i) => {
          const item = normalizeBullet(raw);
          return (
            <li key={i}>
              <FormattedText text={item.text} />
              {showSources && item.source_url && (
                <SourceLink
                  url={item.source_url}
                  title={item.source_title}
                  sourceDate={item.source_date}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TextBullets({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <ul className="mt-1 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-slate-700">
        {items.map((t, i) => (
          <li key={i}>
            <FormattedText text={t} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function InsightCard({ insight }: { insight: RelevanceInsight }) {
  const keyFacts = insight.key_facts ?? [];
  const fit = (insight.why_relevant ?? "")
    .split("\n")
    .map((s) => s.replace(/^[-•\s]+/, "").trim())
    .filter(Boolean);
  const talkingPoints = insight.talking_points ?? [];
  const sources = insight.sources ?? [];
  const warnings = insight.identity_warnings ?? [];
  const verified = insight.identity_verified !== false;

  const hasInlineSources = keyFacts.some(
    (f) => typeof f !== "string" && f.source_url
  );
  const hasStaleFacts = keyFacts.some(
    (f) => typeof f !== "string" && isSourceStale(f.source_date)
  );

  return (
    <Card className="p-5">
      {!verified && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-semibold">Identity not verified</div>
          <p className="mt-1 text-amber-800">
            LinkedIn and CRM data may refer to different people or companies. Review
            before outreach.
          </p>
          {warnings.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-800">
              {warnings.map((w, i) => (
                <li key={i}>
                  <FormattedText text={w} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-slate-700">
            Strategic relevance
          </span>
          {insight.opportunity_type && (
            <Badge tone="purple">
              {insight.opportunity_type.replace(/_/g, " ")}
            </Badge>
          )}
        </div>
        <ScoreBar value={insight.relevance_score} />
      </div>

      {insight.snapshot && (
        <p className="mb-4 text-sm font-medium leading-relaxed text-slate-800">
          <FormattedText text={insight.snapshot} />
        </p>
      )}

      <div className="space-y-4">
        {hasStaleFacts && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            Some facts below are <strong>dated</strong> (e.g. 2023 news). Fine for
            background research, but avoid leading outreach with old stats — verify
            links (some may 404) and prefer timeless hooks.
          </div>
        )}
        <BulletList
          label="Key facts (researched)"
          items={keyFacts}
          showSources
        />
        <TextBullets label="Fit with the principal" items={fit} />
        <TextBullets label="Talking points" items={talkingPoints} />

        {insight.signals && insight.signals.length > 0 && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Signals
            </div>
            <div className="mt-1 flex flex-wrap gap-2">
              {insight.signals.map((s, i) => (
                <Badge key={i} tone="amber">
                  {s}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {sources.length > 0 && !hasInlineSources && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Sources
            </div>
            <div className="mt-1 flex flex-wrap gap-2">
              {sources.map((s, i) => (
                <a
                  key={i}
                  href={s.url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md bg-slate-100 px-2 py-1 text-xs text-blue-600 hover:underline"
                  title={s.title ?? s.url}
                >
                  {hostname(s.url)} ↗
                </a>
              ))}
            </div>
          </div>
        )}
      </div>

      {insight.generated_by && (
        <div className="mt-4 text-xs text-slate-400">
          Generated by {insight.generated_by}
        </div>
      )}
    </Card>
  );
}
