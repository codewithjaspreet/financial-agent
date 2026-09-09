"""
NOTE: these tests call the real Gemini API (no stub/replay fixture built yet
-- a known, deliberate cut given the time available; see limitations). Kept
to the minimum needed to prove each branch of the state machine.
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.agent.graph.graph import build_graph
from app.agent.tools.tools import BudgetExceeded, new_facts, run_tool
from app.agent.utils.render import check_facts_exist, check_no_digits
from app.models.claims import Claim
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.models.user import User
from app.services.policy import save_policy
from app.tests.conftest import dates

DEFAULT_POLICY = {
    "include": ["invoice"], "subtract": ["payment", "credit_note"],
    "net_advances": True, "advances_must_be_approved": True,
    "exclude_disputed": True, "exclude_below_paise": 0, "exclude_tags": [],
    "grace_days": 0, "aging_buckets": [30, 60, 90], "allocation": "oldest_first",
    "priority_weights": {"overdue_amount": 0.4, "overdue_days": 0.25,
                          "broken_promise": 0.2, "open_dispute": -0.3, "no_contact": 0.15},
    "min_name_confidence": 0.75, "min_name_gap": 0.15,
}


def _setup(db_session):
    tenant = Tenant(id=uuid4(), name=f"Agent Test {uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    user = User(id=uuid4(), tenant_id=tenant.id, email=f"u{uuid4()}@test.com",
                password_hash="x", role="manager")
    db_session.add(user)
    db_session.flush()
    save_policy(db_session, tenant.id, DEFAULT_POLICY)
    db_session.commit()
    return tenant, user


# ---- pure, no-Gemini tests -------------------------------------------------

def test_digit_check_catches_a_hallucinated_number():
    """Edge case #9: a tool returns 18.4L and the model writes 21L -- must never reach the user."""
    facts = new_facts()
    text = "The outstanding balance is {{f1}}, or about Rs 21,00,000 depending on how you count."
    problems = check_no_digits(text)
    assert problems != [], "a raw number outside a placeholder must be caught"


def test_digit_check_passes_clean_placeholder_text():
    facts = new_facts()
    from app.agent.tools.tools import add_fact
    key = add_fact(facts, "outstanding", paise=42_00_000_00)
    text = f"The outstanding balance is {{{{{key}}}}}."
    assert check_no_digits(text) == []
    assert check_facts_exist(text, facts) == []


def test_check_facts_exist_rejects_unknown_placeholder():
    facts = new_facts()
    assert check_facts_exist("Balance: {{f99}}", facts) != []


def test_tool_call_budget_stops_the_agent(db_session):
    tenant, user = _setup(db_session)
    state = {"facts": new_facts(), "tool_calls": 0, "max_tool_calls": 1,
              "on_date": date(2026, 9, 5), "at_time": datetime.now(timezone.utc), "policy": DEFAULT_POLICY}

    run_tool(db_session, tenant.id, state, "find_customer", {"mention": "nobody"})
    with pytest.raises(BudgetExceeded):
        run_tool(db_session, tenant.id, state, "find_customer", {"mention": "nobody"})


def test_tool_smuggled_tenant_id_is_ignored(db_session):
    """§3.4: a model-supplied tenant_id in tool args must be dropped, never honored."""
    tenant, user = _setup(db_session)
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Real Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    db_session.add(customer)
    db_session.flush()

    state = {"facts": new_facts(), "tool_calls": 0, "max_tool_calls": 10,
              "on_date": date(2026, 9, 5), "at_time": datetime.now(timezone.utc), "policy": DEFAULT_POLICY}
    result = run_tool(db_session, tenant.id, state, "find_customer",
                       {"mention": "Real Co", "tenant_id": str(uuid4())})
    assert result["status"] == "found"
    assert result["customer_id"] == customer.id


# ---- full pipeline tests (real Gemini calls) -------------------------------

def test_ambiguous_name_ends_in_clarify_not_a_guess(db_session):
    """Edge case #2, run through the actual compiled graph."""
    tenant, user = _setup(db_session)
    for name in ["ABC Traders", "ABC Trading Co", "ABC Suppliers"]:
        db_session.add(Customer(id=uuid4(), tenant_id=tenant.id, name=name, credit_limit=0,
                                 terms_days=30, **dates(date(2026, 1, 1))))
    db_session.flush()
    db_session.commit()

    graph = build_graph(db_session)
    final = graph.invoke({
        "run_id": uuid4(), "tenant_id": tenant.id, "user_id": user.id,
        "question": "ABC payment done, please update",
        "on_date": date(2026, 9, 5), "at_time": datetime.now(timezone.utc),
    })
    assert final["state"] == "clarify"
    assert final["reason"] == "could not tell which customer"
    assert len(final.get("options", [])) >= 2


def test_conflicting_evidence_ends_in_abstain(db_session):
    """Edge case #1, run through the actual compiled graph."""
    tenant, user = _setup(db_session)
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Conflict Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    db_session.add(customer)
    db_session.flush()
    invoice = Invoice(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="INV-1", issue_date=date(2026, 7, 1), due_date=date(2026, 8, 1),
                       amount=42_00_000_00, invoice_type="invoice", **dates(date(2026, 7, 1)))
    claim = Claim(tenant_id=tenant.id, customer_id=customer.id, claim_type="paid",
                  amount_paise=8_00_000_00, claim_date=date(2026, 8, 20), confidence=70,
                  created_at=datetime.now(timezone.utc))
    db_session.add_all([invoice, claim])
    db_session.flush()
    db_session.commit()

    graph = build_graph(db_session)
    final = graph.invoke({
        "run_id": uuid4(), "tenant_id": tenant.id, "user_id": user.id,
        "question": "What did Conflict Co owe as of 1 September?",
        "on_date": date(2026, 9, 5), "at_time": datetime.now(timezone.utc),
    })
    assert final["state"] == "abstain"
    assert final["reason"] == "conflicting evidence"
