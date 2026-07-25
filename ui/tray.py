"""系统托盘：动态四象限图标 + 菜单 + 告警气泡。"""
from __future__ import annotations

from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.models import Snapshot


class TrayIcon(QSystemTrayIcon):
    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.setIcon(self._make_icon({}))
        self.setToolTip("LLM 会员余量监视")

        menu = QMenu()
        act_refresh = menu.addAction("立即刷新")
        act_refresh.triggered.connect(lambda: bar.refresh())
        self.act_pause = menu.addAction("暂停刷新")
        self.act_pause.triggered.connect(self._toggle_pause)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(QGuiApplication.quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activate)

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.bar.setVisible(not self.bar.isVisible())

    def _toggle_pause(self):
        paused = self.bar.toggle_pause()
        self.act_pause.setText("恢复刷新" if paused else "暂停刷新")

    def alert(self, title: str, msg: str):
        if self.supportsMessages():
            self.showMessage(title, msg, QSystemTrayIcon.Warning, 8000)

    def refresh_icon(self, snapshots: dict[str, Snapshot]):
        self.setIcon(self._make_icon(snapshots))
        parts = []
        for name, snap in snapshots.items():
            if snap.ok and snap.headline_used is not None:
                parts.append(f"{name} 剩 {100 - snap.headline_used:.0f}%")
            elif not snap.ok:
                parts.append(f"{name} 异常")
        if parts:
            self.setToolTip(" · ".join(parts))

    @staticmethod
    def _make_icon(snapshots: dict[str, Snapshot]) -> QIcon:
        """四象限：每象限一家，颜色=剩余量等级。"""
        from ui.bar import level_color

        pm = QPixmap(32, 32)
        pm.fill(QColor("#222226"))
        p = QPainter(pm)
        names = list(snapshots.keys()) or ["", "", "", ""]
        rects = [(0, 0), (16, 0), (0, 16), (16, 16)]
        for i, name in enumerate(names[:4]):
            snap = snapshots.get(name)
            if snap and snap.ok and snap.headline_used is not None:
                color = QColor(level_color(100 - snap.headline_used))
            elif snap and not snap.ok:
                color = QColor("#f44336")
            else:
                color = QColor("#666666")
            p.fillRect(rects[i][0] + 1, rects[i][1] + 1, 14, 14, color)
        p.end()
        return QIcon(pm)
