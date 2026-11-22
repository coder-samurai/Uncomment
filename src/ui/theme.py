from __future__ import annotations

PALETTES: dict[str, dict[str, str]] = {
    "instagram_dark": {
        "bg": "#000000",
        "surface": "#121212",
        "surface_alt": "#1a1a1a",
        "hover": "#222222",
        "border": "#262626",
        "border_soft": "#1f1f1f",
        "text": "#f5f5f5",
        "comment_text": "#e2e2e2",
        "context_user": "#d4d4d4",
        "context_text": "#b8b8b8",
        "mention": "#8ab4f8",
        "muted": "#a8a8a8",
        "subtle": "#7a7a7a",
        "avatar_bg": "#2b2b2b",
        "avatar_fg": "#f5f5f5",
        "blue": "#0095f6",
        "blue_hover": "#1877f2",
        "red": "#ed4956",
        "red_soft": "rgba(237, 73, 86, 0.13)",
        "red_border": "rgba(237, 73, 86, 0.55)",
        "green": "#22c55e",
        "media_bg": "#050505",
        "media_bar_bg": "#0d0d0d",
        "media_bar_border": "#1f1f1f",
        "media_btn_bg": "#1a1a1a",
        "media_btn_fg": "#f5f5f5",
        "media_btn_border": "#333333",
    },
    "oled": {
        "bg": "#000000",
        "surface": "#070707",
        "surface_alt": "#111111",
        "hover": "#1a1a1a",
        "border": "#1f1f1f",
        "border_soft": "#151515",
        "text": "#ffffff",
        "comment_text": "#e5e5e5",
        "context_user": "#d4d4d4",
        "context_text": "#b0b0b0",
        "mention": "#8ab4f8",
        "muted": "#9a9a9a",
        "subtle": "#6b6b6b",
        "avatar_bg": "#222222",
        "avatar_fg": "#ffffff",
        "blue": "#0095f6",
        "blue_hover": "#1877f2",
        "red": "#ed4956",
        "red_soft": "rgba(237, 73, 86, 0.15)",
        "red_border": "rgba(237, 73, 86, 0.60)",
        "green": "#22c55e",
        "media_bg": "#000000",
        "media_bar_bg": "#060606",
        "media_bar_border": "#1a1a1a",
        "media_btn_bg": "#141414",
        "media_btn_fg": "#ffffff",
        "media_btn_border": "#2a2a2a",
    },
    "light": {
        "bg": "#fafafa",
        "surface": "#ffffff",
        "surface_alt": "#f3f4f6",
        "hover": "#e9ecef",
        "border": "#dbdbdb",
        "border_soft": "#ebebeb",
        "text": "#111827",
        "comment_text": "#1f2937",
        "context_user": "#262626",
        "context_text": "#4b5563",
        "mention": "#00376b",
        "muted": "#6b7280",
        "subtle": "#8e8e8e",
        "avatar_bg": "#e5e7eb",
        "avatar_fg": "#111827",
        "blue": "#0095f6",
        "blue_hover": "#1877f2",
        "red": "#ed4956",
        "red_soft": "rgba(237, 73, 86, 0.10)",
        "red_border": "rgba(237, 73, 86, 0.50)",
        "green": "#16a34a",
        "media_bg": "#efefef",
        "media_bar_bg": "#ffffff",
        "media_bar_border": "#dbdbdb",
        "media_btn_bg": "#f3f4f6",
        "media_btn_fg": "#111827",
        "media_btn_border": "#d1d5db",
    },
}

def get_palette(theme_name: str = "instagram_dark") -> dict[str, str]:
    return PALETTES.get(theme_name, PALETTES["instagram_dark"])

def build_qss(theme_name: str = "instagram_dark") -> str:
    p = get_palette(theme_name)
    return f"""
    QMainWindow, QDialog, QWidget {{
        background-color: {p['bg']};
        color: {p['text']};
        font-family: "Inter", "Noto Sans Arabic", "Segoe UI", sans-serif;
        font-size: 13px;
    }}

    QFrame#TopBar {{
        background-color: {p['surface']};
        border-bottom: 1px solid {p['border']};
    }}

    QLabel#BrandTitle {{
        font-size: 16px;
        font-weight: 700;
        color: {p['text']};
        background: transparent;
    }}

    QLabel#Muted {{
        color: {p['muted']};
        font-size: 12px;
        background: transparent;
    }}

    QLabel#Subtle {{
        color: {p['subtle']};
        font-size: 11px;
        background: transparent;
    }}

    QLabel#QuotaPill {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 12px;
        padding: 4px 12px;
        color: {p['text']};
        font-size: 12px;
        font-weight: 600;
    }}

    QLineEdit {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 6px 12px;
        color: {p['text']};
    }}

    QLineEdit:focus {{
        border-color: {p['blue']};
    }}

    QFrame#PostCard {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}

    QFrame#MediaPane {{
        background-color: {p['media_bg']};
        border: none;
    }}

    QWidget#VideoStage {{
        background-color: {p['media_bg']};
    }}

    QFrame#MediaBottomBar, QFrame#VideoCtrlBar {{
        background-color: {p['media_bar_bg']};
        border-top: 1px solid {p['media_bar_border']};
    }}

    QLabel#MediaPlaceholder {{
        background: transparent;
        color: {p['subtle']};
        font-size: 13px;
    }}

    QLabel#MediaStatusMsg {{
        color: {p['muted']};
        font-size: 11px;
        background: transparent;
    }}

    QLabel#SlideCounter {{
        color: {p['text']};
        font-size: 12px;
        font-weight: 600;
        background: transparent;
    }}

    QFrame#PostHeader {{
        background-color: {p['surface']};
        border-bottom: 1px solid {p['border']};
    }}

    QFrame#PostFooter {{
        background-color: {p['surface']};
        border-top: 1px solid {p['border']};
    }}

    QFrame#PostStatsBar {{
        background-color: {p['surface']};
        border-top: 1px solid {p['border_soft']};
    }}

    QLabel#PostOwner {{
        font-size: 14px;
        font-weight: 700;
        color: {p['text']};
        background: transparent;
    }}

    QLabel#PostCaption {{
        font-size: 13px;
        color: {p['comment_text']};
        background: transparent;
    }}

    QFrame#CommentMine {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}

    QFrame#CommentMine:hover {{
        background-color: {p['hover']};
    }}

    QFrame#CommentMineSelected {{
        background-color: {p['red_soft']};
        border: 1px solid {p['red_border']};
        border-radius: 10px;
    }}

    QFrame#CommentContext {{
        background-color: transparent;
        border: none;
    }}

    QFrame#ThreadDivider {{
        background-color: {p['border_soft']};
        max-height: 1px;
    }}

    QFrame#Sidebar {{
        background-color: {p['surface']};
        border-left: 1px solid {p['border']};
        border-right: 1px solid {p['border']};
    }}

    QFrame#ActiveCountdownBox {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}

    QLabel#CountdownChip {{
        font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 15px;
        font-weight: 700;
        color: {p['red']};
        background: transparent;
    }}

    QFrame#QueueRow {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border_soft']};
        border-radius: 8px;
    }}

    QPushButton {{
        background-color: {p['surface_alt']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 8px;
        padding: 7px 14px;
        font-weight: 600;
    }}

    QPushButton:hover {{
        background-color: {p['hover']};
    }}

    QPushButton:disabled {{
        color: {p['subtle']};
        background-color: {p['surface']};
        border-color: {p['border_soft']};
    }}

    QPushButton#GhostBtn {{
        background-color: transparent;
        border: none;
        color: {p['blue']};
        padding: 4px 8px;
        font-weight: 600;
    }}

    QPushButton#GhostBtn:hover {{
        color: {p['blue_hover']};
        background-color: {p['hover']};
        border-radius: 6px;
    }}

    QPushButton#SubtleLinkBtn {{
        background-color: transparent;
        border: none;
        color: {p['muted']};
        padding: 0px 4px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#SubtleLinkBtn:hover {{
        color: {p['text']};
    }}

    QPushButton#PrimaryBtn {{
        background-color: {p['blue']};
        border: none;
        color: #ffffff;
        padding: 8px 16px;
        font-weight: 700;
    }}

    QPushButton#PrimaryBtn:hover {{
        background-color: {p['blue_hover']};
    }}

    QPushButton#DangerBtn {{
        background-color: {p['red']};
        border: none;
        color: #ffffff;
        padding: 9px 18px;
        font-weight: 700;
    }}

    QPushButton#DangerBtn:hover {{
        background-color: #dc2626;
    }}

    QPushButton#DangerBtn:disabled {{
        background-color: {p['surface_alt']};
        color: {p['subtle']};
        border: 1px solid {p['border']};
    }}

    QPushButton#NavBtn {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 20px;
        padding: 8px 18px;
        font-weight: 600;
        min-width: 100px;
    }}

    QPushButton#NavBtn:hover {{
        background-color: {p['hover']};
        border-color: {p['muted']};
    }}

    QPushButton#CloseXBtn {{
        background-color: transparent;
        border: none;
        color: {p['muted']};
        font-size: 16px;
        font-weight: 700;
        border-radius: 13px;
        min-width: 26px;
        max-width: 26px;
        min-height: 26px;
        max-height: 26px;
        padding: 0px;
    }}

    QPushButton#CloseXBtn:hover {{
        background-color: {p['red_soft']};
        color: {p['red']};
    }}

    QPushButton#MediaOverlayBtn {{
        background-color: {p['media_btn_bg']};
        border: 1px solid {p['media_btn_border']};
        color: {p['media_btn_fg']};
        border-radius: 18px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#MediaOverlayBtn:hover {{
        background-color: {p['blue']};
        color: #ffffff;
        border-color: transparent;
    }}

    QPushButton#SlideArrowBtn {{
        background-color: {p['media_btn_bg']};
        border: 1px solid {p['media_btn_border']};
        color: {p['media_btn_fg']};
        border-radius: 16px;
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
        font-size: 16px;
        font-weight: 700;
        padding: 0px;
    }}

    QPushButton#SlideArrowBtn:hover {{
        background-color: {p['hover']};
    }}

    QPushButton#VideoCtrlBtn {{
        background-color: {p['media_btn_bg']};
        border: 1px solid {p['media_btn_border']};
        color: {p['media_btn_fg']};
        border-radius: 6px;
        padding: 0px;
        font-size: 14px;
        font-weight: 700;
        min-width: 34px;
        max-width: 34px;
        min-height: 28px;
        max-height: 28px;
    }}

    QPushButton#VideoCtrlBtn:hover {{
        background-color: {p['hover']};
    }}

    QPushButton#SegBtn {{
        background-color: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0px;
        color: {p['muted']};
        padding: 8px 10px;
        font-weight: 600;
    }}

    QPushButton#SegBtn[active="true"] {{
        color: {p['text']};
        border-bottom: 2px solid {p['text']};
    }}

    QCheckBox {{
        spacing: 8px;
        color: {p['text']};
        background: transparent;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1.5px solid {p['border']};
        border-radius: 4px;
        background-color: {p['surface_alt']};
    }}

    QCheckBox::indicator:checked {{
        background-color: {p['blue']};
        border-color: {p['blue']};
    }}

    QProgressBar {{
        background-color: {p['border_soft']};
        border: none;
        border-radius: 2px;
        height: 4px;
    }}

    QProgressBar::chunk {{
        background-color: {p['red']};
        border-radius: 2px;
    }}

    QScrollArea {{
        border: none;
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 0px;
    }}

    QScrollBar::handle:vertical {{
        background: {p['border']};
        border-radius: 3px;
        min-height: 30px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {p['subtle']};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
    }}

    QListWidget::item {{
        background: transparent;
        border: none;
        padding: 3px 0px;
    }}

    QPlainTextEdit {{
        background-color: {p['bg']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        font-family: "JetBrains Mono", "Fira Code", monospace;
        font-size: 11px;
        color: {p['muted']};
    }}

    QTabWidget::pane {{
        border: 1px solid {p['border']};
        border-radius: 8px;
        background-color: {p['surface']};
        padding: 8px;
    }}

    QTabBar::tab {{
        background-color: transparent;
        color: {p['muted']};
        padding: 8px 14px;
        border-bottom: 2px solid transparent;
    }}

    QTabBar::tab:selected {{
        color: {p['text']};
        border-bottom: 2px solid {p['blue']};
        font-weight: 700;
    }}

    QSpinBox, QComboBox {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 6px;
        padding: 5px 10px;
        color: {p['text']};
    }}

    QLabel#WarningBanner {{
        background-color: {p['red_soft']};
        border-bottom: 1px solid {p['red_border']};
        color: {p['red']};
        padding: 8px 16px;
        font-weight: 600;
    }}

    QPushButton#AccountPillBtn {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border']};
        border-radius: 16px;
        padding: 4px 12px 4px 8px;
        color: {p['text']};
        font-weight: 600;
        font-size: 12.5px;
    }}

    QPushButton#AccountPillBtn:hover {{
        background-color: {p['hover']};
        border-color: {p['muted']};
    }}

    QFrame#WelcomeCard {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 16px;
    }}

    QFrame#AccountRow {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['border_soft']};
        border-radius: 10px;
    }}

    QFrame#AccountRowActive {{
        background-color: {p['surface_alt']};
        border: 1px solid {p['green']};
        border-radius: 10px;
    }}
    """
