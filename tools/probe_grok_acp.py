"""一次性探针：通过 ACP(stdio) 调 Grok Build 的 x.ai/session/usage 扩展方法。"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

GROK = r"C:\Users\miner\.grok\bin\grok.exe"

proc = subprocess.Popen(
    [GROK, "agent", "stdio"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, encoding="utf-8", errors="replace", bufsize=1,
)

responses: dict[int, dict] = {}
lock = threading.Lock()


def reader():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print("NON-JSON:", line[:200])
            continue
        if "id" in msg:
            with lock:
                responses[msg["id"]] = msg
        else:
            print("NOTIFY:", json.dumps(msg, ensure_ascii=False)[:300])


threading.Thread(target=reader, daemon=True).start()

_id = 0


def call(method: str, params: dict, wait: float = 20.0) -> dict:
    global _id
    _id += 1
    with lock:
        responses.pop(_id, None)
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    t0 = time.time()
    while time.time() - t0 < wait:
        with lock:
            if _id in responses:
                return responses.pop(_id)
        time.sleep(0.1)
    return {"error": "timeout", "method": method}


init = call("initialize", {
    "protocolVersion": 1,
    "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
    "clientInfo": {"name": "quota-probe", "version": "0.1.0"},
})
print("initialize ->", json.dumps(init, ensure_ascii=False)[:400])

sess = call("session/new", {"cwd": "D:\\grok-lab", "mcpServers": []})
print("session/new ->", json.dumps(sess, ensure_ascii=False)[:400])
sid = (sess.get("result") or {}).get("sessionId")

if sid:
    for method in ("x.ai/session/usage", "session/usage", "_x.ai/session/usage"):
        r = call(method, {"sessionId": sid}, wait=15.0)
        print(f"{method} ->", json.dumps(r, ensure_ascii=False)[:800])
    for method in ("_x.ai/auth/check_subscription", "_x.ai/auth/get"):
        r = call(method, {"sessionId": sid}, wait=15.0)
        print(f"{method} ->", json.dumps(r, ensure_ascii=False)[:1200])
    r = call("_x.ai/commands/list", {"sessionId": sid}, wait=15.0)
    cmds = [c.get("name") for c in ((r.get("result") or {}).get("commands") or [])]
    print("commands:", cmds)

    # 通过 session/prompt 触发内建 /usage，收集会话输出
    print("--- session/prompt /usage ---")
    t_end = time.time() + 60
    call_id_before = _id
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "session/prompt",
                                 "params": {"sessionId": sid, "prompt": [{"type": "text", "text": "/usage"}]}}) + "\n")
    proc.stdin.flush()
    t0 = time.time()
    while time.time() - t0 < 60:
        with lock:
            if 99 in responses:
                print("prompt result:", json.dumps(responses.pop(99), ensure_ascii=False)[:600])
                break
        time.sleep(0.2)

proc.kill()
