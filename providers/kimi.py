"""Kimi Code 会员配额。

端点：GET https://api.kimi.com/coding/v1/usages（官方 CLI 与 Kimi Code Console 共用）。
认证：优先 config.local.toml 里的 Console API Key（sk-kimi-...，长期有效）；
否则读 CLI 的 OAuth 凭据（~/.kimi-code/credentials/kimi-code.json），
access_token 过期/401 时先用 refresh_token 刷新并原子写回（与 CodexBar/cc-switch 同策略）。
"""
from __future__ import annotations

import time

import httpx

from core.config import HTTP_TIMEOUT, atomic_write_json, home, read_json
from core.models import Snapshot, UsageWindow
from providers.base import Provider, sub_status_line

USAGE_URL = "https://api.kimi.com/coding/v1/usages"
TOKEN_URL = "https://auth.kimi.com/api/oauth/token"
CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"

CRED_CANDIDATES = (
    home() / ".kimi-code" / "credentials" / "kimi-code.json",
    home() / ".kimi-code" / "credentials" / "oauth" / "kimi-code.json",
)


def _cred_file():
    for p in CRED_CANDIDATES:
        if p.is_file():
            return p
    return None


def _get_usage(token: str) -> httpx.Response:
    return httpx.get(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=HTTP_TIMEOUT,
    )


def _refresh(path, cred: dict) -> str | None:
    rt = cred.get("refresh_token")
    if not rt:
        return None
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return None
    data = resp.json()
    new = dict(cred)
    new.update({k: v for k, v in data.items() if k in ("access_token", "refresh_token", "expires_in", "scope", "token_type")})
    if "expires_in" in data:
        new["expires_at"] = int(time.time()) + int(data["expires_in"])
    atomic_write_json(path, new)
    return new.get("access_token")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse(data: dict) -> Snapshot:
    snap = Snapshot(provider="Kimi", ok=True)
    if not data:
        # 会员到期后接口返回空对象 {}（2026-09-22 实测）：视为未订阅，不再造一个"周 未知"的空窗口
        snap.subscribed = False
        snap.extra.append("无有效会员（接口返回空）")
        return snap

    def window_from(d: dict, fallback_label: str) -> UsageWindow | None:
        if not isinstance(d, dict):
            return None
        used = _num(d.get("used"))
        limit = _num(d.get("limit"))
        remaining = _num(d.get("remaining"))
        pct = None
        if used is not None and limit:
            pct = used / limit * 100
        elif remaining is not None and limit:
            pct = (1 - remaining / limit) * 100
        resets = d.get("resetAt") or d.get("reset_at") or d.get("resetTime") or d.get("reset_time")
        resets_at = None
        if isinstance(resets, str):
            from datetime import datetime
            try:
                resets_at = datetime.fromisoformat(resets.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        elif isinstance(resets, (int, float)):
            resets_at = float(resets) / (1000 if resets > 1e12 else 1)
        label = d.get("name") or fallback_label
        # 名字英文化转简表
        low = str(label).lower()
        if "week" in low or "7" in low:
            label = "周"
        elif "5" in low or "hour" in low:
            label = "5h"
        # limit==100 时数值本身就是百分比，不再显示 "19/100" 这种易误读的 detail
        detail = f"{int(used)}/{int(limit)}" if used is not None and limit and int(limit) != 100 else ""
        return UsageWindow(label=str(label), used_percent=pct, resets_at=resets_at, detail=detail)

    w = window_from(data.get("usage") or {}, "周")
    if w:
        snap.windows.append(w)
    for item in data.get("limits") or []:
        detail = item.get("detail") if isinstance(item, dict) else None
        w = window_from(detail or item, "5h")
        if w:
            snap.windows.append(w)

    user = data.get("user") or {}
    level = ((user.get("membership") or {}).get("level") or "").replace("LEVEL_", "")
    if level:
        snap.extra.append(f"会员: {level}")
        snap.subscribed = "FREE" not in level.upper()

    wallet = data.get("boosterWallet")
    if isinstance(wallet, dict):
        bal = wallet.get("balance") or {}
        left = _num(bal.get("amountLeft"))
        if left is not None:
            snap.extra.append(f"加量包剩余 ¥{left / 100:.2f}")
    if not snap.windows and not snap.extra:
        return Snapshot(provider="Kimi", error="返回结构无法识别（接口可能已变更）")
    return snap


class KimiProvider(Provider):
    name = "Kimi"
    # Allegro ¥699/月：2026-07-19 开订按月滚，08-25 已关自动续费，已付周期约至 09-19（约数，以官网账号页为准）
    SUB_EXPIRES = "2026-09-19"

    def fetch(self, cfg: dict) -> Snapshot:
        # 无论成败都附上订阅到期状态行——到期后接口报错恰是常态，这行就是原因说明
        snap = self._fetch(cfg)
        snap.extra.append(sub_status_line(self.SUB_EXPIRES, "已关自动续费"))
        return snap

    def _fetch(self, cfg: dict) -> Snapshot:
        api_key = (cfg.get("kimi") or {}).get("api_key")
        try:
            if api_key:
                resp = _get_usage(api_key)
                if resp.status_code == 200:
                    return _parse(resp.json())
                return Snapshot(provider="Kimi", error=f"HTTP {resp.status_code}（检查 config 里的 api_key）")

            path = _cred_file()
            if not path:
                return Snapshot(provider="Kimi", error="未找到 CLI 凭据，也未配置 api_key")
            cred = read_json(path) or {}
            token = cred.get("access_token")
            if not token:
                return Snapshot(provider="Kimi", error="凭据文件缺少 access_token")

            # 过期先刷新，省一次 401 往返
            if cred.get("expires_at") and time.time() > float(cred["expires_at"]) - 30:
                token = _refresh(path, cred) or token

            resp = _get_usage(token)
            if resp.status_code == 401:
                # CLI 可能刚刷新过：重读文件再试
                cred2 = read_json(path) or {}
                token2 = cred2.get("access_token")
                if token2 and token2 != token:
                    resp = _get_usage(token2)
                if resp.status_code == 401:
                    token3 = _refresh(path, cred2)
                    if token3:
                        resp = _get_usage(token3)
            if resp.status_code != 200:
                return Snapshot(provider="Kimi", error=f"HTTP {resp.status_code}（可试跑一次 kimi 让其刷新登录态）")
            return _parse(resp.json())
        except httpx.HTTPError as e:
            return Snapshot(provider="Kimi", error=f"网络错误: {type(e).__name__}")
