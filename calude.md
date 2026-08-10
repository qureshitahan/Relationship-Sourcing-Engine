---

## Core Rules

* Keep functions small and single-purpose.
* Prefer modular architecture over large files.
* Use meaningful file and folder names.
* Minimize unnecessary whitespace and verbosity to preserve tokens.
* Add only essential comments.
* Write reusable and maintainable code.
* Avoid duplicate logic.
* Prefer configuration over hardcoding.
* Tuning thresholds and non-secret settings go in versioned config files; secrets go in an untracked environment file (never committed).
* Load both through one central config module — business logic never reads environment variables or config files directly.
* Always use an isolated environment (virtual env, container, or equivalent) — set it up before starting any project development.

---

## Dependency Management

* Declare dependencies explicitly in a manifest file appropriate to the stack.
* Commit lock files so installs are reproducible.
* Prefer lightweight, well-maintained packages.
* Remove unused dependencies regularly.
* Install into an isolated environment, never system-wide.

---

## Coding Standards

### Functions

* One responsibility per function.
* Target 5–20 lines when possible.
* Avoid deep nesting.
* Return early.
* Use helper functions for repeated logic.

Example:

```
function validate_email(email):
    return email contains "@" and email contains "."
```

---

### Modular Design

* Split logic into modules.
* Keep business logic separate from API/UI.
* Use purpose-named helper modules for shared logic — never a generic dumping ground.
* Keep configuration centralized.



## Environment Setup

General steps, regardless of stack:

1. Create an isolated environment (virtual env, container, or workspace).
2. Activate/enter it.
3. Install dependencies from the manifest/lock file.
4. Copy the example environment file to the real one and fill in secrets.
5. Run the project's standard start/dev command.

---

## Comments

* Write comments only when logic is non-obvious.
* Avoid redundant comments.
* Prefer self-explanatory naming.

Good:

```
# Retry because external API fails intermittently
```

Bad:

```
# Increment i
i += 1
```

---

## Naming Rules

* Use a consistent casing convention per language/ecosystem norm (e.g. snake_case, kebab-case, camelCase) — pick one per project and apply it uniformly.
* Names must be clear and descriptive.
* Avoid vague or throwaway names.

Good:

```
user_service
payment-handler
```

Bad:

```
final
newcode
```

---

## Error Handling

Fail fast. Fail closed. Never silently ignore.

### Exception hierarchy

Define a base application error and specific subtypes, e.g.:

```
AppError              # base — everything ours inherits
ConfigError           # missing secret / bad config — abort boot
ValidationError       # bad input
AuthError             # unauthenticated / unauthorized
ScopeViolation        # crossed an isolation boundary
NotFound
LimitExceeded         # quota / budget / rate cap
UpstreamUnavailable   # transient — retryable
```

Rules:

* Raise the **specific** type. Never raise a generic/base exception directly.
* Catch **narrow**, at the boundary that can actually act on it.
* Never swallow an error silently or log-and-continue without a decision.
* Retry **only** transient errors — bounded attempts, exponential backoff. Never retry auth, validation, or scope errors.
* Security errors are logged CRITICAL and audited. Prefer "not found" over "forbidden" where confirming existence leaks information.

### Partial failure in batch work

One bad item must not kill a batch, and a silently half-finished run is worse than a failed one:

```
failed = 0
for item in items:
    try:
        process(item)
    except ValidationError as e:
        failed += 1
        log.warning("item_skipped", item_id=item.id, reason=e)
if failed / len(items) > SETTINGS.batch.max_fail_ratio:
    raise AppError("batch aborted: too many failures")
```

* Isolate per item, count failures, **abort** if the failure ratio breaches the configured ceiling.
* Always report how many were skipped — never let a partial run look complete.

### API errors

* Mapped in **one** place. Bindings raise domain exceptions; they don't build responses.
* Never leak stack traces, driver messages, connection strings, or internal paths to the caller.
* Stable response shape:

```json
{"error":{"code":"VALIDATION_FAILED","message":"human-readable","details":[],"correlation_id":"..."}}
```

---

## Logging

Structured JSON, one event per line. Never use raw print/console statements for application logging.

```
log.info("batch_complete", read=1204, written=87, duration_ms=3120)
```

### Every log line carries

A correlation id plus whatever identifies the actor/scope in this system. Set once at the request/job boundary; never thread it manually through every call signature.

### Levels

| Level | Use for |
| --- | --- |
| DEBUG | local only — never enabled in prod |
| INFO | lifecycle: boot, connect, job start/complete, state change |
| WARNING | degraded but continuing: item skipped, retry fired, fallback used |
| ERROR | operation failed and the caller is affected |
| CRITICAL | fail-closed halt, security violation, hard cap hit |

### Never log

Connection strings · tokens · API keys · credentials · full request bodies · raw records · PII. Log **identifiers and counts**, not payloads.

### Make load measurable

Every scheduled job logs its work: items read, items written, duration, skipped, produced. Put a budget with an alarm on top — this is how cost and load stay provable rather than assumed.

### Audit log is separate

Append-only, durable, never trimmed. Records consequential actions: state changes, config changes, permission changes, security violations. Answers "who did what, on what basis, when."

---

## Token Optimization

* Avoid unnecessary explanations in code.
* Keep prompts concise.
* Remove dead code.
* Prefer compact implementations when readability is maintained.

---

## Configuration

Two sources, one accessor.

| Source | Holds | Committed |
| --- | --- | --- |
| Versioned config files | thresholds, tuning constants, non-secret settings | yes |
| Environment file | secrets, connection strings, environment-varying values | no (example file only) |

### Thresholds

The versioned config is the **single source of truth**. Values live there and **nowhere else** — not in code, not in tests, not in documentation.

Rules:

* Read via the config module only — never a literal in logic.
* Changing a value needs a stated reason and a source, not a hunch.
* Never restate a value in a code comment, doc, or test — a copy is a second source of truth that will go stale.
* Mark guardrail keys explicitly: they bound what runtime edits are allowed to set, and are not tuning knobs.

### Secrets

Kept in an untracked environment file; only an example/template version is committed.

### Central config module

```
# config module — the only place that reads env/config files
load thresholds from versioned config
load secrets from environment
expose both as one accessor
```

Usage — import the module, never the raw source:

```
from config import THRESHOLDS
if value < THRESHOLDS.limits.min:
    ...
```

Rules:

* Fail fast on a missing required secret — don't default silently.
* Tuning a threshold is a one-line config edit, no code change.
* Never read the environment file or config files outside the config module.

---

## Testing

### Layers

| Layer | Tests | Rules |
| --- | --- | --- |
| Unit | pure domain logic | fast, **no mocks needed** — if you need a mock, the function isn't pure |
| Integration | jobs, repositories, wiring | in-memory or disposable test DB — **never** a real/production DB |
| Contract | response shapes across every binding | all bindings asserted against the same schema |
| Security | auth, scoping, fail-closed | a leak test must fail closed to pass |

### What makes a test good

* Tests **behavior and contract**, not implementation. Refactoring internals must not break it.
* **One reason to fail.** The name states the assertion.
* **Deterministic** — injected clock, fixed fixtures, no network, no randomness, no live "now".
* Asserts the **actual value**, not truthiness.
* Expected values are **hand-written constants**, not recomputed by the test.
* Config-dependent behavior takes the threshold as a **parameter**, so tuning the config never breaks a test.

```
test discount_applies_percentage:
    assert apply_discount(price=200, pct=15) == 170

test discount_rejects_above_cap:
    expect ValidationError:
        apply_discount(price=200, pct=90, cap_pct=50)   # cap injected, not read from config
```

### What makes a test bad

| Bad | Why it's worthless |
| --- | --- |
| asserting a mock was called | tests the mock, not the code |
| asserting a value is "not null/empty" | passes for wrong answers |
| hits a real DB / network / third-party | slow, flaky, and a data-safety risk |
| recomputing the expected value with the same logic | a shared bug passes |
| depends on today's date or randomness | green today, red tomorrow |
| one test asserting many unrelated things | can't tell what broke |
| depends on a previous test's leftovers | order-dependent, breaks on parallel runs |
| reads a live config value | breaks when someone tunes the config |
| updated to match new output whenever it fails | the test now asserts the bug |

### Mandatory gates before "done"

1. **Authorization leak** — a request for another scope's data fails closed.
2. **Idempotency** — running the same operation twice produces the same result, no duplicates.
3. **Fail-closed startup** — any failed validation → nothing starts.
4. **Layering** — no data-access outside repositories; no foreign field paths outside adapters (enforced by lint/grep in CI).
5. **Contract** — every binding returns the agreed schema.
6. **Optional features off** — the system runs fully with every optional/flagged feature disabled.

Never self-grade. Tests are written **before** an item moves to in-progress, and acceptance criteria are copied verbatim from the spec.

---

## Backlog

Every item lives in a tracked backlog document. **No code without a backlog ID.** The backlog moves in lockstep with the code — same change set.

Status: `TODO → IN_PROGRESS → IN_REVIEW → DONE` (or `BLOCKED`, which must name the blocker).

Item format:

```md
### ID-06 — Short imperative title
Status: TODO | Milestone: M0 | Depends: ID-02 | Owner: TBD
Acceptance:
  Given <state>, when <action>, then <observable result>.
  Given <edge case>, when <action>, then <safe behavior>.
Tests: <test paths>
Notes: constraints worth remembering.
```

Rules:

* Acceptance criteria and test paths are filled in **before** work starts — an item without them stays TODO.
* One item = one change set = one reviewable unit.
* Completed work is logged in a work-done record.
* Open questions get IDs too, and block the items that depend on them.

---


## Performance

* Avoid unnecessary loops.
* Cache expensive operations.
* Lazy load when possible.
* Optimize DB/API calls.
* Read expensive sources once, store a compact result, serve everything downstream from that.

---

## Security & Auth

### Auth

* Keys/tokens hashed at rest, rotatable, revocable. Compare in constant time.
* Identity is resolved **from the credential** — never from the path, body, or query. A caller-supplied identity is forgeable and is ignored.
* Any scope the caller names is validated against what that credential owns.
* **Deny by default.** A route with no explicit auth declaration does not serve.

### Scoping — enforced where it can't be forgotten

Scoping belongs in the repository layer, not in bindings. A binding author must not be *able* to write an unscoped query.

```
# repository base — every read/write goes through this
function scoped(filter):
    ctx = get_scope()                # from the authenticated request/job
    if not ctx.owner_id: raise ScopeViolation("missing scope")
    return filter + {owner_id: ctx.owner_id}
```

Any repository method that bypasses this scoping is a defect, and the security tests exist to catch it.

### Credentials

* **Least privilege, never one admin credential.** Separate read and write access; scope write access to exactly what it needs.
* Secrets come from the environment file **through the config module only**. Missing required secret → abort boot, never default.
* Never log, echo, or return a connection string or key — not in errors, not in validation output.

### Input & query safety

* Validate every input at the boundary; reject unknown fields.
* Never string-concatenate a query. Never pass caller input into a raw query fragment.
* Bound every list endpoint (page size ceiling) and every range parameter — an unbounded range is a load attack.
* Rate-limit per credential. Lock CORS down to declared origins.

### Webhooks

Signed (HMAC over the raw body), timestamped, replay-protected via a nonce window. Retries use exponential backoff with a dead-letter after N attempts. Delivery targets validated at config time.

### Authenticity — output you can defend

* **Provenance travels with the data.** Where a value came from, and whether it is real or simulated, is recorded and surfaced. Never present simulated or inferred data as established fact.
* **Grounding.** Anything generated cites the values it used. No fabrication, no filling gaps from assumption. An unretrieved fact is not stated.
* **Reproducibility.** Stamp version + computed-at on every derived record; derived data must be rebuildable from its source.
* **Attribution.** Every consequential action is audited with actor and timestamp.

---

## Final Development Checklist

* Small functions
* Modular structure, one-way dependencies
* Clean naming
* Essential comments only
* Environment configured
* No dead code
* Specific exceptions, mapped in one place, nothing leaked to the caller
* Structured logs with correlation id; no raw print statements; no secrets or payloads logged
* No config value hardcoded or restated
* Every query scoped; security tests green
* Tests added — behavior-asserting, deterministic, one reason to fail
* Backlog item updated in the same change set
* Token-efficient implementation
Whenever a new library or framework dependency is introduced in the code, add it to the dependency manifest (e.g. requirements.txt, package.json) in the same change — never leave an import that isn't declared in the manifest.