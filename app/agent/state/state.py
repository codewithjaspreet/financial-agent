"""
The whole agent is this dict plus 8 functions of (session, state) -> state
(nodes.py). No hidden state anywhere else -- if it isn't a key here, the
agent doesn't know it. That's what makes a run fully inspectable later:
dump this dict at any point and you have the entire story so far.
"""
from datetime import date, datetime
from typing import TypedDict
from uuid import UUID


class State(TypedDict, total=False):
    # identity -- set once in step_start, never touched again
    run_id: UUID
    tenant_id: UUID
    user_id: UUID

    # the question and the two clocks it's answered against
    question: str
    on_date: date      # business date: "as of when"
    at_time: datetime  # knowledge time: "as believed when" (bitemporal §3.1)

    # policy pinned for this run (§3.2) -- never re-read mid-run
    policy: dict
    policy_version: int

    # step_understand's output (GEMINI call 1)
    intent: str                 # one of: daily_list, one_customer, explain, draft, as_of
    mentions: list[str]

    # step_find_names' output
    customer_ids: list[UUID]
    options: list[dict]         # populated only when a name was ambiguous

    # step_plan / step_gather
    plan: list[tuple[str, dict]]
    results: list[dict]         # one entry per tool call, in order
    facts: dict                 # the {{fN}} -> real-value dict (tools.py)
    on_date_fact: str           # "{{fN}}" for on_date -- dates go through placeholders too
    tool_calls: int
    max_tool_calls: int
    max_seconds: int
    started_at: float           # time.time(), for the wall-clock budget

    # step_check
    conflicts: list[dict]

    # step_write (GEMINI call 2) / step_verify
    written: dict
    problems: list[str]
    retries: int
    answer: str

    # terminal outcome
    state: str                  # answered | clarify | abstain | failed
    reason: str | None

    # set by step_finalize only when intent == "draft" and state == "answered"
    draft: dict | None          # {"outbound_id": ..., "idem_key": ...}
