from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer


def find_customer(session: Session, tenant_id: UUID, policy: dict,
                   mention: str, phone: str | None = None, gstin: str | None = None) -> dict:

    # Trigram match on name for everything else.
    if phone or gstin:
        conditions = []
        if phone:
            conditions.append(Customer.phone == phone)
        if gstin:
            conditions.append(Customer.gstin == gstin)
        exact = session.scalar(
            select(Customer).where(Customer.tenant_id == tenant_id, *conditions)
        )
        if exact:
            return {"status": "found", "customer_id": exact.id, "confidence": 1.0}

    similarity = func.similarity(Customer.name, mention)
    rows = session.execute(
        select(Customer.id, Customer.name, similarity.label("score"))
        .where(Customer.tenant_id == tenant_id, similarity > 0.1)
        .order_by(similarity.desc())
        .limit(3)
    ).all()

    if not rows:
        return {"status": "none"}

    best = rows[0]
    second_score = rows[1].score if len(rows) > 1 else 0.0
    gap = best.score - second_score

    if best.score >= policy["min_name_confidence"] and gap >= policy["min_name_gap"]:
        return {"status": "found", "customer_id": best.id, "confidence": best.score}

    return {
        "status": "unclear",
        "options": [{"customer_id": row.id, "name": row.name, "score": row.score} for row in rows],
    }
