"""生成《合作开发协议》PDF（软著合作开发申请附件）。

输出：软著材料/合作开发协议.pdf —— 打印后双方亲笔签名，拍照/扫描回传即可上传。
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "软著材料" / "合作开发协议.pdf"

pdfmetrics.registerFont(TTFont("msyh", r"C:\Windows\Fonts\msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("msyhbd", r"C:\Windows\Fonts\msyhbd.ttc", subfontIndex=0))

PAGE_W, PAGE_H = A4
MX = 25 * mm


def para(c, text, y, size=11, leading=7.2, indent=0):
    c.setFont("msyh", size)
    # 单位均为 pt：中文字符宽度≈字号，留 5% 余量
    max_chars = int((PAGE_W - 2 * MX) / size * 0.95)
    for raw in text.split("\n"):
        line = raw
        first = True
        while line:
            cut = min(len(line), max_chars - (indent if first else 0))
            c.drawString(MX + (indent * size if first else 0), y, line[:cut])
            y -= leading * mm
            line = line[cut:]
            first = False
        y -= 1.5 * mm
    return y


c = canvas.Canvas(str(OUT), pagesize=A4)
c.setTitle("合作开发协议")

y = PAGE_H - 30 * mm
c.setFont("msyhbd", 20)
c.drawCentredString(PAGE_W / 2, y, "合作开发协议")
y -= 20 * mm

y = para(c, "甲方：冯灿（身份证号：411502198702110532）", y)
y = para(c, "乙方：陈艺苒（身份证号：321121199208171022）", y)
y -= 4 * mm

y = para(c, (
    "鉴于甲、乙双方共同参与了“LLM余量监视悬浮条软件 V1.0”（以下简称“本软件”）的设计与开发工作，"
    "为明确双方权利义务，经友好协商，达成如下协议："
), y)

y = para(c, "一、本软件由甲、乙双方合作开发完成，开发完成日期为 2026 年 7 月 25 日。", y)
y = para(c, "二、本软件的著作权由甲、乙双方共同享有，双方均为本软件的著作权人。", y)
y = para(c, "三、双方同意，本软件申请著作权登记时，著作权人署名顺序为：甲方冯灿（第 1 位）、乙方陈艺苒（第 2 位）。", y)
y = para(c, "四、本软件著作权的行使（包括但不限于使用、许可他人使用、转让）由双方协商一致后进行，任何一方不得擅自单独处分。", y)
y = para(c, "五、因本协议产生的争议，双方应友好协商解决。", y)
y = para(c, "六、本协议一式两份，甲、乙双方各执一份，自双方签字之日起生效。", y)

y -= 20 * mm
c.setFont("msyh", 12)
c.drawString(MX, y, "甲方（签字）：____________________")
c.drawString(PAGE_W / 2 + 10 * mm, y, "乙方（签字）：____________________")
y -= 15 * mm
c.drawString(MX, y, "日期：2026 年 7 月 25 日")
c.drawString(PAGE_W / 2 + 10 * mm, y, "日期：2026 年 7 月 25 日")

c.showPage()
c.save()
print("生成:", OUT)
