import { useEffect } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import ProviderHealthBanner from "./ProviderHealthBanner";

type NavItem = {
  to: string;
  label: string;
  hint?: string;
  end?: boolean;
  /** Path prefixes that belong to this section, for both highlighting the
   * link as active and deciding when to remember a sub-page/filter under it
   * (e.g. "/campaigns/3" or "/prospects?run=5&stage=approved" belong to the
   * "Campaigns" / "Prospects" sidebar entries even though their own `to` is
   * "/agent" / "/prospects"). Defaults to [to] when omitted. */
  matchPrefixes?: string[];
};

const NAV_SECTIONS: { label: string; items: NavItem[] }[] = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Dashboard", end: true },
      {
        to: "/agent",
        label: "Campaigns",
        hint: "Autonomous daily outreach",
        matchPrefixes: ["/agent", "/campaigns"],
      },
      {
        to: "/bulk",
        label: "Send bulk emails",
        hint: "Paste a list, draft, review & send",
      },
      { to: "/guide", label: "How this works", hint: "Guided tour for new users" },
    ],
  },
  {
    label: "Setup",
    items: [{ to: "/principals", label: "Principals" }],
  },
  {
    label: "Workflow",
    items: [
      { to: "/discover", label: "1. Discover", hint: "Find board influencers" },
      { to: "/prospects", label: "2. Prospects", hint: "Research, reveal & approve" },
      { to: "/emails", label: "3. Drafts", hint: "Edit, approve & send" },
      { to: "/linkedin", label: "4. LinkedIn", hint: "Connect & message on LinkedIn" },
      {
        to: "/linkedin-responses",
        label: "LinkedIn Responses",
        hint: "Replies from your DMs",
      },
      { to: "/outreach", label: "5. Conversations", hint: "Replies & follow-ups" },
    ],
  },
  {
    label: "Reference",
    items: [
      { to: "/organizations", label: "Organizations", hint: "Supporting data" },
      { to: "/calls", label: "Call Queue", hint: "Optional voice outreach" },
    ],
  },
];

const NAV_STORAGE_PREFIX = "nav:lastPath:";

function prefixesFor(item: NavItem): string[] {
  return item.matchPrefixes ?? [item.to];
}

function matchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export default function Layout() {
  const location = useLocation();
  const fullPath = `${location.pathname}${location.search}`;

  // Remember the exact path (including query params — filters, selected
  // run, etc.) the user was last on within each section. A sidebar click
  // later returns to THIS, not the section's default landing page, so
  // switching to another module and back reopens exactly where they left
  // off. Dashboard ("/") is excluded — it has no sub-pages to remember.
  useEffect(() => {
    for (const section of NAV_SECTIONS) {
      for (const item of section.items) {
        if (item.end) continue;
        const prefixes = prefixesFor(item);
        if (prefixes.some((p) => matchesPrefix(location.pathname, p))) {
          sessionStorage.setItem(`${NAV_STORAGE_PREFIX}${item.to}`, fullPath);
        }
      }
    }
  }, [location.pathname, location.search, fullPath]);

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 flex-shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="px-5 py-5">
          <div className="text-sm font-semibold uppercase tracking-wider text-slate-400">
            Relationship
          </div>
          <div className="text-lg font-bold text-slate-900">Sourcing Engine</div>
        </div>
        <nav className="flex-1 space-y-5 px-3">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <div className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                {section.label}
              </div>
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const isActive = item.end
                    ? location.pathname === item.to
                    : prefixesFor(item).some((p) => matchesPrefix(location.pathname, p));
                  const destination = item.end
                    ? item.to
                    : sessionStorage.getItem(`${NAV_STORAGE_PREFIX}${item.to}`) || item.to;
                  return (
                    <Link
                      key={item.to}
                      to={destination}
                      className={`block rounded-lg px-3 py-2 transition ${
                        isActive
                          ? "bg-slate-900 text-white"
                          : "text-slate-600 hover:bg-slate-100"
                      }`}
                    >
                      <span className="text-sm font-medium">{item.label}</span>
                      {item.hint && (
                        <span
                          className={`mt-0.5 block text-[11px] ${
                            isActive ? "text-slate-300" : "text-slate-400"
                          }`}
                        >
                          {item.hint}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="px-5 py-4 text-xs text-slate-400">
          Executive networking · human-in-the-loop
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-8 py-8">
          <ProviderHealthBanner />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
