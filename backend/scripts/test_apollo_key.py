#!/usr/bin/env python3
"""Diagnose Apollo API key + plan access (does not print the key)."""
from __future__ import annotations

import sys

import httpx

from app.core.config import get_settings

get_settings.cache_clear()
settings = get_settings()

key = (settings.apollo_api_key or "").strip()
if not key:
    print("FAIL  APOLLO_API_KEY is empty in backend/.env")
    sys.exit(1)

print(f"OK    Key loaded ({len(key)} characters, no surrounding quotes expected)")
headers = {"x-api-key": key, "Content-Type": "application/json"}
base = (settings.apollo_base_url or "https://api.apollo.io/api/v1").rstrip("/")

checks = [
    (
        "auth health (Apollo's key test)",
        "GET",
        f"{base}/auth/health",
        None,
    ),
    (
        "org search (discovery)",
        "POST",
        f"{base}/mixed_companies/search",
        {"page": 1, "per_page": 1, "q_organization_keyword_tags": ["Healthcare Services"]},
    ),
    (
        "people search",
        "POST",
        f"{base}/mixed_people/api_search",
        {"page": 1, "per_page": 1},
    ),
    (
        "usage stats (master key)",
        "GET",
        f"{base}/usage_stats/api_usage_stats",
        None,
    ),
]

ok = False
with httpx.Client(timeout=25.0, trust_env=False) as client:
    for label, method, url, body in checks:
        if method == "GET":
            resp = client.get(url, headers=headers)
        else:
            resp = client.post(url, headers=headers, json=body)
        snippet = (resp.text or "").strip().replace("\n", " ")[:120]
        print(f"\n{label}")
        print(f"  HTTP {resp.status_code}")
        print(f"  {snippet}")
        if label.startswith("auth health") and resp.status_code == 200:
            try:
                logged_in = resp.json().get("is_logged_in")
                if logged_in is True:
                    print("  is_logged_in: true (key is valid per Apollo)")
                    ok = True
                else:
                    print("  is_logged_in: false → Apollo does NOT recognize this key")
            except Exception:
                pass
        elif resp.status_code == 200:
            ok = True

print()
if ok:
    print("PASS  Apollo accepted the API key for at least one endpoint.")
    print("      Run discovery again in the UI.")
    sys.exit(0)

print("FAIL  Apollo rejected this API key or plan.")
print()
print("Checklist:")
print("  1. Apollo → Settings → Integrations → API Keys → Create new key")
print("  2. Toggle 'Set as master key' ON")
print("  3. Copy the FULL key when shown (only shown once)")
print("  4. Paste into backend/.env as APOLLO_API_KEY=... (no quotes, no spaces)")
print("  5. Restart backend: uvicorn app.main:app --port 8001 --reload")
print()
print("If HTTP 403 on search: Free plan may not include API search — upgrade to Basic ($49+) or Professional.")
print("If HTTP 401 everywhere: Key is wrong, revoked, or incomplete — create a fresh master key.")
sys.exit(1)
