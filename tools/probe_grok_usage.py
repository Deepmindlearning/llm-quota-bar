"""一次性探针：找 Grok Build 订阅用量端点。不打印任何凭据。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

AUTH = Path.home() / ".grok" / "auth.json"
BASE = "https://cli-chat-proxy.grok.com"

data = json.load(open(AUTH, encoding="utf-8"))
acct = next(iter(data.values()))
token = acct["key"]
print("expires_at:", acct.get("expires_at"), "| now:", int(time.time()))

candidates = [
    "/v1/models",
    "/v1/settings",
    "/v1/usage",
    "/v1/me",
    "/v1/credits",
    "/v1/subscription",
    "/v1/rate_limits",
    "/v1/billing",
    "/v1/quota",
]

h = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "User-Agent": "grok-cli/1.0.4",
}
for path in candidates:
    try:
        r = httpx.get(BASE + path, headers=h, timeout=15.0)
        body = r.text[:400].replace("\n", " ")
        print(f"{r.status_code} {path} :: {body}")
    except httpx.HTTPError as e:
        print(f"ERR {path} :: {type(e).__name__}")
