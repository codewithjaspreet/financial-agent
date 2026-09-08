"""
The boundary between the model and real numbers (§3.3).

A tool never returns a paise value directly -- it stores the value in
`facts` and hands back a placeholder string like "{{f3}}". Gemini only ever
sees placeholders. The only code that turns a placeholder back into a real
number is render.fill_in_numbers, and it only runs after the model's text
has passed the digit check.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.run_step import RunStep
from app.services import claims, entities, finance, messages
from app.utils.money import format_money

LARGE_BALANCE_PAISE = 5_000_000_00  # Rs 5L


class BudgetExceeded(Exception):
    """Raised by run_tool when a run goes over its tool-call budget."""


def new_facts() -> dict:
    return {"items": {}, "next": 1}


def add_fact(facts: dict, kind: str, paise: int | None = None, value=None,
             is_claim: bool = False, source: str = "") -> str:
    """
    Stores one value, returns its placeholder key ("f3"). `paise` is a real
    verified amount; pass is_claim=True for a value that came from a claim,
    not the ledger -- render.py renders those as "reportedly ... (unverified)".
    """
    key = f"f{facts['next']}"
    display = format_money(paise) if paise is not None else str(value)
    facts["items"][key] = {
        "kind": kind, "paise": paise, "value": value,
        "display": display, "is_claim": is_claim, "source": source,
    }
    facts["next"] += 1
    return key


def add_date_fact(facts: dict, kind: str, value, source: str = "") -> str | None:
    """
    Dates go through the SAME placeholder mechanism as money (§3.3 is a
    bright line: no raw digit escapes without going through a tool, and a
    date written in prose is still a digit). Returns None if value is None.
    """
    if value is None:
        return None
    return add_fact(facts, kind, value=value.isoformat(), source=source)


# ---- tools the agent can call -------------------------------------------

def tool_find_customer(session: Session, tenant_id: UUID, run: dict[str, Any],
                        mention: str, phone: str | None = None) -> dict:
    """No money here -- safe to return to the model as-is."""
    return entities.find_customer(session, tenant_id, run["policy"], mention, phone=phone)


def tool_get_balance(session: Session, tenant_id: UUID, run: dict[str, Any], customer_id: str) -> dict:
    customer_uuid = UUID(customer_id)
    customer = session.get(Customer, customer_uuid)
    balance = finance.get_balance(
        session, tenant_id, [customer_uuid], run["on_date"], run["at_time"],
        run["policy"], run["policy_version"],
    )[customer_uuid]

    outstanding_key = add_fact(run["facts"], "outstanding", paise=balance["outstanding"], source="get_balance")
    overdue_key = add_fact(run["facts"], "overdue", paise=balance["overdue"], source="get_balance")

    return {
        "customer": customer.name if customer else None,
        "outstanding": "{{" + outstanding_key + "}}",
        "overdue": "{{" + overdue_key + "}}",
        "size": "large" if balance["outstanding"] > LARGE_BALANCE_PAISE else "small",
        "is_credit": balance["is_credit"],
    }


def tool_list_priority(session: Session, tenant_id: UUID, run: dict[str, Any], limit: int = 10) -> dict:
    customer_ids = list(session.scalars(
        select(Customer.id).where(Customer.tenant_id == tenant_id)
    ).all())

    balances = finance.get_balance(
        session, tenant_id, customer_ids, run["on_date"], run["at_time"],
        run["policy"], run["policy_version"],
    )
    signals = claims.get_signals(session, tenant_id, customer_ids, run["on_date"])
    ranked = finance.rank_customers(balances, signals, run["policy"].get("priority_weights", {}))

    names = {
        row.id: row.name
        for row in session.execute(
            select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
        ).all()
    }

    items = []
    for entry in ranked[:limit]:
        customer_id = entry["customer_id"]
        outstanding_key = add_fact(
            run["facts"], "outstanding",
            paise=balances[customer_id]["outstanding"], source="list_priority",
        )
        items.append({
            "customer_id": str(customer_id),
            "customer": names.get(customer_id),
            "rank": entry["rank"],
            "outstanding": "{{" + outstanding_key + "}}",
            "reasons": entry["reasons"],
        })
    return {"customers": items}


def tool_get_invoices(session: Session, tenant_id: UUID, run: dict[str, Any], customer_id: str) -> dict:
    customer_uuid = UUID(customer_id)
    invoices = finance._fetch(session, Invoice, tenant_id, [customer_uuid], run["on_date"], run["at_time"])

    items = []
    for invoice in invoices:
        amount_key = add_fact(run["facts"], "invoice_amount", paise=invoice.amount, source="get_invoices")
        due_date_key = add_fact(run["facts"], "due_date", value=invoice.due_date.isoformat(), source="get_invoices")
        items.append({
            "number": invoice.number,
            "due_date": "{{" + due_date_key + "}}",
            "amount": "{{" + amount_key + "}}",
        })
    return {"invoices": items}


def tool_get_payments(session: Session, tenant_id: UUID, run: dict[str, Any], customer_id: str) -> dict:
    customer_uuid = UUID(customer_id)
    payments = finance._fetch(session, Payment, tenant_id, [customer_uuid], run["on_date"], run["at_time"])

    items = []
    for payment in payments:
        amount_key = add_fact(run["facts"], "payment_amount", paise=payment.amount, source="get_payments")
        value_date_key = add_fact(run["facts"], "value_date", value=payment.value_date.isoformat(), source="get_payments")
        items.append({
            "ref": payment.ref,
            "value_date": "{{" + value_date_key + "}}",
            "amount": "{{" + amount_key + "}}",
        })
    return {"payments": items}


def tool_get_claims(session: Session, tenant_id: UUID, run: dict[str, Any], customer_id: str) -> dict:
    customer_uuid = UUID(customer_id)
    customer_claims = claims.get_claims(session, tenant_id, [customer_uuid], run["on_date"]).get(customer_uuid, [])

    items = []
    for claim in customer_claims:
        claim_date_key = add_date_fact(run["facts"], "claim_date", claim.claim_date, source="get_claims")
        entry = {
            "claim_type": claim.claim_type,
            "claim_date": ("{{" + claim_date_key + "}}") if claim_date_key else None,
        }
        if claim.amount_paise is not None:
            amount_key = add_fact(run["facts"], "claim_amount", paise=claim.amount_paise, is_claim=True, source="get_claims")
            entry["amount"] = "{{" + amount_key + "}}"
        items.append(entry)
    return {"claims": items}


def tool_search_messages(session: Session, tenant_id: UUID, run: dict[str, Any],
                          customer_id: str, text: str | None = None) -> dict:
    customer_uuid = UUID(customer_id)
    found = messages.search_messages(session, tenant_id, customer_uuid, text=text, since_days=180)
    return {
        "messages": [messages.wrap_message(m) for m in found[:20]],
        "has_injection_flagged": any(messages.looks_like_injection(m.message_text) for m in found),
    }


def tool_get_promises(session: Session, tenant_id: UUID, run: dict[str, Any], customer_id: str) -> dict:
    customer_uuid = UUID(customer_id)
    statuses = claims.update_promises(session, tenant_id, customer_uuid, run["on_date"])
    customer_claims = {
        claim.id: claim
        for claim in claims.get_claims(session, tenant_id, [customer_uuid], run["on_date"]).get(customer_uuid, [])
    }

    items = []
    for claim_id, status in statuses.items():
        claim = customer_claims.get(claim_id)
        if claim is None:
            continue
        promised_date_key = add_date_fact(run["facts"], "promised_date", claim.claim_date, source="get_promises")
        entry = {"status": status, "promised_date": ("{{" + promised_date_key + "}}") if promised_date_key else None}
        if claim.amount_paise is not None:
            amount_key = add_fact(run["facts"], "promise_amount", paise=claim.amount_paise, is_claim=True, source="get_promises")
            entry["amount"] = "{{" + amount_key + "}}"
        items.append(entry)
    return {"promises": items}


TOOLS = {
    "find_customer": tool_find_customer,
    "get_balance": tool_get_balance,
    "list_priority": tool_list_priority,
    "get_invoices": tool_get_invoices,
    "get_payments": tool_get_payments,
    "get_claims": tool_get_claims,
    "search_messages": tool_search_messages,
    "get_promises": tool_get_promises,
}


def run_tool(session: Session, tenant_id: UUID, run: dict[str, Any], name: str, args: dict) -> dict:
    """
    Where tenant isolation and the tool-call budget are actually enforced.
    `tenant_id` comes from the caller (ultimately the JWT), never from `args`.
    """
    if name not in TOOLS:
        return {"error": "unknown tool"}

    run["tool_calls"] = run.get("tool_calls", 0) + 1
    if run["tool_calls"] > run["max_tool_calls"]:
        raise BudgetExceeded("used too many tool calls")

    if "tenant_id" in args:
        args = {k: v for k, v in args.items() if k != "tenant_id"}  # model tried to pass one -- dropped

    result = TOOLS[name](session, tenant_id, run, **args)

    if run.get("run_id"):
        session.add(RunStep(
            run_id=run["run_id"], step_number=run["tool_calls"], state="tool_call",
            tool_name=name, tool_args=args, tool_result=result,
            started_at=datetime.now(timezone.utc),
        ))

    return result
