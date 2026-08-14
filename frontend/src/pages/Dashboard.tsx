import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { getStats, listLinkedInAccounts, listPrincipals } from "../api/client";
import PipelineModeCard from "../components/PipelineModeCard";
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

/** One row's account cell: the connected account's own name, or what we do know. */
function AccountNameCell({
  accountId,
  name,
  isDefault,
  disconnected,
}: {
  accountId: string;
  name: string | null;
  isDefault: boolean;
  disconnected: boolean;
}) {
  if (name) return <span className="font-medium text-slate-900">{name}</span>;

  // No name available. Never invent one — an account's history can span several
  // principals, so guessing a person from the data would mislabel real results.
  // Describe what is actually known instead, and keep the full id in the tooltip.
  return (
    <span title={accountId}>
      {isDefault ? (
        <span className="font-medium text-slate-700">Default account</span>
      ) : (
        <span className="font-mono text-xs text-slate-500">
          {accountId.slice(0, 10)}…
        </span>
      )}
      {/* Only asserted when the listing actually came back and lacked this id —
          never when the provider is simply unreachable. */}
      {disconnected && (
        <span className="ml-2 text-xs text-slate-400">not connected</span>
      )}
    </span>
  );
}

export default function Dashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["stats"], queryFn: getStats });
  const { data: principals } = useQuery({
    queryKey: ["principals"],
    queryFn: () => listPrincipals(),
  });
  // Names only, and the call that keeps them current: fetching the account list
  // is what teaches the server each account's own name, so simply loading this
  // page names the table — nothing is entered by hand. Shares its cache with the
  // LinkedIn pages' picker.
  //
  // It is also the one call here that reaches the provider, so it must never hold
  // the dashboard back: the figures come from /api/stats and rows fall back to
  // the account id if this fails. A short staleness window and one retry mean
  // names appear on their own soon after the provider recovers, rather than
  // waiting out a long cache.
  const { data: accounts } = useQuery({
    queryKey: ["linkedin-accounts"],
    queryFn: listLinkedInAccounts,
    retry: 1,
    staleTime: 60 * 1000,
    refetchOnWindowFocus: true,
  });

  if (isLoading || !data) return <Loading />;

  // The live listing first, so a renamed LinkedIn account shows its current name
  // immediately. The locally cached copy is the fallback that keeps rows named
  // while the provider is unreachable — which is the only reason a cache exists.
  const accountName = (id: string): string | null => {
    const live = accounts?.accounts?.find((a) => a.id === id)?.name?.trim();
    if (live) return live;
    return data.linkedin_account_names?.[id]?.trim() || null;
  };
  const byAccount = data.linkedin_by_account ?? [];
  // An id missing from a NON-empty listing is genuinely no longer connected. An
  // empty listing just means the provider did not answer, which proves nothing.
  const listing = accounts?.accounts ?? [];
  const isDisconnected = (id: string) =>
    listing.length > 0 && !listing.some((a) => a.id === id);
  // Only accounts that could still be named are "waiting". An account removed
  // from the provider never will be, so promising it would fill in is a promise
  // that never comes true — its row says "not connected", which is the whole
  // explanation it needs.
  const awaitingNames = byAccount.filter(
    (r) => !accountName(r.account_id) && !isDisconnected(r.account_id)
  ).length;

  const principal = principals?.items[0];
  const documentFocus = principal?.document_focus;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Your outreach at a glance: who you've found, approved, contacted, and heard back from."
      />

      <PipelineModeCard />

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
      <div className="mb-3 text-sm font-semibold text-slate-700">
        Outreach funnel — Email
      </div>
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

      {/*
        LinkedIn gets its own row rather than being folded into the funnel above.
        The two channels are not comparable stage for stage: LinkedIn sends no
        read receipts, so it has no Opened step, and email has no invitation
        step. Prospects/Approved are not repeated here either — they are
        channel-agnostic (the same people feed both), and duplicating them would
        imply a separate pipeline.
      */}
      <div className="mb-3 text-sm font-semibold text-slate-700">
        Outreach funnel — LinkedIn
      </div>
      <div className="mb-2 flex flex-col gap-2 md:flex-row md:items-stretch">
        <FunnelStep
          label="Drafts"
          value={data.linkedin_drafts}
          sub="awaiting approval"
          to="/linkedin"
        />
        <FunnelStep
          label="Invited"
          value={data.linkedin_invited}
          sub="connection requests sent"
          tone="amber"
          to="/linkedin"
        />
        <FunnelStep
          label="Sent"
          value={data.linkedin_sent}
          sub="messages delivered"
          tone="blue"
          to="/linkedin"
        />
        <FunnelStep
          label="Replied"
          value={data.linkedin_replied}
          sub={`${pct(data.linkedin_reply_rate)} reply rate`}
          tone="green"
          to="/linkedin-responses"
        />
      </div>
      <p className="mb-4 text-xs text-slate-400">
        Prospect outreach only. LinkedIn reports no opens, so there is no open rate
        on this side.
      </p>

      {/*
        The funnel above is every account combined. Averaged together, one
        account warming up or getting throttled is invisible, so the same
        activity is broken out per sender here.
      */}
      <Card className="mb-8 p-5">
        <div className="text-sm font-semibold text-slate-700">
          Performance by account
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Attributed to the account that sent each message. Drafts are not listed
          — a message has no sending account until it goes out.
        </p>
        {/*
          Not a failure — the figures are correct either way, only the labels are
          missing. Explains the ids without asking anything of the user, since
          names arrive on their own.
        */}
        {awaitingNames > 0 && (
          <p className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
            Names come from your connected LinkedIn accounts automatically, and
            fill in as soon as LinkedIn responds. Ids are shown until then.
          </p>
        )}

        {byAccount.length === 0 ? (
          <p className="mt-3 text-sm text-slate-400">
            Nothing sent from any account yet.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[46rem] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-400">
                  <th className="py-2 pr-4 text-left font-medium">Account</th>
                  <th className="px-3 py-2 text-right font-medium">Invited</th>
                  <th className="px-3 py-2 text-right font-medium">Accepted</th>
                  <th className="px-3 py-2 text-right font-medium">Acceptance</th>
                  <th className="px-3 py-2 text-right font-medium">Sent</th>
                  <th className="px-3 py-2 text-right font-medium">Replied</th>
                  <th className="px-3 py-2 text-right font-medium">Reply rate</th>
                  <th className="py-2 pl-3 text-right font-medium">Follower DMs</th>
                </tr>
              </thead>
              <tbody>
                {byAccount.map((row) => (
                  <tr
                    key={row.account_id}
                    className="border-b border-slate-100 last:border-0"
                  >
                    <td className="py-2.5 pr-4">
                      <AccountNameCell
                        accountId={row.account_id}
                        name={accountName(row.account_id)}
                        isDefault={row.account_id === accounts?.default_account_id}
                        disconnected={isDisconnected(row.account_id)}
                      />
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                      {row.invited}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                      {row.accepted}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {row.invited ? (
                        <span className="font-medium text-amber-700">
                          {pct(row.acceptance_rate)}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                      {row.sent}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                      {row.replied}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums">
                      {row.sent ? (
                        <span className="font-medium text-emerald-700">
                          {pct(row.reply_rate)}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="py-2.5 pl-3 text-right tabular-nums text-slate-700">
                      {row.follower_dms_sent}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Followers module — counted apart from the prospect funnel above. */}
      <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-5">
        <MiniStat
          label="Follower DMs"
          value={data.follower_dms_sent}
          to="/followers-linkedin"
        />
        <MiniStat
          label="Followers synced"
          value={data.followers_total}
          to="/followers-linkedin"
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

      <div className="grid gap-6 md:grid-cols-3">
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

        <Card className="p-5">
          <div className="mb-3 text-sm font-semibold text-slate-700">
            LinkedIn by status
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.linkedin_by_status ?? {}).length === 0 && (
              <span className="text-sm text-slate-400">
                No LinkedIn messages yet.
              </span>
            )}
            {Object.entries(data.linkedin_by_status ?? {}).map(([status, count]) => (
              <Badge
                key={status}
                tone={
                  status === "replied"
                    ? "green"
                    : status === "sent"
                    ? "blue"
                    : status === "invite_sent"
                    ? "amber"
                    : status === "failed"
                    ? "red"
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
