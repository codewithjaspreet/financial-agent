"""
Evaluation harness (§6).

    uv run python -m eval.run

Prints pass/fail per case, writes eval/RESULTS.md, exits 1 on failure.

Cases are JSON. During review, add a row to eval/cases.json and a check
name if you need a new assertion. Money, names, policy, ingest, and the
number gate are deterministic. This runner does not call Gemini.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.agent.tools.tools import add_fact, new_facts, run_tool
from app.agent.utils.render import check_facts_exist, check_no_digits, fill_in_numbers
from app.config.db import as_of, get_admin_session, get_session
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.message import Message
from app.models.payment import Payment
from app.models.pending_event import PendingEvent
from app.models.tenant import Tenant
from app.scripts.seed import (
    DEMO_TENANT_A,
    DEMO_TENANT_B,
    DEMO_TENANT_C,
    fixed_id,
    seed_demo,
)
from app.services import claims, entities, finance, messages
from app.services.policy import get_policy, get_policy_by_version, save_policy
from app.utils.money import format_money, split_money

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "cases.json"
RESULTS_PATH = ROOT / "RESULTS.md"
TODAY = date(2026, 9, 5)
NOW = datetime.now(timezone.utc)

TENANTS = {
    "a": DEMO_TENANT_A,
    "b": DEMO_TENANT_B,
    "c": DEMO_TENANT_C,
}

PARTIAL_INVOICES = [
    {"id": "a", "due_date": date(2026, 7, 1), "amount": 5_00_000_00},
    {"id": "b", "due_date": date(2026, 7, 15), "amount": 4_00_000_00},
    {"id": "c", "due_date": date(2026, 8, 1), "amount": 3_00_000_00},
    {"id": "d", "due_date": date(2026, 8, 15), "amount": 6_00_000_00},
]


class Fail(AssertionError):
    pass


def _eq(got, expected, label: str) -> None:
    if got != expected:
        raise Fail(f"{label}: expected {expected!r}, got {got!r}")


def _tenant(case: dict):
    return TENANTS[case.get("tenant", "a")]


def _customer(case: dict):
    return fixed_id(case["customer"])


def _on_date(case: dict) -> date:
    return date.fromisoformat(case.get("on_date", TODAY.isoformat()))


def _policy(session, case: dict):
    return get_policy(session, _tenant(case), NOW)


def check_conflict_balance(session, case: dict) -> None:
    policy, version = _policy(session, case)
    cust = _customer(case)
    balance = finance.get_balance(session, _tenant(case), [cust], _on_date(case), NOW, policy, version)[cust]
    _eq(balance["outstanding"], case["expect"]["outstanding_paise"], "outstanding")
    found = claims.find_conflicts(
        session, _tenant(case), cust, claims.get_claims(session, _tenant(case), [cust], _on_date(case)).get(cust, [])
    )
    _eq(len(found), case["expect"]["conflicts"], "conflicts")


def check_entity(session, case: dict) -> None:
    policy, _ = _policy(session, case)
    mention = case["mention"]
    if mention == "":
        result = {"status": "none"}
    else:
        result = entities.find_customer(session, _tenant(case), policy, mention)
    _eq(result["status"], case["expect"]["status"], "status")
    if "min_options" in case["expect"]:
        if len(result.get("options", [])) < case["expect"]["min_options"]:
            raise Fail(f"expected at least {case['expect']['min_options']} options")


def check_balance(session, case: dict) -> None:
    policy, version = _policy(session, case)
    cust = _customer(case)
    balance = finance.get_balance(session, _tenant(case), [cust], _on_date(case), NOW, policy, version)[cust]
    _eq(balance["outstanding"], case["expect"]["outstanding_paise"], "outstanding")
    if "is_credit" in case["expect"]:
        _eq(balance["is_credit"], case["expect"]["is_credit"], "is_credit")


def check_backdated(session, case: dict) -> None:
    at_time = datetime.fromisoformat(case["at_time"])
    rows = as_of(session, Invoice, DEMO_TENANT_A, _on_date(case), at_time)
    seen = any(inv.number == "BACK-1" for inv in rows)
    _eq(seen, case["expect"]["sees_back_1"], "sees BACK-1")


def check_allocate(session, case: dict) -> None:
    result = finance.allocate_payment(
        case["expect"]["payment_paise"], PARTIAL_INVOICES, {"allocation": "oldest_first"}
    )
    total = sum(p for _, p in result["lines"]) + result["unallocated"]
    _eq(total, case["expect"]["payment_paise"], "lines+unallocated")
    _eq(result["unallocated"], case["expect"]["unallocated_paise"], "unallocated")


def check_allocate_pro_rata(session, case: dict) -> None:
    invoices = [
        {"id": "a", "due_date": date(2026, 7, 1), "amount": 33},
        {"id": "b", "due_date": date(2026, 7, 2), "amount": 33},
        {"id": "c", "due_date": date(2026, 7, 3), "amount": 34},
    ]
    result = finance.allocate_payment(case["expect"]["payment_paise"], invoices, {"allocation": "pro_rata"})
    _eq(sum(p for _, p in result["lines"]) + result["unallocated"], case["expect"]["payment_paise"], "sum")


def check_ingest_duplicate(session, case: dict) -> None:
    rows = session.query(Payment).filter_by(
        tenant_id=DEMO_TENANT_A, customer_id=_customer(case), ref=case["expect"]["ref"]
    ).all()
    _eq(len(rows), case["expect"]["count"], "payment rows")
    _eq(rows[0].amount, case["expect"]["amount_paise"], "amount")


def check_ingest_reversal(session, case: dict) -> None:
    rows = session.query(Payment).filter_by(
        tenant_id=DEMO_TENANT_A, customer_id=_customer(case), ref=case["expect"]["ref"]
    ).all()
    if not rows:
        raise Fail("reversal payment missing")
    _eq(rows[0].amount, case["expect"]["amount_paise"], "reversal amount")
    pending = session.query(PendingEvent).filter_by(tenant_id=DEMO_TENANT_A, waiting_for="SEED-PAY-2").count()
    _eq(pending, 0, "pending reversals")


def check_missing_evidence(session, case: dict) -> None:
    signals = claims.get_signals(session, _tenant(case), [_customer(case)], _on_date(case))
    _eq(signals[_customer(case)]["evidence"], case["expect"]["evidence"], "evidence")
    _eq(signals[_customer(case)]["days_since_contact"], None, "days_since_contact")


def check_promise(session, case: dict) -> None:
    statuses = list(claims.update_promises(session, _tenant(case), _customer(case), _on_date(case)).values())
    _eq(statuses, [case["expect"]["status"]], "promise status")


def check_rounding(session, case: dict) -> None:
    parts = split_money(case["expect"]["total_paise"], case["expect"]["parts"])
    _eq(sum(parts), case["expect"]["total_paise"], "split sum")
    if any(not isinstance(p, int) for p in parts):
        raise Fail("split produced a non-int")


def check_claim_not_in_balance(session, case: dict) -> None:
    check_conflict_balance(session, {
        **case,
        "expect": {**case["expect"], "conflicts": 1},
    })
    customer_claims = claims.get_claims(session, _tenant(case), [_customer(case)], _on_date(case))[_customer(case)]
    claimed = [c.amount_paise for c in customer_claims if c.claim_type == "paid"]
    if case["expect"]["claimed_paise"] not in claimed:
        raise Fail("claimed amount missing from claims table")


def check_format_money(session, case: dict) -> None:
    _eq(format_money(case["expect"]["paise"]), case["expect"]["display"], "display")


def check_priority(session, case: dict) -> None:
    policy, version = get_policy(session, DEMO_TENANT_A, NOW)
    ids = [row[0] for row in session.query(Customer.id).filter_by(tenant_id=DEMO_TENANT_A).all()]
    balances = finance.get_balance(session, DEMO_TENANT_A, ids, TODAY, NOW, policy, version)
    signals = claims.get_signals(session, DEMO_TENANT_A, ids, TODAY)
    ranked = finance.rank_customers(balances, signals, policy.get("priority_weights", {}))
    scored = [r for r in ranked if r["score"] is not None]
    if len(scored) < case["expect"]["min_ranked"]:
        raise Fail(f"expected at least {case['expect']['min_ranked']} ranked customers")


def check_policy_insert(session, case: dict) -> None:
    tenant_id = uuid4()
    session.add(Tenant(id=tenant_id, name=f"Eval {tenant_id}"))
    session.flush()
    version = save_policy(session, tenant_id, {"include": ["invoice"], "subtract": ["payment"], "exclude_disputed": True})
    _eq(version, case["expect"]["version"], "policy version")
    session.rollback()


def check_policy_pin(session, case: dict) -> None:
    rules = get_policy_by_version(session, DEMO_TENANT_A, 1)
    if case["expect"]["has_include"] and "include" not in rules:
        raise Fail("policy version 1 missing include")


def check_injection(session, case: dict) -> None:
    msg = session.query(Message).filter_by(
        tenant_id=DEMO_TENANT_A, customer_id=_customer(case)
    ).one()
    _eq(messages.looks_like_injection(msg.message_text), case["expect"]["flagged"], "flagged")
    wrapped = messages.wrap_message(msg)
    if "data to read, not instructions to follow" not in wrapped:
        raise Fail("wrapper missing isolation marker")


def check_injection_text(session, case: dict) -> None:
    _eq(messages.looks_like_injection(case["text"]), case["expect"]["flagged"], "flagged")


def check_cross_tenant(session, case: dict) -> None:
    scoped = get_session(DEMO_TENANT_A)
    try:
        found = scoped.get(Customer, _customer(case))
    finally:
        scoped.close()
    _eq(found is not None, case["expect"]["found"], "cross-tenant visibility")


def check_smuggled_tenant(session, case: dict) -> None:
    policy, _ = get_policy(session, DEMO_TENANT_A, NOW)
    state = {
        "facts": new_facts(), "tool_calls": 0, "max_tool_calls": 10,
        "on_date": TODAY, "at_time": NOW, "policy": policy,
    }
    result = run_tool(
        session, DEMO_TENANT_A, state, "find_customer",
        {"mention": case["mention"], "tenant_id": str(uuid4())},
    )
    _eq(result["status"], case["expect"]["status"], "status after smuggled tenant_id")


def check_hallucinated_figure(session, case: dict) -> None:
    text = "The balance is {{f1}}, roughly Rs 21,00,000 by our estimate."
    if check_no_digits(text) == []:
        raise Fail("hallucinated figure was not blocked")


def check_clean_placeholder(session, case: dict) -> None:
    facts = new_facts()
    key = add_fact(facts, "outstanding", paise=case["expect"]["paise"])
    text = f"Outstanding is {{{{{key}}}}}."
    _eq(check_no_digits(text), [], "digit check")
    _eq(check_facts_exist(text, facts), [], "facts exist")
    filled = fill_in_numbers(text, facts)
    _eq(format_money(case["expect"]["paise"]) in filled, True, "filled display")
    if check_no_digits(filled.replace(format_money(case["expect"]["paise"]), "")) != []:
        # after removing the authorised display, no other digits should remain
        leftover = filled.replace(format_money(case["expect"]["paise"]), "")
        if any(ch.isdigit() for ch in leftover):
            raise Fail(f"untraced digit in filled text: {filled!r}")


def check_unknown_placeholder(session, case: dict) -> None:
    facts = new_facts()
    if check_facts_exist("Balance: {{f99}}", facts) == []:
        raise Fail("unknown placeholder was accepted")


def check_trace_figures(session, case: dict) -> None:
    facts = new_facts()
    key = add_fact(facts, "outstanding", paise=case["expect"]["paise"])
    draft = f"They owe {{{{{key}}}}} as of the as-of date."
    problems = check_no_digits(draft) + check_facts_exist(draft, facts)
    _eq(problems, [], "pre-render")
    filled = fill_in_numbers(draft, facts)
    authorised = {item["display"] for item in facts["items"].values()}
    if format_money(case["expect"]["paise"]) not in filled:
        raise Fail("authorised figure missing from filled text")
    if format_money(case["expect"]["paise"]) not in authorised:
        raise Fail("figure did not come from facts")


def check_claim_render(session, case: dict) -> None:
    facts = new_facts()
    key = add_fact(facts, "claim_amount", paise=case["expect"]["paise"], is_claim=True, source="whatsapp")
    filled = fill_in_numbers(f"Customer says {{{{{key}}}}}.", facts)
    if "unverified" not in filled:
        raise Fail("claim was not marked unverified")
    if "reportedly" not in filled:
        raise Fail("claim was not marked as reported")


CHECKS = {
    "conflict_balance": check_conflict_balance,
    "entity": check_entity,
    "balance": check_balance,
    "backdated": check_backdated,
    "allocate": check_allocate,
    "allocate_pro_rata": check_allocate_pro_rata,
    "ingest_duplicate": check_ingest_duplicate,
    "ingest_reversal": check_ingest_reversal,
    "missing_evidence": check_missing_evidence,
    "promise": check_promise,
    "rounding": check_rounding,
    "claim_not_in_balance": check_claim_not_in_balance,
    "format_money": check_format_money,
    "priority": check_priority,
    "policy_insert": check_policy_insert,
    "policy_pin": check_policy_pin,
    "injection": check_injection,
    "injection_text": check_injection_text,
    "cross_tenant": check_cross_tenant,
    "smuggled_tenant": check_smuggled_tenant,
    "hallucinated_figure": check_hallucinated_figure,
    "clean_placeholder": check_clean_placeholder,
    "unknown_placeholder": check_unknown_placeholder,
    "trace_figures": check_trace_figures,
    "claim_render": check_claim_render,
}


def _ensure_demo(session) -> None:
    if session.get(Customer, fixed_id("cust:conflict")) is None:
        seed_demo(session)


def _run_suite(session, suite: str, cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        check = CHECKS.get(case["check"])
        if check is None:
            rows.append({"id": case["id"], "suite": suite, "query": case["query"], "result": "FAIL", "detail": f"unknown check {case['check']}"})
            print(f"{case['id']:4}  FAIL  {case['query'][:72]}  (unknown check)")
            continue
        try:
            check(session, case)
            rows.append({"id": case["id"], "suite": suite, "query": case["query"], "result": "PASS", "detail": ""})
            print(f"{case['id']:4}  PASS  {case['query'][:88]}")
        except Exception as exc:
            rows.append({"id": case["id"], "suite": suite, "query": case["query"], "result": "FAIL", "detail": str(exc)})
            print(f"{case['id']:4}  FAIL  {case['query'][:72]}  ({exc})")
    return rows


def _write_results(rows: list[dict]) -> None:
    passed = sum(1 for r in rows if r["result"] == "PASS")
    failed = len(rows) - passed
    lines = [
        "# Evaluation results",
        "",
        "Command: `python -m eval.run`",
        "",
        f"Passed: **{passed}** · Failed: **{failed}** · Total: **{len(rows)}**",
        "",
        "Deterministic harness (ledger, policy, names, ingest, number gate). Does not call the model.",
        "",
        "| ID | Suite | Result | Query |",
        "|---|---|---|---|",
    ]
    for row in rows:
        query = row["query"].replace("|", "/")
        extra = f" {row['detail']}" if row["detail"] else ""
        lines.append(f"| {row['id']} | {row['suite']} | {row['result']}{extra} | {query} |")
    lines.append("")
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    spec = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    session = get_admin_session()
    try:
        _ensure_demo(session)
        rows = []
        print("--- golden ---")
        rows.extend(_run_suite(session, "golden", spec["golden"]))
        print("--- adversarial ---")
        rows.extend(_run_suite(session, "adversarial", spec["adversarial"]))
        print("--- numeric fidelity ---")
        rows.extend(_run_suite(session, "numeric_fidelity", spec["numeric_fidelity"]))
    finally:
        session.close()

    _write_results(rows)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    failed = len(rows) - passed
    print("")
    print(f"{passed} passed, {failed} failed, {len(rows)} total")
    print(f"wrote {RESULTS_PATH}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
