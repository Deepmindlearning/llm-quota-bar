#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_cookies_windows.py — 请在 **Windows 那台机器**上运行(本脚本经 Dropbox 同步过去)。

作用:把 .chrome-automation 浏览器配置里已登录网站的 cookie 解密,
写入同目录 config.local.toml;Dropbox 同步回 Mac 后,llm-quota-bar 即可使用。

用法(Windows,任意 Python 3.10+):
    pip install pywin32 pycryptodome
    python export_cookies_windows.py

说明:
- cookie 是 v10(DPAPI)加密,只有在本机本用户下才能解开,所以必须在 Windows 上跑。
- 不会打印 cookie 内容,只报告成功/失败。
- qoder.com.cn 的 cookie 当前在该浏览器配置里不存在——如需导出,
  请先用这个自动化 Chrome(或改 PROFILE 指向你的日常 Chrome 配置)登录 qoder.com.cn,再重跑本脚本。
"""
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, ".chrome-automation")
CONFIG = os.path.join(BASE, "config.local.toml")

# config.local.toml 段名 -> cookie host 匹配
HOSTS = {
    "claude": "%claude.ai%",
    "qoder": "%qoder.com.cn%",
}


def load_key():
    from win32crypt import CryptUnprotectData

    with open(os.path.join(PROFILE, "Local State"), encoding="utf-8") as f:
        local_state = json.load(f)
    raw = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    if raw[:5] != b"DPAPI":
        sys.exit("无法识别的加密密钥格式")
    return CryptUnprotectData(raw[5:], None, None, None, 0)[1]


def decrypt_value(key, ev):
    from Crypto.Cipher import AES
    from win32crypt import CryptUnprotectData

    if ev[:3] == b"v10":
        nonce, ct, tag = ev[3:15], ev[15:-16], ev[-16:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        pt = cipher.decrypt_and_verify(ct, tag)
    else:
        # 更老的条目直接用 DPAPI
        pt = CryptUnprotectData(ev, None, None, None, 0)[1]
    # 新版 Chrome 会在明文前加 32 字节 host 哈希,必须在解码前按字节去掉;
    # 先试整体按 ASCII 解码,失败则去 32 字节前缀再试。
    for cand in (pt, pt[32:]):
        try:
            return cand.decode("ascii")
        except UnicodeDecodeError:
            continue
    return ""


def collect_cookies():
    src = os.path.join(PROFILE, "Default", "Network", "Cookies")
    if not os.path.exists(src):
        sys.exit(f"找不到 Cookies 数据库: {src}")
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(src, tmp)  # 复制一份,避开浏览器占用
    try:
        con = sqlite3.connect(tmp)
        key = None
        out = {}
        for section, pattern in HOSTS.items():
            rows = con.execute(
                "SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE ?",
                (pattern,),
            ).fetchall()
            rows = [(h, n, ev) for h, n, ev in rows if ev]
            if not rows:
                print(f"[跳过] {section}: 该浏览器配置里没有 {pattern} 的 cookie")
                continue
            if key is None:
                key = load_key()
            pairs = {}
            for _host, name, ev in rows:
                try:
                    value = decrypt_value(key, ev)
                except Exception:
                    continue
                if value:
                    pairs[name] = value
            if pairs:
                out[section] = "; ".join(f"{k}={v}" for k, v in pairs.items())
        return out
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def write_config(cookies):
    """按行更新 config.local.toml:有 cookie 行就替换,没有就插到段首,缺段就补段。"""
    lines = []
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            lines = f.read().splitlines()

    for section, cookie in cookies.items():
        new_line = f"cookie = '{cookie}'"
        header_idx = next(
            (i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None
        )
        if header_idx is None:
            lines += ["", f"[{section}]", new_line]
            continue
        # 找该段范围内(到下一个段头为止)的 cookie 行
        end = next(
            (
                i
                for i in range(header_idx + 1, len(lines))
                if lines[i].strip().startswith("[")
            ),
            len(lines),
        )
        cookie_idx = next(
            (
                i
                for i in range(header_idx + 1, end)
                if lines[i].strip().startswith("cookie")
            ),
            None,
        )
        if cookie_idx is not None:
            lines[cookie_idx] = new_line
        else:
            lines.insert(header_idx + 1, new_line)

    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    try:
        import win32crypt  # noqa: F401
        import Crypto  # noqa: F401
    except ImportError:
        sys.exit("缺少依赖,请先运行: pip install pywin32 pycryptodome")

    found = collect_cookies()
    if not found:
        sys.exit("没有导出任何 cookie")
    write_config(found)
    for section in found:
        print(f"[ok] {section} 的 cookie 已写入 config.local.toml(内容不显示)")
    print()
    print("等 Dropbox 同步后,在 Mac 上把它合并进 llm-quota-bar 的 config.local.toml 即可。")


if __name__ == "__main__":
    main()
