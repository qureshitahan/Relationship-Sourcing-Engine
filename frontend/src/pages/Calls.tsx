import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getCallConfig, listCalls, placeCall, setCallStatus } from "../api/client";
import type { Call } from "../types";
import WorkflowSteps from "../components/WorkflowSteps";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Loading,
  PageHeader,
  StatusBadge,
} from "../components/ui";

function CallCard({ call }: { call: Call }) {
  const qc = useQueryClient();

  const approve = useMutation({
    mutationFn: () => setCallStatus(call.id, "approved"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calls"] }),
  });
  const dial = useMutation({
    mutationFn: () => placeCall(call.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calls"] }),
  });
  const outcome = useMutation({
    mutationFn: (s: string) => setCallStatus(call.id, s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calls"] }),
  });

  const canPlace =
    call.status === "approved" && Boolean(call.phone_number) && !call.provider_call_id;

  return (
    <Card className="overflow-hidden">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 bg-slate-50/80 px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            {call.contact_id ? (
              <Link
                to={`/prospects/${call.contact_id}`}
                className="text-base font-semibold text-slate-900 hover:underline"
              >
                {call.contact_name ?? `Prospect #${call.contact_id}`}
              </Link>
            ) : (
              <span className="text-base font-semibold text-slate-900">Unknown prospect</span>
            )}
            <StatusBadge status={call.status} />
          </div>
          <div className="mt-0.5 text-sm text-slate-500">
            {call.contact_title && <span>{call.contact_title}</span>}
            {call.contact_title && call.company_name && <span> · </span>}
            {call.company_name && <span>{call.company_name}</span>}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            On behalf of {call.principal_name ?? `Principal #${call.principal_id}`}
          </div>
        </div>
      </div>

      <div className="space-y-0 px-5 py-2">
        <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 border-b border-slate-100 py-2.5">
          <span className="pt-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
            To
          </span>
          <div>
            {call.phone_number ? (
              <span className="text-sm text-slate-800">{call.phone_number}</span>
            ) : (
              <Badge tone="amber">Phone not revealed</Badge>
            )}
          </div>
        </div>

        {call.script && (
          <div className="py-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Script preview
            </div>
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">
              {call.script}
            </pre>
          </div>
        )}

        {call.transcript && (
          <div className="border-t border-slate-100 py-3">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Transcript
            </div>
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-emerald-50 p-3 text-sm leading-relaxed text-slate-700">
              {call.transcript}
            </pre>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/50 px-5 py-3">
        <span className="text-xs text-slate-400">
          {call.provider_call_id
            ? `Vapi call ${call.provider_call_id}`
            : "AI voice via Vapi + ElevenLabs + Claude"}
        </span>
        <div className="flex flex-wrap gap-2">
          {call.status === "queued" && (
            <Button
              onClick={() => approve.mutate()}
              disabled={!call.phone_number || approve.isPending}
              title={!call.phone_number ? "Reveal phone first" : undefined}
            >
              Approve call
            </Button>
          )}
          {canPlace && (
            <Button onClick={() => dial.mutate()} disabled={dial.isPending}>
              {dial.isPending ? "Dialing…" : "Place call now"}
            </Button>
          )}
          {(call.status === "completed" ||
            call.status === "dialing" ||
            call.status === "interested") && (
            <>
              <Button
                variant="secondary"
                onClick={() => outcome.mutate("interested")}
              >
                Mark interested
              </Button>
              <Button variant="ghost" onClick={() => outcome.mutate("not_interested")}>
                Not interested
              </Button>
            </>
          )}
        </div>
        {dial.isError && (
          <p className="w-full text-right text-xs text-rose-600">
            {(dial.error as Error)?.message || "Call failed"}
          </p>
        )}
      </div>
    </Card>
  );
}

export default function Calls() {
  const { data: config } = useQuery({
    queryKey: ["call-config"],
    queryFn: getCallConfig,
  });
  const { data, isLoading } = useQuery({
    queryKey: ["calls"],
    queryFn: () => listCalls({ limit: 100 }),
  });

  return (
    <div>
      <WorkflowSteps />

      <PageHeader
        title="Call Queue"
        subtitle="Review the script, approve, then place the call via Vapi. Claude speaks with an ElevenLabs voice and discloses it is AI."
      />

      {config && !config.vapi_configured && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Voice calling is not configured yet. Set <code>VOICE_PROVIDER=vapi</code>,{" "}
          <code>VAPI_API_KEY</code>, and <code>VAPI_PHONE_NUMBER_ID</code> in backend{" "}
          <code>.env</code>, and add Anthropic + ElevenLabs credentials in the Vapi dashboard.
        </div>
      )}
      {config && config.vapi_configured && !config.webhook_configured && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Set <code>APP_PUBLIC_URL</code> (e.g. ngrok) so Vapi can POST transcripts back after
          calls end.
        </div>
      )}

      {isLoading ? (
        <Loading />
      ) : !data || data.items.length === 0 ? (
        <Card>
          <EmptyState message="No calls queued. Open a prospect, generate an insight, then click Queue call script." />
        </Card>
      ) : (
        <div className="space-y-4">
          {data.items.map((c) => (
            <CallCard key={c.id} call={c} />
          ))}
        </div>
      )}
    </div>
  );
}
