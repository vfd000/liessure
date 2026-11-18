"""Zitadel (OIDC) sign-in, ported from hgss-coop's host/sso.py.

Authorization-code flow with PKCE (S256) and a confidential client
(client_secret_basic). Identity comes from the userinfo endpoint over TLS, so
no JWT library is needed. Access is an explicit allowlist of Zitadel subs,
checked at sign-in and again on every request (removing a sub ends its live
sessions). Sessions are random tokens in an httpOnly cookie; only their
sha256 is stored.

Off unless ZITADEL_CLIENT_ID, ZITADEL_CLIENT_SECRET and ZITADEL_USER_SUBS are
all set: then everyone is the single owner, as before multi-user.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .config import Settings
from .store import Store

log = logging.getLogger("liessure.auth")

COOKIE = "liessure_session"
SESSION_TTL = 30 * 24 * 3600
PENDING_TTL = 600
OWNER = "owner"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SSO:
    def __init__(self, s: Settings, store: Store):
        self.s = s
        self.store = store
        self.issuer = s.zitadel_issuer.rstrip("/")
        self.redirect_uri = s.public_url.rstrip("/") + "/auth/callback"
        self.allowed = set(s.zitadel_user_subs)
        self.pending: dict[str, tuple[str, float]] = {}  # state -> (pkce verifier, created)
        self.meta: dict[str, Any] | None = None

    def user_key(self, sub: str) -> str:
        """The owner's data predates multi-user and is keyed "owner"; everyone else by their sub."""
        return OWNER if sub == self.s.owner_sub else sub

    async def _discover(self, client: httpx.AsyncClient) -> dict[str, Any]:
        if self.meta is None:
            r = await client.get(self.issuer + "/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            self.meta = r.json()
        return self.meta

    async def login_url(self, client: httpx.AsyncClient) -> str:
        meta = await self._discover(client)
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v[1] < PENDING_TTL}
        state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
        self.pending[state] = (verifier, now)
        q = {"response_type": "code", "client_id": self.s.zitadel_client_id, "redirect_uri": self.redirect_uri,
             "scope": "openid profile email", "state": state, "nonce": nonce,
             "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
        return meta["authorization_endpoint"] + "?" + urlencode(q)

    async def callback(self, client: httpx.AsyncClient, params: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Returns (cookie token, session) or raises PermissionError / ValueError."""
        if params.get("error"):
            raise PermissionError(params["error"])
        pend = self.pending.pop(params.get("state", ""), None)
        if not pend or time.time() - pend[1] > PENDING_TTL:
            raise ValueError("sign-in expired or wasn't started here; try again")
        meta = await self._discover(client)
        basic = base64.b64encode(f"{quote(self.s.zitadel_client_id)}:{quote(self.s.zitadel_client_secret)}".encode()).decode()
        r = await client.post(meta["token_endpoint"], timeout=15, headers={"Authorization": "Basic " + basic}, data={
            "grant_type": "authorization_code", "code": params.get("code", ""), "redirect_uri": self.redirect_uri,
            "code_verifier": pend[0]})
        r.raise_for_status()
        r2 = await client.get(meta["userinfo_endpoint"], timeout=15,
                              headers={"Authorization": "Bearer " + r.json()["access_token"]})
        r2.raise_for_status()
        info = r2.json()
        sub = str(info.get("sub", ""))
        if sub not in self.allowed:
            log.warning("sign-in refused for sub %s (%s)", sub, info.get("preferred_username"))
            raise PermissionError("this account isn't allowed here")
        token = secrets.token_urlsafe(32)
        session = {"sub": sub, "user": self.user_key(sub),
                   "name": info.get("given_name") or info.get("name") or info.get("preferred_username") or "",
                   "exp": time.time() + SESSION_TTL}
        self.store.put_session(token_hash(token), **session)
        log.info("signed in: %s", info.get("preferred_username") or sub)
        return token, session

    def session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        sess = self.store.get_session(token_hash(token))
        if sess and sess["exp"] > time.time() and sess["sub"] in self.allowed:
            return sess
        return None

    def logout(self, token: str | None) -> None:
        if token:
            self.store.delete_session(token_hash(token))
