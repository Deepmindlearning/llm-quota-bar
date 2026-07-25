"""生成软著申请材料：源代码文档.pdf + 操作说明书.pdf。

输出到 软著材料/（已 gitignore，含个人信息不进仓库）。
用法：uv run python tools/make_copyright_docs.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "软著材料"
SOFT_NAME = "LLM余量监视悬浮条软件"
VERSION = "V1.0"

CODE_FILES = [
    "main.py",
    "core/__init__.py", "core/config.py", "core/models.py",
    "providers/__init__.py", "providers/base.py", "providers/claude.py",
    "providers/kimi.py", "providers/chatgpt.py", "providers/qoder.py", "providers/qwen.py",
    "ui/__init__.py", "ui/bar.py", "ui/tray.py",
    "tools/claude_statusline.py",
]

LINES_PER_PAGE = 50
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
LINE_H = (PAGE_H - 2 * MARGIN - 8 * mm) / LINES_PER_PAGE

pdfmetrics.registerFont(TTFont("msyh", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("msyh-bold", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0))


def _header_footer(c: canvas.Canvas, page_no: int, title: str):
    c.setFont("msyh", 9)
    c.drawString(MARGIN, PAGE_H - MARGIN + 2 * mm, title)
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 2 * mm, f"第 {page_no} 页")
    c.line(MARGIN, PAGE_H - MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN)


def make_code_pdf():
    """源代码文档：全部代码按 50 行/页排版（总量 <60 页，按规定全部提交）。"""
    lines: list[str] = []
    for rel in CODE_FILES:
        path = ROOT / rel
        if not path.is_file():
            continue
        lines.append(f"===== {rel} =====")
        lines.extend(path.read_text(encoding="utf-8").splitlines())
        lines.append("")

    out = OUT / "源代码文档.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    page = 1
    _header_footer(c, page, f"{SOFT_NAME} {VERSION} 源代码")
    y = PAGE_H - MARGIN - 6 * mm
    c.setFont("msyh", 8)
    for line in lines:
        if y < MARGIN:
            c.showPage()
            page += 1
            _header_footer(c, page, f"{SOFT_NAME} {VERSION} 源代码")
            c.setFont("msyh", 8)
            y = PAGE_H - MARGIN - 6 * mm
        c.drawString(MARGIN, y, line[:120])
        y -= LINE_H
    c.showPage()
    c.save()
    print(f"源代码文档.pdf: {page} 页, {len(lines)} 行")


def _para(c, text, x, y, font="msyh", size=10, leading=5.5):
    """简易段落排版：按宽度手动折行，返回新 y。"""
    c.setFont(font, size)
    max_chars = int((PAGE_W - 2 * MARGIN) / size * 0.95)  # pt 单位，中文宽≈字号
    for raw in text.split("\n"):
        line = raw
        while line:
            cut = min(len(line), max_chars)
            c.drawString(x, y, line[:cut])
            y -= leading * mm
            line = line[cut:]
        y -= 1.2 * mm
    return y


def make_manual_pdf():
    out = OUT / "操作说明书.pdf"
    c = canvas.Canvas(str(out), pagesize=A4)
    page = 1

    # 封面
    c.setFont("msyh-bold", 22)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 80 * mm, SOFT_NAME)
    c.setFont("msyh", 16)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 95 * mm, "操作说明书")
    c.setFont("msyh", 12)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 110 * mm, VERSION)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 125 * mm, "2026 年 7 月")
    c.showPage()

    page += 1
    _header_footer(c, page, f"{SOFT_NAME} {VERSION} 操作说明书")
    y = PAGE_H - MARGIN - 8 * mm

    y = _para(c, "一、软件简介", MARGIN, y, font="msyh-bold", size=13)
    y = _para(c, (
        "本软件是一款运行于 Windows 桌面的大语言模型（LLM）会员订阅余量监视工具。"
        "软件以悬浮条形式常驻桌面，同屏显示 Claude、Kimi Code、ChatGPT（Codex）、"
        "Qoder CN 四家服务商的订阅配额或积分剩余百分比，并提供明细查看、重置倒计时、"
        "超额告警等功能，帮助用户合理安排各平台的使用节奏，避免额度耗尽中断工作。"
    ), MARGIN, y)

    y = _para(c, "二、运行环境", MARGIN, y, font="msyh-bold", size=13)
    y = _para(c, (
        "操作系统：Windows 10 / Windows 11（64 位）\n"
        "运行环境：Python 3.11 及以上，依赖 PySide6、httpx（由 uv 自动管理）\n"
        "硬件要求：普通办公 PC 即可，内存占用约 100 MB"
    ), MARGIN, y)

    y = _para(c, "三、安装与启动", MARGIN, y, font="msyh-bold", size=13)
    y = _para(c, (
        "1. 安装 uv 后，在软件目录执行 uv sync 完成依赖安装；\n"
        "2. 执行 uv run python main.py 启动；Windows 下也可直接双击「启动悬浮条.bat」后台常驻；\n"
        "3. 需要开机自启时，将该批处理文件的快捷方式放入 shell:startup 文件夹。\n"
        "首次启动后，Claude 与 Qoder 两格需在 config.local.toml 中粘贴一次浏览器 Cookie"
        "（详见软件目录 README.md），其余两家自动读取本机命令行工具的登录凭据，无需配置。"
    ), MARGIN, y)

    # 图 1：悬浮条
    img1 = ROOT / "docs" / "screenshot_bar.png"
    if img1.is_file():
        if y < 90 * mm:
            c.showPage(); page += 1
            _header_footer(c, page, f"{SOFT_NAME} {VERSION} 操作说明书")
            y = PAGE_H - MARGIN - 8 * mm
        c.drawImage(str(img1), MARGIN, y - 30 * mm, width=140 * mm, preserveAspectRatio=True, mask="auto")
        y -= 34 * mm
        y = _para(c, "图 1  悬浮条主界面：四格分别显示四家平台最紧张窗口的剩余百分比", MARGIN, y, size=9)

    y = _para(c, "四、功能说明", MARGIN, y, font="msyh-bold", size=13)
    y = _para(c, (
        "1. 余量总览：每格以环形进度条与百分比显示该平台最紧张配额窗口的剩余量，"
        "颜色随剩余量变化（绿 >50%，橙 20–50%，红 <20%）；\n"
        "2. 明细查看：单击任意格子展开明细面板，显示该平台各配额窗口（如 Claude 的 "
        "Current session / All models / Fable）的剩余百分比、重置倒计时与附加信息，再次单击收起；\n"
        "3. 位置调整：按住任意格子拖动，可将悬浮条移动到桌面任意位置；\n"
        "4. 超额告警：任一窗口用量达到 80% 时，系统托盘弹出气泡提醒，回落至 70% 以下自动复位；\n"
        "5. 托盘驻留：托盘图标以四象限颜色对应四家平台状态，左键单击显示/隐藏悬浮条，"
        "右键菜单提供立即刷新、暂停刷新、退出；\n"
        "6. 自动刷新：各平台每 5 分钟自动查询一次，数据超过 15 分钟未更新时对应格子置灰提示。"
    ), MARGIN, y)

    # 图 2：明细
    img2 = ROOT / "docs" / "screenshot.png"
    if img2.is_file():
        if y < 100 * mm:
            c.showPage(); page += 1
            _header_footer(c, page, f"{SOFT_NAME} {VERSION} 操作说明书")
            y = PAGE_H - MARGIN - 8 * mm
        c.drawImage(str(img2), MARGIN, y - 50 * mm, width=120 * mm, preserveAspectRatio=True, mask="auto")
        y -= 54 * mm
        y = _para(c, "图 2  单击 Claude 格展开的明细面板：三个配额窗口余量与重置倒计时", MARGIN, y, size=9)

    y = _para(c, "五、安全与隐私", MARGIN, y, font="msyh-bold", size=13)
    y = _para(c, (
        "软件全部查询均为只读操作，不发起任何模型推理调用；各平台凭据于运行时从本机"
        "命令行工具的凭据文件现读现用，不复制、不上传、不打印；需手动配置的 Cookie "
        "仅保存于本机配置文件，不随软件分发。"
    ), MARGIN, y)

    c.showPage()
    c.save()
    print(f"操作说明书.pdf: {page} 页")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    make_code_pdf()
    make_manual_pdf()
    print("输出目录:", OUT)
