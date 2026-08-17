"""一次性探针：playwright + 本机真 Chrome + 注入 cookie 过 CF 拿 grok.com rate-limits。"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import PROJECT_DIR, load_config  # noqa: E402

PROFILE = PROJECT_DIR / ".chrome-automation"


def header_to_cookies(header: str) -> list[dict]:
    out = []
    for part in header.split("; "):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        out.append({"name": name.strip(), "value": value, "domain": ".grok.com", "path": "/"})
    return out


def main() -> int:
    cookie = load_config()["grok"]["cookie"]
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx.add_cookies(header_to_cookies(cookie))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        resp = page.goto("https://grok.com/rest/rate-limits", wait_until="domcontentloaded", timeout=60000)
        print("status:", resp.status if resp else None)
        if resp and resp.status == 403:
            page.wait_for_timeout(9000)          # 给 CF 托管挑战自动求解的时间
            resp = page.reload(wait_until="domcontentloaded", timeout=60000)
            print("after challenge wait:", resp.status if resp else None)
        text = page.locator("body").inner_text()[:1500]
        print(text)
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
