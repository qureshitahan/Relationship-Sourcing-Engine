import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  indexPrincipalDocument,
  listPrincipalDocuments,
  uploadPrincipalDocuments,
} from "../api/client";
import type { IndexFileResult } from "../types";
import { Button } from "./ui";

const ACCEPT = ".pdf,.docx,.txt,.md";

type FileStep = {
  name: string;
  phase: "pending" | "uploading" | "indexing" | "done" | "failed" | "rejected";
  detail?: string;
  result?: IndexFileResult;
};

function stepIcon(step: FileStep): string {
  switch (step.phase) {
    case "done":
      return "✓";
    case "failed":
    case "rejected":
      return "✗";
    case "uploading":
    case "indexing":
      return "…";
    default:
      return "○";
  }
}

function resultDetail(step: FileStep): string {
  if (step.phase === "rejected") return step.detail ?? "Unsupported file type";
  if (step.phase === "failed") return step.detail ?? "Failed";
  if (step.result?.action === "skipped") return "Already indexed (unchanged)";
  if (step.result?.status === "irrelevant")
    return `Not used for outreach (${Math.round(step.result.relevance_score ?? 0)}% fit)`;
  if (step.result?.status === "peripheral")
    return `Partial fit (${Math.round(step.result.relevance_score ?? 0)}%) · ${step.result.proof_points ?? 0} proof points`;
  if (step.result?.action === "indexed" || step.result?.status === "indexed")
    return `Core document (${Math.round(step.result.relevance_score ?? 0)}% fit) · ${step.result.proof_points ?? 0} proof points`;
  return step.detail ?? "Indexed";
}

export default function PrincipalDocuments({
  principalId,
  onIndexed,
}: {
  principalId: number;
  onIndexed?: () => void;
}) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [progress, setProgress] = useState<{
    active: boolean;
    phase: "upload" | "index";
    current: number;
    total: number;
    label: string;
    steps: FileStep[];
  } | null>(null);

  const { data: docs } = useQuery({
    queryKey: ["principal-documents", principalId],
    queryFn: () => listPrincipalDocuments(principalId),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["principal-documents", principalId] });
    qc.invalidateQueries({ queryKey: ["principal-dossier", principalId] });
    qc.invalidateQueries({ queryKey: ["stats"] });
    onIndexed?.();
  };

  const [processing, setProcessing] = useState(false);

  const validateFiles = (files: File[]): { ok: File[]; rejected: FileStep[] } => {
    const ok: File[] = [];
    const rejected: FileStep[] = [];
    for (const f of files) {
      const ext = f.name.includes(".")
        ? f.name.slice(f.name.lastIndexOf(".")).toLowerCase()
        : "";
      if (ext === ".doc") {
        rejected.push({
          name: f.name,
          phase: "rejected",
          detail: "Legacy .doc — open in Word and Save As .docx",
        });
      } else if (!ACCEPT.split(",").includes(ext)) {
        rejected.push({
          name: f.name,
          phase: "rejected",
          detail: `Unsupported type (${ext || "unknown"})`,
        });
      } else {
        ok.push(f);
      }
    }
    return { ok, rejected };
  };

  const runUploadAndIndex = async (files: File[]) => {
    const { ok, rejected } = validateFiles(files);
    if (ok.length === 0 && rejected.length === 0) return;

    const steps: FileStep[] = [
      ...rejected,
      ...ok.map((f) => ({ name: f.name, phase: "pending" as const })),
    ];
    const total = steps.length;
    setNote(null);
    setProcessing(true);
    setProgress({
      active: true,
      phase: "upload",
      current: rejected.length,
      total,
      label: rejected.length ? "Checking files…" : "Uploading…",
      steps,
    });

    try {
      let toIndex: string[] = [];

      if (ok.length > 0) {
        setProgress((p) =>
          p ? { ...p, label: `Uploading ${ok.length} file(s)…` } : p
        );
        setProgress((p) => {
          if (!p) return p;
          return {
            ...p,
            steps: p.steps.map((s) =>
              ok.some((f) => f.name === s.name)
                ? { ...s, phase: "uploading" }
                : s
            ),
          };
        });

        const uploadResult = await uploadPrincipalDocuments(principalId, ok);
        toIndex = uploadResult.uploaded ?? [];

        for (const r of uploadResult.rejected ?? []) {
          setProgress((p) => {
            if (!p) return p;
            return {
              ...p,
              steps: p.steps.map((s) =>
                s.name === r.file
                  ? { ...s, phase: "rejected", detail: r.reason }
                  : s
              ),
            };
          });
        }
      }

      let done = rejected.length;
      for (let i = 0; i < toIndex.length; i++) {
        const filename = toIndex[i];
        setProgress((p) =>
          p
            ? {
                ...p,
                phase: "index",
                current: done,
                label: `Indexing ${i + 1}/${toIndex.length}: ${filename}`,
                steps: p.steps.map((s) =>
                  s.name === filename ? { ...s, phase: "indexing" } : s
                ),
              }
            : p
        );

        try {
          const result = await indexPrincipalDocument(principalId, filename);
          done += 1;
          setProgress((p) =>
            p
              ? {
                  ...p,
                  current: done,
                  steps: p.steps.map((s) =>
                    s.name === filename
                      ? { ...s, phase: "done", result }
                      : s
                  ),
                }
              : p
          );
        } catch (err: unknown) {
          done += 1;
          const msg =
            err && typeof err === "object" && "response" in err
              ? String(
                  (err as { response?: { data?: { detail?: string } } }).response
                    ?.data?.detail ?? "Indexing failed"
                )
              : "Indexing failed";
          setProgress((p) =>
            p
              ? {
                  ...p,
                  current: done,
                  steps: p.steps.map((s) =>
                    s.name === filename
                      ? { ...s, phase: "failed", detail: msg }
                      : s
                  ),
                }
              : p
          );
        }
        refresh();
      }

      const finalSteps = steps; // stale closure avoided via setProgress reads above
      setProgress((p) => {
        const succeeded = (p?.steps ?? finalSteps).filter(
          (s) => s.phase === "done"
        ).length;
        const failed = (p?.steps ?? finalSteps).filter(
          (s) => s.phase === "failed" || s.phase === "rejected"
        ).length;
        setNote(
          failed
            ? `Finished: ${succeeded} indexed, ${failed} skipped/failed.`
            : `Finished: ${succeeded} document(s) indexed.`
        );
        return p
          ? { ...p, active: false, current: total, label: "Complete" }
          : p;
      });
    } catch {
      setNote("Upload failed. Check your connection and try again.");
      setProgress(null);
    } finally {
      setProcessing(false);
      refresh();
    }
  };

  const reindexAll = async () => {
    if (!docs?.length) return;
    setProcessing(true);
    setNote(null);
    const filenames = docs.map((d) => d.filename);
    const steps: FileStep[] = filenames.map((name) => ({
      name,
      phase: "pending",
    }));
    setProgress({
      active: true,
      phase: "index",
      current: 0,
      total: filenames.length,
      label: "Re-indexing…",
      steps,
    });

    let done = 0;
    for (let i = 0; i < filenames.length; i++) {
      const filename = filenames[i];
      setProgress((p) =>
        p
          ? {
              ...p,
              label: `Re-indexing ${i + 1}/${filenames.length}: ${filename}`,
              steps: p.steps.map((s) =>
                s.name === filename ? { ...s, phase: "indexing" } : s
              ),
            }
          : p
      );
      try {
        const result = await indexPrincipalDocument(principalId, filename, true);
        done += 1;
        setProgress((p) =>
          p
            ? {
                ...p,
                current: done,
                steps: p.steps.map((s) =>
                  s.name === filename ? { ...s, phase: "done", result } : s
                ),
              }
            : p
        );
      } catch (err: unknown) {
        done += 1;
        const msg =
          err && typeof err === "object" && "response" in err
            ? String(
                (err as { response?: { data?: { detail?: string } } }).response
                  ?.data?.detail ?? "Failed"
              )
            : "Failed";
        setProgress((p) =>
          p
            ? {
                ...p,
                current: done,
                steps: p.steps.map((s) =>
                  s.name === filename
                    ? { ...s, phase: "failed", detail: msg }
                    : s
                ),
              }
            : p
        );
      }
      refresh();
    }
    setNote(`Re-indexed ${done} document(s).`);
    setProgress((p) => (p ? { ...p, active: false, label: "Complete" } : p));
    setProcessing(false);
  };

  const onFiles = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0 || processing) return;
    void runUploadAndIndex(Array.from(fileList));
    if (inputRef.current) inputRef.current.value = "";
  };

  const pct =
    progress && progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : 0;

  return (
    <div className="mt-4 border-t border-slate-100 pt-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Context documents
          {docs && docs.length > 0 && (
            <span className="ml-2 font-normal normal-case text-slate-400">
              ({docs.length} indexed)
            </span>
          )}
        </span>
        {docs && docs.length > 0 && (
          <Button variant="ghost" onClick={() => void reindexAll()} disabled={processing}>
            Re-index all
          </Button>
        )}
      </div>

      <div
        onClick={() => !processing && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (!processing) onFiles(e.dataTransfer.files);
        }}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm transition ${
          dragOver
            ? "border-slate-500 bg-slate-50"
            : "border-slate-300 hover:border-slate-400"
        } ${processing ? "pointer-events-none opacity-60" : ""}`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => onFiles(e.target.files)}
        />
        <span className="text-slate-500">
          Drop resume, deal sheets, bios here, or{" "}
          <span className="font-medium text-slate-700">browse</span>
          <br />
          <span className="text-xs text-slate-400">
            PDF, DOCX (Word), TXT, MD · off-topic files are flagged automatically
          </span>
        </span>
      </div>

      {progress && (
        <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="font-medium text-slate-700">{progress.label}</span>
            <span className="text-slate-500">
              {progress.current}/{progress.total} ({pct}%)
            </span>
          </div>
          <div className="mb-3 h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className="h-full rounded-full bg-slate-800 transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          <ul className="max-h-48 space-y-1 overflow-y-auto text-xs">
            {progress.steps.map((step) => (
              <li
                key={step.name}
                className={`flex items-start gap-2 ${
                  step.phase === "indexing" || step.phase === "uploading"
                    ? "font-medium text-slate-800"
                    : "text-slate-600"
                }`}
              >
                <span
                  className={`mt-0.5 w-3 shrink-0 ${
                    step.phase === "done"
                      ? "text-emerald-600"
                      : step.phase === "failed" || step.phase === "rejected"
                        ? "text-rose-500"
                        : step.phase === "indexing" || step.phase === "uploading"
                          ? "text-slate-800"
                          : "text-slate-300"
                  }`}
                >
                  {stepIcon(step)}
                </span>
                <span className="min-w-0 flex-1 truncate" title={step.name}>
                  {step.name}
                </span>
                {(step.phase === "done" ||
                  step.phase === "failed" ||
                  step.phase === "rejected") && (
                  <span className="shrink-0 text-slate-400">{resultDetail(step)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {note && !progress?.active && (
        <div className="mt-2 text-xs text-slate-500">{note}</div>
      )}
    </div>
  );
}
