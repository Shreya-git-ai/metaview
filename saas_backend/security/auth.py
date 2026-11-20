"""Authentication & tenant authorisation.

* Collectors authenticate with a per-collector bearer token (only its SHA-256 is stored).
* Dashboard users authenticate with a signed JWT (HS256).
* `TenantContext` is resolved server-side from the authenticated principal. Tenant admins are
  pinned to their own tenant; any attempt to address another tenant is rejected and audited.
  Every repository query filters on `ctx.tenant_id` — the UI is never trusted for isolation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from saas_backend.database.db import utcnow
from saas_backend.models.models import AuditLog, Collector, IngestAudit, Tenant, User

JWT_ALG = "HS256"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_collector_token() -> str:
    return "col_" + secrets.token_urlsafe(32)


def hash_password(pw: str, salt: bytes | None = None, iterations: int = 200_000) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
    return hmac.compare_digest(dk.hex(), h)


def issue_jwt(user: User, secret: str, hours: int = 12) -> str:
    now = utcnow()
    return jwt.encode({"sub": user.username, "role": user.role, "tenant_id": user.tenant_id,
                       "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=hours)).timestamp())},
                      secret, algorithm=JWT_ALG)


# ----------------------------------------------------------------------- dependencies
def get_db(request: Request):
    db: Session = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_collector(request: Request, authorization: str | None = Header(None),
                      db: Session = Depends(get_db)) -> Collector:
    token = _bearer(authorization)
    col = db.scalar(select(Collector).where(Collector.token_hash == hash_token(token))) if token else None
    if col is None:
        db.add(IngestAudit(endpoint=request.url.path, outcome="auth_failed",
                           detail="missing or invalid collector token"))
        db.commit()
        raise HTTPException(401, "invalid collector credentials")
    return col


@dataclass
class Principal:
    username: str
    role: str
    tenant_id: str | None


def require_user(request: Request, authorization: str | None = Header(None)) -> Principal:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(token, request.app.state.settings.jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")
    return Principal(claims["sub"], claims["role"], claims.get("tenant_id"))


@dataclass
class TenantContext:
    tenant_id: str
    principal: Principal


def tenant_context(principal: Principal = Depends(require_user), db: Session = Depends(get_db),
                   x_tenant_id: str | None = Header(None)) -> TenantContext:
    if principal.role == "tenant_admin":
        if x_tenant_id and x_tenant_id != principal.tenant_id:
            db.add(AuditLog(actor=principal.username, tenant_id=principal.tenant_id, action="cross_tenant_denied",
                            target=x_tenant_id, detail=json.dumps({"via": "X-Tenant-Id header"})))
            db.commit()
            raise HTTPException(403, "tenant access denied")
        return TenantContext(principal.tenant_id, principal)
    if principal.role == "platform_operator":
        tid = x_tenant_id or db.scalar(select(Tenant.id).order_by(Tenant.id))
        if not tid or db.get(Tenant, tid) is None:
            raise HTTPException(404, "unknown tenant")
        return TenantContext(tid, principal)
    raise HTTPException(403, "role not permitted")


def audit(db: Session, ctx: TenantContext | None, action: str, target: str | None = None, detail: dict | None = None,
          actor: str | None = None) -> None:
    db.add(AuditLog(actor=actor or (ctx.principal.username if ctx else "system"),
                    tenant_id=ctx.tenant_id if ctx else None, action=action, target=target,
                    detail=json.dumps(detail or {})))
