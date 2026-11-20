"""Idempotent seeding of tenants, synthetic demo users, enrollment tokens and default thresholds."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.config import DEMO_USERS, TENANTS, Settings
from saas_backend.models.models import EnrollmentToken, Tenant, Threshold, User
from saas_backend.security.auth import hash_password, hash_token
from shared.health import DEFAULT_THRESHOLDS


def seed(db: Session, settings: Settings) -> None:
    for t in TENANTS:
        if db.get(Tenant, t["id"]) is None:
            db.add(Tenant(**t))
    db.flush()
    for u in DEMO_USERS:
        if db.scalar(select(User).where(User.username == u["username"])) is None:
            db.add(User(username=u["username"], display_name=u["display"], role=u["role"], tenant_id=u["tenant_id"],
                        password_hash=hash_password(settings.demo_password)))
    for tenant_id, token in settings.enrollment_tokens.items():
        h = hash_token(token)
        if db.get(EnrollmentToken, h) is None:
            db.add(EnrollmentToken(token_hash=h, tenant_id=tenant_id))
    for t in TENANTS:
        for metric, (a, b, c) in DEFAULT_THRESHOLDS.items():
            if db.get(Threshold, (t["id"], metric)) is None:
                db.add(Threshold(tenant_id=t["id"], metric=metric, excellent_below=a, good_below=b, fair_below=c))
    db.commit()
