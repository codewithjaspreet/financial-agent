from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graph.graph import build_graph
from app.config.config import settings
from app.config.dependencies import get_current_user, get_db_session
from app.models.agent_run import Run
from app.models.outbound import Outbound
from app.models.run_step import RunStep
from app.services import actions, auth, claims, entities, finance, ingest
from app.services.auth import User
from app.services.policy import get_policy, save_policy
from app.utils.money import format_money

router = APIRouter()

# One shared fake provider for the whole process. Its mode is a config
# value (settings.provider_mode) so the live review can flip it and demo
# the timeout -> unknown -> reconcile path without touching code.
_provider = actions.FakeProvider(mode=settings.provider_mode)


# ---- auth ----------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginRequest) -> dict:
    try:
        token = auth.login(body.email, body.password)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"token": token}


# ---- the agent ------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    on_date: date | None = None
    at_time: datetime | None = None


@router.post("/ask")
def ask(body: AskRequest, user: User = Depends(get_current_user),
        session: Session = Depends(get_db_session)) -> dict:
    graph = build_graph(session)
    initial_state = {
        "run_id": uuid4(),
        "tenant_id": user.tenant_id,
        "user_id": user.user_id,
        "question": body.question,
        "on_date": body.on_date or datetime.now(timezone.utc).date(),
        "at_time": body.at_time or datetime.now(timezone.utc),
    }
    final_state = graph.invoke(initial_state)
    return {
        "run_id": str(final_state["run_id"]),
        "state": final_state.get("state"),
        "reason": final_state.get("reason"),
        "answer": final_state.get("answer"),
        "options": final_state.get("options"),
        "conflicts": final_state.get("conflicts"),
        "policy_version": final_state.get("policy_version"),
        "on_date": final_state["on_date"].isoformat(),
        "draft": final_state.get("draft"),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, session: Session = Depends(get_db_session)) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "run_id": str(run.id),
        "question": run.question,
        "state": run.state,
        "reason": run.reason,
        "answer": run.answer,
        "policy_version": run.policy_version,
        "tool_calls": run.tool_calls,
        "on_date": run.on_date.isoformat() if run.on_date else None,
    }


@router.get("/runs/{run_id}/trace")
def get_run_trace(run_id: UUID, session: Session = Depends(get_db_session)) -> dict:
    """Every tool call this run made, in order -- the full provenance of the answer."""
    if session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")

    steps = session.scalars(
        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_number)
    ).all()
    return {
        "run_id": str(run_id),
        "steps": [
            {
                "step_number": step.step_number,
                "tool_name": step.tool_name,
                "tool_args": step.tool_args,
                "tool_result": step.tool_result,
                "started_at": step.started_at.isoformat(),
            }
            for step in steps
        ],
    }


# ---- deterministic reads, NO Gemini in this path --------------------------

@router.get("/customers/{customer_id}/balance")
def get_customer_balance(customer_id: UUID, on_date: date | None = None,
                          at_time: datetime | None = None,
                          user: User = Depends(get_current_user),
                          session: Session = Depends(get_db_session)) -> dict:
    on_date = on_date or datetime.now(timezone.utc).date()
    at_time = at_time or datetime.now(timezone.utc)
    policy, policy_version = get_policy(session, user.tenant_id, at_time)

    balance = finance.get_balance(session, user.tenant_id, [customer_id], on_date, at_time, policy, policy_version)
    if customer_id not in balance:
        raise HTTPException(status_code=404, detail="customer not found")

    result = balance[customer_id]
    return {
        **{k: v for k, v in result.items() if k not in ("outstanding", "overdue")},
        "outstanding": {"paise": result["outstanding"], "display": format_money(result["outstanding"])},
        "overdue": {"paise": result["overdue"], "display": format_money(result["overdue"])},
        "policy_version": policy_version,
        "on_date": on_date.isoformat(),
    }


@router.get("/customers/{customer_id}/evidence")
def get_customer_evidence(customer_id: UUID, on_date: date | None = None,
                           user: User = Depends(get_current_user),
                           session: Session = Depends(get_db_session)) -> dict:
    on_date = on_date or datetime.now(timezone.utc).date()
    customer_claims = claims.get_claims(session, user.tenant_id, [customer_id], on_date).get(customer_id, [])
    conflicts = claims.find_conflicts(session, user.tenant_id, customer_id, customer_claims)

    return {
        "claims": [
            {"claim_type": c.claim_type, "claim_date": c.claim_date.isoformat() if c.claim_date else None,
             "amount": {"paise": c.amount_paise, "display": format_money(c.amount_paise)} if c.amount_paise else None}
            for c in customer_claims
        ],
        "conflicts": conflicts,
    }


@router.get("/priority")
def get_priority(limit: int = 10, on_date: date | None = None,
                  user: User = Depends(get_current_user),
                  session: Session = Depends(get_db_session)) -> dict:
    """Pure Python ranking -- no Gemini call needed just to sort a list."""
    from app.models.customer import Customer

    on_date = on_date or datetime.now(timezone.utc).date()
    at_time = datetime.now(timezone.utc)
    policy, policy_version = get_policy(session, user.tenant_id, at_time)

    customer_ids = list(session.scalars(
        select(Customer.id).where(Customer.tenant_id == user.tenant_id)
    ).all())
    balances = finance.get_balance(session, user.tenant_id, customer_ids, on_date, at_time, policy, policy_version)
    signals = claims.get_signals(session, user.tenant_id, customer_ids, on_date)
    ranked = finance.rank_customers(balances, signals, policy.get("priority_weights", {}))

    for entry in ranked:
        cid = entry["customer_id"]
        if cid in balances:
            entry["outstanding"] = {"paise": balances[cid]["outstanding"], "display": format_money(balances[cid]["outstanding"])}

    return {"customers": ranked[:limit], "policy_version": policy_version}


# ---- admin -----------------------------------------------------------------

class PolicyRequest(BaseModel):
    rules: dict


@router.post("/admin/policy")
def create_policy(body: PolicyRequest, user: User = Depends(get_current_user),
                   session: Session = Depends(get_db_session)) -> dict:
    """Adding a tenant's rule is this one INSERT -- no deployment, no code change."""
    version = save_policy(session, user.tenant_id, body.rules)
    return {"policy_version": version}


class WebhookPaymentRequest(BaseModel):
    source: str
    event_id: str
    event_type: str
    payload: dict


@router.post("/webhook/payment")
def webhook_payment(body: WebhookPaymentRequest, user: User = Depends(get_current_user),
                     session: Session = Depends(get_db_session)) -> dict:
    """
    Simplification: a real bank/ERP webhook would authenticate via a
    per-tenant signed URL or secret, not a user JWT. tenant_id still comes
    from the token, never the body -- same rule as every other route.
    """
    return ingest.receive_event(session, user.tenant_id, body.source, body.event_id, body.event_type, body.payload)


# ---- drafts / outbound ------------------------------------------------------

@router.post("/drafts/{outbound_id}/approve")
def approve_draft(outbound_id: UUID, idempotency_key: str = Header(..., alias="Idempotency-Key"),
                   user: User = Depends(get_current_user),
                   session: Session = Depends(get_db_session)) -> dict:
    draft = session.get(Outbound, outbound_id)
    if draft is None or draft.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="draft not found")
    if draft.idempotency_key != idempotency_key:
        raise HTTPException(status_code=409, detail="Idempotency-Key does not match this draft")

    return actions.approve_and_send(session, user.tenant_id, idempotency_key, _provider)


@router.get("/outbound/{outbound_id}")
def get_outbound(outbound_id: UUID, user: User = Depends(get_current_user),
                  session: Session = Depends(get_db_session)) -> dict:
    draft = session.get(Outbound, outbound_id)
    if draft is None or draft.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "outbound_id": str(draft.id),
        "customer_id": str(draft.customer_id),
        "channel": draft.channel,
        "state": draft.status,
        "attempts": draft.attempts,
        "provider_id": draft.provider_id,
    }


@router.post("/admin/reconcile")
def admin_reconcile(session: Session = Depends(get_db_session)) -> dict:
    """Resolves every send stuck in 'unknown' by actually asking the provider what happened."""
    return actions.reconcile(session, _provider)
