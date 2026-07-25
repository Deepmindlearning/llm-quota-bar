"""Claude Code statusLine 命令脚本。

Claude Code >= 2.1.80 会在每次刷新状态栏时把一段 JSON 通过 stdin 传给
statusLine 配置的命令，其中 rate_limits 含官方配额百分比（仅订阅账号）。
本脚本把完整 JSON 原子落盘到 state/claude_rate_limits.json 供悬浮条读取，
stdout 则成为 CLI 底部状态栏显示的一行字。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state" / "claude_rate_limits.json"


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"captured_at": time.time(), "data": data}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, STATE)

    rl = data.get("rate_limits") or {}

    def remaining(key: str) -> str:
        w = rl.get(key) or {}
        used = w.get("used_percentage")
        return "--" if used is None else f"{100 - float(used):.0f}%"

    print(f"⏳5h剩{remaining('five_hour')} · 周剩{remaining('seven_day')}")


if __name__ == "__main__":
    main()
