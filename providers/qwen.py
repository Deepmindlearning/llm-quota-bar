"""Qwen / 阿里云百炼 Coding Plan 配额（中国大陆站）。

请求流程照 CodexBar 的 Swift 实现复刻：
1. 先用 Cookie 拉控制台页面 / user info，解析 sec_token（一次性令牌）；
2. 带 sec_token + csrf 头 POST 控制台 RPC 网关查询配额。

端点（CN）：POST https://bailian-cs.console.aliyun.com/data/api.json
    ?action=BroadScopeAspnGateway&product=sfm_bailian
    &api=zeldaEasy.broadscope-bailian.codingPlan.queryCodingPlanInstanceInfoV2&_v=undefined
认证：控制台浏览器 Cookie，在 config.local.toml 配置一次：

    [qwen]
    cookie = "从百炼控制台 F12 → Network → 任意请求里复制的完整 Cookie 头"

Cookie 失效后该格显示需重新登录。接口为非官方内部接口，结构可能变化。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import httpx

from core.config import HTTP_TIMEOUT
from core.models import Snapshot, UsageWindow
from providers.base import Provider

GATEWAY = "https://bailian.console.aliyun.com"
DASHBOARD_URL = "https://bailian.console.aliyun.com/cn-beijing/?tab=model#/efm/coding_plan"
REFERER = "https://bailian.console.aliyun.com/cn-beijing/?tab=model"
RPC_URL = (
    "https://bailian-cs.console.aliyun.com/data/api.json"
    "?action=BroadScopeAspnGateway&product=sfm_bailian"
    "&api=zeldaEasy.broadscope-bailian.codingPlan.queryCodingPlanInstanceInfoV2&_v=undefined"
)
API_NAME = "zeldaEasy.broadscope-bailian.codingPlan.queryCodingPlanInstanceInfoV2"
COMMODITY_CODE = "sfm_codingplan_public_cn"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

_SEC_PATTERNS = [
    re.compile(r'SEC_TOKEN\s*:\s*"([^"]+)"'),
    re.compile(r"SEC_TOKEN\s*:\s*'([^']+)'"),
    re.compile(r'"?sec_?[Tt]oken"?\s*:\s*"([^"]+)"'),
]


def _cookie_value(cookie: str, name: str) -> str | None:
    for seg in cookie.split(";"):
        if "=" not in seg:
            continue
        k, _, v = seg.partition("=")
        if k.strip() == name and v.strip():
            return v.strip()
    return None


def _resolve_sec_token(client: httpx.Client, cookie: str) -> str | None:
    """CodexBar 三级回退：控制台 HTML → user/info.json → cookie 里的 sec_token。"""
    try:
        resp = client.get(
            DASHBOARD_URL,
            headers={
                "Cookie": cookie,
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code == 200:
            for pat in _SEC_PATTERNS:
                m = pat.search(resp.text)
                if m:
                    return m.group(1)
    except httpx.HTTPError:
        pass

    try:
        resp = client.get(
            f"{GATEWAY}/tool/user/info.json",
            headers={
                "Cookie": cookie,
                "User-Agent": BROWSER_UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": GATEWAY + "/",
            },
        )
        if resp.status_code == 200:
            def _find(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("secToken", "sec_token") and isinstance(v, str) and v:
                            return v
                        found = _find(v)
                        if found:
                            return found
                elif isinstance(obj, list):
                    for item in obj:
                        found = _find(item)
                        if found:
                            return found
                return None
            token = _find(resp.json())
            if token:
                return token
    except (httpx.HTTPError, ValueError):
        pass

    return _cookie_value(cookie, "sec_token")


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


def _window(quota: dict, used_key: str, total_key: str, reset_key: str, label: str) -> UsageWindow | None:
    used = _num(quota.get(used_key))
    total = _num(quota.get(total_key))
    if used is None and total is None:
        return None
    pct = used / total * 100 if used is not None and total else None
    detail = f"{int(used)}/{int(total)} 次" if used is not None and total else ""
    return UsageWindow(label=label, used_percent=pct, resets_at=_parse_time(quota.get(reset_key)), detail=detail)


def _deep_find(obj, keys: tuple[str, ...]):
    """递归找第一个命中 key 的值；字符串里的嵌套 JSON 也会展开（仿 CodexBar expandedJSON）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                return v
        for v in obj.values():
            found = _deep_find(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find(item, keys)
            if found is not None:
                return found
    elif isinstance(obj, str):
        s = obj.strip()
        if s[:1] in ("{", "["):
            try:
                return _deep_find(json.loads(s), keys)
            except ValueError:
                return None
    return None


class QwenProvider(Provider):
    name = "Qwen"

    def fetch(self, cfg: dict) -> Snapshot:
        cookie = (cfg.get("qwen") or {}).get("cookie")
        if not cookie or not cookie.isascii():
            return Snapshot(provider="Qwen", error="未配置 cookie（见 README：config.local.toml [qwen]）")
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                sec_token = _resolve_sec_token(client, cookie)
                if not sec_token:
                    return Snapshot(provider="Qwen", error="拿不到 sec_token（cookie 可能失效，需重新复制）")

                cornerstone = {
                    "feTraceId": str(uuid.uuid4()),
                    "feURL": DASHBOARD_URL,
                    "protocol": "V2",
                    "console": "ONE_CONSOLE",
                    "productCode": "p_efm",
                    "domain": "bailian.console.aliyun.com",
                    "consoleSite": "BAILIAN_ALIYUN",
                    "userNickName": "",
                    "userPrincipalName": "",
                    "xsp_lang": "zh-CN",
                }
                cna = _cookie_value(cookie, "cna")
                if cna:
                    cornerstone["X-Anonymous-Id"] = cna
                params = json.dumps({
                    "Api": API_NAME,
                    "V": "1.0",
                    "Data": {
                        "queryCodingPlanInstanceInfoRequest": {
                            "commodityCode": COMMODITY_CODE,
                            "onlyLatestOne": True,
                        },
                        "cornerstoneParam": cornerstone,
                    },
                }, separators=(",", ":"))

                headers = {
                    "Cookie": cookie,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": BROWSER_UA,
                    "Origin": GATEWAY,
                    "Referer": REFERER,
                }
                csrf = _cookie_value(cookie, "login_aliyunid_csrf") or _cookie_value(cookie, "csrf")
                if csrf:
                    headers["x-xsrf-token"] = csrf
                    headers["x-csrf-token"] = csrf

                resp = client.post(
                    RPC_URL,
                    data={"params": params, "region": "cn-beijing", "sec_token": sec_token},
                    headers=headers,
                )
        except httpx.HTTPError as e:
            return Snapshot(provider="Qwen", error=f"网络错误: {type(e).__name__}")

        if resp.status_code in (401, 403):
            return Snapshot(provider="Qwen", error=f"HTTP {resp.status_code}（cookie 失效，需重新复制）")
        if resp.status_code != 200:
            return Snapshot(provider="Qwen", error=f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return Snapshot(provider="Qwen", error="返回非 JSON（cookie 可能已失效，需重新登录控制台复制）")

        code = str(data.get("code", ""))
        msg = str(data.get("message") or data.get("errorMsg") or "")
        if "login" in (code + msg).lower():
            return Snapshot(provider="Qwen", error="控制台要求重新登录（cookie 失效，请重新复制）")
        if code and code not in ("0", "200") and not data.get("successResponse", True):
            return Snapshot(provider="Qwen", error=f"接口拒绝: {msg or code}")

        infos = _deep_find(data, ("codingPlanInstanceInfos", "coding_plan_instance_infos")) or []
        if not infos:
            return Snapshot(provider="Qwen", error="无 Coding Plan 实例（未订阅或接口已变更）")
        inst = infos[0] if isinstance(infos[0], dict) else {}
        quota = _deep_find(inst, ("codingPlanQuotaInfo", "coding_plan_quota_info")) or _deep_find(
            data, ("codingPlanQuotaInfo", "coding_plan_quota_info")) or {}

        snap = Snapshot(provider="Qwen", ok=True)
        plan = inst.get("planName") or inst.get("instanceName") or inst.get("packageName")
        if plan:
            snap.extra.append(str(plan))
        for args in (
            ("per5HourUsedQuota", "per5HourTotalQuota", "per5HourQuotaNextRefreshTime", "5h"),
            ("perWeekUsedQuota", "perWeekTotalQuota", "perWeekQuotaNextRefreshTime", "周"),
            ("perBillMonthUsedQuota", "perBillMonthTotalQuota", "perBillMonthQuotaNextRefreshTime", "月"),
        ):
            w = _window(quota, *args)
            if w:
                snap.windows.append(w)
        if not snap.windows:
            return Snapshot(provider="Qwen", error="配额字段缺失（接口结构可能已变更）")
        return snap
