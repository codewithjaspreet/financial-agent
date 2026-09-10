# LLM boundary

What the model may do, what it is structurally prevented from doing, and how that prevention is enforced. Prompt text is not the control.

The model is called in two places only (`app/agent/providers/llm.py`): `understand()` and `write()`.

## Permitted

- Classify a question into one of five intents: `daily_list`, `one_customer`, `explain`, `draft`, `as_of`. The JSON schema uses an enum. A sixth intent cannot be invented.
- Extract customer *name strings* from the question. It does not return a `customer_id`.
- Compose the final sentences using `{{fN}}` placeholders that tools already created.

## Prevented

| The model must not | Enforcement (not a prompt) |
|---|---|
| Compute, round, convert, or invent a rupee figure | Tools store paise in a facts dict and return `{{f1}}`. `fill_in_numbers` is the only substitution, and it runs only after the digit check passes. |
| Choose a tenant | `tenant_id` comes from the JWT into `get_session()`. `run_tool` drops any `tenant_id` in arguments. Postgres RLS returns empty rows for another tenant. |
| Pick which tools to call | `PLANS[intent]` is a Python dict in `nodes.py`. |
| Act on a customer WhatsApp message | `write()` is configured with no tools. Retrieved text is wrapped as data. |
| Loop until it feels done | Fixed graph. One write/verify retry, then abstain. Tool-call and wall-clock budgets. |
| Turn a claim into a payment | Claims live in `claims`. `finance.py` never imports that module. Claim amounts render as `reportedly … (unverified)`. |
| Answer when the name is ambiguous | Entity resolution requires a confidence *and* a gap over the second-best match. Otherwise the state is `clarify`. |
| Answer when evidence conflicts | `step_check` abstains with `conflicting evidence`. The verified balance is unchanged. |

## How a number reaches a user

1. A tool reads the ledger (or a claim) and calls `add_fact`.
2. The model is shown `{{f3}}`, never `4200000`.
3. `check_no_digits` fails the draft if any digit, currency symbol, or number-word sits outside a placeholder.
4. `check_facts_exist` fails unknown placeholders (`{{f99}}`).
5. `fill_in_numbers` substitutes from the facts dict.

The digit check is a tripwire. The lock is that the model is never given a number, and the renderer only reads facts that tools wrote.

Dates go through the same placeholders. A date in prose is still a number the model could get wrong.

## What we accept as residual risk

- The model can *read* digits that appear inside a retrieved WhatsApp message. It cannot call tools in response. If it copies a digit, verify fails and the run abstains.
- Spelled-out numbers not in the tripwire list would need the list extended. The facts-dict path still would not have produced that amount.
- `understand()` can mis-classify intent. The blast radius is a wrong *plan* from a fixed table, not a wrong rupee. `clarify` / `abstain` remain available.
