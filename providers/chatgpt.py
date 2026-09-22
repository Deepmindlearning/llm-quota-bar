"""ChatGPT Plus / Codex 订阅配额。

端点：GET https://chatgpt.com/backend-api/wham/usage（CodexBar 事实标准）。
认证：~/.codex/auth.json 的 tokens.access_token。401 时先重读文件
（Codex CLI 可能已刷新），仍 401 才用 refresh_token 自行刷新并原子写回。
"""
from __future__ import annotations

import time

import httpx

from core.config import HTTP_TIMEOUT, atomic_write_json, home, read_json
from core.models import Snapshot, UsageWindow
from providers.base import Provider, sub_status_line

AUTH_PATH = home() / ".codex" / "auth.json"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def _get_usage(auth: dict) -> httpx.Response:
    tokens = auth.get("tokens") or {}
    headers = {
        "Authorization": f"Bearer {tokens.get('access_token', '')}",
        "Accept": "application/json",
    }
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = tokens["account_id"]
    return httpx.get(USAGE_URL, headers=headers, timeout=HTTP_TIMEOUT)


def _refresh(auth: dict) -> bool:
    rt = (auth.get("tokens") or {}).get("refresh_token")
    if not rt:
        return False
    resp = httpx.post(
        TOKEN_URL,
        json={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "scope": "openid profile email",
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return False
    data = resp.json()
    new = dict(auth)
    tokens = dict(new.get("tokens") or {})
    for k in ("access_token", "refresh_token", "id_token"):
        if data.get(k):
            tokens[k] = data[k]
    new["tokens"] = tokens
    new["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_write_json(AUTH_PATH, new)
    return True


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _window_label(seconds) -> str:
    """按窗口实际时长打标签：primary/secondary 的语义随套餐会变（实测 plus 的
    primary 是 7 天周窗，secondary 为 null），不能按名字写死。"""
    s = _num(seconds)
    if s is None:
        return "窗口"
    if s <= 6 * 3600:
        return "5h"
    if s <= 8 * 86400:
        return "周"
    return "月"


def _parse(data: dict) -> Snapshot:
    snap = Snapshot(provider="ChatGPT", ok=True)
    plan = data.get("plan_type")
    rl = data.get("rate_limit") or {}
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        snap.windows.append(UsageWindow(
            label=_window_label(w.get("limit_window_seconds")),
            used_percent=_num(w.get("used_percent")),
            resets_at=_num(w.get("reset_at")),
        ))
    credits = data.get("credits") or {}
    if credits.get("has_credits") and not credits.get("unlimited"):
        snap.extra.append(f"credits ${credits.get('balance')}")
    if plan:
        snap.extra.append(f"plan: {plan}")
    if not snap.windows:
        return Snapshot(provider="ChatGPT", error="返回结构无法识别（接口可能已变更）")
    return snap


class ChatGPTProvider(Provider):
    name = "ChatGPT"
    # Plus $20/月：08-17 被休眠自动续费截胡 $20 备扣意外续活一个月，08-25 已关自动续费（到期日为约数）
    SUB_EXPIRES = "2026-09-17"

    def fetch(self, cfg: dict) -> Snapshot:
        # 无论成败都附上订阅到期状态行——到期后接口报错恰是常态，这行就是原因说明
        snap = self._fetch(cfg)
        snap.extra.append(sub_status_line(self.SUB_EXPIRES, "已关自动续费"))
        return snap

    def _fetch(self, cfg: dict) -> Snapshot:
        try:
            auth = read_json(AUTH_PATH)
            if not auth:
                return Snapshot(provider="ChatGPT", error="未找到 ~/.codex/auth.json")
            if not (auth.get("tokens") or {}).get("access_token"):
                if auth.get("OPENAI_API_KEY"):
                    return Snapshot(provider="ChatGPT", error="Codex 为 API key 登录，无订阅配额可查")
                return Snapshot(provider="ChatGPT", error="auth.json 缺少 tokens，请重跑 codex 登录")

            resp = _get_usage(auth)
            if resp.status_code == 401:
                auth2 = read_json(AUTH_PATH) or auth
                if (auth2.get("tokens") or {}).get("access_token") != (auth.get("tokens") or {}).get("access_token"):
                    resp = _get_usage(auth2)
                if resp.status_code == 401 and _refresh(auth2):
                    resp = _get_usage(read_json(AUTH_PATH) or auth2)
            if resp.status_code != 200:
                return Snapshot(provider="ChatGPT", error=f"HTTP {resp.status_code}（可试重跑 codex 登录）")
            return _parse(resp.json())
        except httpx.HTTPError as e:
            return Snapshot(provider="ChatGPT", error=f"网络错误: {type(e).__name__}")
