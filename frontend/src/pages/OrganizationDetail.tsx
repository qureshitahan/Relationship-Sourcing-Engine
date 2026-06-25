import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  enrichOrganization,
  getOrganization,
  getOrganizationInsights,
  listProspects,
} from "../api/client";
import InsightCard from "../components/InsightCard";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  ScoreBar,
  Table,
  Td,
  Th,
} from "../components/ui";

function Row({ label, value }: { label: string; value?: string | number | null }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-700">{value}</span>
    </div>
  );
}

export default function OrganizationDetail() {
  const { id } = useParams();
  const orgId = Number(id);
  const qc = useQueryClient();
  const [maxContacts, setMaxContacts] = useState(5);

  const { data: org, isLoading } = useQuery({
    queryKey: ["organization", orgId],
    queryFn: () => getOrganization(orgId),
  });
  const { data: prospects } = useQuery({
    queryKey: ["prospects", { company_id: orgId }],
    queryFn: () => listProspects({ company_id: orgId, limit: 100 }),
  });
  const { data: insights } = useQuery({
    queryKey: ["organization-insights", orgId],
    queryFn: () => getOrganizationInsights(orgId),
  });

  const enrich = useMutation({
    mutationFn: () => enrichOrganization(orgId, maxContacts),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prospects", { company_id: orgId }] });
      qc.invalidateQueries({ queryKey: ["organization", orgId] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  if (isLoading || !org) return <Loading />;

  return (
    <div>
      <PageHeader
        title={org.name}
        subtitle={org.industry ?? undefined}
        actions={
          <div className="flex items-center gap-2">
            <input
              type="number"
              className="w-20 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              value={maxContacts}
              min={1}
              max={25}
              onChange={(e) => setMaxContacts(Number(e.target.value))}
            />
            <Button onClick={() => enrich.mutate()} disabled={enrich.isPending}>
              {enrich.isPending ? "Finding…" : "Find prospects"}
            </Button>
          </div>
        }
      />

      <div className="grid gap-6 md:grid-cols-3">
        <div className="space-y-6 md:col-span-2">
          <Card>
            <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
              Prospects at this organization
            </div>
            {!prospects || prospects.items.length === 0 ? (
              <EmptyState message="No prospects yet. Use 'Find prospects' to discover people here." />
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Name</Th>
                    <Th>Title</Th>
                    <Th>Relevance</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {prospects.items.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-50">
                      <Td>
                        <Link
                          to={`/prospects/${p.id}`}
                          className="font-medium text-slate-900 hover:underline"
                        >
                          {p.name}
                        </Link>
                      </Td>
                      <Td>{p.title ?? "—"}</Td>
                      <Td>
                        <ScoreBar value={p.relevance_score} />
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          {insights && insights.length > 0 && (
            <div className="space-y-4">
              {insights.map((ins) => (
                <InsightCard key={ins.id} insight={ins} />
              ))}
            </div>
          )}
        </div>

        <div className="space-y-6">
          <Card className="p-5">
            <div className="mb-2 text-sm font-semibold text-slate-700">Firmographics</div>
            {org.company_type && (
              <div className="mb-2">
                <Badge tone="purple">{org.company_type.replace(/_/g, " ")}</Badge>
              </div>
            )}
            <Row label="Employees" value={org.employee_count ?? undefined} />
            <Row label="HQ" value={org.headquarters} />
            <Row label="Domain" value={org.domain} />
            <Row label="Funding" value={org.funding} />
            <Row label="Revenue" value={org.revenue} />
            <Row label="Phone" value={org.phone} />
            {org.website && (
              <a
                href={org.website}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-sm text-blue-600 hover:underline"
              >
                Website ↗
              </a>
            )}
            {org.sectors && org.sectors.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {org.sectors.map((s) => (
                  <Badge key={s} tone="blue">
                    {s}
                  </Badge>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
