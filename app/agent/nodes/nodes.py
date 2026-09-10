"""
The 8 fixed steps (§3.6). Two call Gemini (understand, write); the other six
are plain Python. Each is a function of (session, state) -> state -- no
hidden state, no free-form looping. graph.py just wires these in order with
a couple of conditional branches; all the actual decisions happen here.
"""
import time
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.providers import llm
from app.agent.state.state import State
from app.agent.tools.tools import BudgetExceeded, add_fact, new_facts, run_tool
from app.agent.utils.render import check_facts_exist, check_no_digits, fill_in_numbers
from app.config.config import settings
from app.models.agent_run import Run
from app.services import actions
from app.services import claims as claims_service
from app.services import entities
from app.services.policy import get_policy

# The model picks one of these five; Python picks the plan for it (step_plan).
PLANS: dict[str, list[tuple[str, dict]]] = {
    "daily_list": [("list_priority", {"limit": 10})],
    "one_customer": [("get_balance", {}), ("get_claims", {}), ("search_messages", {})],
    "explain": [("get_balance", {}), ("get_invoices", {}), ("get_payments", {})],
    "draft": [("get_balance", {}), ("get_promises", {})],
    "as_of": [("get_balance", {})],
}
NEEDS_CUSTOMER = {"one_customer", "explain", "draft", "as_of"}


def _check_budget(state: State) -> str | None:
    """Returns an abstain reason if over budget, else None."""
    if state["tool_calls"] > state["max_tool_calls"]:
        return "used too many tool calls"
    if time.time() - state["started_at"] > state["max_seconds"]:
        return "took too long"
    return None


def step_start(session: Session, state: State) -> State:
    """Pins policy version and dates, saves the runs row."""
    rules, version = get_policy(session, state["tenant_id"], state["at_time"])

    run = Run(
        id=state["run_id"], tenant_id=state["tenant_id"], user_id=state["user_id"],
        question=state["question"], on_date=state["on_date"], at_time=state["at_time"],
        policy_version=version, state="running", tool_calls=0,
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()

    state["policy"] = rules
    state["policy_version"] = version
    state["facts"] = new_facts()
    date_key = add_fact(state["facts"], "as_of_date", value=state["on_date"].isoformat(), source="question")
    state["on_date_fact"] = "{{" + date_key + "}}"
    state["tool_calls"] = 0
    state["max_tool_calls"] = settings.max_tool_calls
    state["max_seconds"] = settings.max_seconds
    state["started_at"] = time.time()
    state["retries"] = 0
    state["problems"] = []
    state["results"] = []
    state["conflicts"] = []
    return state


def step_understand(session: Session, state: State) -> State:
    """GEMINI call 1. Returns {"intent": one of five, "mentions": [...]}."""
    result = llm.understand(state["question"])
    state["intent"] = result["intent"]
    state["mentions"] = result["mentions"]
    return state


def step_find_names(session: Session, state: State) -> State:
    """entities.find_customer for each mention. Unclear -> state = clarify."""
    customer_ids = []
    for mention in state["mentions"]:
        result = entities.find_customer(session, state["tenant_id"], state["policy"], mention)
        if result["status"] == "found":
            customer_ids.append(result["customer_id"])
        elif result["status"] in ("unclear", "none"):
            state["state"] = "clarify"
            state["reason"] = "could not tell which customer"
            state["options"] = result.get("options", [])
            return state

    if state["intent"] in NEEDS_CUSTOMER and not customer_ids:
        state["state"] = "clarify"
        state["reason"] = "could not tell which customer"
        state["options"] = []
        return state

    state["customer_ids"] = customer_ids
    return state


def step_plan(session: Session, state: State) -> State:
    """PLANS[intent] -> a fixed list of tool calls. The model does not invent this."""
    state["plan"] = list(PLANS[state["intent"]])
    return state


def step_gather(session: Session, state: State) -> State:
    """run_tool for each planned call. Fills facts. Stops early on budget overrun."""
    customer_id = state["customer_ids"][0] if state.get("customer_ids") else None

    for tool_name, extra_args in state["plan"]:
        args = dict(extra_args)
        if customer_id is not None and "customer_id" not in args:
            args["customer_id"] = str(customer_id)

        try:
            # tools.py only ever touches keys State already declares (facts,
            # tool_calls, policy, ...) -- this cast documents that boundary
            # rather than widening tools.py's signature to plain dict.
            result = run_tool(session, state["tenant_id"], cast(dict[str, Any], state), tool_name, args)
        except BudgetExceeded:
            state["state"] = "abstain"
            state["reason"] = "used too many tool calls"
            return state

        state["results"].append({"tool": tool_name, "args": args, "result": result})

        reason = _check_budget(state)
        if reason:
            state["state"] = "abstain"
            state["reason"] = reason
            return state

    return state


def step_check(session: Session, state: State) -> State:
    """
    Do we have what we need?
    missing balance for an intent that needs one -> abstain 'not enough data'
    conflicting evidence on a single-number question -> abstain 'conflicting evidence'
    """
    by_tool = {entry["tool"]: entry["result"] for entry in state["results"]}

    if state["intent"] in NEEDS_CUSTOMER and "get_balance" not in by_tool:
        state["state"] = "abstain"
        state["reason"] = "not enough data"
        return state

    if state.get("customer_ids") and state["intent"] in ("one_customer", "as_of"):
        customer_id = state["customer_ids"][0]
        on_date = state["on_date"]
        customer_claims = claims_service.get_claims(session, state["tenant_id"], [customer_id], on_date)
        conflicts = claims_service.find_conflicts(
            session, state["tenant_id"], customer_id, customer_claims.get(customer_id, [])
        )
        if conflicts:
            state["conflicts"] = conflicts
            state["state"] = "abstain"
            state["reason"] = "conflicting evidence"
            return state

    return state


def _build_write_prompt(state: State) -> str:
    lines = [
        "You are writing a collections summary for a finance manager.",
        "Use ONLY the {{fN}} placeholders below for any figures OR dates -- never write "
        "a digit yourself, not even a date, even if one appears in the question below.",
        f"The as-of date for this question is {state['on_date_fact']} -- use that placeholder "
        "any time you need to refer to it, do not restate it as text.",
        f"Question (context only, do not copy any digit from it): {state['question']}",
        f"Question type: {state['intent']}",
        "",
        "Gathered data (numbers and dates already replaced with placeholders):",
    ]
    for entry in state["results"]:
        lines.append(f"- {entry['tool']}: {entry['result']}")

    if state.get("conflicts"):
        lines.append("")
        lines.append("Conflicts between customer claims and the ERP (report these, don't resolve them):")
        for conflict in state["conflicts"]:
            lines.append(f"- {conflict['note']}")

    return "\n".join(lines)


def step_write(session: Session, state: State) -> State:
    """GEMINI call 2. No tools attached. Returns the summary/draft/notes JSON."""
    prompt = _build_write_prompt(state)
    if state.get("problems"):
        prompt += "\n\nYour previous answer had a problem: " + "; ".join(state["problems"])
        prompt += "\nRewrite it using ONLY {{fN}} placeholders for numbers."
    state["written"] = llm.write(prompt)
    return state


def step_verify(session: Session, state: State) -> State:
    """
    check_no_digits + check_facts_exist on every piece of text the model wrote.
    Clean -> fill in numbers, state = answered.
    Dirty -> caller (graph.py) decides whether to retry step_write or abstain.
    """
    written = state["written"]
    text = " ".join([written.get("summary", ""), written.get("draft", ""), *written.get("notes", [])])

    problems = check_no_digits(text) + check_facts_exist(text, state["facts"])
    state["problems"] = problems

    if not problems:
        state["answer"] = fill_in_numbers(written.get("summary", ""), state["facts"])
        written["draft"] = fill_in_numbers(written.get("draft", ""), state["facts"])
        written["notes"] = [fill_in_numbers(note, state["facts"]) for note in written.get("notes", [])]
        state["state"] = "answered"
        state["reason"] = None

    return state


def step_finalize(session: Session, state: State) -> State:
    """
    Writes the final outcome back to the runs row. Every past answer must be
    inspectable later -- this is what makes that true.

    For a "draft" question that answered cleanly, this is also where the
    actual Outbound row gets created -- using the RENDERED text (after
    fill_in_numbers), never the model's raw {{fN}} version. A human still has
    to call /drafts/{id}/approve before anything is sent.
    """
    state["draft"] = None
    if state.get("state") == "answered" and state.get("intent") == "draft" and state.get("customer_ids"):
        draft_result = actions.make_draft(
            session, state["tenant_id"], state["customer_ids"][0], state["run_id"],
            channel="whatsapp", body=state["written"]["draft"],
        )
        state["draft"] = {"outbound_id": draft_result["outbound_id"], "idem_key": draft_result["idem_key"]}

    run = session.get(Run, state["run_id"])
    if run is not None:
        run.state = state.get("state", "failed")
        run.reason = state.get("reason")
        run.answer = state.get("answer")
        run.tool_calls = state.get("tool_calls", 0)
        run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return state
