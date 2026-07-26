"""悬浮条主界面：四格余量 + 点击展开明细 + 后台轮询。"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from core.models import Snapshot
from providers.base import Provider

BAR_BG = "rgba(30, 30, 34, 215)"


def level_color(remaining: float | None) -> str:
    if remaining is None:
        return "#888888"
    if remaining > 50:
        return "#4caf50"
    if remaining > 20:
        return "#ff9800"
    return "#f44336"


def fmt_countdown(resets_at: float | None) -> str:
    if not resets_at:
        return ""
    delta = int(resets_at - time.time())
    if delta <= 0:
        return "即将重置"
    h, rem = divmod(delta, 3600)
    m, _ = divmod(rem, 60)
    if h >= 24:
        return f"{h // 24}天{h % 24}时后重置"
    if h:
        return f"{h}时{m}分后重置"
    return f"{m}分后重置"


class _WorkerSignals(QObject):
    result = Signal(str, object)  # provider name, Snapshot


class _FetchWorker(QRunnable):
    def __init__(self, provider: Provider, cfg: dict):
        super().__init__()
        self.provider = provider
        self.cfg = cfg
        self.signals = _WorkerSignals()

    def run(self):
        try:
            snap = self.provider.fetch(self.cfg)
        except Exception as e:  # provider 内部已兜底，这里双保险
            snap = Snapshot(provider=self.provider.name, error=f"内部错误: {type(e).__name__}")
        self.signals.result.emit(self.provider.name, snap)


class RingWidget(QWidget):
    """环形剩余量指示。"""

    def __init__(self):
        super().__init__()
        self.remaining: float | None = None
        self.stale = False
        self.setFixedSize(46, 46)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)
        pen = QPen(QColor("#555555"))
        pen.setWidth(4)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        if self.remaining is not None:
            color = QColor(level_color(self.remaining))
            if self.stale:
                color.setAlpha(110)
            pen = QPen(color)
            pen.setWidth(4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, int(-self.remaining / 100 * 360 * 16))
        p.end()


class CellWidget(QFrame):
    clicked = Signal(str)

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.setStyleSheet(f"CellWidget {{ background: {BAR_BG}; border-radius: 8px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        self.title = QLabel(name)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("color: #bbbbbb; font-size: 11px; background: transparent;")
        self.ring = RingWidget()
        self.pct = QLabel("—")
        self.pct.setAlignment(Qt.AlignCenter)
        self.pct.setStyleSheet("color: white; font-size: 13px; font-weight: bold; background: transparent;")
        ring_row = QHBoxLayout()
        ring_row.addStretch()
        ring_row.addWidget(self.ring)
        ring_row.addStretch()
        lay.addWidget(self.title)
        lay.addLayout(ring_row)
        lay.addWidget(self.pct)
        self._drag_start: QPoint | None = None

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._drag_start = e.globalPosition().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_start is not None:
            bar = self.window()
            bar.move(bar.pos() + e.globalPosition().toPoint() - self._drag_start)
            self._drag_start = e.globalPosition().toPoint()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_start is not None:
            if (e.globalPosition().toPoint() - self._drag_start).manhattanLength() < 6:
                self.clicked.emit(self.name)
            self._drag_start = None
        super().mouseReleaseEvent(e)


class MonitorBar(QWidget):
    def __init__(self, providers: list[Provider], cfg: dict):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # macOS 下 Tool 窗口在应用失去焦点时会被系统自动隐藏,此属性让它常驻(其他平台无效果)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.cfg = cfg
        self.providers = {p.name: p for p in providers}
        self.snapshots: dict[str, Snapshot] = {}
        self.paused = False
        self.alerted: set[str] = set()   # "provider|label" 已告警集合

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.cells: dict[str, CellWidget] = {}
        for p in providers:
            cell = CellWidget(p.name)
            cell.clicked.connect(self.toggle_detail)
            self.cells[p.name] = cell
            row.addWidget(cell)
        outer.addLayout(row)

        # 明细面板
        self.detail = QFrame()
        self.detail.setStyleSheet(f"QFrame {{ background: {BAR_BG}; border-radius: 8px; }}"
                                  "QLabel { color: #dddddd; background: transparent; }")
        self.detail_lay = QVBoxLayout(self.detail)
        self.detail_lay.setContentsMargins(10, 8, 10, 8)
        self.detail_lay.setSpacing(4)
        self.detail.hide()
        outer.addWidget(self.detail)
        self.detail_for: str | None = None

        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(4)

        # 每 provider 独立轮询定时器
        self.timers: list[QTimer] = []
        for p in providers:
            t = QTimer(self)
            t.setInterval(p.interval * 1000)
            t.timeout.connect(lambda name=p.name: self.refresh(name))
            t.start()
            self.timers.append(t)

        # 1 秒滴答：刷新倒计时与置灰
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self.on_tick)
        self.tick.start()

        from ui.tray import TrayIcon
        self.tray = TrayIcon(self)
        self.tray.show()

        for p in providers:
            self.refresh(p.name)

    # ---- 轮询 ----
    def refresh(self, name: str | None = None):
        if self.paused:
            return
        targets = [name] if name else list(self.providers)
        for n in targets:
            w = _FetchWorker(self.providers[n], self.cfg)
            w.signals.result.connect(self.on_result)
            self.threadpool.start(w)

    def on_result(self, name: str, snap: Snapshot):
        self.snapshots[name] = snap
        self.update_cell(name)
        if self.detail_for == name:
            self.render_detail(name)
        self.check_alerts(name, snap)
        self.tray.refresh_icon(self.snapshots)

    # ---- 显示 ----
    def update_cell(self, name: str):
        cell = self.cells[name]
        snap = self.snapshots.get(name)
        if not snap:
            return
        if not snap.ok:
            cell.pct.setText("!")
            cell.pct.setStyleSheet("color: #f44336; font-size: 13px; font-weight: bold; background: transparent;")
            cell.ring.remaining = None
            cell.setToolTip(snap.error)
        else:
            used = snap.headline_used
            remaining = None if used is None else 100 - used
            stale = (time.time() - snap.fetched_at) > self.providers[name].stale_after
            cell.ring.remaining = remaining
            cell.ring.stale = stale
            color = level_color(remaining)
            cell.pct.setText("—" if remaining is None else f"{remaining:.0f}%")
            cell.pct.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold; background: transparent;")
            tip = time.strftime("更新于 %H:%M:%S", time.localtime(snap.fetched_at))
            if stale:
                tip += "（数据陈旧）"
            cell.setToolTip(tip)
        cell.ring.update()

    def toggle_detail(self, name: str):
        if self.detail_for == name and self.detail.isVisible():
            self.detail.hide()
            self.detail_for = None
        else:
            self.detail_for = name
            self.render_detail(name)
            self.detail.show()
        self.adjustSize()

    def _clear_detail(self):
        """递归清空明细面板——嵌套 layout 里的子控件必须一并 deleteLater，
        否则旧控件会留在原位置重绘，造成文字重合。"""
        def _clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    _clear(item.layout())
        _clear(self.detail_lay)
        self._countdown_refs = []

    def render_detail(self, name: str):
        snap = self.snapshots.get(name)
        if not snap:
            return
        # 数据没变就只刷新倒计时文字，不重建控件（避免闪烁与控件泄漏）
        key = (name, snap.fetched_at, snap.ok, snap.error,
               tuple((w.label, w.used_percent, w.detail) for w in snap.windows),
               tuple(snap.extra))
        if key == getattr(self, "_render_key", None):
            self._update_countdowns()
            return
        self._render_key = key
        self._clear_detail()

        title = QLabel(f"<b>{name}</b>")
        self.detail_lay.addWidget(title)
        if not snap.ok:
            err = QLabel(snap.error)
            err.setWordWrap(True)
            err.setStyleSheet("color: #f44336; background: transparent;")
            self.detail_lay.addWidget(err)
            return
        self._countdown_refs = []
        for w in snap.windows:
            row = QHBoxLayout()
            lab = QLabel(w.label)
            lab.setFixedWidth(100)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            remaining = w.remaining_percent
            bar.setValue(int(remaining) if remaining is not None else 0)
            bar.setStyleSheet(
                "QProgressBar { background: #444; border-radius: 4px; }"
                f"QProgressBar::chunk {{ background: {level_color(remaining)}; border-radius: 4px; }}")
            txt = ("剩余 %.0f%%" % remaining) if remaining is not None else "未知"
            info = QLabel()
            row.addWidget(lab)
            row.addWidget(bar, 1)
            row.addWidget(info)
            self.detail_lay.addLayout(row)
            self._countdown_refs.append((info, txt, w))
            if w.detail:
                d = QLabel("  " + w.detail)
                d.setStyleSheet("color: #999; background: transparent; font-size: 11px;")
                self.detail_lay.addWidget(d)
        for line in snap.extra:
            e = QLabel(line)
            e.setWordWrap(True)
            e.setStyleSheet("color: #999; background: transparent; font-size: 11px;")
            self.detail_lay.addWidget(e)
        ts = QLabel(time.strftime("更新于 %H:%M:%S", time.localtime(snap.fetched_at)))
        ts.setStyleSheet("color: #777; background: transparent; font-size: 10px;")
        self.detail_lay.addWidget(ts)
        self._update_countdowns()
        self.adjustSize()

    def _update_countdowns(self):
        for lab, txt, w in getattr(self, "_countdown_refs", []):
            lab.setText(f"{txt} · {fmt_countdown(w.resets_at)}")

    def on_tick(self):
        for name in self.cells:
            if name in self.snapshots:
                self.update_cell(name)
        if self.detail_for and self.detail.isVisible():
            self.render_detail(self.detail_for)

    # ---- 告警 ----
    def check_alerts(self, name: str, snap: Snapshot):
        if not snap.ok:
            return
        for w in snap.windows:
            if w.used_percent is None:
                continue
            key = f"{name}|{w.label}"
            if w.used_percent >= 80 and key not in self.alerted:
                self.alerted.add(key)
                self.tray.alert(f"{name} {w.label}窗口已用 {w.used_percent:.0f}%",
                                fmt_countdown(w.resets_at))
            elif w.used_percent < 70 and key in self.alerted:
                self.alerted.discard(key)

    def toggle_pause(self):
        self.paused = not self.paused
        return self.paused
