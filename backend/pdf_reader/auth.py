"""Cloudflare Access JWT verification.

Cloudflare Access sits in front of pdf2audio.ca. Every request that makes it to the
backend carries:
  • `Cf-Access-Jwt-Assertion` header — signed JWT
  • `Cf-Access-Authenticated-User-Email` — convenience header (NOT trusted alone —
    can be spoofed if the request bypasses Cloudflare)

The JWT is signed by Cloudflare with keys exposed at
    https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
We verify the signature + audience tag, then read the user's verified email from
the `email` claim.

Configuration (all via env vars):
  PDF_READER_CF_TEAM      — Cloudflare team slug (e.g. "tintutor")
  PDF_READER_CF_AUD       — Access application's audience tag (from the app overview)
  PDF_READER_DEV_USER     — when set, bypasses JWT verification and uses this as the user_id
                            (use only on the laptop for local dev — NEVER in production)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from fastapi import HTTPException, Request
from jose import jwt

log = logging.getLogger(__name__)

TEAM_DOMAIN = os.environ.get("PDF_READER_CF_TEAM", "").strip()
POLICY_AUD = os.environ.get("PDF_READER_CF_AUD", "").strip()
DEV_USER = os.environ.get("PDF_READER_DEV_USER", "").strip().lower()

_jwks_cache: dict[str, Any] = {"keys": [], "fetched_at": 0.0}
_JWKS_TTL_S = 3600.0


def _certs_url() -> str:
    return f"https://{TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs"


def _jwks() -> list[dict]:
    now = time.time()
    if now - _jwks_cache["fetched_at"] < _JWKS_TTL_S and _jwks_cache["keys"]:
        return _jwks_cache["keys"]
    if not TEAM_DOMAIN:
        return []
    try:
        resp = requests.get(_certs_url(), timeout=8)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = now
        return keys
    except Exception as e:
        log.warning("Failed to refresh CF Access JWKS: %s", e)
        return _jwks_cache["keys"]


def verify_jwt(token: str) -> dict:
    """Return the verified claims dict; raise on any failure."""
    if not POLICY_AUD:
        raise ValueError("PDF_READER_CF_AUD not configured")
    keys = _jwks()
    if not keys:
        raise ValueError("no Cloudflare JWKS available")
    # python-jose accepts the full JWKS dict as the key argument
    return jwt.decode(
        token,
        {"keys": keys},
        algorithms=["RS256"],
        audience=POLICY_AUD,
        issuer=f"https://{TEAM_DOMAIN}.cloudflareaccess.com",
    )


def _extract_jwt(request: Request) -> str | None:
    """JWT can arrive two ways: as the Cf-Access-Jwt-Assertion header (CF injects it on
    protected destinations) or as the CF_Authorization cookie (set on the user's browser
    after sign-in, sent on every request to the protected domain — including paths the
    application does NOT protect, like our public landing page)."""
    token = request.headers.get("cf-access-jwt-assertion") or request.headers.get(
        "Cf-Access-Jwt-Assertion"
    )
    if token:
        return token
    return request.cookies.get("CF_Authorization")


def maybe_get_user(request: Request) -> str | None:
    """Returns the user's verified email if a valid JWT is present anywhere, None otherwise.
    Used by public endpoints like /whoami that want to render different UI for signed-in vs
    anonymous users without 401-ing the page."""
    if DEV_USER:
        return DEV_USER
    token = _extract_jwt(request)
    if not token:
        return None
    try:
        claims = verify_jwt(token)
    except Exception:
        return None
    email = (claims.get("email") or "").lower()
    return email or None


def require_user(request: Request) -> str:
    """FastAPI dependency. Returns the verified user email (lowercased).

    Dev escape hatch: PDF_READER_DEV_USER bypasses JWT verification.
    """
    if DEV_USER:
        return DEV_USER
    token = _extract_jwt(request)
    if not token:
        raise HTTPException(401, "Missing Cloudflare Access JWT")
    try:
        claims = verify_jwt(token)
    except Exception as e:
        raise HTTPException(401, f"Invalid Access JWT: {e}")
    email = (claims.get("email") or "").lower()
    if not email:
        raise HTTPException(401, "JWT missing email claim")
    return email
