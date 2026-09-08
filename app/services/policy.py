from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.policy import Policy

# The only keys a policy row is allowed to contain.
# Adding a tenant with a new rule means adding a key here, not an if-branch in finance.py.
KNOWN_KEYS = {
    "include", "subtract", "net_advances", "advances_must_be_approved",
    "exclude_disputed", "exclude_below_paise", "exclude_tags", "grace_days",
    "aging_buckets", "allocation", "priority_weights",
    "min_name_confidence", "min_name_gap",
}


def check_policy(rules: dict) -> list[str]:
    """Unknown key = problem. Empty list = fine."""
    return [f"unknown key: {key}" for key in rules if key not in KNOWN_KEYS]


def get_policy(session: Session, tenant_id: UUID, at_time: datetime) -> tuple[dict, int]:
    """The policy in force at at_time: latest version whose effective_from <= at_time."""
    policy = session.scalar(
        select(Policy)
        .where(Policy.tenant_id == tenant_id, Policy.effective_from <= at_time)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    if policy is None:
        raise ValueError(f"no policy in force for tenant {tenant_id} at {at_time}")
    return policy.rules, policy.version


def get_policy_by_version(session: Session, tenant_id: UUID, version: int) -> dict:
    """Used when recomputing an old answer with the policy that produced it."""
    policy = session.scalar(
        select(Policy).where(Policy.tenant_id == tenant_id, Policy.version == version)
    )
    if policy is None:
        raise ValueError(f"tenant {tenant_id} has no policy version {version}")
    return policy.rules


def save_policy(session: Session, tenant_id: UUID, rules: dict) -> int:
    """Validates keys, then inserts the next version. Never edits an existing version."""
    problems = check_policy(rules)
    if problems:
        raise ValueError(f"invalid policy: {problems}")

    latest = session.scalar(
        select(Policy)
        .where(Policy.tenant_id == tenant_id)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    next_version = (latest.version + 1) if latest else 1

    now = datetime.now(timezone.utc)
    policy = Policy(
        tenant_id=tenant_id,
        version=next_version,
        rules=rules,
        effective_from=now,
        created_at=now,
    )
    session.add(policy)
    session.flush()
    return next_version
