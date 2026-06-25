import { NavLink, Outlet } from "react-router-dom";
import ProviderHealthBanner from "./ProviderHealthBanner";

const NAV_SECTIONS = [
  {
    label: "Overview",
    items: [
      { to: "/", label: "Dashboard", end: true },
      { to: "/agent", label: "Campaigns", hint: "Autonomous daily outreach" },
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
      { to: "/outreach", label: "4. Conversations", hint: "Replies & follow-ups" },
    ],
  },
  {
    label: "Reference",
    items: [
      { to: "/organizations", label: "Organizations", hint: "Supporting data" },
      { to: "/calls", label: "Call Queue", hint: "Optional voice outreach" },
    ],
  },
] as const;

export default function Layout() {
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
                {section.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={"end" in item ? item.end : false}
                    className={({ isActive }) =>
                      `block rounded-lg px-3 py-2 transition ${
                        isActive
                          ? "bg-slate-900 text-white"
                          : "text-slate-600 hover:bg-slate-100"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span className="text-sm font-medium">{item.label}</span>
                        {"hint" in item && item.hint && (
                          <span
                            className={`mt-0.5 block text-[11px] ${
                              isActive ? "text-slate-300" : "text-slate-400"
                            }`}
                          >
                            {item.hint}
                          </span>
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
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
