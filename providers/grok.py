"""Grok（SuperGrok 订阅）配额。

数据源优先级：
1. grok.com 网页 rate-limits 接口（cookie 认证，订阅账号的限速窗口，主路径）；
2. Grok Build CLI 的 OAuth 凭据（~/.grok/auth.json）→ cli-chat-proxy /v1/billing，
   该接口只覆盖 API 按量账单（订阅用户恒为 0），兜底显示登录态与账期。

cookie 配置（config.local.toml）：
    [grok]
    cookie = "浏览器登录 grok.com 后 F12 复制的完整 Cookie 头"
注意 grok.com 在 Cloudflare 后面：cookie 里的 cf_clearance 与浏览器 UA 绑定，
失效（403 / 返回非 JSON）后需用同一浏览器重新复制。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.config import HTTP_TIMEOUT, atomic_write_json, home, read_json
from core.models import Snapshot, UsageWindow
from providers.base import Provider

RATE_LIMITS_URL = "https://grok.com/rest/rate-limits"
CREDITS_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
AUTH_PATH = home() / ".grok" / "auth.json"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
CLI_UA = "grok-cli/1.0.4"


def _parse_ts(x) -> float | None:
    if isinstance(x, (int, float)):
        return float(x) / (1000 if x > 1e12 else 1)
    if isinstance(x, str):
        try:
            return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- web cookie

def _windows_from_buckets(data) -> list[UsageWindow]:
    """容错解析 grok.com rate-limits：兼容 models 字典 / 桶列表两种包装，
    桶内认 remaining+total / used+limit / percent 三类字段。"""
    buckets = []
    if isinstance(data, dict):
        src = data.get("models") or data.get("rateLimits") or data.get("rate_limits") or data
        if isinstance(src, dict):
            for name, b in src.items():
                if isinstance(b, dict):
                    buckets.append((str(name), b))
        elif isinstance(src, list):
            for b in src:
                if isinstance(b, dict):
                    name = b.get("model") or b.get("name") or b.get("feature") or b.get("kind") or "?"
                    buckets.append((str(name), b))
    elif isinstance(data, list):
        for b in data:
            if isinstance(b, dict):
                name = b.get("model") or b.get("name") or b.get("feature") or "?"
                buckets.append((str(name), b))

    windows: list[UsageWindow] = []
    for name, b in buckets:
        pct = _num(b.get("percent") or b.get("usedPercent") or b.get("used_percent") or b.get("utilization"))
        remaining = _num(b.get("remaining") or b.get("remainingValue"))
        total = _num(b.get("total") or b.get("limit") or b.get("limitValue") or b.get("totalValue"))
        used = _num(b.get("used") or b.get("usedValue"))
        detail = ""
        if pct is None:
            if remaining is not None and total:
                pct = (1 - remaining / total) * 100
            elif used is not None and total:
                pct = used / total * 100
        if remaining is not None and total:
            detail = f"剩余 {remaining:.0f}/{total:.0f}"
        elif used is not None and total:
            detail = f"{used:.0f}/{total:.0f}"
        resets = _parse_ts(b.get("resetTime") or b.get("resetsAt") or b.get("resets_at") or b.get("reset_at"))
        if pct is None and resets is None:
            continue
        windows.append(UsageWindow(label=name, used_percent=pct, resets_at=resets, detail=detail))
    return windows


def _web_fetch(cookie: str) -> Snapshot:
    headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,
        "Referer": "https://grok.com/",
    }
    resp = httpx.get(RATE_LIMITS_URL, headers=headers, timeout=HTTP_TIMEOUT)
    if resp.status_code in (401, 403):
        return Snapshot(provider="Grok", error="grok.com 登录态失效或被 CF 拦截（请重新复制 cookie）")
    if resp.status_code != 200:
        return Snapshot(provider="Grok", error=f"grok.com HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return Snapshot(provider="Grok", error="返回非 JSON（可能被 Cloudflare 拦截，需重复制 cookie）")
    windows = _windows_from_buckets(data)
    if not windows:
        return Snapshot(provider="Grok", error="rate-limits 返回结构无法识别（接口可能已变更）")
    snap = Snapshot(provider="Grok", ok=True)
    snap.windows.extend(windows)
    snap.extra.append("grok.com 官方数据（订阅限速窗口）")
    return snap


# ------------------------------------------------------------- CLI OAuth 兜底

def _refresh(acct_key: str, acct: dict) -> str | None:
    rt = acct.get("refresh_token")
    client_id = acct_key.split("::")[-1] if "::" in acct_key else None
    if not rt or not client_id:
        return None
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": client_id},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    new_token = data.get("access_token")
    if not new_token:
        return None
    new = dict(acct)
    new["key"] = new_token
    if data.get("refresh_token"):
        new["refresh_token"] = data["refresh_token"]
    if data.get("expires_in"):
        exp = time.time() + int(data["expires_in"])
        new["expires_at"] = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        atomic_write_json(AUTH_PATH, {acct_key: new})
    except OSError:
        pass  # 写回不致命：本次仍用新 token
    return new_token


def _cli_token() -> tuple[str | None, Snapshot | None]:
    """读 ~/.grok/auth.json 拿访问令牌，过期先刷新。失败时返回对应的错误快照。"""
    if not AUTH_PATH.is_file():
        return None, Snapshot(provider="Grok", error="未找到 ~/.grok/auth.json（先运行一次 grok 登录）")
    blob = read_json(AUTH_PATH) or {}
    if not blob:
        return None, Snapshot(provider="Grok", error="auth.json 为空或损坏（重跑 grok login）")
    acct_key, acct = next(iter(blob.items()))
    token = acct.get("key")
    exp = _parse_ts(acct.get("expires_at"))
    if exp and time.time() > exp - 30:
        token = _refresh(acct_key, acct) or token
    if not token:
        return None, Snapshot(provider="Grok", error="auth.json 缺少访问令牌（重跑 grok login）")
    return token, None


_PERIOD_LABELS = {"USAGE_PERIOD_TYPE_WEEKLY": "周", "USAGE_PERIOD_TYPE_MONTHLY": "月"}


def _billing_fetch() -> Snapshot:
    """CLI 代理 credits 接口（CodexBar 已逆向：?format=credits + x-xai-token-auth 头），
    返回订阅用量池的百分比窗口；再取 /v1/settings 补订阅档位名。"""
    token, err = _cli_token()
    if err:
        return err
    headers = {
        "Authorization": f"Bearer {token}",
        "x-xai-token-auth": "xai-grok-cli",
        "Accept": "application/json",
        "User-Agent": CLI_UA,
    }
    try:
        resp = httpx.get(CREDITS_URL, headers=headers, timeout=HTTP_TIMEOUT)
    except httpx.HTTPError as e:
        return Snapshot(provider="Grok", error=f"网络错误: {type(e).__name__}")
    if resp.status_code in (401, 403):
        return Snapshot(provider="Grok", error="CLI 登录态失效（请运行一次 grok 让其刷新）")
    if resp.status_code != 200:
        return Snapshot(provider="Grok", error=f"billing HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return Snapshot(provider="Grok", error="billing 返回非 JSON")

    cfg = data.get("config") or {}
    period = cfg.get("currentPeriod") or {}
    resets_at = _parse_ts(period.get("end")) or _parse_ts(cfg.get("billingPeriodEnd"))
    label = _PERIOD_LABELS.get(period.get("type") or "", "额度")

    # 订阅用量池百分比；未上报（proto3 省略）即本周期零用量
    pct = _num(cfg.get("creditUsagePercent"))
    detail = ""
    if pct is None:
        cap = _num((cfg.get("onDemandCap") or {}).get("val"))
        used = _num((cfg.get("onDemandUsed") or {}).get("val"))
        if cap and cap > 0 and used is not None:
            pct = used / cap * 100
            detail = f"按量 {used:.0f}/{cap:.0f}"
        else:
            pct = 0.0
    prepaid = _num((cfg.get("prepaidBalance") or {}).get("val"))
    if prepaid:
        detail = (detail + " · " if detail else "") + f"预付余量 {prepaid:.0f}"

    snap = Snapshot(provider="Grok", ok=True)
    snap.windows.append(UsageWindow(label=label, used_percent=min(100.0, pct), resets_at=resets_at, detail=detail))

    try:
        sresp = httpx.get(SETTINGS_URL, headers=headers, timeout=5.0)
        if sresp.status_code == 200:
            tier = (sresp.json() or {}).get("subscription_tier_display")
            if tier:
                snap.extra.append(f"套餐: {tier}")
    except (httpx.HTTPError, ValueError):
        pass  # 档位名只是点缀，失败不降级
    return snap


class GrokProvider(Provider):
    name = "Grok"

    def fetch(self, cfg: dict) -> Snapshot:
        cookie = (cfg.get("grok") or {}).get("cookie")
        if cookie and cookie.isascii():
            snap = _web_fetch(cookie)
            if snap.ok:
                return snap
            fallback = _billing_fetch()
            if fallback.ok:
                fallback.extra.append(f"grok.com 路径失败: {snap.error}")
            return fallback
        return _billing_fetch()
