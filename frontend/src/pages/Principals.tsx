import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPrincipal,
  deletePrincipal,
  deletePrincipalDocument,
  getPrincipalDossier,
  listMailboxes,
  listPrincipals,
  updatePrincipal,
  type PrincipalPayload,
} from "../api/client";
import PrincipalDocuments from "../components/PrincipalDocuments";
import { Badge, Button, Card, EmptyState, Loading, PageHeader } from "../components/ui";
import type { Principal } from "../types";

interface FormState {
  name: string;
  linkedin_url: string;
  phone: string;
  document_focus: string;
  email_signature: string;
  outreach_mailbox_id: string;
}

const EMPTY: FormState = {
  name: "",
  linkedin_url: "",
  phone: "",
  document_focus: "",
  email_signature: "",
  outreach_mailbox_id: "",
};

function fromPrincipal(p: Principal): FormState {
  return {
    name: p.name,
    linkedin_url: p.linkedin_url ?? "",
    phone: p.phone ?? "",
    document_focus: p.document_focus ?? "",
    email_signature: p.email_signature ?? "",
    outreach_mailbox_id: p.outreach_mailbox_id ?? "",
  };
}

function toPayload(f: FormState): PrincipalPayload {
  return {
    name: f.name,
    linkedin_url: f.linkedin_url,
    phone: f.phone,
    document_focus: f.document_focus || undefined,
    email_signature: f.email_signature.trim() || null,
    outreach_mailbox_id: f.outreach_mailbox_id || null,
  };
}

function principalFormValid(f: FormState): boolean {
  const name = f.name.trim();
  return (
    name.split(/\s+/).length >= 2 &&
    f.linkedin_url.trim().includes("linkedin.com") &&
    f.phone.trim().length > 0
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

const CLOSER_RE =
  /^(thanks|thank you|best|best regards|regards|cheers|sincerely|all the best|warmly)\b[,!. ]*$/i;
const PHONE_RE = /^\+?[\d\s().-]{7,}$/;
const LABEL_RE =
  /^(websites?|links?|phone|mobile|tel|linkedin|email|e-mail|calendar|book a call|schedule)\s*:?\s*$/i;
const URL_RE = /https?:\/\/[^\s]+/gi;

interface ParsedSignature {
  closer: string;
  name: string;
  details: string[];
  phone: string;
  linkedin: string;
  websites: string[];
  calendly: string;
}

function displayHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function parseSignature(raw: string): ParsedSignature {
  const parts: ParsedSignature = {
    closer: "Thanks,",
    name: "",
    details: [],
    phone: "",
    linkedin: "",
    websites: [],
    calendly: "",
  };
  const lines = raw
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (!lines.length) return parts;

  let start = 0;
  if (CLOSER_RE.test(lines[0])) {
    let closer = lines[0].replace(/\.$/, "");
    if (!closer.endsWith(",")) closer = `${closer.replace(/[,!]+$/, "")},`;
    parts.closer = closer;
    start = 1;
  }

  for (const line of lines.slice(start)) {
    if (LABEL_RE.test(line)) continue;
    const urls = line.match(URL_RE) ?? [];
    if (urls.length) {
      for (const rawUrl of urls) {
        const url = rawUrl.replace(/[.,);]+$/, "");
        const low = url.toLowerCase();
        if (low.includes("linkedin.com") && !parts.linkedin) parts.linkedin = url;
        else if (low.includes("calendly.com") && !parts.calendly) parts.calendly = url;
        else parts.websites.push(url);
      }
      continue;
    }
    if (PHONE_RE.test(line) && (line.match(/\d/g) ?? []).length >= 7) {
      if (!parts.phone) parts.phone = line;
      continue;
    }
    if (!parts.name) parts.name = line;
    else parts.details.push(line);
  }
  return parts;
}

/** How the signature will look in the emails recipients open. */
function SignaturePreview({ text }: { text: string }) {
  const trimmed = text.trim();
  if (!trimmed) {
    return (
      <p className="mt-1.5 text-sm text-slate-500">
        Using the short default (Thanks / name / LinkedIn). Recipients will see a
        clean styled block — paste a fuller signature above to customize it.
      </p>
    );
  }
  const p = parseSignature(trimmed);
  return (
    <div className="mt-1.5 rounded-xl bg-white px-4 py-4 ring-1 ring-slate-200">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        How recipients see it
      </p>
      <div className="mt-3 border-t border-slate-200 pt-3 text-sm leading-relaxed text-slate-600">
        <p className="mb-2.5 text-slate-500">{p.closer}</p>
        {p.name && (
          <p className="text-[15px] font-bold text-slate-900">{p.name}</p>
        )}
        {p.details.map((d) => (
          <p key={d} className="mt-0.5 text-slate-600">
            {d}
          </p>
        ))}
        {(p.linkedin || p.phone) && (
          <p className="mt-2.5 text-[13px]">
            {p.linkedin && (
              <a
                href={p.linkedin}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 hover:underline"
              >
                LinkedIn
              </a>
            )}
            {p.linkedin && p.phone && (
              <span className="px-2 text-slate-300">·</span>
            )}
            {p.phone && <span className="text-slate-700">{p.phone}</span>}
          </p>
        )}
        {p.websites.length > 0 && (
          <p className="mt-1.5 text-[13px]">
            {p.websites.map((url, i) => (
              <span key={url}>
                {i > 0 && <span className="px-2 text-slate-300">·</span>}
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  {displayHost(url)}
                </a>
              </span>
            ))}
          </p>
        )}
        {p.calendly && (
          <a
            href={p.calendly}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-block rounded-md bg-slate-900 px-3.5 py-2 text-[13px] font-semibold text-white hover:bg-slate-800"
          >
            Book a 30-min call
          </a>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
  multiline,
  rows = 2,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
  rows?: number;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
      {multiline ? (
        <textarea
          className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm leading-relaxed focus:border-slate-500 focus:outline-none"
          rows={rows}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}

/** Which mailbox this principal's outreach is sent FROM. */
function MailboxPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { data: mailboxes = [], isLoading } = useQuery({
    queryKey: ["mailboxes"],
    queryFn: listMailboxes,
    staleTime: 5 * 60 * 1000,
  });
  const selected = mailboxes.find((m) => m.id === value);
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">Send outreach from</span>
      <p className="mt-0.5 text-xs text-slate-400">
        Every campaign email for this principal is sent from this mailbox. Required
        when more than one mailbox is configured — we refuse to send as someone else.
      </p>
      <select
        className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {isLoading ? "Loading mailboxes…" : "— Select a mailbox —"}
        </option>
        {mailboxes.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label} · {m.from_email}
          </option>
        ))}
      </select>
      {value && selected && (
        <p className="mt-1.5 text-xs text-slate-500">
          Sends as <span className="font-medium text-slate-700">{selected.from_email}</span>
        </p>
      )}
      {!value && mailboxes.length > 1 && (
        <p className="mt-1.5 text-xs text-amber-700">
          No mailbox set — this principal's campaigns cannot send until you pick one.
        </p>
      )}
    </label>
  );
}

/** Read-only view of the mailbox a principal sends from. */
function MailboxSummary({ mailboxId }: { mailboxId: string }) {
  const { data: mailboxes = [] } = useQuery({
    queryKey: ["mailboxes"],
    queryFn: listMailboxes,
    staleTime: 5 * 60 * 1000,
  });
  const selected = mailboxes.find((m) => m.id === mailboxId);
  if (!mailboxId || !selected) {
    return (
      <p className="mt-1.5 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 ring-1 ring-amber-200">
        {mailboxes.length > 1
          ? "Not set — campaigns for this principal will not send. Click Edit and choose their mailbox."
          : "Using the only configured mailbox."}
      </p>
    );
  }
  return (
    <p className="mt-1.5 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200">
      <span className="font-semibold">{selected.from_email}</span>
      <span className="text-slate-400"> · {selected.label}</span>
    </p>
  );
}

function statusTone(s: string): "green" | "amber" | "red" | "slate" {
  if (s === "indexed") return "green";
  if (s === "peripheral") return "amber";
  if (s === "irrelevant") return "red";
  return "slate";
}

function PrincipalListItem({
  principal,
  selected,
  onSelect,
}: {
  principal: Principal;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition ${
        selected
          ? "bg-slate-900 text-white"
          : "text-slate-700 hover:bg-slate-100"
      }`}
    >
      <span
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
          selected ? "bg-white/20 text-white" : "bg-slate-200 text-slate-600"
        }`}
      >
        {initials(principal.name)}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{principal.name}</span>
        {principal.document_focus ? (
          <span
            className={`mt-0.5 block truncate text-xs ${
              selected ? "text-slate-300" : "text-slate-400"
            }`}
          >
            Focus: {principal.document_focus}
          </span>
        ) : (
          <span
            className={`mt-0.5 block text-xs ${
              selected ? "text-slate-400" : "text-slate-400"
            }`}
          >
            All relevant content
          </span>
        )}
      </span>
    </button>
  );
}

function PrincipalProfile({
  principal,
  onRemoved,
}: {
  principal: Principal;
  onRemoved: () => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [expandedDoc, setExpandedDoc] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(() => fromPrincipal(principal));

  useEffect(() => {
    setForm(fromPrincipal(principal));
    setEditing(false);
    setExpandedDoc(null);
  }, [principal.id]);

  const { data: dossier, isLoading: dossierLoading } = useQuery({
    queryKey: ["principal-dossier", principal.id],
    queryFn: () => getPrincipalDossier(principal.id),
  });

  const save = useMutation({
    mutationFn: () => updatePrincipal(principal.id, toPayload(form)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["principals"] });
      setEditing(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => deletePrincipal(principal.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["principals"] });
      onRemoved();
    },
  });

  const removeDoc = useMutation({
    mutationFn: (documentId: number) =>
      deletePrincipalDocument(principal.id, documentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["principal-dossier", principal.id] });
      qc.invalidateQueries({ queryKey: ["principal-documents", principal.id] });
    },
  });

  const set = (k: keyof FormState) => (v: string) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            {editing ? (
              <div className="space-y-4">
                <Field
                  label="Full name"
                  hint="First and last name — used in email signatures."
                  value={form.name}
                  onChange={set("name")}
                  placeholder="e.g. Taha Qureshi"
                />
                <Field
                  label="LinkedIn URL"
                  hint="Required — used on the profile and as a fallback in the default signature."
                  value={form.linkedin_url}
                  onChange={set("linkedin_url")}
                  placeholder="https://www.linkedin.com/in/…"
                />
                <Field
                  label="Phone number"
                  hint="Required — used for the profile and scheduling."
                  value={form.phone}
                  onChange={set("phone")}
                  placeholder="e.g. +1 555 123 4567"
                />
                <MailboxPicker
                  value={form.outreach_mailbox_id}
                  onChange={set("outreach_mailbox_id")}
                />
                <Field
                  label="Email signature"
                  hint="Paste freely — we format it for drafts and style it as HTML when the email is sent (clickable LinkedIn/websites, Book a call button)."
                  value={form.email_signature}
                  onChange={set("email_signature")}
                  placeholder={`Thanks,\nTaha Qureshi\nLead AI Architect\nhttps://www.linkedin.com/in/qureshitaha/\n+1 857 832 0365\nTekhqs AI (Subsidiary of Tekhqs INC)\nWebsites:\nhttps://tekhqs.com/\nhttps://tekhqs.ai/\nhttps://calendly.com/…`}
                  multiline
                  rows={9}
                />
                <SignaturePreview text={form.email_signature} />
                <Field
                  label="Document focus"
                  hint="Optional — emphasize a niche from their files (e.g. AI Engineering). Leave blank for everything relevant."
                  value={form.document_focus}
                  onChange={set("document_focus")}
                  placeholder="e.g. AI Engineering"
                  multiline
                />
                <div className="flex gap-2">
                  <Button
                    onClick={() => save.mutate()}
                    disabled={!principalFormValid(form) || save.isPending}
                  >
                    {save.isPending ? "Saving…" : "Save"}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setForm(fromPrincipal(principal));
                      setEditing(false);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3">
                  <span className="flex h-11 w-11 items-center justify-center rounded-full bg-slate-900 text-sm font-semibold text-white">
                    {initials(principal.name)}
                  </span>
                  <div>
                    <h2 className="text-lg font-bold text-slate-900">{principal.name}</h2>
                    {principal.linkedin_url && (
                      <a
                        href={principal.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm text-blue-600 hover:underline"
                      >
                        LinkedIn profile
                      </a>
                    )}
                    {principal.phone && (
                      <p className="text-sm text-slate-500">{principal.phone}</p>
                    )}
                    {(!principal.linkedin_url || !principal.phone) && (
                      <p className="text-sm text-amber-700">
                        Add LinkedIn URL and phone before drafting emails.
                      </p>
                    )}
                  </div>
                </div>
                {principal.document_focus ? (
                  <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200">
                    <span className="font-semibold">Document focus:</span>{" "}
                    {principal.document_focus}
                  </p>
                ) : (
                  <p className="mt-4 text-sm text-slate-500">
                    No focus set — indexing uses all relevant information from uploaded
                    files.
                  </p>
                )}
                <div className="mt-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Sends outreach from
                  </p>
                  <MailboxSummary mailboxId={principal.outreach_mailbox_id ?? ""} />
                </div>
                <div className="mt-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Email signature
                  </p>
                  <SignaturePreview text={principal.email_signature ?? ""} />
                </div>
              </>
            )}
          </div>
          {!editing && (
            <div className="flex shrink-0 gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  setForm(fromPrincipal(principal));
                  setEditing(true);
                }}
              >
                Edit
              </Button>
              {!confirmRemove ? (
                <Button variant="danger" onClick={() => setConfirmRemove(true)}>
                  Remove
                </Button>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-rose-700">Remove {principal.name}?</span>
                  <Button
                    variant="danger"
                    onClick={() => remove.mutate()}
                    disabled={remove.isPending}
                  >
                    {remove.isPending ? "Removing…" : "Confirm"}
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmRemove(false)}>
                    Cancel
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </Card>

      {dossierLoading ? (
        <Loading />
      ) : dossier && dossier.documents_total > 0 ? (
        <>
          <Card className="p-5">
            <h3 className="text-sm font-semibold text-slate-800">Indexed content</h3>
            <p className="mt-1 text-xs text-slate-500">
              Proof points and keywords extracted from uploaded documents.
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                {
                  label: "Documents",
                  value: dossier.documents_usable,
                  sub: `of ${dossier.documents_total}`,
                },
                {
                  label: "Proof points",
                  value: dossier.proof_points_total,
                  sub: `${dossier.proof_points_unique} unique`,
                },
                { label: "Keywords", value: dossier.themes.length, sub: "tags" },
                {
                  label: "Ready",
                  value: dossier.documents.filter((d) => d.status === "indexed").length,
                  sub: "core docs",
                },
              ].map((stat) => (
                <div
                  key={stat.label}
                  className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-center"
                >
                  <div className="text-2xl font-bold text-slate-900">{stat.value}</div>
                  <div className="text-xs font-medium text-slate-600">{stat.label}</div>
                  <div className="text-[10px] text-slate-400">{stat.sub}</div>
                </div>
              ))}
            </div>

            {dossier.top_proof_points.length > 0 && (
              <div className="mt-5">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Top proof points
                </div>
                <ul className="mt-2 space-y-1.5">
                  {dossier.top_proof_points.slice(0, 8).map((pt) => (
                    <li
                      key={pt}
                      className="text-sm text-slate-700 before:mr-2 before:font-bold before:text-emerald-600 before:content-['•']"
                    >
                      {pt}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {dossier.themes.length > 0 && (
              <div className="mt-5">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Keywords
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {dossier.themes.map((t) => (
                    <Badge key={t} tone="blue">
                      {t}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card className="p-5">
            <h3 className="text-sm font-semibold text-slate-800">Documents</h3>
            <ul className="mt-3 space-y-2">
              {dossier.documents.map((doc) => {
                const open = expandedDoc === doc.id;
                const used = doc.status === "indexed" || doc.status === "peripheral";
                return (
                  <li
                    key={doc.id}
                    className="overflow-hidden rounded-lg border border-slate-200"
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedDoc(open ? null : doc.id)}
                      className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-medium text-slate-800">
                            {doc.filename}
                          </span>
                          {doc.doc_type && <Badge tone="slate">{doc.doc_type}</Badge>}
                          <Badge tone={statusTone(doc.status)}>
                            {doc.status === "indexed"
                              ? "core"
                              : doc.status === "peripheral"
                                ? "partial"
                                : "not used"}
                          </Badge>
                        </div>
                        {doc.summary && !open && (
                          <p className="mt-1 line-clamp-1 text-xs text-slate-500">
                            {doc.summary}
                          </p>
                        )}
                      </div>
                      <span className="shrink-0 text-xs text-slate-400">
                        {used ? `${doc.key_facts.length} pts` : "excluded"}
                        {open ? " ▲" : " ▼"}
                      </span>
                      <button
                        type="button"
                        title="Delete document"
                        className="shrink-0 rounded px-1.5 py-0.5 text-xs text-rose-600 hover:bg-rose-50"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (
                            window.confirm(
                              `Delete "${doc.filename}"? This removes the file and its indexed content.`
                            )
                          ) {
                            removeDoc.mutate(doc.id);
                          }
                        }}
                      >
                        Delete
                      </button>
                    </button>
                    {open && (
                      <div className="border-t border-slate-100 bg-slate-50/50 px-4 py-3">
                        {doc.summary && (
                          <p className="text-sm text-slate-600">{doc.summary}</p>
                        )}
                        {doc.key_facts.length > 0 && (
                          <ul className="mt-2 space-y-1">
                            {doc.key_facts.map((f) => (
                              <li
                                key={f}
                                className="text-sm text-slate-700 before:mr-2 before:text-emerald-600 before:content-['•']"
                              >
                                {f}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </Card>
        </>
      ) : (
        <Card className="p-5">
          <EmptyState message="No documents indexed yet. Upload files below." />
        </Card>
      )}

      <Card className="p-5">
        <h3 className="mb-1 text-sm font-semibold text-slate-800">Upload documents</h3>
        <p className="mb-4 text-xs text-slate-500">
          Résumés, bios, case studies, articles — proof points are extracted for outreach.
        </p>
        <PrincipalDocuments
          principalId={principal.id}
          onIndexed={() => {
            qc.invalidateQueries({ queryKey: ["principal-dossier", principal.id] });
          }}
        />
      </Card>
    </div>
  );
}

function NewPrincipalForm({
  onCreated,
  onCancel,
}: {
  onCreated: (id: number) => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);

  const create = useMutation({
    mutationFn: () => createPrincipal(toPayload(form)),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["principals"] });
      onCreated(p.id);
    },
  });

  return (
    <Card className="p-5">
      <h2 className="text-lg font-semibold text-slate-900">New principal</h2>
      <p className="mt-1 text-sm text-slate-500">
        Full name, LinkedIn, and phone are required. Set an email signature now or later —
        campaigns will use whatever is saved on this principal.
      </p>
      <div className="mt-5 space-y-4">
        <Field
          label="Full name"
          hint="First and last name."
          value={form.name}
          onChange={(v) => setForm((f) => ({ ...f, name: v }))}
          placeholder="e.g. Taha Qureshi"
        />
        <Field
          label="LinkedIn URL"
          value={form.linkedin_url}
          onChange={(v) => setForm((f) => ({ ...f, linkedin_url: v }))}
          placeholder="https://www.linkedin.com/in/…"
        />
        <Field
          label="Phone number"
          value={form.phone}
          onChange={(v) => setForm((f) => ({ ...f, phone: v }))}
          placeholder="e.g. +1 555 123 4567"
        />
        <MailboxPicker
          value={form.outreach_mailbox_id}
          onChange={(v) => setForm((f) => ({ ...f, outreach_mailbox_id: v }))}
        />
        <Field
          label="Email signature"
          hint="Optional — paste freely; we format and style it when emails go out."
          value={form.email_signature}
          onChange={(v) => setForm((f) => ({ ...f, email_signature: v }))}
          placeholder={`Thanks,\nYour Name\nTitle\nhttps://www.linkedin.com/in/…\n+1 …\nCompany\nWebsites:\nhttps://…`}
          multiline
          rows={9}
        />
        <SignaturePreview text={form.email_signature} />
        <Field
          label="Document focus"
          hint="Optional — e.g. AI Engineering. Leave blank to use all relevant content."
          value={form.document_focus}
          onChange={(v) => setForm((f) => ({ ...f, document_focus: v }))}
          placeholder="e.g. AI Engineering"
          multiline
        />
      </div>
      <div className="mt-5 flex gap-2">
        <Button
          onClick={() => create.mutate()}
          disabled={!principalFormValid(form) || create.isPending}
        >
          {create.isPending ? "Creating…" : "Create"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </Card>
  );
}

export default function Principals() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["principals", "active"],
    queryFn: () => listPrincipals({ active: true }),
    retry: 2,
  });

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [search, setSearch] = useState("");

  const principals = data?.items ?? [];

  useEffect(() => {
    if (principals.length === 0) {
      setSelectedId(null);
      return;
    }
    if (selectedId === null || !principals.some((p) => p.id === selectedId)) {
      setSelectedId(principals[0].id);
    }
  }, [principals, selectedId]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return principals;
    return principals.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.document_focus ?? "").toLowerCase().includes(q)
    );
  }, [principals, search]);

  const selected = principals.find((p) => p.id === selectedId) ?? null;

  if (isLoading) return <Loading />;

  if (isError || !data) {
    const message =
      (error as { message?: string })?.message ||
      "Could not reach the backend API. Make sure the FastAPI server is running on port 8000.";
    return (
      <div>
        <PageHeader
          title="Principals"
          subtitle="Who you're representing — upload their documents so we can extract proof points."
        />
        <Card className="p-6">
          <p className="text-sm text-rose-700">{message}</p>
          <Button className="mt-4" onClick={() => void refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Principals"
        subtitle="Select a person to view their profile and documents."
        actions={
          <Button
            onClick={() => {
              setShowNew(true);
              setSelectedId(null);
            }}
          >
            New principal
          </Button>
        }
      />

      {principals.length === 0 ? (
        showNew ? (
          <NewPrincipalForm
            onCreated={(id) => {
              setShowNew(false);
              setSelectedId(id);
            }}
            onCancel={() => setShowNew(false)}
          />
        ) : (
          <Card className="flex flex-col items-center gap-4 p-10 text-center">
            <EmptyState message="No principals yet." />
            <Button onClick={() => setShowNew(true)}>Create your first principal</Button>
          </Card>
        )
      ) : (
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
          {/* Sidebar list */}
          <Card className="w-full shrink-0 overflow-hidden lg:w-72">
            <div className="border-b border-slate-100 p-3">
              <input
                type="search"
                placeholder="Search principals…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-slate-400 focus:outline-none"
              />
              <p className="mt-2 text-xs text-slate-400">
                {principals.length} principal{principals.length === 1 ? "" : "s"}
              </p>
            </div>
            <div className="max-h-[calc(100vh-14rem)] space-y-0.5 overflow-y-auto p-2">
              {filtered.length === 0 ? (
                <p className="px-3 py-4 text-center text-xs text-slate-400">
                  No matches for &ldquo;{search}&rdquo;
                </p>
              ) : (
                filtered.map((p) => (
                  <PrincipalListItem
                    key={p.id}
                    principal={p}
                    selected={p.id === selectedId && !showNew}
                    onSelect={() => {
                      setSelectedId(p.id);
                      setShowNew(false);
                    }}
                  />
                ))
              )}
            </div>
          </Card>

          {/* Detail panel */}
          <div className="min-w-0 flex-1">
            {showNew ? (
              <NewPrincipalForm
                onCreated={(id) => {
                  setShowNew(false);
                  setSelectedId(id);
                }}
                onCancel={() => {
                  setShowNew(false);
                  if (selectedId === null && principals.length > 0) {
                    setSelectedId(principals[0].id);
                  }
                }}
              />
            ) : selected ? (
              <PrincipalProfile
                principal={selected}
                onRemoved={() => {
                  setSelectedId(null);
                  void refetch();
                }}
              />
            ) : (
              <Card className="flex h-64 items-center justify-center p-8">
                <p className="text-sm text-slate-500">
                  Select a principal from the list to view details.
                </p>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
