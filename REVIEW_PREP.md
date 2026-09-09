# Review Prep — Understand the Whole Codebase

Read this top to bottom once. It explains every file in plain language, in the
order data actually flows, and ties each piece back to what the challenge is
grading. Keep it open during the live review as a cheat sheet.

---

## 1. The one-paragraph mental model

A user asks a question in plain English. FastAPI reads their login token to
get `tenant_id` (never trusts anything else for that). A LangGraph state
machine runs 8 fixed steps. Two steps call Gemini (classify the question,
write the final sentences); the other six are plain Python — fetch data,
compute money, check rules. Every number Gemini is allowed to talk about is
first stored in a Python dict and handed to Gemini only as a placeholder like
`{{f1}}`. Gemini never sees a real rupee figure. After Gemini writes its
answer, Python checks the text contains no stray digits, then — and only
then — swaps the placeholders back for real numbers. That's the whole trick
behind "the model may not produce numbers" (§3.3).

---

## 2. File-by-file, in the order to explain them

### Config layer — the plumbing everything else sits on

- **`app/config/config.py`** — reads `.env` into one `settings` object.
  Nothing exciting, but note `MAX_TOOL_CALLS`, `MAX_SECONDS`, `FAKE_BAD_NUMBER`
  live here — these are the agent's budget knobs (§3.6) and the demo switch
  for proving the hallucination guard (§3.3) really works.

- **`app/config/db.py`** — **the most important small file in the repo.**
  Two engines: `admin_engine` (superuser — only used for login-by-email and
  scripts) and `app_engine` (the `app_user` role — used for every real
  request). `get_session(tenant_id)` calls Postgres's `set_config()` to pin
  the tenant for that connection *before any query runs*. This is the whole
  mechanism behind §3.4 ("tenant identity is never model-controlled") —
  Postgres itself refuses rows from another tenant, not a Python `if`.
  Also has `as_of()`, `insert_fact()`, `correct_fact()` — the bitemporal
  helpers (§3.1). Every financial table has 4 date columns
  (`valid_from/valid_to`, `tx_from/tx_to`); `as_of()` is the one function
  that reads them, so every query in the system is bitemporally correct by
  construction, not by remembering to add a filter each time.

- **`app/config/dependencies.py`** — `get_current_user` decodes the JWT
  (that's where `tenant_id` *always* comes from — never a request body).
  `get_db_session` wraps `get_session()` as a FastAPI dependency so every
  route gets a properly tenant-scoped connection automatically.

### Migrations — read these two, skip the rest

- **`b5f1eea5ce99_row_level_security.py`** — creates the `app_user` Postgres
  role and turns on Row-Level Security: every tenant table gets a policy
  `tenant_id = current_setting('app.tenant_id')`. This is §3.4's *actual*
  enforcement — not a code review talking point, a database constraint.
- **`de7dca853b7f_...`** — fixes a real bug found while testing: a pooled
  connection reused after another tenant's session resets the setting to an
  empty string, not `NULL`. `NULLIF(..., '')` makes it fail closed (zero
  rows) instead of crashing. Good story for "what broke and how you fixed it."

### Services layer — deterministic business logic, no AI here at all

- **`app/utils/money.py`** — money is always an integer count of paise.
  `split_money`/`split_by_weights` always sum back to the original amount
  exactly (edge case #15). `format_money` uses real Indian lakh/crore comma
  grouping (`₹42,00,000.00`), not Python's default Western grouping — a real
  bug I caught and fixed.

- **`app/services/policy.py`** — a tenant's calculation rules live as a JSON
  row in the `policies` table, versioned, never edited (§3.2). `save_policy`
  always *inserts* a new version; `get_policy` finds whichever version was
  active at a given moment. This is why "add tenant C's weird rule" is an
  INSERT via `/admin/policy`, never a code change.

- **`app/services/finance.py`** — the money math. `apply_policy` reads the
  policy's JSON keys (`include`, `subtract`, `exclude_disputed`, ...) and
  computes `outstanding`/`overdue`/`aging` — one function, no per-tenant
  branches. `get_balance` fetches ALL of a tenant's invoices/payments in ONE
  query each (not a loop per customer — that's the §7 performance
  requirement). `allocate_payment` implements the 3 allocation strategies
  and always returns `unallocated` separately, never silently netted (edge
  case #6). `rank_customers` only ever reads `balances` (verified) and
  `signals` (flags) — never touches a claim's money.

- **`app/services/claims.py`** — a WhatsApp "I already paid ₹8L" is stored
  in its own `claims` table, completely separate from `payments` (§3.7).
  `find_conflicts` only ever *compares* a claim against real payments and
  reports a mismatch — it never adjusts the balance. `update_promises`
  computes a promise's status (`open`/`kept`/`broken`) fresh every call
  instead of storing it — a promise isn't a fact, it's an inference. Also
  fixed a real N+1-queries-in-a-loop bug here (§7) — `get_signals` used to
  query payments once per customer; now it's one batched query for all of
  them.

- **`app/services/entities.py`** — turns a fuzzy name ("ABC payment done")
  into a `customer_id`. Exact phone/GSTIN match first, then Postgres
  trigram similarity. Critically: it checks the gap between the best and
  second-best match, not just a confidence threshold — that's what makes
  "ABC Traders vs ABC Trading Co vs ABC Suppliers" correctly return
  "unclear" instead of a wrong guess (edge case #2).

- **`app/services/messages.py`** — stores/searches customer messages,
  flags obvious injection phrasing, and wraps every message in
  `[CUSTOMER MESSAGE]...[END CUSTOMER MESSAGE]` markers before it ever
  reaches a prompt. The *real* defense for §3.5 isn't this wrapper though —
  it's that the Gemini call that writes the final answer has literally zero
  tools attached, so there's nothing for an injected instruction to invoke.

- **`app/services/actions.py`** — sending a draft message. Every send
  carries a caller-supplied idempotency key; `approve_and_send` uses
  `SELECT ... FOR UPDATE` so two people approving the same draft at the same
  moment produce exactly one message (proved with a real concurrent-thread
  test). A network timeout becomes `"unknown"`, never a guessed
  success/failure — `reconcile()` resolves it later by actually asking the
  provider (§3.8, edge case #11).

- **`app/services/ingest.py`** — webhook intake. A `(tenant, source,
  event_id)` unique constraint makes a duplicate delivery a no-op. A
  reversal that arrives before the payment it reverses gets parked in
  `pending_events` and auto-applied the moment that payment lands (edge
  case #7) — never guessed, never dropped.

- **`app/services/auth.py`** — login, password hashing (`bcrypt` directly,
  not `passlib` — a real compatibility bug I hit and fixed), JWT issuing.

### Agent layer — this is the "agentic" part, and where the graded design lives

- **`app/agent/providers/llm.py`** — the *only* file that calls Gemini.
  Two calls: `understand()` (picks one of 5 fixed question types, pulls out
  name mentions) and `write()` (composes the final sentences). `write()`'s
  config has no `tools` — that's the structural guarantee behind §3.5.

- **`app/agent/tools/tools.py`** — the §3.3 boundary. `add_fact()` stores a
  real paise value and hands back a string like `"f3"`; every `tool_*`
  function returns `"{{f3}}"` to the caller, never the number itself.
  `run_tool()` is the single choke point where the tool-call budget is
  enforced and where a model-supplied `tenant_id` in the arguments gets
  silently dropped (§3.4) — the real `tenant_id` always comes from the
  caller, sourced from the JWT.

- **`app/agent/utils/render.py`** — the verification layer. `check_no_digits`
  scans the model's text (placeholders stripped out first) for any raw
  digit, currency symbol, or spelled-out number — including dates, which go
  through placeholders too, on purpose (a date is still a number a model
  could get wrong). `fill_in_numbers` is the *only* place in the whole
  codebase a digit gets substituted into user-facing text, and it only runs
  after the digit check passes.

- **`app/agent/state/state.py`** — one `State` dict. Literally everything
  the agent knows about a single request lives here — nothing is hidden in
  a class attribute or a global. That's what makes a past run fully
  replayable: dump this dict at any step and you have the whole story.

- **`app/agent/nodes/nodes.py`** — the 8 fixed steps as 8 plain functions:
  `step_start` (pins policy + dates, opens the `runs` row), `step_understand`
  (Gemini call 1), `step_find_names` (entity resolution), `step_plan` (a
  fixed lookup table `PLANS[intent]` — the model never invents its own
  plan), `step_gather` (calls tools, enforces the budget), `step_check`
  (abstain on missing data or conflicting evidence), `step_write` (Gemini
  call 2), `step_verify` (the digit check), `step_finalize` (writes the
  outcome back to `runs`, and — only for a "draft" question that answered
  cleanly — persists the real Outbound draft using the *rendered* text).

- **`app/agent/graph/graph.py`** — wires the 8 nodes with LangGraph.
  Only one real loop in the whole graph: `step_write ↔ step_verify`, capped
  at exactly one retry, then a hard abstain (`"could not verify the
  numbers"`). Every other edge is a straight line or a two-way branch to
  `finalize`. This is the answer to "why not let the agent loop freely" —
  because a fixed graph gives a hard upper bound and a trace you can read.

### API + everything else

- **`app/api/routes.py`** — FastAPI routes. `/ask` runs the whole graph.
  `/customers/{id}/balance` and `/priority` are deliberately *not* going
  through the agent at all — pure Python, no Gemini call, because sorting a
  list doesn't need an LLM and it has to be fast (§7). `/runs/{id}/trace`
  is the provenance endpoint — it shows the raw `{{fN}}` tool results before
  rendering, proof that nothing was invented.

- **`app/scripts/seed.py`** — `--demo` hand-builds all 15 challenge
  scenarios with fixed, deterministic ids (so tests can hardcode them).
  `--full` bulk-generates 500 customers / 50k invoices / 200k messages
  (runs in ~8.5s).

- **`app/tests/`** — 51 tests. `test_edge_cases.py` has one named test per
  challenge scenario, `test_tenant.py` proves RLS with real cross-tenant
  queries (not mocked), `test_actions.py` proves the concurrent-approve
  case with real threads, `test_agent.py` runs the actual compiled graph.

- **`frontend/app.py`** — a thin Streamlit client. Talks to the API over
  plain HTTP only, same contract anyone else's frontend would use.

---

## 3. Trace one question through every file (say this out loud in the review)

**"What did ABC Traders owe as of 1 September?"**

1. `routes.py` `/ask` — `get_current_user` decodes the JWT → `tenant_id`.
   `get_db_session` opens a connection pinned to that tenant.
2. `graph.py` invokes the compiled graph with the question + dates.
3. `step_start` (`nodes.py`) — `policy.get_policy()` finds the rule version
   active right now, opens a `runs` row, creates the empty `facts` dict.
4. `step_understand` — Gemini call 1 → `{"intent": "as_of", "mentions":
   ["ABC Traders"]}`.
5. `step_find_names` — `entities.find_customer()` runs trigram similarity.
   If it's unambiguous, we get a `customer_id`.
6. `step_plan` — looks up `PLANS["as_of"]` → `[("get_balance", {})]`. Fixed,
   not invented.
7. `step_gather` — `tools.run_tool()` calls `tool_get_balance`, which calls
   `finance.get_balance()` (one query per table, bitemporal filter, RLS
   double-enforced at the DB layer). The real number goes into `facts` as
   `"f1"`; the tool returns `"{{f1}}"`.
8. `step_check` — is there a conflicting claim? If yes, abstain here.
9. `step_write` — Gemini call 2, no tools attached, writes
   `"...owes {{f1}}..."`.
10. `step_verify` — `render.check_no_digits()` passes → `fill_in_numbers()`
    swaps `{{f1}}` for `₹42,00,000.00`. This is the only place a digit
    appears in the response.
11. `step_finalize` — writes the answer back to the `runs` row.
12. Response goes back with the answer, `run_id`, and `policy_version`.
    `/runs/{id}/trace` can replay every one of these steps later.

---

## 4. Cheat sheet — requirement → file

| Challenge requirement | Where it lives |
|---|---|
| §3.1 Bitemporal ledger | `db.py` (`as_of`, `insert_fact`, `correct_fact`), 4 date cols on every financial model |
| §3.2 Policy as data | `services/policy.py` |
| §3.3 Model can't produce numbers | `agent/tools/tools.py` (facts dict) + `agent/utils/render.py` (digit check) |
| §3.4 Tenant never model-controlled | `db.py` `get_session()` + RLS migration + `tools.py` `run_tool()` dropping smuggled `tenant_id` |
| §3.5 Retrieved content is data | `services/messages.py` wrapper + `llm.py` `write()` has no tools |
| §3.6 Explicit state machine | `agent/state/state.py` + `agent/nodes/nodes.py` + `agent/graph/graph.py` |
| §3.7 Claims vs. verified state | `services/claims.py` — `finance.py` never imports it |
| §3.8 Idempotent actions | `services/actions.py` |
| §7 Performance | composite indexes migration + batched queries in `finance.py`/`claims.py` |
| Edge cases #1–15 | `app/tests/test_edge_cases.py`, one named test each |

---

## 5. Answers to keep ready (from the plan's own "what to defend" list)

- **"Add a tenant with this new rule."** → `POST /admin/policy`. If the rule
  needs a key that doesn't exist yet, that's a one-line `if` in
  `apply_policy` — the honest boundary of "policy as data," not a gap.
- **"Make it emit a wrong number."** → flip `FAKE_BAD_NUMBER`, ask again,
  show the abstain. The model never had the number — the check runs before
  render.
- **"Your digit check is just a regex."** → yes, and it's the *second*
  layer. The first is that the model is never given a number to copy. The
  third is that the renderer only reads from the facts dict, which only
  tools write to.
- **"Two people approve at once?"** → unique idempotency key + `SELECT ...
  FOR UPDATE`. There's a real concurrent-thread test proving it.
- **"Isn't the claim separation just naming?"** → open `finance.py` and
  `claims.py` side by side. No import.
