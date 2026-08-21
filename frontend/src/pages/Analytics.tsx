/**
 * Analytics — email and LinkedIn reported side by side, never merged.
 *
 * The two channels get their own sections, their own stat rows, their own charts
 * and their own tables. Nothing on this page adds one channel's numbers to the
 * other's, because the acts are not the same: an email open has no LinkedIn
 * equivalent, a connection invitation has no email equivalent, and a blended
 * reply rate would average two different things into a number that means
 * neither. Two labelled totals tell the truth; one combined total does not.
 *
 * Everything is read from the existing rows, so new activity shows up on the
 * next load with nothing to rebuild.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getAnalytics } from "../api/client";
import type {
  AnalyticsChannel,
  AnalyticsFollowers,
  AnalyticsGroupRow,
} from "../types";
import { BarList, StatTile, TrendChart } from "../components/charts";
import { VIZ } from "../components/vizPalette";
import type { BarRow, Series } from "../components/vizPalette";
import { Badge, Card, Loading, PageHeader } from "../components/ui";

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 0, label: "All time" },
] as const;

function pct(n: number): string {
  return `${(n * 100).toFixed(n > 0 && n < 0.01 ? 2 : 1)}%`;
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-700 focus:border-slate-400 focus:outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function StatusBadges({
  by_status,
  tone,
}: {
  by_status: Record<string, number>;
  tone: (status: string) => "slate" | "blue" | "green" | "amber" | "red" | "purple";
}) {
  const entries = Object.entries(by_status ?? {});
  if (entries.length === 0)
    return <span className="text-sm text-slate-400">Nothing yet.</span>;
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map(([status, count]) => (
        <Badge key={status} tone={tone(status)}>
          {status.replace(/_/g, " ")}: {count}
        </Badge>
      ))}
    </div>
  );
}

function groupRows(rows: AnalyticsGroupRow[], limit = 8): BarRow[] {
  return rows.slice(0, limit).map((r) => ({
    key: r.key ?? r.label,
    label: r.label,
    value: r.sent,
    note: `${r.total} total · ${r.replied} replied · ${pct(r.reply_rate)} reply`,
  }));
}

/** Table of every group, so no value is reachable only through a chart. */
function GroupTable({
  title,
  rows,
  totalLabel,
}: {
  title: string;
  rows: AnalyticsGroupRow[];
  totalLabel: string;
}) {
  return (
    <Card className="p-5">
      <div className="mb-3 text-sm font-semibold text-slate-800">{title}</div>
      {rows.length === 0 ? (
        <p className="py-4 text-sm text-slate-400">Nothing in this range.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[30rem] text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 pr-3 text-left font-medium">Name</th>
                <th className="px-3 py-2 text-right font-medium">{totalLabel}</th>
                <th className="px-3 py-2 text-right font-medium">Sent</th>
                <th className="px-3 py-2 text-right font-medium">Replied</th>
                <th className="py-2 pl-3 text-right font-medium">Reply rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.key ?? r.label}
                  className="border-b border-slate-100 last:border-0"
                >
                  <td className="py-2 pr-3 text-slate-700">{r.label}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                    {r.total}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-800">
                    {r.sent}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                    {r.replied}
                  </td>
                  <td className="py-2 pl-3 text-right tabular-nums">
                    {r.sent ? (
                      <span className="font-medium text-emerald-700">
                        {pct(r.reply_rate)}
                      </span>
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function EmailSection({ channel }: { channel: AnalyticsChannel }) {
  const t = channel.totals;
  const labels = channel.trend.map((p) => p.date);
  const series: Series[] = [
    {
      key: "email.sent",
      label: "Sent",
      color: VIZ.series[0],
      values: channel.trend.map((p) => p.sent),
    },
    {
      key: "email.replied",
      label: "Replied",
      color: VIZ.series[1],
      values: channel.trend.map((p) => p.replied),
    },
    {
      key: "email.opened",
      label: "Opened",
      color: VIZ.series[2],
      values: channel.trend.map((p) => p.opened),
    },
  ];

  return (
    <section className="mb-12">
      <div className="mb-1 flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: VIZ.series[0] }}
        />
        <h2 className="text-base font-semibold text-slate-900">Email</h2>
      </div>
      <p className="mb-4 text-xs text-slate-400">
        From email drafts only. No LinkedIn activity is counted anywhere in this
        section.
      </p>

      {/* Same tile width as the LinkedIn section, so the two read as siblings. */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {/*
          Not "Total drafts": the table is called email_drafts, so every row is a
          "draft" in the storage sense even after it has been sent — which reads,
          wrongly, as a pile of unsent work sitting in the queue. "Total emails"
          counts rows; "Awaiting approval" is the one that is actually a draft.
        */}
        <StatTile label="Total emails" value={t.total} sub="every status" />
        <StatTile label="Awaiting approval" value={t.drafts} sub="status: draft" />
        <StatTile label="Approved" value={t.approved} tone="amber" />
        <StatTile label="Sent" value={t.sent} tone="blue" />
        <StatTile
          label="Opened"
          value={t.opened}
          sub={`${pct(t.open_rate)} open rate`}
          tone="blue"
        />
        <StatTile
          label="Replied"
          value={t.replied}
          sub={`${pct(t.reply_rate)} reply rate`}
          tone="green"
        />
      </div>

      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <TrendChart
          title="Email activity over time"
          subtitle="Each event counts on the day it happened"
          labels={labels}
          series={series}
          emptyMessage="No email activity in this range."
        />
        <BarList
          title="Campaign performance"
          subtitle="Emails sent per campaign — hover a row for replies"
          rows={groupRows(channel.by_campaign)}
          valueLabel="Sent"
          color={VIZ.series[0]}
          emptyMessage="No campaigns with email activity."
        />
      </div>

      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <BarList
          title="Outreach by principal"
          subtitle="Emails sent on each principal's behalf"
          rows={groupRows(channel.by_principal)}
          valueLabel="Sent"
          color={VIZ.series[0]}
          emptyMessage="No principals with email activity."
        />
        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold text-slate-800">
            Emails by status
          </div>
          <StatusBadges
            by_status={t.by_status}
            tone={(s) =>
              s === "replied"
                ? "green"
                : s === "sent"
                ? "blue"
                : s === "bounced"
                ? "red"
                : s === "scheduled"
                ? "purple"
                : "slate"
            }
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <GroupTable
          title="Every campaign — email"
          rows={channel.by_campaign}
          totalLabel="Drafts"
        />
        <GroupTable
          title="Every principal — email"
          rows={channel.by_principal}
          totalLabel="Drafts"
        />
      </div>
    </section>
  );
}

function LinkedInSection({ channel }: { channel: AnalyticsChannel }) {
  const t = channel.totals;
  const labels = channel.trend.map((p) => p.date);
  const series: Series[] = [
    {
      key: "li.invited",
      label: "Invitations",
      color: VIZ.series[1],
      values: channel.trend.map((p) => p.invited),
    },
    {
      key: "li.sent",
      label: "DMs delivered",
      color: VIZ.series[0],
      values: channel.trend.map((p) => p.sent),
    },
    {
      key: "li.replied",
      label: "Replied",
      color: VIZ.series[2],
      values: channel.trend.map((p) => p.replied),
    },
  ];

  return (
    <section className="mb-10">
      <div className="mb-1 flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: VIZ.series[1] }}
        />
        <h2 className="text-base font-semibold text-slate-900">LinkedIn</h2>
      </div>
      <p className="mb-4 max-w-4xl text-xs text-slate-400">
        Prospect messages only — follower DMs belong to the Followers module and
        are not counted here, and anything sent by hand on linkedin.com is not
        recorded at all. LinkedIn has no opens, so it reports invitations instead.
        Reaching a non-connection takes two steps: an invitation goes out first,
        and the DM only arrives once that invitation is accepted — so{" "}
        <b>total outreach</b> is what left, while <b>DMs delivered</b> is what
        landed.
      </p>

      {/*
        "Sent" alone reads as the whole outreach effort, which understates it
        badly: an unaccepted invitation still put a note in front of someone, and
        on this side most invitations are never accepted. So the volume figure
        leads, and the delivered-DM figure says plainly what it is.
      */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        <StatTile label="Total messages" value={t.total} sub="all statuses" />
        <StatTile label="Awaiting approval" value={t.drafts} sub="status: draft" />
        <StatTile label="Approved" value={t.approved} tone="amber" />
        <StatTile
          label="Total outreach"
          value={t.outreach_total}
          sub={
            t.direct_dms
              ? `${t.invited} invitations + ${t.direct_dms} direct DMs`
              : "invitations + direct DMs"
          }
          tone="blue"
        />
        <StatTile
          label="Invitations sent"
          value={t.invited}
          sub={`${t.accepted} accepted · ${pct(t.acceptance_rate)}`}
          tone="amber"
        />
        <StatTile
          label="DMs delivered"
          value={t.sent}
          sub="reached the inbox"
          tone="blue"
        />
        <StatTile
          label="Replied"
          value={t.replied}
          sub={`${pct(t.reply_rate)} of delivered DMs`}
          tone="green"
        />
        <StatTile
          label="Awaiting acceptance"
          value={Math.max(0, t.invited - t.accepted)}
          sub="invited, not yet connected"
        />
      </div>

      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <TrendChart
          title="LinkedIn activity over time"
          subtitle="Each event counts on the day it happened"
          labels={labels}
          series={series}
          emptyMessage="No LinkedIn activity in this range."
        />
        <BarList
          title="Campaign performance"
          subtitle="DMs delivered per campaign — hover a row for replies"
          rows={groupRows(channel.by_campaign)}
          valueLabel="Sent"
          color={VIZ.series[1]}
          emptyMessage="No campaigns with LinkedIn activity."
        />
      </div>

      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <BarList
          title="Outreach by principal"
          subtitle="DMs delivered on each principal's behalf"
          rows={groupRows(channel.by_principal)}
          valueLabel="Sent"
          color={VIZ.series[1]}
          emptyMessage="No principals with LinkedIn activity."
        />
        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold text-slate-800">
            LinkedIn by status
          </div>
          <StatusBadges
            by_status={t.by_status}
            tone={(s) =>
              s === "replied"
                ? "green"
                : s === "sent"
                ? "blue"
                : s === "invite_sent"
                ? "amber"
                : s === "failed"
                ? "red"
                : "slate"
            }
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <GroupTable
          title="Every campaign — LinkedIn"
          rows={channel.by_campaign}
          totalLabel="Messages"
        />
        <GroupTable
          title="Every principal — LinkedIn"
          rows={channel.by_principal}
          totalLabel="Messages"
        />
      </div>
    </section>
  );
}

/**
 * Followers — its own section, never folded into LinkedIn above.
 *
 * The two measure different acts: prospect outreach invites a stranger and waits
 * for acceptance, while a follower DM goes to an audience that already follows
 * the account, with no invitation step. Merging them would let one bulk follower
 * send swamp the prospect reply rate.
 *
 * Grouped by account because that is the only grouping this module has — a
 * follower belongs to the account they follow, and the DM is sent from that same
 * account. There is no campaign or principal behind it, which is also why the
 * filters at the top of the page do not apply here; the note below says so
 * rather than letting the numbers look filtered when they are not.
 */
function FollowersSection({ followers }: { followers: AnalyticsFollowers }) {
  const rows = followers.by_account ?? [];
  const t = followers.totals;
  const needsReview = rows.reduce((n, r) => n + r.needs_review, 0);

  return (
    <section className="mb-10">
      <div className="mb-1 flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: VIZ.series[2] }}
        />
        <h2 className="text-base font-semibold text-slate-900">
          LinkedIn followers
        </h2>
      </div>
      <p className="mb-4 max-w-4xl text-xs text-slate-400">
        DMs to people who already follow a connected account — counted apart from
        the prospect outreach above, and never added to it. Audience size and who
        has been contacted are <b>all-time</b> figures: a follower carries no
        "started following" date, so there is nothing to measure a window
        against. Everything sent and replied does follow the selected range. The
        principal and campaign filters do not apply here, because a follower
        belongs to an account rather than to a campaign.
      </p>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        <StatTile label="Followers" value={t.followers} sub="all time" />
        <StatTile
          label="Contacted"
          value={t.contacted}
          sub={`${t.never_contacted} never contacted`}
          tone="amber"
        />
        <StatTile label="DMs sent" value={t.sent} sub="in range" tone="blue" />
        <StatTile
          label="Replied"
          value={t.replied}
          sub={`${pct(t.reply_rate)} reply rate`}
          tone="green"
        />
      </div>

      {/*
        Surfaced rather than buried in the table: an interrupted send left an
        outcome nobody knows, and it is deliberately never retried automatically
        (a possible duplicate is worse than a missed message), so it stays open
        until a person looks at it.
      */}
      {needsReview > 0 && (
        <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {needsReview} send{needsReview === 1 ? "" : "s"} need review — the job
          stopped mid-send, so whether the message went out is unknown. These are
          never retried on their own; check them on the Followers page.
        </p>
      )}

      <Card className="p-5">
        <div className="mb-3 text-sm font-semibold text-slate-800">
          By sending account
        </div>
        {rows.length === 0 ? (
          <p className="py-4 text-sm text-slate-400">
            No followers synced yet.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
                  <th className="py-2 pr-3 text-left font-medium">Account</th>
                  <th className="px-3 py-2 text-right font-medium">Followers</th>
                  <th className="px-3 py-2 text-right font-medium">Contacted</th>
                  <th className="px-3 py-2 text-right font-medium">
                    Never contacted
                  </th>
                  <th className="px-3 py-2 text-right font-medium">DMs sent</th>
                  <th className="px-3 py-2 text-right font-medium">Replied</th>
                  <th className="px-3 py-2 text-right font-medium">Reply rate</th>
                  <th className="px-3 py-2 text-right font-medium">
                    Not reachable
                  </th>
                  <th className="py-2 pl-3 text-right font-medium">
                    Needs review
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.account_id}
                    className="border-b border-slate-100 last:border-0"
                  >
                    {/*
                      The account's own name when it is known, otherwise a
                      shortened id with the full value in the tooltip. Never a
                      guessed name — a wrong label on a performance report is
                      worse than a raw id.
                    */}
                    <td className="py-2 pr-3" title={r.account_id}>
                      {r.account_name ? (
                        <span className="font-medium text-slate-900">
                          {r.account_name}
                        </span>
                      ) : (
                        <span className="font-mono text-xs text-slate-500">
                          {r.account_id.slice(0, 10)}…
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                      {r.followers}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                      {r.contacted}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.never_contacted}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-800">
                      {r.sent}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                      {r.replied}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {r.sent ? (
                        <span className="font-medium text-emerald-700">
                          {pct(r.reply_rate)}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                      {r.not_reachable || <span className="text-slate-300">—</span>}
                    </td>
                    <td className="py-2 pl-3 text-right tabular-nums">
                      {r.needs_review ? (
                        <span className="font-medium text-amber-700">
                          {r.needs_review}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </section>
  );
}

export default function Analytics() {
  const [days, setDays] = useState<number>(30);
  // A custom range, when the presets are not the shape of the question. Empty
  // strings mean "not set", so the presets keep working exactly as before.
  const [start, setStart] = useState<string>("");
  const [end, setEnd] = useState<string>("");
  const [principalId, setPrincipalId] = useState<string>("");
  const [campaignId, setCampaignId] = useState<string>("");

  const custom = Boolean(start || end);

  const query = useMemo(
    () => ({
      days,
      // Sent only when set; the server then ignores `days`. Leaving `days` in
      // the payload keeps the request shape unchanged for every existing call.
      start: start || undefined,
      end: end || undefined,
      principal_id: principalId ? Number(principalId) : undefined,
      campaign_id: campaignId ? Number(campaignId) : undefined,
    }),
    [days, start, end, principalId, campaignId]
  );

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["analytics", query],
    queryFn: () => getAnalytics(query),
    // Hold the previous slice while a new one loads, so changing a filter dims
    // the page instead of flashing a skeleton and jumping the layout.
    placeholderData: (previous) => previous,
  });

  if (isLoading || !data) return <Loading />;

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="Email and LinkedIn outreach, reported separately. Every figure comes from your existing data and updates as new activity arrives."
      />

      {/* One filter row above everything it scopes — never per-chart filters. */}
      <Card className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-3 p-4">
        <div className="flex items-center gap-1">
          <span className="mr-1 text-xs font-medium text-slate-500">Range</span>
          {RANGES.map((r) => (
            <button
              key={r.label}
              type="button"
              // Choosing a preset clears the custom dates, so the two controls
              // can never both claim to be active.
              onClick={() => {
                setStart("");
                setEnd("");
                setDays(r.days);
              }}
              className={`rounded-lg px-2.5 py-1.5 text-sm transition ${
                !custom && days === r.days
                  ? "bg-slate-900 font-medium text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-slate-500">Custom</span>
          <input
            type="date"
            aria-label="Start date"
            value={start}
            max={end || undefined}
            onChange={(e) => setStart(e.target.value)}
            className={`rounded-lg border px-2.5 py-1.5 text-sm focus:outline-none ${
              custom
                ? "border-slate-400 bg-white text-slate-800"
                : "border-slate-300 bg-white text-slate-600"
            }`}
          />
          <span className="text-xs text-slate-400">to</span>
          <input
            type="date"
            aria-label="End date"
            value={end}
            min={start || undefined}
            onChange={(e) => setEnd(e.target.value)}
            className={`rounded-lg border px-2.5 py-1.5 text-sm focus:outline-none ${
              custom
                ? "border-slate-400 bg-white text-slate-800"
                : "border-slate-300 bg-white text-slate-600"
            }`}
          />
          {custom && (
            <button
              type="button"
              onClick={() => {
                setStart("");
                setEnd("");
              }}
              className="text-xs font-medium text-blue-700 hover:underline"
              title="Go back to the preset range"
            >
              Clear dates
            </button>
          )}
        </div>

        <Select
          label="Principal"
          value={principalId}
          onChange={setPrincipalId}
          options={[
            { value: "", label: "All principals" },
            ...data.principals.map((p) => ({ value: String(p.id), label: p.label })),
          ]}
        />
        <Select
          label="Campaign"
          value={campaignId}
          onChange={setCampaignId}
          options={[
            { value: "", label: "All campaigns" },
            ...data.campaigns.map((c) => ({ value: String(c.id), label: c.label })),
          ]}
        />

        {(principalId || campaignId) && (
          <button
            type="button"
            onClick={() => {
              setPrincipalId("");
              setCampaignId("");
            }}
            className="text-xs font-medium text-blue-700 hover:underline"
          >
            Clear filters
          </button>
        )}

        <span className="ml-auto text-xs text-slate-400">
          {data.since && data.until
            ? `${data.since.slice(0, 10)} to ${data.until}`
            : data.since
            ? `Since ${data.since.slice(0, 10)}`
            : data.until
            ? `Up to ${data.until}`
            : "All data since the beginning"}
        </span>
      </Card>

      <div className={isFetching ? "opacity-60 transition-opacity" : undefined}>
        <EmailSection channel={data.email} />
        <LinkedInSection channel={data.linkedin} />
        {/* Optional so an API that predates this section renders as before. */}
        {data.followers && <FollowersSection followers={data.followers} />}
      </div>
    </div>
  );
}
