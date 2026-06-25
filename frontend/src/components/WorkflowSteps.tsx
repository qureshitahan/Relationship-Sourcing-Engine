import { Link, useLocation } from "react-router-dom";

const STEPS = [
  { n: 1, label: "Discover", path: "/discover", hint: "Find people via Apollo" },
  { n: 2, label: "Prospects", path: "/prospects", hint: "Research, reveal & approve" },
  { n: 3, label: "Drafts", path: "/emails", hint: "Write & send to approved" },
  { n: 4, label: "Conversations", path: "/outreach", hint: "Replies & follow-ups" },
] as const;

export default function WorkflowSteps({ active }: { active?: number }) {
  const location = useLocation();

  const current =
    active ??
    (location.pathname.startsWith("/discover")
      ? 1
      : location.pathname.startsWith("/prospects") ||
          location.pathname.startsWith("/organizations")
        ? 2
        : location.pathname.startsWith("/emails")
          ? 3
          : location.pathname.startsWith("/outreach")
            ? 4
            : undefined);

  return (
    <div className="mb-6 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="mr-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Workflow
        </span>
        {STEPS.map((step, i) => (
          <div key={step.n} className="flex items-center gap-2">
            {i > 0 && <span className="text-slate-300">→</span>}
            <Link
              to={step.path}
              className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition ${
                current === step.n
                  ? "bg-slate-900 font-medium text-white"
                  : "text-slate-600 hover:bg-white hover:shadow-sm"
              }`}
              title={step.hint}
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold ${
                  current === step.n
                    ? "bg-white text-slate-900"
                    : "bg-slate-200 text-slate-600"
                }`}
              >
                {step.n}
              </span>
              {step.label}
            </Link>
          </div>
        ))}
      </div>
      <p className="text-xs text-slate-500">
        <strong>Prospects</strong> — approve who gets an email.{" "}
        <strong>Drafts</strong> — write and send to approved people only.{" "}
        <strong>Conversations</strong> — track replies and follow-ups.
      </p>
    </div>
  );
}
