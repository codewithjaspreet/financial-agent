# Collections Intelligence

A collections system a finance manager can ask in plain language:

> Who should we follow up with today, how much are they expected to pay, and what should I tell them?

The model writes sentences. Python computes money. Postgres decides whose data is visible. A wrong rupee figure is treated as a product failure, not an LLM quirk.

## Quick start

One command from a clone (Docker Compose, Python 3.13, [uv](https://docs.astral.sh/uv/)):

```bash
make setup
```

That starts Postgres, applies migrations, and loads the demo tenant (the 15 named edge cases). Then:

```bash
uv run uvicorn main:app --reload --port 8000
uv run streamlit run frontend/app.py   # optional UI
```

| | |
|---|---|
| UI | http://localhost:8501 |
| API | http://localhost:8000 |
| Login | `demo@tenant-a.test` / `demo-password-123` |

`POST /ask` needs `GEMINI_API_KEY` in `.env`. Balance, priority, ingest, and the eval harness do not.

Volume seed for the performance section (500 customers, 50k invoices, 200k messages):

```bash
make seed-full
```

## What you get

| Path | Role |
|---|---|
| `POST /ask` | Agent. Classifies the question, fetches facts, writes prose around placeholders. |
| `GET /priority` | Ranked follow-up list. No model. |
| `GET /customers/{id}/balance` | Outstanding as of a date. No model. |
| `GET /customers/{id}/evidence` | Claims and conflicts. Never mixed into the balance. |
| `GET /runs/{id}/trace` | Tool calls and `{{fN}}` results for one answer. |
| `POST /admin/policy` | New tenant rule = an insert, not a deploy. |
| `POST /webhook/payment` | Idempotent bank events. |
| `POST /drafts/{id}/approve` | Human send. Idempotency key required. |

## Architecture

```
Manager
  |  JWT  (tenant_id lives here, never in the prompt)
  v
FastAPI
  |-- /ask        LangGraph: 8 fixed steps, 2 Gemini calls
  |-- /priority   Python only
  |-- /balance    Python only
  |-- /policy, /webhook, /drafts
  v
Services     finance, policy, claims, entities, ingest, actions
  v
Postgres     two clocks on money rows · row-level tenant wall · unique event/send keys
```

The agent is a state machine, not an unbounded loop:

```
start -> understand -> find names -> plan -> gather -> check -> write -> verify -> done
                         |                              |
                      clarify                        abstain
```

`plan` is a lookup table keyed by intent. The model does not invent tools. `write` has no tools attached. After `write`, Python refuses any text with a raw digit, then — and only then — swaps `{{f1}}` for a rupee amount.

Longer notes: [docs/LLM_BOUNDARY.md](docs/LLM_BOUNDARY.md).

## Data contracts

Money is integer **paise** (`bigint`). Display uses Indian grouping (`₹42,00,000.00`). Floats are not used in balances.

Every ledger fact carries two clocks:

| Clock | Columns | Question it answers |
|---|---|---|
| Valid time | `valid_from`, `valid_to` | When was this true in the business? |
| Transaction time | `tx_from`, `tx_to` | When did the system know it? |

`as_of(on_date, at_time)` is the only reader. Both questions below are valid and different:

1. What was owed as of 1 September?
2. On 1 September, what did we *believe* was owed as of 1 September?

### Sources (mocked; contracts still hold)

| Source | Tables | Guarantees |
|---|---|---|
| ERP | `invoices`, `payments`, `credit_notes`, `debit_notes`, `advances`, `allocations` | Bitemporal. Corrections close `tx_to` and insert a new row. Never update in place. |
| CRM | `customers`, `policies` | Policy is versioned JSON. Recompute an old answer with `policy_version` on the run, not today's row. |
| Messaging | `messages`, `claims` | Attacker-writable text. Claims are not payments. `finance.py` does not import `claims.py`. |
| Bank | `raw_events`, `pending_events` | Unique `(tenant_id, source, event_id)`. A reversal that arrives early is parked, not guessed. |
| Outbound | `outbound` | Unique `(tenant_id, idempotency_key)`. Timeout is `unknown`, resolved by `reconcile`. |

### Policy keys (declarative)

`include`, `subtract`, `net_advances`, `advances_must_be_approved`, `exclude_disputed`, `exclude_below_paise`, `grace_days`, `aging_buckets`, `allocation`, `priority_weights`, `min_name_confidence`, `min_name_gap`.

A new *combination* of these is an insert via `POST /admin/policy`. A new *kind* of rule still needs one branch in `apply_policy`. That is the honest edge of “policy as data.”

## Assumptions

- Integrations are mocked. Webhooks authenticate with the same user JWT as the rest of the API (a real bank would use a signed URL).
- Demo “today” for seeded scenarios is 5 September 2026.
- Outstanding is computed at customer level (invoices − payments − credit notes, then policy). Invoice-level remaining after allocation is implemented as a function and tested, not written on ingest.
- Five question types: daily list, one customer, explain, draft, as-of. A sixth type is a code change to the plan table.
- Gemini classifies intent and writes prose. It does not pick customer ids or tenants.

## Example requests

```bash
TOKEN=$(curl -s http://localhost:8000/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@tenant-a.test","password":"demo-password-123"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["token"])')

curl -s http://localhost:8000/priority -H "Authorization: Bearer $TOKEN"

curl -s 'http://localhost:8000/customers/CUSTOMER_UUID/balance?on_date=2026-09-01' \
  -H "Authorization: Bearer $TOKEN"

curl -s http://localhost:8000/ask \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"What did AsOf Co owe as of 1 September?","on_date":"2026-09-01"}'

curl -s http://localhost:8000/runs/RUN_UUID/trace \
  -H "Authorization: Bearer $TOKEN"
```

Seed prints deterministic customer ids. `make setup` also prints the demo tenant id.

## Tests and evaluation

```bash
make test    # unit tests + one named test per Section 4 edge case
make eval    # golden set, adversarial suite, numeric-fidelity check
```

Eval prints pass/fail per case and writes [eval/RESULTS.md](eval/RESULTS.md). Cases live in [eval/cases.json](eval/cases.json) — add a row there during review; the runner dispatches on `check`.

The harness is deterministic (ledger, policy, names, render). It does not call Gemini, so results stay stable. Optional live-agent cases can be added later with the same JSON shape.

## Limitations

What breaks first, where it is weakest, what two more weeks would buy.

**Breaks first**

- `POST /ask` without `GEMINI_API_KEY`. Use `/priority` and `/balance` until a key is set.
- A genuinely new policy *meaning* (not a new combination of existing keys) still needs a line in `apply_policy`.
- Naive re-seed is guarded, but full-volume seed is large; run it once.

**Weakest**

- Allocation strategy is policy and is tested; payment ingest does not persist `allocations` rows. Outstanding is a customer net, not invoice remaining.
- Run provenance stores tool calls. It does not persist every state transition or the facts dictionary (`run_facts` exists, unused).
- `write()` can still *see* digits inside retrieved WhatsApp text. It cannot *act* (no tools). The digit check stops those digits being copied into the answer.
- `exclude_tags` is an allowed policy key and is not interpreted yet. Do not use it expecting a different outstanding.
- A few agent tests call live Gemini and can flake. Prefer `make eval` and `app/tests/test_edge_cases.py` in a live review.
- Balance cache is unwired on purpose: the current table is not keyed by policy version or as-of dates.

**Two more weeks**

1. Persist `run_facts` and each state transition so any past answer is fully replayable.
2. Apply `allocate_payment` on ingest; store bitemporal allocation lines; keep unallocated remainder explicit.
3. Cache balances on `(tenant, customer, policy_version, on_date, at_time)`.
4. Extend the eval harness with optional `/ask` cases once a key is in CI.
5. Implement `exclude_tags` (or drop the key) so the policy surface matches the interpreter.

## Layout

```
app/agent/     state machine, tools, number gate
app/services/  finance, policy, claims, ingest, actions
app/models/    schema
app/tests/     unit + named edge cases
eval/          harness and committed results
frontend/      thin Streamlit client
migrations/    Alembic
```

Demo password is for local review only. Do not reuse it.
