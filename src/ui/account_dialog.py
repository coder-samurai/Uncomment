from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.i18n import tr
from ui.post_widgets import make_circular_pixmap, make_initial_avatar

class AccountSwitcherDialog(QDialog):
    switch_account_requested = pyqtSignal(str)
    add_account_requested = pyqtSignal()
    logout_requested = pyqtSignal()
    remove_saved_requested = pyqtSignal(str)

    def __init__(
        self,
        accounts: list[dict[str, Any]],
        active_user_id: str,
        lang: str,
        palette: dict[str, str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.lang = lang
        self.palette = palette
        self.active_user_id = active_user_id
        is_ar = lang == "ar"

        self.setWindowTitle(tr(lang, "accounts_title"))
        self.setMinimumWidth(370)
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_ar else Qt.LayoutDirection.LeftToRight
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel(tr(lang, "accounts_title"))
        title.setStyleSheet("font-size: 16px; font-weight: 700; background: transparent;")
        root.addWidget(title)

        note = QLabel(tr(lang, "accounts_privacy_note"))
        note.setObjectName("Muted")
        note.setWordWrap(True)
        root.addWidget(note)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(8)

        for acc in accounts:
            uid = str(acc["user_id"])
            uname = str(acc["username"])
            is_active = uid == active_user_id
            row = self._build_account_row(uid, uname, str(acc.get("avatar_path") or ""), is_active)
            self.rows_box.addWidget(row)

        root.addLayout(self.rows_box)

        div = QFrame()
        div.setObjectName("ThreadDivider")
        div.setFixedHeight(1)
        root.addWidget(div)

        btn_add = QPushButton(tr(lang, "add_account_btn"))
        btn_add.setObjectName("PrimaryBtn")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._on_add)
        root.addWidget(btn_add)

        if active_user_id:
            btn_logout = QPushButton(tr(lang, "logout_btn"))
            btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_logout.clicked.connect(self._on_logout)
            root.addWidget(btn_logout)

    def _build_account_row(
        self,
        uid: str,
        username: str,
        avatar_path: str,
        is_active: bool,
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("AccountRowActive" if is_active else "AccountRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        lbl_av = QLabel()
        lbl_av.setFixedSize(36, 36)
        pix = QPixmap(avatar_path) if avatar_path and Path(avatar_path).is_file() else QPixmap()
        if not pix.isNull():
            lbl_av.setPixmap(make_circular_pixmap(pix, 36))
        else:
            lbl_av.setPixmap(
                make_initial_avatar(
                    username, 36, self.palette["avatar_bg"], self.palette["avatar_fg"]
                )
            )
        layout.addWidget(lbl_av)

        col = QVBoxLayout()
        col.setSpacing(1)
        lbl_u = QLabel(f"@{username}")
        lbl_u.setStyleSheet(
            f"font-weight: 700; font-size: 13.5px; color: {self.palette['text']}; background: transparent;"
        )
        col.addWidget(lbl_u)

        sub = QLabel(
            tr(self.lang, "account_active_tag") if is_active else tr(self.lang, "account_signin_hint")
        )
        sub.setStyleSheet(
            f"font-size: 11px; color: {self.palette['green'] if is_active else self.palette['subtle']}; "
            "background: transparent;"
        )
        col.addWidget(sub)
        layout.addLayout(col, 1)

        if is_active:
            chk = QLabel("●")
            chk.setStyleSheet(
                f"color: {self.palette['green']}; font-size: 12px; background: transparent;"
            )
            layout.addWidget(chk)
        else:
            btn_use = QPushButton(tr(self.lang, "switch_btn"))
            btn_use.setObjectName("GhostBtn")
            btn_use.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_use.clicked.connect(lambda _=False, u=username: self._on_switch(u))
            layout.addWidget(btn_use)

            btn_rm = QPushButton("×")
            btn_rm.setObjectName("CloseXBtn")
            btn_rm.setToolTip(tr(self.lang, "remove_account_tip"))
            btn_rm.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_rm.clicked.connect(lambda _=False, u_id=uid, f=frame: self._on_remove(u_id, f))
            layout.addWidget(btn_rm)

        return frame

    def _on_switch(self, username: str) -> None:
        self.switch_account_requested.emit(username)
        self.accept()

    def _on_add(self) -> None:
        self.add_account_requested.emit()
        self.accept()

    def _on_logout(self) -> None:
        self.logout_requested.emit()
        self.accept()

    def _on_remove(self, uid: str, frame: QFrame) -> None:
        self.remove_saved_requested.emit(uid)
        frame.hide()
        frame.deleteLater()
