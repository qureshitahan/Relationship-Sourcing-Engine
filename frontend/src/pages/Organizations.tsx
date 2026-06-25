import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { listOrganizations } from "../api/client";
import {
  Badge,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
  Table,
  Td,
  Th,
} from "../components/ui";

export default function Organizations() {
  const [searchParams, setSearchParams] = useSearchParams();
  const runId = searchParams.get("run")
    ? Number(searchParams.get("run"))
    : undefined;
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["organizations", search, runId],
    queryFn: () =>
      listOrganizations({
        search: search || undefined,
        discovery_run_id: runId,
        limit: 100,
      }),
  });

  const clearRunFilter = () => {
    searchParams.delete("run");
    setSearchParams(searchParams);
  };

  return (
    <div>
      <PageHeader
        title="Organizations"
        subtitle="Companies, funds, and firms discovered as relevant to your principals."
      />

      {runId && (
        <div className="mb-4 flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-800">
          <span>
            Showing organizations from discovery run{" "}
            <span className="font-semibold">#{runId}</span> only.
          </span>
          <button
            type="button"
            onClick={clearRunFilter}
            className="font-medium text-blue-700 underline hover:text-blue-900"
          >
            Show all organizations
          </button>
        </div>
      )}

      <Card className="mb-4 p-4">
        <input
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
          placeholder="Search organizations…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </Card>

      <Card>
        {isLoading ? (
          <Loading />
        ) : !data || data.items.length === 0 ? (
          <EmptyState message="No organizations yet. Run discovery to populate." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Industry</Th>
                <Th>Employees</Th>
                <Th>HQ</Th>
                <Th>Links</Th>
                <Th>Enrichment</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((o) => (
                <tr key={o.id} className="hover:bg-slate-50">
                  <Td>
                    <Link
                      to={`/organizations/${o.id}`}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {o.name}
                    </Link>
                    {o.domain && (
                      <div className="text-xs text-slate-400">{o.domain}</div>
                    )}
                  </Td>
                  <Td>
                    {o.company_type ? (
                      <Badge tone="purple">{o.company_type.replace(/_/g, " ")}</Badge>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td>{o.industry ?? "—"}</Td>
                  <Td>{o.employee_count ?? "—"}</Td>
                  <Td>{o.headquarters ?? "—"}</Td>
                  <Td>
                    <div className="flex gap-2">
                      {o.website && (
                        <a
                          href={o.website}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs font-medium text-blue-600 hover:underline"
                        >
                          Site ↗
                        </a>
                      )}
                      {o.linkedin_url && (
                        <a
                          href={o.linkedin_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs font-medium text-blue-600 hover:underline"
                        >
                          LinkedIn ↗
                        </a>
                      )}
                      {!o.website && !o.linkedin_url && (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </div>
                  </Td>
                  <Td>
                    <StatusBadge status={o.enrichment_status} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
