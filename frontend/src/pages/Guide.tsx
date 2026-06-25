import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Badge, Card, PageHeader } from "../components/ui";

// --- small presentational helpers ------------------------------------------

function StepPill({ n, label }: { n: number | string; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
        {n}
      </span>
      <span className="text-sm font-medium text-slate-700">{label}</span>
    </div>
  );
}

function Section({
  id,
  step,
  title,
  subtitle,
  children,
}: {
  id: string;
  step?: number;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  return (
    <Card className="overflow-hidden">
      <div id={id} className="scroll-mt-6 border-b border-slate-100 bg-slate-50/60 px-6 py-4">
        <div className="flex items-center gap-3">
          {step !== undefined && (
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-900 text-sm font-bold text-white">
              {step}
            </span>
          )}
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
            <p className="text-sm text-slate-500">{subtitle}</p>
          </div>
        </div>
      </div>
      <div className="px-6 py-5">{children}</div>
    </Card>
  );
}

function Callout({
  tone = "blue",
  title,
  children,
}: {
  tone?: "blue" | "amber" | "green";
  title: string;
  children: ReactNode;
}) {
  const tones = {
    blue: "bg-blue-50 text-blue-900 ring-blue-200",
    amber: "bg-amber-50 text-amber-900 ring-amber-200",
    green: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  } as const;
  return (
    <div className={`rounded-lg px-4 py-3 text-sm ring-1 ring-inset ${tones[tone]}`}>
      <div className="font-semibold">{title}</div>
      <div className="mt-1 leading-relaxed">{children}</div>
    </div>
  );
}

// A faux browser frame to present screenshots/diagrams cleanly.
function Frame({ caption, children }: { caption?: string; children: ReactNode }) {
  return (
    <figure className="overflow-hidden rounded-xl border border-slate-200 shadow-sm">
      <div className="flex items-center gap-1.5 border-b border-slate-200 bg-slate-100 px-3 py-2">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-300" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-300" />
      </div>
      <div className="bg-white">{children}</div>
      {caption && (
        <figcaption className="border-t border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-500">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}

function Img({ src, alt }: { src: string; alt: string }) {
  return <img src={src} alt={alt} className="block w-full" />;
}

// --- the page ----------------------------------------------------------------

export default function Guide() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="How this works"
        subtitle="A guided tour of the Relationship Sourcing Engine — what each page does, and the logic underneath."
      />

      {/* Big picture */}
      <Card className="overflow-hidden">
        <div className="bg-gradient-to-br from-slate-900 to-slate-700 px-6 py-6 text-white">
          <h2 className="text-xl font-semibold">What this engine does</h2>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-200">
            It helps a <strong>principal</strong> achieve a specific goal — winning a board
            seat, selling a product, forming a partnership, or reaching a decision-maker —
            by finding the right people, researching them, writing short personalized
            outreach grounded in the principal's real track record, and tracking replies.
            You set the <strong>outreach goal</strong> on the Agent or Discover page;
            the Principal page is just who they are and their documents.{" "}
            <strong>You stay in the loop at every step.</strong>
          </p>
        </div>
        <div className="grid gap-3 px-6 py-5 sm:grid-cols-2 lg:grid-cols-7">
          {[
            { n: 0, label: "Principal", hint: "Name + documents" },
            { n: 1, label: "Discover", hint: "Find people via Apollo" },
            { n: 2, label: "Prospects", hint: "Research, reveal & approve" },
            { n: 3, label: "Drafts", hint: "Personalized emails" },
            { n: 4, label: "Conversations", hint: "Replies & follow-ups" },
            { n: "A", label: "Agent", hint: "Optional — runs daily on its own" },
          ].map((s, i) => (
            <div key={s.label} className="relative">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <StepPill n={s.n} label={s.label} />
                <p className="mt-2 text-xs text-slate-500">{s.hint}</p>
              </div>
              {i < 5 && (
                <span className="absolute -right-2 top-1/2 hidden -translate-y-1/2 text-slate-300 lg:block">
                  →
                </span>
              )}
            </div>
          ))}
        </div>
      </Card>

      {/* Principal */}
      <Section
        id="principal"
        step={0}
        title="Principal — the foundation"
        subtitle="Everything starts with who you're representing and the proof of their track record."
      >
        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-3 text-sm text-slate-600">
            <p>
              Add the person's <strong>name</strong> and upload their documents — résumés,
              bios, case studies, articles. Claude reads each file and extracts{" "}
              <strong>verbatim proof points</strong> and <strong>keywords/themes</strong>.
            </p>
            <p>
              Optionally set a <strong>document focus</strong> if they have many skills —
              e.g. "AI Engineering" or "Data Analysis" — so indexing emphasizes that niche.
              Leave it blank to pull all relevant information from every upload.
            </p>
            <p>
              These become the engine's evidence base. They are injected
              automatically when researching a prospect and when drafting an email —
              so outreach always references something <em>real</em> and relevant.
            </p>
            <Callout tone="green" title="Why it matters">
              Generic outreach gets ignored. Grounding every message in specific,
              true achievements is what makes a 2–3 line note credible to a busy
              operating partner.
            </Callout>
          </div>
          <Frame caption="Principals page — indexed documents become proof points & keywords">
            <div className="space-y-3 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-base font-semibold text-slate-900">Dalbir Bains</div>
                  <div className="text-xs text-slate-500">
                    Healthcare operator · board &amp; advisory candidate
                  </div>
                </div>
                <Badge tone="green">8 core docs</Badge>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                {[
                  ["91", "Proof points"],
                  ["34", "Keywords"],
                  ["8", "Ready"],
                ].map(([v, l]) => (
                  <div key={l} className="rounded-lg bg-slate-50 py-2">
                    <div className="text-lg font-bold text-slate-900">{v}</div>
                    <div className="text-[11px] text-slate-500">{l}</div>
                  </div>
                ))}
              </div>
              <div className="rounded-lg border border-slate-100 p-3 text-xs text-slate-600">
                "Founder &amp; CEO, FGC Health (2019–2024): led 20 acquisitions
                generating $7M recurring EBITDA; raised $50M…"
              </div>
              <div className="flex flex-wrap gap-1">
                {["roll-up", "board governance", "ebitda growth", "capital raising"].map(
                  (k) => (
                    <Badge key={k} tone="slate">
                      {k}
                    </Badge>
                  )
                )}
              </div>
            </div>
          </Frame>
        </div>
      </Section>

      {/* Discover */}
      <Section
        id="discover"
        step={1}
        title="Discover — find the right people"
        subtitle="Define an ideal profile in plain chips; we query Apollo for matching decision-makers."
      >
        <div className="space-y-4 text-sm text-slate-600">
          <p>
            You build a target profile by clicking chips: <strong>titles</strong> and{" "}
            <strong>seniorities</strong> (who they are), <strong>industries,
            sectors, company types &amp; geography</strong> (where they work), and{" "}
            <strong>themes</strong> (what they care about). Type your own values too.
          </p>
          <Frame caption="Discover — People (who to reach) and Seniorities">
            <Img src="/guide/discover-top.png" alt="Discover page — titles and seniorities" />
          </Frame>
          <Frame caption="Discover — Organization filters, themes, and result caps">
            <Img
              src="/guide/discover-bottom.png"
              alt="Discover page — company types, geography, size & caps"
            />
          </Frame>

          <div className="grid gap-3 md:grid-cols-2">
            <Callout tone="blue" title="The logic underneath">
              Apollo <strong>People Search</strong> runs directly using your title,
              seniority, industry, and company-type filters. Employee size min/max and
              geography narrow the employer pool — use employee max when you want
              smaller or mid-market companies only.
            </Callout>
            <Callout tone="amber" title="Per-person research (on Prospects)">
              After discovery, use the <strong>Prospects</strong> page to research
              individuals with Claude (short call + light web search) and judge
              relevance to your principal before outreach.
            </Callout>
          </div>

          <Callout tone="green" title="Tip — run in focused batches">
            One run per persona beats one giant run. E.g. Run 1: PE operating
            partners. Run 2: board-search consultants. Run 3: independent directors.
            Tighter filters = higher-quality matches.
          </Callout>
        </div>
      </Section>

      {/* Prospects */}
      <Section
        id="prospects"
        step={2}
        title="Prospects — your working hub"
        subtitle="Every discovered person, scored for board relevance. Review, research, and reveal contacts."
      >
        <div className="space-y-4 text-sm text-slate-600">
          <p>
            Each person is auto-scored with a <strong>board-fit score</strong> and a{" "}
            <strong>tier</strong> based on their title and industry:
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-slate-100 p-3">
              <Badge tone="green">Tier 1 · gatekeepers</Badge>
              <p className="mt-2 text-xs text-slate-500">
                Operating partners, talent partners, board-search consultants — they
                directly place people on boards.
              </p>
            </div>
            <div className="rounded-lg border border-slate-100 p-3">
              <Badge tone="amber">Tier 2 · board peers</Badge>
              <p className="mt-2 text-xs text-slate-500">
                Independent directors, audit-committee chairs, governance leaders who
                can refer or sponsor.
              </p>
            </div>
            <div className="rounded-lg border border-slate-100 p-3">
              <Badge tone="slate">Tier 3 · referrers</Badge>
              <p className="mt-2 text-xs text-slate-500">
                CEOs, founders, investors — useful network, lower direct influence.
              </p>
            </div>
          </div>

          <Frame caption="Prospects — scored, tiered, and ready to research (sample row layout)">
            <div className="overflow-x-auto p-1">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-slate-400">
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">Title</th>
                    <th className="px-3 py-2">Board-fit</th>
                    <th className="px-3 py-2">Email</th>
                    <th className="px-3 py-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {[
                    ["Nicole N.", "Operating Partner — Growth PE", "Tier 1 · 96", "reveal"],
                    ["Jon D'A.", "SVP, HR Talent Partner", "Tier 1 · 92", "reveal"],
                    ["Kurt W.", "Operating Partner", "Tier 1 · 90", "reveal"],
                    ["Yosmany C.", "Managing Partner", "Tier 3 · 66", "—"],
                  ].map(([name, title, fit, email]) => (
                    <tr key={name as string}>
                      <td className="px-3 py-2 font-medium text-slate-700">{name}</td>
                      <td className="px-3 py-2 text-slate-500">{title}</td>
                      <td className="px-3 py-2">
                        <Badge tone={(fit as string).startsWith("Tier 1") ? "green" : "slate"}>
                          {fit}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-400">
                        {email === "—" ? "—" : <Badge tone="blue">reveal</Badge>}
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone="amber">review</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Frame>

          <Callout tone="amber" title="Emails are hidden on purpose">
            Discovery never spends Apollo credits revealing contact info. You{" "}
            <strong>reveal email only for the people you actually want to reach</strong>{" "}
            (typically high Tier-1 scores) — protecting your credit balance and keeping
            the list clean.
          </Callout>
          <p>
            Open a prospect to <strong>research</strong> them: Claude compares their
            background to the principal's indexed proof points and explains the fit.
            Approve the good ones to move them toward outreach.
          </p>
        </div>
      </Section>

      {/* Drafts */}
      <Section
        id="drafts"
        step={3}
        title="Drafts — edit, approve, send"
        subtitle="Short, personalized emails grounded in the principal's real proof points."
      >
        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-3 text-sm text-slate-600">
            <p>
              For each approved prospect, the engine drafts a tight{" "}
              <strong>2–3 line email</strong>. It automatically picks{" "}
              <strong>1–2 proof points</strong> from the principal's documents that
              match that person's world (e.g. healthcare roll-ups, governance).
            </p>
            <p>
              You review and edit every draft before anything sends. Approved emails
              go out via <strong>Microsoft Outlook (Graph)</strong> from the
              principal's mailbox.
            </p>
            <Callout tone="green" title="Pacing">
              Sends go out in <strong>drip batches</strong> (default: 10 emails, then a
              ~2 minute pause) up to a <strong>daily cap</strong> (default: 50/day).
              This keeps deliverability healthy. The Agent page can run this on a
              schedule for you.
            </Callout>
          </div>
          <Frame caption="Outreach draft — concise and evidence-backed">
            <div className="space-y-2 p-4 text-sm">
              <div className="text-xs text-slate-400">To: operating partner @ PE firm</div>
              <div className="rounded-lg border border-slate-100 bg-slate-50 p-3 text-slate-700">
                <p>Hi Nicole,</p>
                <p className="mt-2">
                  I built and exited two PE-backed healthcare platforms — 50+
                  acquisitions, $120M raised, a 10x EV/EBITDA exit. I'd value a short
                  conversation about board or operating-partner work across your
                  healthcare portfolio.
                </p>
                <p className="mt-2">Best, Dalbir</p>
              </div>
              <div className="flex flex-wrap gap-1">
                <Badge tone="purple">proof point: 10x exit</Badge>
                <Badge tone="purple">proof point: 50+ acquisitions</Badge>
              </div>
            </div>
          </Frame>
        </div>
      </Section>

      {/* Conversations */}
      <Section
        id="conversations"
        step={4}
        title="Conversations — replies & follow-ups"
        subtitle="Track who responded, who went quiet, and draft the next touch."
      >
        <div className="space-y-3 text-sm text-slate-600">
          <p>
            After sending from <strong>Drafts</strong>, open{" "}
            <strong>Conversations</strong>. The system <strong>polls your mailbox
            automatically</strong> (every ~10 minutes) via Microsoft Graph, matches
            responses to the original thread, and moves those prospects to a{" "}
            <strong>Replied</strong> tab. You can also click{" "}
            <strong>"Check for replies"</strong> for an immediate scan.
          </p>
          <p>
            <strong>Follow-up drafts</strong> are created automatically after a few days
            with no reply (configurable on the Agent page). New drafts appear on the
            Drafts page for your review before sending.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="slate">draft</Badge>
            <span className="text-slate-300">→</span>
            <Badge tone="blue">sent</Badge>
            <span className="text-slate-300">→</span>
            <Badge tone="green">replied</Badge>
          </div>
        </div>
      </Section>

      {/* Agent */}
      <Section
        id="agent"
        title="Agent — autonomous daily outreach"
        subtitle="Set a goal once; the agent discovers, researches, drafts, and drips sends on a schedule."
      >
        <div className="space-y-3 text-sm text-slate-600">
          <p>
            The <strong>Agent</strong> page is the hands-off path. You pick a principal,
            describe your goal in plain language, and enable the scheduler. Each run the
            agent:
          </p>
          <ol className="list-decimal space-y-1 pl-5">
            <li>Picks an A/B search variant (titles, seniorities, industries) and queries Apollo</li>
            <li>Scores plausible fits with light per-person research</li>
            <li>Reveals emails and drafts personalized outreach</li>
            <li>Sends approved drafts in drip batches up to your daily cap</li>
            <li>Drafts follow-ups when people go quiet</li>
          </ol>
          <p>
            The <strong>A/B variants panel</strong> shows which search criteria are
            winning (reply rate per variant). Over time the agent leans toward what works.
          </p>
          <Callout tone="blue" title="Manual vs agentic">
            Use <strong>Discover → Prospects → Drafts</strong> when you want full control
            on each step. Use <strong>Agent</strong> when you want the same pipeline to
            run every day without babysitting it — you still approve drafts before they
            send unless you enable auto-send.
          </Callout>
          <Link
            to="/agent"
            className="inline-block rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Open Agent →
          </Link>
        </div>
      </Section>

      {/* Principles */}
      <Section
        id="principles"
        title="The principles behind it"
        subtitle="Why the engine is built this way."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            [
              "Human-in-the-loop",
              "Nothing is revealed or sent without your approval. The engine drafts and scores; you decide.",
            ],
            [
              "Evidence over fluff",
              "Every message is grounded in the principal's real, indexed achievements — not generic flattery.",
            ],
            [
              "People-first, not company-first",
              "We use companies only to scope the search. The deliverable is the right person to talk to.",
            ],
            [
              "Spend credits deliberately",
              "Contact reveals and AI research happen on the people you choose, keeping costs and noise low.",
            ],
          ].map(([t, d]) => (
            <div key={t} className="rounded-lg border border-slate-100 p-4">
              <div className="font-medium text-slate-800">{t}</div>
              <p className="mt-1 text-sm text-slate-500">{d}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* CTA */}
      <Card className="flex flex-col items-center gap-3 px-6 py-6 text-center sm:flex-row sm:justify-between sm:text-left">
        <div>
          <div className="text-base font-semibold text-slate-900">Ready to start?</div>
          <p className="text-sm text-slate-500">
            Set up your principal, then run your first focused discovery.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to="/principals"
            className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50"
          >
            Set up principal
          </Link>
          <Link
            to="/discover"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
          >
            Go to Discover →
          </Link>
        </div>
      </Card>
    </div>
  );
}
