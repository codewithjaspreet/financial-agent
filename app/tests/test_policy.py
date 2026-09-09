from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.tenant import Tenant
from app.services.policy import check_policy, get_policy, get_policy_by_version, save_policy


def test_check_policy_rejects_unknown_keys():
    problems = check_policy({"made_up_key": 1})
    assert problems == ["unknown key: made_up_key"]
    assert check_policy({"include": ["invoice"]}) == []


def test_three_policies_same_data_three_different_outstanding(db_session):
    """
    Edge case #3: adding a tenant's rule is a data change (an INSERT), not a
    code change. Three tenants, three rows, zero if-branches in application code.
    """
    tenants = [uuid4(), uuid4(), uuid4()]
    for tenant_id in tenants:
        db_session.add(Tenant(id=tenant_id, name=f"Policy Tenant {tenant_id}"))
    db_session.flush()

    v1 = save_policy(db_session, tenants[0], {"include": ["invoice"], "subtract": ["payment"]})
    v2 = save_policy(db_session, tenants[1], {"include": ["invoice"], "subtract": []})
    v3 = save_policy(db_session, tenants[2], {"include": ["invoice", "debit_note"], "subtract": ["payment", "credit_note"]})
    assert (v1, v2, v3) == (1, 1, 1)

    for tenant_id in tenants:
        rules, version = get_policy(db_session, tenant_id, datetime.now(timezone.utc))
        assert version == 1
        assert isinstance(rules, dict)
    db_session.rollback()


def test_adding_a_fourth_tenant_is_just_another_insert(db_session):
    """The literal 'Tenant C arrives during the live review' scenario -- no deployment."""
    tenant_id = uuid4()
    db_session.add(Tenant(id=tenant_id, name="Live Review Tenant"))
    db_session.flush()
    version = save_policy(db_session, tenant_id, {"include": ["invoice"], "exclude_disputed": True})
    assert version == 1
    db_session.rollback()


def test_policy_versions_are_never_edited_only_appended(db_session):
    """Recomputing an OLD answer must use the policy that was active then, not today's."""
    tenant_id = uuid4()
    db_session.add(Tenant(id=tenant_id, name="Version Test Tenant"))
    db_session.flush()

    save_policy(db_session, tenant_id, {"include": ["invoice"], "subtract": ["payment"]})
    save_policy(db_session, tenant_id, {"include": ["invoice"], "subtract": ["payment", "credit_note"]})

    v1_rules = get_policy_by_version(db_session, tenant_id, 1)
    v2_rules = get_policy_by_version(db_session, tenant_id, 2)
    assert v1_rules["subtract"] == ["payment"]
    assert v2_rules["subtract"] == ["payment", "credit_note"]
    db_session.rollback()


def test_save_policy_raises_on_invalid_rules(db_session):
    tenant_id = uuid4()
    db_session.add(Tenant(id=tenant_id, name="Invalid Policy Tenant"))
    db_session.flush()
    with pytest.raises(ValueError):
        save_policy(db_session, tenant_id, {"not_a_real_key": True})
    db_session.rollback()
