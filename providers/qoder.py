"""Qoder CN 积分余额（Quest 试用赠送积分同口径）。

端点（CodexBar 已逆向，支撑 qoder.com.cn 官网"账户 → 用量"页）：
    GET https://qoder.com.cn/api/v2/me/usages/big_model_credits
认证：qoder.com.cn 的浏览器登录 Cookie，在 config.local.toml 配置一次：

    [qoder]
    cookie = "浏览器登录 qoder.com.cn/account/usage 后 F12 复制的完整 Cookie 头"

Cookie 失效（401/403）后需重新复制。国际站是 qoder.com，本实现固定国内站。
"""
from __future__ import annotations

from datetime import datetime

import httpx

from core.config import HTTP_TIMEOUT
from core.models import Snapshot, UsageWindow
from providers.base import Provider

USAGE_URL = "https://qoder.com.cn/api/v2/me/usages/big_model_credits"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
    ),
    "Origin": "https://qoder.com.cn",
    "Referer": "https://qoder.com.cn/account/usage",
    "X-Requested-With": "XMLHttpRequest",
    "Bx-V": "2.5.35",
}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse_time(x):
    if isinstance(x, (int, float)):
        return float(x) / (1000 if x > 1e12 else 1)
    if isinstance(x, str):
        try:
            return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _summary(block: dict) -> dict | None:
    """同时兼容 camelCase 和 snake_case 的 quotaSummary 包装。"""
    if not isinstance(block, dict):
        return None
    s = block.get("quotaSummary") or block.get("quota_summary") or block
    return s if isinstance(s, dict) else None


def _fmt_credits(v: float) -> str:
    return f"{v:.0f}" if v == int(v) else f"{v:.1f}"


class QoderProvider(Provider):
    name = "Qoder"

    def fetch(self, cfg: dict) -> Snapshot:
        cookie = (cfg.get("qoder") or {}).get("cookie")
        if not cookie or not cookie.isascii():
            return Snapshot(provider="Qoder", error="未配置 cookie（见 README：config.local.toml [qoder]）")
        try:
            resp = httpx.get(USAGE_URL, headers={**HEADERS, "Cookie": cookie}, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as e:
            return Snapshot(provider="Qoder", error=f"网络错误: {type(e).__name__}")
        if resp.status_code in (401, 403):
            return Snapshot(provider="Qoder", error="登录态失效（请到 qoder.com.cn 重新复制 cookie）")
        if resp.status_code != 200:
            return Snapshot(provider="Qoder", error=f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return Snapshot(provider="Qoder", error="返回非 JSON（cookie 可能失效）")

        # 业务层错误包装（code/message 形式）容错
        if isinstance(data, dict) and data.get("code") not in (None, 0, "0", 200, "200") and "totalQuota" not in data:
            return Snapshot(provider="Qoder", error=f"接口拒绝: {data.get('message') or data.get('code')}")

        used = limit = 0.0
        found = False
        for key in ("totalQuota", "sharedQuota", "total_quota", "shared_quota"):
            s = _summary(data.get(key) if isinstance(data, dict) else None)
            if not s:
                continue
            u = _num(s.get("usedValue") or s.get("used_value"))
            l = _num(s.get("limitValue") or s.get("limit_value"))
            if u is None and l is None:
                continue
            used += u or 0.0
            limit += l or 0.0
            found = True

        if not found or limit <= 0:
            return Snapshot(provider="Qoder", error="配额字段缺失（接口结构可能已变更）")

        resets_at = _parse_time(data.get("nextResetAt") or data.get("next_reset_at"))
        remaining = limit - used
        snap = Snapshot(provider="Qoder", ok=True)
        snap.windows.append(UsageWindow(
            label="积分",
            used_percent=min(100.0, used / limit * 100),
            resets_at=resets_at,
            detail=f"剩余 {_fmt_credits(remaining)}/{_fmt_credits(limit)} credits",
        ))
        return snap
