import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createBulkCampaign, listBulkCampaigns, listMailboxes } from "../api/client";
import type { BulkCampaign } from "../types";
import { Badge, Button, Card, EmptyState, Loading, PageHeader } from "../components/ui";

const STATUS_LABEL: Record<string, string> = {
  collecting: "Setting up",
  drafting: "Writing drafts",
  ready: "Ready to review",
  sending: "Sending",
  sent: "Sent",
};

const STATUS_TONE: Record<string, "slate" | "amber" | "blue" | "green" | "purple"> = {
  collecting: "slate",
  drafting: "amber",
  ready: "amber",
  sending: "purple",
  sent: "green",
};

export default function BulkEmails() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [mailboxId, setMailboxId] = useState("");
  const [error, setError] = useState("");

  const { data: mailboxes = [], isLoading: loadingMailboxes } = useQuery({
    queryKey: ["mailboxes"],
    queryFn: listMailboxes,
    staleTime: 5 * 60 * 1000,
  });
  const { data: campaigns, isLoading } = useQuery({
    queryKey: ["bulk-campaigns"],
    queryFn: listBulkCampaigns,
    refetchInterval: (q) => {
      const items = q.state.data as BulkCampaign[] | undefined;
      const busy = items?.some((c) => c.status === "drafting" || c.status === "sending");
      return busy ? 4000 : false;
    },
  });

  const create = useMutation({
    mutationFn: () => createBulkCampaign({ name: name.trim(), mailbox_id: mailboxId }),
    onSuccess: (campaign) => {
      qc.invalidateQueries({ queryKey: ["bulk-campaigns"] });
      navigate(`/bulk/${campaign.id}`);
    },
    onError: () => setError("Could not start the campaign. Check the name and mailbox."),
  });

  const start = () => {
    setError("");
    if (!name.trim()) {
      setError("Give this campaign a name so you can find it later.");
      return;
    }
    if (!mailboxId) {
      setError("Pick the mailbox these emails should come from.");
      return;
    }
    create.mutate();
  };

  return (
    <div>
      <PageHeader
        title="Send bulk emails"
        subtitle="Paste a list of people, tell the assistant what to say, then review and send from the mailbox you choose."
      />

      <Card className="p-5">
        <h2 className="text-sm font-semibold text-slate-900">New bulk campaign</h2>

        <div className="mt-4">
          <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-400">
            Campaign name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") start();
            }}
            placeholder="e.g. FTA conference follow-up"
            className="w-full max-w-md rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none"
          />
        </div>

        <div className="mt-5">
          <label className="mb-2 block text-xs font-semibold uppercase tracking-wide text-slate-400">
            Send from
          </label>
          {loadingMailboxes ? (
            <Loading />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {mailboxes.map((mb) => {
                const active = mb.id === mailboxId;
                return (
                  <button
                    key={mb.id}
                    type="button"
                    onClick={() => setMailboxId(mb.id)}
                    className={`flex items-start gap-3 rounded-xl border p-3 text-left transition ${
                      active
                        ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                        : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                    }`}
                  >
                    <span
                      className={`mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
                        active ? "bg-white/15 text-white" : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {initials(mb.from_name || mb.label || mb.from_email)}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">
                        {mb.from_name || mb.label}
                      </span>
                      <span
                        className={`block truncate text-xs ${
                          active ? "text-slate-300" : "text-slate-500"
                        }`}
                      >
                        {mb.from_email}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}

        <div className="mt-5">
          <Button onClick={start} disabled={create.isPending}>
            {create.isPending ? "Starting…" : "Start campaign"}
          </Button>
        </div>
      </Card>

      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-900">Your bulk campaigns</h2>
      {isLoading ? (
        <Loading />
      ) : !campaigns?.length ? (
        <Card>
          <EmptyState message="No bulk campaigns yet. Name one above and pick a mailbox to get started." />
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {campaigns.map((campaign) => (
            <CampaignCard key={campaign.id} campaign={campaign} />
          ))}
        </div>
      )}
    </div>
  );
}

function CampaignCard({ campaign }: { campaign: BulkCampaign }) {
  const pending = campaign.drafted + campaign.approved;
  return (
    <Link to={`/bulk/${campaign.id}`} className="block">
      <Card className="h-full p-4 transition hover:border-slate-300 hover:shadow">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-slate-900">{campaign.name}</h3>
          <Badge tone={STATUS_TONE[campaign.status] ?? "slate"}>
            {STATUS_LABEL[campaign.status] ?? campaign.status}
          </Badge>
        </div>
        <p className="mt-1 truncate text-xs text-slate-500">
          From {campaign.from_email ?? "default mailbox"}
        </p>
        {campaign.purpose && (
          <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-slate-500">
            {campaign.purpose}
          </p>
        )}
        <div className="mt-4 grid grid-cols-4 gap-2 border-t border-slate-100 pt-3 text-center">
          <Stat label="People" value={campaign.recipients} />
          <Stat label="To review" value={pending} />
          <Stat label="Sent" value={campaign.sent} />
          <Stat label="Replied" value={campaign.replied} />
        </div>
      </Card>
    </Link>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-base font-semibold tabular-nums text-slate-900">{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
    </div>
  );
}

function initials(value: string): string {
  const parts = value.trim().split(/[\s.@]+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?").toUpperCase() + (parts[1]?.[0] ?? "").toUpperCase();
}
