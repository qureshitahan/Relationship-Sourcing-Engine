import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getStats, listPrincipals } from "../api/client";
import { Badge, Card, Loading, PageHeader } from "../components/ui";

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

/** One stage in the left-to-right outreach funnel. */
function FunnelStep({
  label,
  value,
  sub,
  to,
  tone = "slate",
}: {
  label: string;
  value: number | string;
  sub?: string;
  to?: string;
  tone?: "slate" | "blue" | "amber" | "green";
}) {
  const tones: Record<string, string> = {
    slate: "text-slate-900",
    blue: "text-blue-700",
    amber: "text-amber-700",
    green: "text-emerald-700",
  };
  const inner = (
    <div className="flex-1 rounded-xl border border-slate-200 bg-white px-5 py-4 transition hover:shadow-md">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className={`mt-1 text-3xl font-semibold ${tones[tone]}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
  return to ? (
    <Link to={to} className="flex-1">
      {inner}
    </Link>
  ) : (
    inner
  );
}

function MiniStat({ label, value, to }: { label: string; value: number; to?: string }) {
  const inner = (
    <Card className="px-4 py-3 transition hover:shadow-md">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold text-slate-900">{value}</div>
    </Card>
  );
  return to ? <Link to={to}>{inner}</Link> : inner;
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });

  if (isLoading || !data) return <Loading />;

  const principal = principals?.items[0];
  const documentFocus = principal?.document_focus;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Your outreach at a glance: who you've found, approved, contacted, and heard back from."
      />

      <Card className="mb-6 border-slate-200 bg-slate-50/60 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Principal{principal ? ` · ${principal.name}` : ""}
            </div>
            {documentFocus ? (
              <p className="mt-1 max-w-3xl text-sm text-slate-800">
                Document focus: {documentFocus}
              </p>
            ) : (
              <p className="mt-1 text-sm text-slate-600">
                {principal
                  ? "Documents indexed without a niche filter."
                  : "Add a principal and upload their documents to get started."}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Link
              to="/principals"
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              {principal ? "Manage documents" : "Add principal"}
            </Link>
            <Link
              to="/agent"
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              Set outreach goal →
            </Link>
          </div>
        </div>
      </Card>

      {/* Outreach funnel — the core story */}
      <div className="mb-3 text-sm font-semibold text-slate-700">Outreach funnel</div>
      <div className="mb-8 flex flex-col gap-2 md:flex-row md:items-stretch">
        <FunnelStep
          label="Prospects"
          value={data.prospects_total}
          sub={`${data.prospects_researched} researched`}
          to="/prospects"
        />
        <FunnelStep
          label="Approved"
          value={data.prospects_approved}
          sub="cleared for outreach"
          tone="amber"
          to="/prospects?stage=approved"
        />
        <FunnelStep
          label="Contacted"
          value={data.emails_sent}
          sub="emails sent"
          tone="blue"
          to="/outreach"
        />
        <FunnelStep
          label="Opened"
          value={data.emails_opened}
          sub={`${pct(data.open_rate)} open rate`}
          tone="blue"
          to="/outreach"
        />
        <FunnelStep
          label="Replied"
          value={data.emails_replied}
          sub={`${pct(data.reply_rate)} reply rate`}
          tone="green"
          to="/outreach"
        />
      </div>

      {/* Secondary counts */}
      <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-5">
        <MiniStat label="Principals" value={data.principals_total} to="/principals" />
        <MiniStat
          label="Organizations"
          value={data.organizations_total}
          to="/organizations"
        />
        <MiniStat
          label="Discovery runs"
          value={data.discovery_runs_total}
          to="/discover"
        />
        <MiniStat label="Insights" value={data.insights_total} />
        <MiniStat label="Drafts" value={data.email_drafts_total} to="/emails" />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold text-slate-700">
            Prospect pipeline
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.prospects_by_status ?? {}).length === 0 && (
              <span className="text-sm text-slate-400">No prospects yet.</span>
            )}
            {Object.entries(data.prospects_by_status ?? {}).map(([status, count]) => (
              <Badge key={status}>
                {status.replace(/_/g, " ")}: {count}
              </Badge>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold text-slate-700">
            Emails by status
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.emails_by_status ?? {}).length === 0 && (
              <span className="text-sm text-slate-400">No emails yet.</span>
            )}
            {Object.entries(data.emails_by_status ?? {}).map(([status, count]) => (
              <Badge
                key={status}
                tone={
                  status === "replied"
                    ? "green"
                    : status === "sent"
                    ? "blue"
                    : "slate"
                }
              >
                {status.replace(/_/g, " ")}: {count}
              </Badge>
            ))}
          </div>
        </Card>
      </div>

      {data.principals_total === 0 && (
        <Card className="mt-6 p-6 text-center text-sm text-slate-500">
          Start by creating a{" "}
          <Link to="/principals" className="font-medium text-slate-900 underline">
            principal profile
          </Link>
          , then run{" "}
          <Link to="/discover" className="font-medium text-slate-900 underline">
            discovery
          </Link>
          .
        </Card>
      )}
    </div>
  );
}
