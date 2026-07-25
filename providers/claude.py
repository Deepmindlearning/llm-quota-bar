"""Claude 订阅配额（桌面 App / 网页 / CLI 同一账号配额）。

数据源优先级：
1. claude.ai 网页 usage 接口（cookie 认证，账号级总用量，覆盖桌面 App）；
2. statusline 官方 rate_limits（Claude Code CLI 活动时落盘）；
3. OAuth usage API（需要 ~/.claude/.credentials.json，本机暂无）；
4. 本地 JSONL 日志估算（ccusage 算法，仅覆盖 CLI，兜底）。

无论走哪条，都附加近 7 天按模型（Fable/Opus/Sonnet/Haiku）的 CLI token 分布。

cookie 配置（config.local.toml）：
    [claude]
    cookie = "浏览器登录 claude.ai 后 F12 复制的完整 Cookie 头"
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.config import HTTP_TIMEOUT, PROJECT_DIR, home, read_json
from core.models import Snapshot, UsageWindow
from providers.base import Provider

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_AI_ORGS_URL = "https://claude.ai/api/organizations"
STATE_PATH = PROJECT_DIR / "state" / "claude_rate_limits.json"
STATE_MAX_AGE = 900  # statusline 状态超过 15 分钟视为陈旧

BLOCK_SECONDS = 5 * 3600
LEARN_DAYS = 8

TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

MODEL_FAMILIES = ("fable", "opus", "sonnet", "haiku")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def _parse_ts(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _block_start(ts: float) -> float:
    return math.floor(ts / 3600) * 3600.0


def _model_family(model: str) -> str:
    m = (model or "").lower()
    for fam in MODEL_FAMILIES:
        if fam in m:
            return fam.capitalize()
    return "其他"


def _iter_records(cutoff: float):
    """产出 (ts, total_tokens, dedup_key, model)，只读近 LEARN_DAYS 天改动的文件。"""
    projects = home() / ".claude" / "projects"
    if not projects.is_dir():
        return
    for f in projects.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") != "assistant":
                        continue
                    msg = rec.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    ts = _parse_ts(rec.get("timestamp", ""))
                    if ts is None:
                        continue
                    total = sum(int(usage.get(k) or 0) for k in TOKEN_KEYS)
                    key = f"{msg.get('id')}|{rec.get('requestId')}"
                    yield ts, total, key, msg.get("model") or ""
        except OSError:
            continue


def _dedup_records() -> list[tuple[float, int, str]]:
    """按 message.id+requestId 去重（流式 chunk 的 usage 是累计值，取最后一次）。"""
    cutoff = time.time() - LEARN_DAYS * 86400
    latest: dict[str, tuple[float, int, str]] = {}
    for ts, total, key, model in _iter_records(cutoff):
        prev = latest.get(key)
        if prev is None or ts >= prev[0]:
            latest[key] = (ts, total, model)
    return sorted(latest.values())


def _model_extras(records: list[tuple[float, int, str]]) -> list[str]:
    """近 7 天按模型家族的 token 分布。"""
    now = time.time()
    by_model: dict[str, int] = {}
    for ts, total, model in records:
        if ts >= now - 7 * 86400:
            fam = _model_family(model)
            by_model[fam] = by_model.get(fam, 0) + total
    if not by_model:
        return []
    parts = [f"{fam} {v / 1e6:.1f}M" for fam, v in sorted(by_model.items(), key=lambda kv: -kv[1])]
    return ["近7天分模型: " + " · ".join(parts) + " tok"]


def _local_estimate(records: list[tuple[float, int, str]] | None = None) -> Snapshot:
    """ccusage 算法：整点 floor + 5h block；上限取近 8 天各 block 总量的 P90。"""
    records = records if records is not None else _dedup_records()
    if not records:
        return Snapshot(provider="Claude", error="本地日志为空，且 statusline/OAuth 均不可用")

    toks = [(ts, t) for ts, t, _ in records]
    blocks: list[list[int]] = []
    start = _block_start(toks[0][0])
    cur: list[int] = []
    for ts, total in toks:
        while ts >= start + BLOCK_SECONDS:
            blocks.append(cur)
            cur = []
            start = _block_start(ts)
        cur.append(total)
    blocks.append(cur)

    last_ts = toks[-1][0]
    active_start = _block_start(last_ts)
    active_tokens = sum(t for ts, t in toks if active_start <= ts < active_start + BLOCK_SECONDS)

    sizes = sorted(sum(b) for b in blocks[:-1] if b)
    if sizes:
        p90 = sizes[min(len(sizes) - 1, math.ceil(len(sizes) * 0.9) - 1)]
        limit = max(p90, active_tokens)
    else:
        limit = max(active_tokens, 1)

    snap = Snapshot(provider="Claude", ok=True)
    snap.windows.append(UsageWindow(
        label="5h",
        used_percent=min(100.0, active_tokens / limit * 100) if limit else None,
        resets_at=active_start + BLOCK_SECONDS,
        detail=f"{active_tokens:,}/{limit:,} tok · 本地估算(P90)",
    ))
    snap.extra.extend(_model_extras(records))
    snap.extra.append("本地日志估算（statusline 数据未就绪）")
    return snap


# rate_limits 键 → 显示标签（与 Claude Code /usage 面板用词一致）；未知键自动兜底
_RL_LABELS = {
    "five_hour": "Current session",
    "seven_day": "All models",
    "seven_day_fable": "Fable",
    "seven_day_sonnet": "Sonnet",
    "seven_day_opus": "Opus",
}


def _statusline_fetch(records) -> Snapshot | None:
    state = read_json(STATE_PATH)
    if not state:
        return None
    captured = state.get("captured_at") or 0
    stale = (time.time() - captured) > STATE_MAX_AGE
    rl = ((state.get("data") or {}).get("rate_limits")) or {}
    if not rl:
        return None

    snap = Snapshot(provider="Claude", ok=True)
    for key, w in rl.items():
        if not isinstance(w, dict):
            continue
        used = w.get("used_percentage")
        resets = w.get("resets_at")
        label = _RL_LABELS.get(key)
        if label is None:
            # 未知键：seven_day_xxx → Xxx，其余原样
            label = key.removeprefix("seven_day_").capitalize() if key.startswith("seven_day") else key
        snap.windows.append(UsageWindow(
            label=label,
            used_percent=float(used) if used is not None else None,
            resets_at=float(resets) if resets else None,
        ))
    if not snap.windows:
        return None
    snap.extra.extend(_model_extras(records))
    if stale:
        snap.extra.append("官方 statusline 数据（>15分钟前，偏旧）")
    else:
        snap.extra.append("官方 statusline 数据")
    return snap


def _oauth_fetch() -> Snapshot | None:
    cred = read_json(home() / ".claude" / ".credentials.json")
    if not cred:
        return None
    oauth = cred.get("claudeAiOauth") or cred
    token = oauth.get("accessToken") or oauth.get("access_token")
    if not token:
        return None
    resp = httpx.get(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        return Snapshot(provider="Claude", error=f"usage API HTTP {resp.status_code}")
    data = resp.json()
    snap = Snapshot(provider="Claude", ok=True)
    for key, w in data.items():
        if not isinstance(w, dict) or "utilization" not in w:
            continue
        used = w.get("utilization")
        resets = w.get("resets_at")
        label = _RL_LABELS.get(key) or key
        snap.windows.append(UsageWindow(
            label=label,
            used_percent=float(used) if used is not None else None,
            resets_at=_parse_ts(resets) if isinstance(resets, str) else resets,
        ))
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        used = extra.get("used_credits")
        limit = extra.get("monthly_limit")
        if used is not None and limit:
            snap.extra.append(f"超额用量 ${used}/{limit}")
    if not snap.windows:
        return Snapshot(provider="Claude", error="usage API 返回无窗口数据")
    snap.extra.append("官方 OAuth usage API")
    return snap


def _claude_ai_cookie_fetch(cfg: dict) -> Snapshot | None:
    """claude.ai 网页 usage 接口（sessionKey cookie）。账号级总用量，
    覆盖桌面 App / 网页 / CLI——桌面 App 用户的主路径。"""
    cookie = (cfg.get("claude") or {}).get("cookie")
    if not cookie or not cookie.isascii():
        return None
    headers = {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": BROWSER_UA,
        "Referer": "https://claude.ai/settings/usage",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        orgs_resp = client.get(CLAUDE_AI_ORGS_URL, headers=headers)
        if orgs_resp.status_code in (401, 403):
            return Snapshot(provider="Claude", error="claude.ai 登录态失效（请重新复制 cookie）")
        if orgs_resp.status_code != 200:
            return Snapshot(provider="Claude", error=f"claude.ai HTTP {orgs_resp.status_code}")
        try:
            orgs = orgs_resp.json()
        except ValueError:
            return Snapshot(provider="Claude", error="claude.ai 返回非 JSON（cookie 可能失效）")
        org_id = None
        if isinstance(orgs, list) and orgs:
            org_id = orgs[0].get("uuid") or orgs[0].get("id")
        elif isinstance(orgs, dict):
            org_id = orgs.get("uuid") or orgs.get("id")
        if not org_id:
            return Snapshot(provider="Claude", error="claude.ai 未返回组织信息")

        usage_resp = client.get(f"{CLAUDE_AI_ORGS_URL}/{org_id}/usage", headers=headers)
        if usage_resp.status_code != 200:
            return Snapshot(provider="Claude", error=f"usage HTTP {usage_resp.status_code}")
        try:
            data = usage_resp.json()
        except ValueError:
            return Snapshot(provider="Claude", error="usage 返回非 JSON")

    snap = Snapshot(provider="Claude", ok=True)
    # 主结构：limits 数组（session / weekly_all / weekly_scoped 带模型名）
    limits = data.get("limits") if isinstance(data, dict) else None
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict) or item.get("percent") is None:
                continue
            kind = item.get("kind")
            if kind == "session":
                label = "Current session"
            elif kind == "weekly_all":
                label = "All models"
            else:
                label = (((item.get("scope") or {}).get("model") or {}).get("display_name")) or kind or "?"
            snap.windows.append(UsageWindow(
                label=label,
                used_percent=float(item["percent"]),
                resets_at=_parse_ts(item.get("resets_at") or ""),
            ))
    # 兜底：顶层 five_hour / seven_day 等键
    if not snap.windows:
        for key, w in data.items() if isinstance(data, dict) else []:
            if not isinstance(w, dict):
                continue
            used = w.get("utilization")
            if used is None:
                used = w.get("used_percentage")
            if used is None:
                continue
            resets = w.get("resets_at")
            label = _RL_LABELS.get(key) or (
                key.removeprefix("seven_day_").capitalize() if key.startswith("seven_day") else key)
            snap.windows.append(UsageWindow(
                label=label,
                used_percent=float(used),
                resets_at=_parse_ts(resets) if isinstance(resets, str) else resets,
            ))
    if not snap.windows:
        return Snapshot(provider="Claude", error="usage 返回结构无法识别（接口可能已变更）")
    snap.extra.append("claude.ai 官方数据（全端总用量）")
    return snap


class ClaudeProvider(Provider):
    name = "Claude"

    def fetch(self, cfg: dict) -> Snapshot:
        records = None
        try:
            records = _dedup_records()
        except OSError:
            pass

        try:
            snap = _claude_ai_cookie_fetch(cfg)
            if snap is not None:
                if records:
                    snap.extra.extend(_model_extras(records))
                return snap
        except httpx.HTTPError as e:
            net_err = e
        else:
            net_err = None

        snap = _statusline_fetch(records or [])
        if snap is not None:
            return snap
        try:
            snap = _oauth_fetch()
            if snap is not None:
                if records:
                    snap.extra.extend(_model_extras(records))
                return snap
        except httpx.HTTPError:
            pass
        local = _local_estimate(records)
        cookie = (cfg.get("claude") or {}).get("cookie")
        if net_err is not None:
            local.extra.append(f"claude.ai 不可达: {type(net_err).__name__}")
        elif not cookie or not cookie.isascii():
            local.extra.append("仅 CLI 口径（配 claude.ai cookie 可看全端总用量）")
        return local
