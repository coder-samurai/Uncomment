from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.account_manager import AccountManager
from core.database import CommentDatabase
from core.worker import UncommentWorker
from ui.account_dialog import AccountSwitcherDialog
from ui.i18n import SUPPORT_URL, tr
from ui.post_widgets import (
    CommentRowWidget,
    MediaPaneWidget,
    QueueItemWidget,
    apply_auto_bidi,
    format_comment_html,
    format_compact_count,
    make_circular_pixmap,
    make_initial_avatar,
)
from ui.settings_dialog import SettingsDialog
from ui.theme import build_qss, get_palette

class MainWindow(QMainWindow):
    STAGE_POST = 0
    STAGE_DONE = 1
    STAGE_WELCOME = 2
    STAGE_LOADING = 3

    def __init__(self, data_dir: Path, profile_dir: Path):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.profile_dir = Path(profile_dir)
        self.account_mgr = AccountManager(self.data_dir)
        self.settings = self.account_mgr.get_global_settings()

        self.active_account: dict[str, Any] | None = None
        self.db: CommentDatabase | None = None
        self.media_cache_dir: Path = self.data_dir / "media_cache"

        self.resize(1300, 820)
        self.setMinimumSize(840, 580)

        self.post_ids: list[str] = []
        self.current_index: int = 0
        self.current_bundle: dict[str, Any] | None = None
        self.comment_widgets: list[CommentRowWidget] = []
        self.all_row_widgets: list[CommentRowWidget] = []
        self.selected_ids: set[str] = set()
        self._caption_expanded: bool = False
        self._full_caption: str = ""
        self._welcome_msg_code: str = ""
        self._slides_cache: dict[str, list[dict[str, str]]] = {}

        self._hydrate_timer = QTimer(self)
        self._hydrate_timer.setSingleShot(True)
        self._hydrate_timer.setInterval(650)
        self._hydrate_timer.timeout.connect(self._trigger_silent_hydration)

        self.active_countdown_id: str | None = None
        self.is_connected: bool = False
        self.active_queue_tab: str = "queue"

        self.net_manager = QNetworkAccessManager(self)
        self._bytes_cache: OrderedDict[str, bytes] = OrderedDict()
        self._in_flight: dict[str, list[Any]] = {}

        self._build_ui()
        self._setup_shortcuts()
        self._apply_theme_and_language()

        self.worker = UncommentWorker(
            data_dir=self.data_dir,
            profile_dir=self.profile_dir,
            min_delay=int(self.settings["min_delay"]),
            max_delay=int(self.settings["max_delay"]),
            start_paused=not bool(self.settings["auto_start_queue"]),
            parent=self,
        )
        self._connect_worker_signals()
        self._update_pause_button()

        initial_acc = self.account_mgr.get_active_account()
        if initial_acc and Path(initial_acc["db_path"]).is_file():
            self._bind_account_ui(initial_acc)
        else:
            self._set_workspace_visible(False)
            self._show_loading_stage("loading_account")

        self.worker.start()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self.settings.get("language", "en"), key, **kwargs)

    def _palette(self) -> dict[str, str]:
        return get_palette(str(self.settings.get("theme", "instagram_dark")))

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(54)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 0, 16, 0)
        top_layout.setSpacing(10)

        self.lbl_brand = QLabel("Uncomment")
        self.lbl_brand.setObjectName("BrandTitle")
        top_layout.addWidget(self.lbl_brand)

        self.btn_account_pill = QPushButton()
        self.btn_account_pill.setObjectName("AccountPillBtn")
        self.btn_account_pill.setIconSize(QSize(22, 22))
        self.btn_account_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_account_pill.clicked.connect(self._on_open_account_switcher)
        self.btn_account_pill.hide()
        top_layout.addWidget(self.btn_account_pill)

        self.lbl_conn_dot = QLabel("●")
        self.lbl_conn_dot.setStyleSheet("color: #f59e0b; font-size: 11px; background: transparent;")
        self.lbl_conn_text = QLabel("")
        self.lbl_conn_text.setObjectName("Muted")
        top_layout.addWidget(self.lbl_conn_dot)
        top_layout.addWidget(self.lbl_conn_text)

        self.lbl_quota = QLabel("")
        self.lbl_quota.setObjectName("QuotaPill")
        top_layout.addWidget(self.lbl_quota)

        top_layout.addStretch()

        self.txt_search = QLineEdit()
        self.txt_search.setMinimumWidth(140)
        self.txt_search.setMaximumWidth(210)
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.returnPressed.connect(self._load_post_batch)
        top_layout.addWidget(self.txt_search)

        self.btn_sync = QPushButton()
        self.btn_sync.setObjectName("GhostBtn")
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.clicked.connect(lambda: self._on_sync_clicked(False))
        top_layout.addWidget(self.btn_sync)

        self.btn_settings = QPushButton()
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.clicked.connect(self._on_open_settings)
        top_layout.addWidget(self.btn_settings)

        root_layout.addWidget(top_bar)

        self.lbl_warning = QLabel("")
        self.lbl_warning.setObjectName("WarningBanner")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.hide()
        root_layout.addWidget(self.lbl_warning)

        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        self.stage_stack = QStackedWidget()
        self.stage_stack.addWidget(self._build_post_stage())
        self.stage_stack.addWidget(self._build_done_stage())
        self.stage_stack.addWidget(self._build_welcome_stage())
        self.stage_stack.addWidget(self._build_loading_stage())

        self.sidebar_widget = self._build_sidebar()
        body_row.addWidget(self.stage_stack, 1)
        body_row.addWidget(self.sidebar_widget)
        root_layout.addLayout(body_row, 1)

    def _build_post_stage(self) -> QWidget:
        stage = QWidget()
        layout = QVBoxLayout(stage)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("PostCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.media_pane = MediaPaneWidget()
        self.media_pane.load_media_requested.connect(self._on_request_post_media)
        self.media_pane.slide_image_requested.connect(self._on_slide_image_requested)

        details_pane = QWidget()
        details_pane.setMinimumWidth(300)
        details_pane.setMaximumWidth(540)
        details_layout = QVBoxLayout(details_pane)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("PostHeader")
        header.setFixedHeight(58)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(12, 0, 12, 0)
        h_layout.setSpacing(8)

        self.lbl_owner_avatar = QLabel()
        self.lbl_owner_avatar.setFixedSize(34, 34)
        h_layout.addWidget(self.lbl_owner_avatar)

        owner_col = QVBoxLayout()
        owner_col.setSpacing(1)
        self.lbl_post_owner = QLabel("@owner")
        self.lbl_post_owner.setObjectName("PostOwner")
        self.lbl_post_time = QLabel("")
        self.lbl_post_time.setObjectName("Subtle")
        owner_col.addWidget(self.lbl_post_owner)
        owner_col.addWidget(self.lbl_post_time)
        h_layout.addLayout(owner_col)
        h_layout.addStretch()

        self.btn_refresh_post = QPushButton("↻")
        self.btn_refresh_post.setObjectName("GhostBtn")
        self.btn_refresh_post.setFixedWidth(30)
        self.btn_refresh_post.setEnabled(False)
        self.btn_refresh_post.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh_post.clicked.connect(self._on_force_refresh_post)
        h_layout.addWidget(self.btn_refresh_post)

        self.btn_open_ig = QPushButton()
        self.btn_open_ig.setObjectName("GhostBtn")
        self.btn_open_ig.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_ig.clicked.connect(self._on_open_post)
        h_layout.addWidget(self.btn_open_ig)
        details_layout.addWidget(header)

        self.comments_scroll = QScrollArea()
        self.comments_scroll.setWidgetResizable(True)
        self.comments_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.comments_container = QWidget()
        self.comments_container.setStyleSheet("background: transparent;")
        self.comments_vbox = QVBoxLayout(self.comments_container)
        self.comments_vbox.setContentsMargins(12, 12, 12, 12)
        self.comments_vbox.setSpacing(8)

        self.lbl_post_caption = QLabel("")
        self.lbl_post_caption.setObjectName("PostCaption")
        self.lbl_post_caption.setWordWrap(True)
        self.lbl_post_caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.comments_vbox.addWidget(self.lbl_post_caption)

        self.btn_toggle_caption = QPushButton()
        self.btn_toggle_caption.setObjectName("SubtleLinkBtn")
        self.btn_toggle_caption.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_caption.clicked.connect(self._on_toggle_caption)
        self.btn_toggle_caption.hide()
        self.comments_vbox.addWidget(self.btn_toggle_caption, 0, Qt.AlignmentFlag.AlignLeft)

        self.caption_divider = QFrame()
        self.caption_divider.setObjectName("ThreadDivider")
        self.caption_divider.setFixedHeight(1)
        self.comments_vbox.addWidget(self.caption_divider)

        self.threads_host = QWidget()
        self.threads_host.setStyleSheet("background: transparent;")
        self.threads_vbox = QVBoxLayout(self.threads_host)
        self.threads_vbox.setContentsMargins(0, 4, 0, 4)
        self.threads_vbox.setSpacing(8)
        self.comments_vbox.addWidget(self.threads_host)
        self.comments_vbox.addStretch()

        self.comments_scroll.setWidget(self.comments_container)
        details_layout.addWidget(self.comments_scroll, 1)

        self.post_stats_bar = QFrame()
        self.post_stats_bar.setObjectName("PostStatsBar")
        ps_layout = QHBoxLayout(self.post_stats_bar)
        ps_layout.setContentsMargins(14, 7, 14, 7)
        self.lbl_post_stats = QLabel("")
        self.lbl_post_stats.setStyleSheet("font-size: 12.5px; font-weight: 700; background: transparent;")
        ps_layout.addWidget(self.lbl_post_stats)
        ps_layout.addStretch()
        self.post_stats_bar.hide()
        details_layout.addWidget(self.post_stats_bar)

        footer = QFrame()
        footer.setObjectName("PostFooter")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(10, 10, 10, 10)
        f_layout.setSpacing(6)

        self.btn_select_all = QPushButton()
        self.btn_select_all.setObjectName("GhostBtn")
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.clicked.connect(self._on_toggle_select_all)
        f_layout.addWidget(self.btn_select_all)

        f_layout.addStretch()

        self.btn_keep_post = QPushButton()
        self.btn_keep_post.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_keep_post.clicked.connect(self._on_keep_post)
        f_layout.addWidget(self.btn_keep_post)

        self.btn_add_queue = QPushButton()
        self.btn_add_queue.setObjectName("DangerBtn")
        self.btn_add_queue.setEnabled(False)
        self.btn_add_queue.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_queue.clicked.connect(self._on_add_selected_to_queue)
        f_layout.addWidget(self.btn_add_queue)
        details_layout.addWidget(footer)

        card_layout.addWidget(self.media_pane, 10)
        card_layout.addWidget(details_pane, 11)
        layout.addWidget(card, 1)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(12)
        nav_row.addStretch()

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("NavBtn")
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.clicked.connect(self._on_prev_post)
        nav_row.addWidget(self.btn_prev)

        self.lbl_post_counter = QLabel("")
        self.lbl_post_counter.setObjectName("Muted")
        self.lbl_post_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_post_counter.setMinimumWidth(110)
        nav_row.addWidget(self.lbl_post_counter)

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("NavBtn")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.clicked.connect(self._on_next_post)
        nav_row.addWidget(self.btn_next)

        nav_row.addStretch()
        layout.addLayout(nav_row)

        return stage

    def _build_done_stage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_done_title = QLabel("")
        self.lbl_done_title.setStyleSheet("font-size: 20px; font-weight: 700; background: transparent;")
        self.lbl_done_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_done_title)

        self.lbl_done_sub = QLabel("")
        self.lbl_done_sub.setObjectName("Muted")
        self.lbl_done_sub.setWordWrap(True)
        self.lbl_done_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_done_sub.setMaximumWidth(460)
        layout.addWidget(self.lbl_done_sub)

        self.chk_auto_close = QCheckBox()
        self.chk_auto_close.setChecked(bool(self.settings.get("auto_close_done", False)))
        layout.addWidget(self.chk_auto_close, 0, Qt.AlignmentFlag.AlignCenter)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.btn_done_back = QPushButton()
        self.btn_done_back.clicked.connect(self._on_back_from_done)
        self.btn_done_more = QPushButton()
        self.btn_done_more.setObjectName("PrimaryBtn")
        self.btn_done_more.clicked.connect(self._load_post_batch)
        self.btn_done_sync = QPushButton()
        self.btn_done_sync.clicked.connect(lambda: self._on_sync_clicked(False))

        btns.addWidget(self.btn_done_back)
        btns.addWidget(self.btn_done_more)
        btns.addWidget(self.btn_done_sync)
        layout.addLayout(btns)
        return page

    def _build_welcome_stage(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("WelcomeCard")
        card.setFixedWidth(440)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 32, 32, 28)
        layout.setSpacing(16)

        title = QLabel("Uncomment")
        title.setStyleSheet("font-size: 26px; font-weight: 800; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.lbl_welcome_sub = QLabel("")
        self.lbl_welcome_sub.setObjectName("Muted")
        self.lbl_welcome_sub.setWordWrap(True)
        self.lbl_welcome_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_welcome_sub)

        self.lbl_welcome_msg = QLabel("")
        self.lbl_welcome_msg.setStyleSheet("color: #ed4956; font-size: 12px; background: transparent;")
        self.lbl_welcome_msg.setWordWrap(True)
        self.lbl_welcome_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_welcome_msg.hide()
        layout.addWidget(self.lbl_welcome_msg)

        self.lbl_welcome_recent = QLabel("")
        self.lbl_welcome_recent.setObjectName("Subtle")
        self.lbl_welcome_recent.hide()
        layout.addWidget(self.lbl_welcome_recent)

        self.welcome_accounts_host = QWidget()
        self.welcome_accounts_host.setStyleSheet("background: transparent;")
        self.welcome_accounts_box = QVBoxLayout(self.welcome_accounts_host)
        self.welcome_accounts_box.setContentsMargins(0, 0, 0, 0)
        self.welcome_accounts_box.setSpacing(8)
        layout.addWidget(self.welcome_accounts_host)

        self.btn_welcome_signin = QPushButton()
        self.btn_welcome_signin.setObjectName("PrimaryBtn")
        self.btn_welcome_signin.setMinimumHeight(40)
        self.btn_welcome_signin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_welcome_signin.clicked.connect(
            lambda: self._start_interactive_login(clear_cookies=True, prefill_username="")
        )
        layout.addWidget(self.btn_welcome_signin)

        lang_row = QHBoxLayout()
        lang_row.addStretch()
        self.btn_welcome_lang = QPushButton("English / العربية")
        self.btn_welcome_lang.setObjectName("SubtleLinkBtn")
        self.btn_welcome_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_welcome_lang.clicked.connect(self._toggle_language_quick)
        lang_row.addWidget(self.btn_welcome_lang)
        sep = QLabel("•")
        sep.setObjectName("Subtle")
        lang_row.addWidget(sep)

        self.btn_welcome_support = QPushButton()
        self.btn_welcome_support.setObjectName("SubtleLinkBtn")
        self.btn_welcome_support.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_welcome_support.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SUPPORT_URL))
        )
        lang_row.addWidget(self.btn_welcome_support)
        lang_row.addStretch()
        layout.addLayout(lang_row)

        outer.addWidget(card)
        return page

    def _build_loading_stage(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(14)

        self.lbl_loading_title = QLabel("")
        self.lbl_loading_title.setStyleSheet("font-size: 19px; font-weight: 700; background: transparent;")
        self.lbl_loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_loading_title)

        self.lbl_loading_sub = QLabel("")
        self.lbl_loading_sub.setObjectName("Muted")
        self.lbl_loading_sub.setWordWrap(True)
        self.lbl_loading_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_loading_sub.setMaximumWidth(420)
        layout.addWidget(self.lbl_loading_sub)

        bar = QProgressBar()
        bar.setFixedWidth(240)
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        layout.addWidget(bar, 0, Qt.AlignmentFlag.AlignCenter)

        return page

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(295)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.lbl_queue_title = QLabel("")
        self.lbl_queue_title.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        top_row.addWidget(self.lbl_queue_title)
        top_row.addStretch()

        self.btn_start_pause = QPushButton()
        self.btn_start_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_pause.clicked.connect(self._on_toggle_pause)
        top_row.addWidget(self.btn_start_pause)
        layout.addLayout(top_row)

        self.active_box = QFrame()
        self.active_box.setObjectName("ActiveCountdownBox")
        ab_layout = QVBoxLayout(self.active_box)
        ab_layout.setContentsMargins(10, 10, 10, 10)
        ab_layout.setSpacing(6)

        ab_top = QHBoxLayout()
        self.lbl_active_status = QLabel("")
        self.lbl_active_status.setObjectName("CountdownChip")
        ab_top.addWidget(self.lbl_active_status)
        ab_top.addStretch()

        self.btn_abort_active = QPushButton("×")
        self.btn_abort_active.setObjectName("CloseXBtn")
        self.btn_abort_active.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_abort_active.clicked.connect(self._on_abort_active)
        ab_top.addWidget(self.btn_abort_active)
        ab_layout.addLayout(ab_top)

        self.lbl_active_preview = QLabel("")
        self.lbl_active_preview.setObjectName("Muted")
        self.lbl_active_preview.setWordWrap(True)
        ab_layout.addWidget(self.lbl_active_preview)

        self.bar_countdown = QProgressBar()
        self.bar_countdown.setTextVisible(False)
        ab_layout.addWidget(self.bar_countdown)
        self.active_box.hide()
        layout.addWidget(self.active_box)

        self.lbl_queue_hint = QLabel("")
        self.lbl_queue_hint.setObjectName("Muted")
        self.lbl_queue_hint.setWordWrap(True)
        self.lbl_queue_hint.hide()
        layout.addWidget(self.lbl_queue_hint)

        seg_row = QHBoxLayout()
        seg_row.setSpacing(4)
        self.btn_tab_queue = QPushButton()
        self.btn_tab_queue.setObjectName("SegBtn")
        self.btn_tab_queue.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_queue.clicked.connect(lambda: self._switch_sidebar_tab("queue"))

        self.btn_tab_deleted = QPushButton()
        self.btn_tab_deleted.setObjectName("SegBtn")
        self.btn_tab_deleted.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_deleted.clicked.connect(lambda: self._switch_sidebar_tab("deleted"))

        self.btn_tab_logs = QPushButton()
        self.btn_tab_logs.setObjectName("SegBtn")
        self.btn_tab_logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_logs.clicked.connect(lambda: self._switch_sidebar_tab("logs"))

        seg_row.addWidget(self.btn_tab_queue, 1)
        seg_row.addWidget(self.btn_tab_deleted, 1)
        seg_row.addWidget(self.btn_tab_logs, 1)
        layout.addLayout(seg_row)

        self.sidebar_stack = QStackedWidget()
        self.list_queue = QListWidget()
        self.list_deleted = QListWidget()
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(400)

        self.sidebar_stack.addWidget(self.list_queue)
        self.sidebar_stack.addWidget(self.list_deleted)
        self.sidebar_stack.addWidget(self.txt_log)
        layout.addWidget(self.sidebar_stack, 1)

        return sidebar

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Left"), self).activated.connect(self._shortcut_left)
        QShortcut(QKeySequence("Right"), self).activated.connect(self._shortcut_right)
        QShortcut(QKeySequence("A"), self).activated.connect(self._on_toggle_select_all)
        QShortcut(QKeySequence("Return"), self).activated.connect(self._on_add_selected_to_queue)
        QShortcut(QKeySequence("O"), self).activated.connect(self._on_open_post)
        QShortcut(QKeySequence("R"), self).activated.connect(self._on_force_refresh_post)

    def _shortcut_left(self) -> None:
        if self.txt_search.hasFocus() or self.stage_stack.currentIndex() not in (self.STAGE_POST, self.STAGE_DONE):
            return
        if self.settings.get("language") == "ar":
            self._on_next_post()
        else:
            self._on_prev_post()

    def _shortcut_right(self) -> None:
        if self.txt_search.hasFocus() or self.stage_stack.currentIndex() not in (self.STAGE_POST, self.STAGE_DONE):
            return
        if self.settings.get("language") == "ar":
            self._on_prev_post()
        else:
            self._on_next_post()

    def _set_workspace_visible(self, visible: bool) -> None:
        self.sidebar_widget.setVisible(visible)
        self.btn_account_pill.setVisible(visible and self.active_account is not None)
        self.lbl_conn_dot.setVisible(visible)
        self.lbl_conn_text.setVisible(visible)
        self.lbl_quota.setVisible(visible)
        self.txt_search.setVisible(visible)
        self.btn_sync.setVisible(visible)
        self.btn_settings.setVisible(visible)

    def _resolve_auth_msg(self, code: str) -> str:
        if not code or code == "AUTH_NEEDED":
            return ""
        if code == "SESSION_EXPIRED":
            return self._t("err_session_expired")
        if code == "LOGIN_TIMEOUT":
            return self._t("err_login_timeout")
        return code

    def _show_welcome_stage(self, code: str = "") -> None:
        self._hydrate_timer.stop()
        self.media_pane.stop_video()
        self._set_workspace_visible(False)
        self._welcome_msg_code = code

        msg = self._resolve_auth_msg(code)
        if msg:
            self.lbl_welcome_msg.setText(msg)
            self.lbl_welcome_msg.show()
        else:
            self.lbl_welcome_msg.hide()

        while self.welcome_accounts_box.count():
            child = self.welcome_accounts_box.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        saved = self.account_mgr.list_accounts()
        pal = self._palette()
        if saved:
            self.lbl_welcome_recent.show()
            for acc in saved:
                uname = str(acc["username"])
                av_path = str(acc.get("avatar_path") or "")
                btn = QPushButton(f"  @{uname}")
                btn.setObjectName("AccountPillBtn")
                btn.setMinimumHeight(40)
                btn.setIconSize(QSize(24, 24))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                pix = QPixmap(av_path) if av_path and Path(av_path).is_file() else QPixmap()
                if not pix.isNull():
                    btn.setIcon(QIcon(make_circular_pixmap(pix, 24)))
                else:
                    btn.setIcon(QIcon(make_initial_avatar(uname, 24, pal["avatar_bg"], pal["avatar_fg"])))
                btn.clicked.connect(
                    lambda _=False, u=uname: self._start_interactive_login(clear_cookies=True, prefill_username=u)
                )
                self.welcome_accounts_box.addWidget(btn)
        else:
            self.lbl_welcome_recent.hide()

        self.stage_stack.setCurrentIndex(self.STAGE_WELCOME)

    def _show_loading_stage(self, phase: str) -> None:
        self._hydrate_timer.stop()
        self.media_pane.stop_video()
        self._set_workspace_visible(False)
        if phase == "waiting_login":
            self.lbl_loading_title.setText(self._t("loading_waiting_title"))
            self.lbl_loading_sub.setText(self._t("loading_waiting_sub"))
        else:
            self.lbl_loading_title.setText(self._t("loading_account_title"))
            self.lbl_loading_sub.setText(self._t("loading_account_sub"))
        self.stage_stack.setCurrentIndex(self.STAGE_LOADING)

    def _update_account_pill(self) -> None:
        if not self.active_account:
            self.btn_account_pill.hide()
            return
        uname = str(self.active_account["username"])
        av_path = str(self.active_account.get("avatar_path") or "")
        pal = self._palette()
        pix = QPixmap(av_path) if av_path and Path(av_path).is_file() else QPixmap()
        if not pix.isNull():
            self.btn_account_pill.setIcon(QIcon(make_circular_pixmap(pix, 22)))
        else:
            self.btn_account_pill.setIcon(QIcon(make_initial_avatar(uname, 22, pal["avatar_bg"], pal["avatar_fg"])))
        self.btn_account_pill.setText(f" @{uname} ▾")
        self.btn_account_pill.show()

    def _bind_account_ui(self, account_info: dict[str, Any]) -> None:
        same_user = (
            self.active_account is not None
            and self.active_account.get("user_id") == account_info.get("user_id")
            and self.db is not None
        )
        self.active_account = dict(account_info)
        self.media_cache_dir = Path(account_info["media_cache_dir"])
        self.media_cache_dir.mkdir(parents=True, exist_ok=True)

        if not same_user:
            if self.db is not None:
                self.db.close()
            self.db = CommentDatabase(Path(account_info["db_path"]))
            self.settings = self.db.save_settings(self.account_mgr.get_global_settings())
            self._slides_cache.clear()
            self.post_ids.clear()
            self.current_index = 0
            self.current_bundle = None
            self.selected_ids.clear()

        self._update_account_pill()
        self._set_workspace_visible(True)
        self._refresh_sidebar()
        if not same_user or not self.post_ids:
            self._load_post_batch()
        elif self.current_bundle:
            self._render_current_post(preserve_media=True, preserve_selection=True)

    def _on_open_account_switcher(self) -> None:
        dlg = AccountSwitcherDialog(
            accounts=self.account_mgr.list_accounts(),
            active_user_id=str((self.active_account or {}).get("user_id") or ""),
            lang=str(self.settings.get("language", "en")),
            palette=self._palette(),
            parent=self,
        )
        dlg.switch_account_requested.connect(
            lambda uname: self._start_interactive_login(clear_cookies=True, prefill_username=uname)
        )
        dlg.add_account_requested.connect(
            lambda: self._start_interactive_login(clear_cookies=True, prefill_username="")
        )
        dlg.logout_requested.connect(self._on_logout_requested)
        dlg.remove_saved_requested.connect(lambda uid: self.account_mgr.remove_account_entry(uid, delete_data=False))
        dlg.exec()

    def _start_interactive_login(self, clear_cookies: bool = True, prefill_username: str = "") -> None:
        if clear_cookies and self.db is not None:
            self.db.close()
            self.db = None
            self.active_account = None
        self._show_loading_stage("waiting_login")
        self.worker.request_interactive_login(
            clear_cookies=clear_cookies,
            prefill_username=prefill_username,
        )

    def _on_logout_requested(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        self.active_account = None
        self.post_ids.clear()
        self.current_bundle = None
        self.worker.request_logout()

    def _toggle_language_quick(self) -> None:
        new_lang = "en" if self.settings.get("language", "en") == "ar" else "ar"
        self.settings = self.account_mgr.save_global_settings({"language": new_lang})
        self._apply_theme_and_language()
        if self.stage_stack.currentIndex() == self.STAGE_WELCOME:
            self._show_welcome_stage(self._welcome_msg_code)

    def _apply_theme_and_language(self) -> None:
        is_ar = self.settings.get("language", "en") == "ar"
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if is_ar else Qt.LayoutDirection.LeftToRight)
        self.setWindowTitle(self._t("app_title"))

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(self.settings.get("theme", "instagram_dark")))

        self.lbl_brand.setText(self._t("app_title"))
        self.txt_search.setPlaceholderText(self._t("search_placeholder"))
        self.btn_sync.setText(self._t("sync"))
        self.btn_settings.setText(self._t("settings"))
        self.btn_refresh_post.setToolTip(self._t("refresh_post_tip"))
        self.btn_open_ig.setText(self._t("open_ig"))
        self.btn_keep_post.setText(self._t("keep_post"))
        self.btn_keep_post.setToolTip(self._t("keep_post_tip"))
        self.btn_prev.setText(f"‹ {self._t('prev_post')}" if not is_ar else f"{self._t('prev_post')} ›")
        self.btn_next.setText(f"{self._t('next_post')} ›" if not is_ar else f"‹ {self._t('next_post')}")
        self.btn_done_back.setText(self._t("prev_post"))
        self.btn_done_more.setText(self._t("load_more_posts"))
        self.btn_done_sync.setText(self._t("sync_older"))
        self.chk_auto_close.setText(self._t("auto_close_chk"))
        self.btn_abort_active.setToolTip(self._t("undo_tooltip"))
        self.btn_welcome_support.setText(self._t("support_btn"))
        self.lbl_welcome_sub.setText(self._t("welcome_subtitle"))
        self.btn_welcome_signin.setText(self._t("welcome_signin_btn"))
        self.lbl_welcome_recent.setText(self._t("welcome_recent_label"))

        self.btn_tab_logs.setVisible(bool(self.settings.get("show_dev_logs", False)))
        if not self.settings.get("show_dev_logs", False) and self.active_queue_tab == "logs":
            self.active_queue_tab = "queue"
        self._switch_sidebar_tab(self.active_queue_tab)

        self._update_account_pill()
        self._update_connection_badge()
        self._update_action_buttons()

    def _switch_sidebar_tab(self, tab: str) -> None:
        self.active_queue_tab = tab
        idx_map = {"queue": 0, "deleted": 1, "logs": 2}
        self.sidebar_stack.setCurrentIndex(idx_map.get(tab, 0))
        for name, btn in (
            ("queue", self.btn_tab_queue),
            ("deleted", self.btn_tab_deleted),
            ("logs", self.btn_tab_logs),
        ):
            btn.setProperty("active", "true" if name == tab else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _load_post_batch(self) -> None:
        if self.db is None:
            return
        self.post_ids = self.db.get_unreviewed_post_ids(
            sort_mode=str(self.settings.get("sort_mode", "random")),
            limit=int(self.settings.get("batch_posts", 25)),
            search_query=self.txt_search.text(),
        )
        self.current_index = 0
        self._render_current_post()

    def _render_current_post(self, preserve_media: bool = False, preserve_selection: bool = False) -> None:
        if self.db is None:
            return
        self._hydrate_timer.stop()
        if not preserve_media:
            self.media_pane.stop_video()
            self.btn_refresh_post.setEnabled(self.is_connected)
            self.btn_refresh_post.setText("↻")

        if not self.post_ids or self.current_index >= len(self.post_ids):
            self.current_bundle = None
            self.stage_stack.setCurrentIndex(self.STAGE_DONE)
            self._update_done_stage()
            return

        pid = self.post_ids[self.current_index]
        bundle = self.db.get_post_bundle(pid)
        if not bundle:
            self.post_ids.pop(self.current_index)
            self._render_current_post()
            return

        prev_selected = set(self.selected_ids) if preserve_selection else set()
        self.current_bundle = bundle
        self.selected_ids.clear()
        self.comment_widgets.clear()
        self.all_row_widgets.clear()
        pal = self._palette()

        owner = bundle["post_author_username"]
        self.lbl_post_owner.setText(owner)
        self.lbl_post_time.setText(bundle["post_time"])
        self.btn_open_ig.setEnabled(bool(bundle["post_url"]))

        self.lbl_owner_avatar.setPixmap(
            make_initial_avatar(owner, 34, pal["avatar_bg"], pal["avatar_fg"])
        )
        if bundle.get("post_owner_avatar_url"):
            self._fetch_bytes(
                bundle["post_owner_avatar_url"],
                lambda raw, p=pid: self._apply_owner_avatar(raw, p),
            )

        if not preserve_media:
            self.media_pane.reset_for_post(self._t("load_post_btn"), self._t("load_media_tip"))
            if bundle.get("post_image_url"):
                self._fetch_bytes(
                    bundle["post_image_url"],
                    lambda raw, p=pid: self._apply_post_still(raw, p),
                )
            else:
                self.media_pane.lbl_image.setText("Instagram")

        self._full_caption = (bundle.get("post_text") or "").strip()
        if not preserve_selection:
            self._caption_expanded = False
        self._render_caption()

        likes = bundle.get("like_count")
        c_cnt = bundle.get("comment_count")
        if likes is not None:
            likes_str = format_compact_count(likes)
            if c_cnt == 1:
                self.lbl_post_stats.setText(self._t("post_stats_one", likes=likes_str))
            elif c_cnt is not None:
                self.lbl_post_stats.setText(
                    self._t("post_stats", likes=likes_str, comments=format_compact_count(c_cnt))
                )
            else:
                self.lbl_post_stats.setText(self._t("post_likes_only", likes=likes_str))
            self.post_stats_bar.show()
        else:
            self.post_stats_bar.hide()

        while self.threads_vbox.count():
            child = self.threads_vbox.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        status_labels = {
            "QUEUED_DELETE": self._t("status_queued"),
            "DELETING": self._t("status_deleting"),
            "DELETED": self._t("status_deleted"),
            "KEEP": self._t("status_keep"),
            "FAILED": self._t("status_failed"),
        }

        threads = bundle.get("threads") or []
        for t_idx, group in enumerate(threads):
            if t_idx > 0:
                div = QFrame()
                div.setObjectName("ThreadDivider")
                div.setFixedHeight(1)
                self.threads_vbox.addWidget(div)

            for m_idx, item in enumerate(group):
                st = item.get("status", "CONTEXT")
                c_likes = item.get("likes")
                likes_txt = ""
                if isinstance(c_likes, int) and c_likes > 0:
                    likes_txt = (
                        self._t("likes_single")
                        if c_likes == 1
                        else self._t("likes_plural", n=format_compact_count(c_likes))
                    )

                cid = str(item.get("comment_id") or "")
                is_mine = bool(item.get("is_mine"))
                cached_raw = self._read_local_media_cache(cid) if is_mine else None

                row_w = CommentRowWidget(
                    item=item,
                    is_reply_indent=(m_idx > 0),
                    status_label=status_labels.get(st),
                    you_tag=self._t("your_comment_tag"),
                    likes_text=likes_txt,
                    photo_unavailable_text=self._t("photo_unavailable_badge"),
                    palette=pal,
                    has_local_media=(cached_raw is not None),
                )
                if row_w.selectable:
                    row_w.toggled.connect(self._on_comment_toggled)
                    self.comment_widgets.append(row_w)
                    if row_w.comment_id in prev_selected:
                        row_w.set_selected(True, emit_signal=False)
                        self.selected_ids.add(row_w.comment_id)

                self.all_row_widgets.append(row_w)
                self.threads_vbox.addWidget(row_w)

                if row_w.avatar_url:
                    self._fetch_bytes(
                        row_w.avatar_url,
                        lambda raw, w=row_w, p=pid: self._apply_row_avatar(raw, w, p),
                    )
                if cached_raw is not None and row_w.lbl_media is not None:
                    row_w.set_media_bytes(cached_raw)
                elif row_w.media_url:
                    self._fetch_bytes(
                        row_w.media_url,
                        lambda raw, w=row_w, p=pid: self._apply_row_media(raw, w, p),
                    )

        if not preserve_selection:
            self.comments_scroll.verticalScrollBar().setValue(0)
        self.stage_stack.setCurrentIndex(self.STAGE_POST)
        self._update_nav_controls()
        self._update_action_buttons()

        if self.is_connected and not bundle.get("is_hydrated"):
            self._hydrate_timer.start()

    def _trigger_silent_hydration(self) -> None:
        if not self.is_connected or not self.current_bundle or self.current_bundle.get("is_hydrated"):
            return
        pid = self.current_bundle["post_id"]
        my_comments = self.current_bundle.get("my_comments") or []
        cids = [str(c["comment_id"]) for c in my_comments if c.get("comment_id")]
        my_user = str((self.active_account or {}).get("username") or "")
        if cids:
            self.worker.request_hydrate_post(pid, cids, my_username=my_user, force=False)

    def _on_force_refresh_post(self) -> None:
        if not self.is_connected or not self.current_bundle:
            return
        pid = self.current_bundle["post_id"]
        self._slides_cache.pop(pid, None)
        my_comments = self.current_bundle.get("my_comments") or []
        cids = [str(c["comment_id"]) for c in my_comments if c.get("comment_id")]
        my_user = str((self.active_account or {}).get("username") or "")
        if cids:
            self.btn_refresh_post.setEnabled(False)
            self.btn_refresh_post.setText("...")
            self.worker.request_hydrate_post(pid, cids, my_username=my_user, force=True)

    def _on_post_hydrated(self, post_id: str, slides: list[dict[str, str]]) -> None:
        if slides:
            self._slides_cache[post_id] = slides
        if self.current_bundle and self.current_bundle["post_id"] == post_id:
            scroll_pos = self.comments_scroll.verticalScrollBar().value()
            self._render_current_post(preserve_media=True, preserve_selection=True)
            QTimer.singleShot(0, lambda p=scroll_pos: self.comments_scroll.verticalScrollBar().setValue(p))
            self.btn_refresh_post.setEnabled(True)
            self.btn_refresh_post.setText("✓")
            QTimer.singleShot(1200, lambda: self.btn_refresh_post.setText("↻"))
        else:
            self.btn_refresh_post.setEnabled(self.is_connected)
            self.btn_refresh_post.setText("↻")

    def _on_post_hydrate_failed(self, _post_id: str) -> None:
        self.btn_refresh_post.setEnabled(self.is_connected)
        self.btn_refresh_post.setText("!")
        QTimer.singleShot(1500, lambda: self.btn_refresh_post.setText("↻"))

    def _render_caption(self) -> None:
        if not self._full_caption:
            self.lbl_post_caption.hide()
            self.btn_toggle_caption.hide()
            self.caption_divider.hide()
            return

        pal = self._palette()
        is_long = len(self._full_caption) > 160 or self._full_caption.count("\n") >= 3
        display_text = self._full_caption
        if is_long and not self._caption_expanded:
            lines = self._full_caption.splitlines()
            if len(lines) > 3:
                display_text = "\n".join(lines[:3])
            if len(display_text) > 160:
                display_text = display_text[:160].rstrip()
            display_text += "..."

        rich = format_comment_html(display_text, pal["mention"])
        apply_auto_bidi(self.lbl_post_caption, display_text, as_rich_html=rich)
        self.lbl_post_caption.show()
        self.caption_divider.show()

        if is_long:
            self.btn_toggle_caption.setText(self._t("less") if self._caption_expanded else self._t("more"))
            self.btn_toggle_caption.show()
        else:
            self.btn_toggle_caption.hide()

    def _on_toggle_caption(self) -> None:
        self._caption_expanded = not self._caption_expanded
        self._render_caption()

    def _update_nav_controls(self) -> None:
        total = len(self.post_ids)
        curr = min(self.current_index + 1, total)
        self.lbl_post_counter.setText(self._t("post_counter", curr=curr, total=total))
        self.btn_prev.setEnabled(self.current_index > 0)
        self.btn_next.setEnabled(self.current_index < total)

    def _update_action_buttons(self) -> None:
        n_sel = len(self.selected_ids)
        self.btn_add_queue.setText(self._t("add_to_queue", n=n_sel))
        self.btn_add_queue.setEnabled(n_sel > 0)

        selectable_count = len(self.comment_widgets)
        self.btn_select_all.setEnabled(selectable_count > 0)
        all_selected = selectable_count > 0 and n_sel == selectable_count
        self.btn_select_all.setText(
            self._t("unselect_all") if all_selected else self._t("select_all")
        )

    def _on_comment_toggled(self, comment_id: str, selected: bool) -> None:
        if selected:
            self.selected_ids.add(comment_id)
        else:
            self.selected_ids.discard(comment_id)
        self._update_action_buttons()

    def _on_toggle_select_all(self) -> None:
        if not self.comment_widgets:
            return
        should_select = len(self.selected_ids) < len(self.comment_widgets)
        for w in self.comment_widgets:
            w.set_selected(should_select, emit_signal=False)
            if should_select:
                self.selected_ids.add(w.comment_id)
            else:
                self.selected_ids.discard(w.comment_id)
        self._update_action_buttons()

    def _on_add_selected_to_queue(self) -> None:
        if not self.selected_ids or not self.current_bundle or self.db is None:
            return
        self.db.set_many_status(list(self.selected_ids), "QUEUED_DELETE")
        unselected_unreviewed = [
            c["comment_id"]
            for c in self.current_bundle["my_comments"]
            if c["status"] == "UNREVIEWED" and c["comment_id"] not in self.selected_ids
        ]
        if unselected_unreviewed:
            self.db.set_many_status(unselected_unreviewed, "KEEP")

        self._refresh_sidebar()
        self._on_next_post()

    def _on_keep_post(self) -> None:
        if not self.current_bundle or self.db is None:
            return
        unreviewed = [
            c["comment_id"]
            for c in self.current_bundle["my_comments"]
            if c["status"] == "UNREVIEWED"
        ]
        if unreviewed:
            self.db.set_many_status(unreviewed, "KEEP")
        self._refresh_sidebar()
        self._on_next_post()

    def _on_prev_post(self) -> None:
        if self.stage_stack.currentIndex() == self.STAGE_DONE and self.post_ids:
            self.current_index = len(self.post_ids) - 1
            self._render_current_post()
            return
        if self.current_index > 0:
            self.current_index -= 1
            self._render_current_post()

    def _on_next_post(self) -> None:
        if self.current_index < len(self.post_ids):
            self.current_index += 1
            self._render_current_post()

    def _on_back_from_done(self) -> None:
        if self.post_ids:
            self.current_index = len(self.post_ids) - 1
            self._render_current_post()

    def _on_open_post(self) -> None:
        if self.current_bundle and self.current_bundle.get("post_url"):
            QDesktopServices.openUrl(QUrl(self.current_bundle["post_url"]))

    def _on_request_post_media(self) -> None:
        if not self.current_bundle:
            return
        pid = self.current_bundle["post_id"]
        if pid in self._slides_cache:
            self.media_pane.set_loaded_slides(self._slides_cache[pid], self._t("video_unavailable"))
            return
        self.media_pane.set_loading(self._t("loading_media"))
        self.worker.request_post_media(pid)

    def _on_post_media_loaded(self, post_id: str, slides: list[dict[str, str]]) -> None:
        if slides:
            self._slides_cache[post_id] = slides
        if not self.current_bundle or self.current_bundle["post_id"] != post_id:
            return
        self.media_pane.set_loaded_slides(slides, self._t("video_unavailable"))

    def _on_post_media_failed(self, post_id: str, err: str) -> None:
        if not self.current_bundle or self.current_bundle["post_id"] != post_id:
            return
        self.media_pane.set_error(err)

    def _on_slide_image_requested(self, url: str) -> None:
        if not self.current_bundle:
            return
        pid = self.current_bundle["post_id"]
        self._fetch_bytes(url, lambda raw, p=pid: self._apply_post_still(raw, p))

    def _read_local_media_cache(self, comment_id: str) -> bytes | None:
        if not comment_id:
            return None
        for ext in ("gif", "webp", "jpg", "png", "bin"):
            p = self.media_cache_dir / f"{comment_id}.{ext}"
            if p.is_file():
                try:
                    return p.read_bytes()
                except OSError:
                    return None
        return None

    def _save_local_media_cache(self, comment_id: str, raw: bytes) -> None:
        if not comment_id or not raw:
            return
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            ext = "gif"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext = "webp"
        elif raw[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        elif raw[:2] == b"\xff\xd8":
            ext = "jpg"
        else:
            ext = "bin"
        target = self.media_cache_dir / f"{comment_id}.{ext}"
        if not target.exists():
            try:
                target.write_bytes(raw)
            except OSError:
                pass

    def _fetch_bytes(self, url: str, callback) -> None:
        if not url:
            return
        if url in self._bytes_cache:
            self._bytes_cache.move_to_end(url)
            callback(self._bytes_cache[url])
            return

        if url in self._in_flight:
            self._in_flight[url].append(callback)
            return

        self._in_flight[url] = [callback]
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(
            b"User-Agent",
            b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        )
        reply = self.net_manager.get(req)
        reply.finished.connect(lambda r=reply, u=url: self._on_bytes_finished(r, u))

    def _on_bytes_finished(self, reply: QNetworkReply, url: str) -> None:
        callbacks = self._in_flight.pop(url, [])
        if reply.error() == QNetworkReply.NetworkError.NoError:
            raw = bytes(reply.readAll())
            if raw:
                self._bytes_cache[url] = raw
                if len(self._bytes_cache) > 150:
                    self._bytes_cache.popitem(last=False)
                for cb in callbacks:
                    try:
                        cb(raw)
                    except RuntimeError:
                        pass
        reply.deleteLater()

    def _apply_owner_avatar(self, raw: bytes, expected_pid: str) -> None:
        if not self.current_bundle or self.current_bundle["post_id"] != expected_pid:
            return
        pix = QPixmap()
        if pix.loadFromData(raw):
            self.lbl_owner_avatar.setPixmap(make_circular_pixmap(pix, 34))

    def _apply_post_still(self, raw: bytes, expected_pid: str) -> None:
        if not self.current_bundle or self.current_bundle["post_id"] != expected_pid:
            return
        pix = QPixmap()
        if pix.loadFromData(raw):
            self.media_pane.set_still_pixmap(pix)

    def _apply_row_avatar(self, raw: bytes, widget: CommentRowWidget, expected_pid: str) -> None:
        if not self.current_bundle or self.current_bundle["post_id"] != expected_pid:
            return
        if widget not in self.all_row_widgets:
            return
        pix = QPixmap()
        if pix.loadFromData(raw):
            widget.set_avatar_pixmap(pix)

    def _apply_row_media(self, raw: bytes, widget: CommentRowWidget, expected_pid: str) -> None:
        if widget.is_mine and widget.comment_id:
            self._save_local_media_cache(widget.comment_id, raw)
        if not self.current_bundle or self.current_bundle["post_id"] != expected_pid:
            return
        if widget not in self.all_row_widgets:
            return
        widget.set_media_bytes(raw)

    def _on_toggle_pause(self) -> None:
        new_paused = not self.worker.is_paused
        self.worker.set_paused(new_paused)
        self._update_pause_button()
        if new_paused:
            self.active_box.hide()
            self.active_countdown_id = None
            self._show_queue_hint(self._t("queue_idle_paused"))
        else:
            self.lbl_queue_hint.hide()

    def _update_pause_button(self) -> None:
        if self.worker.is_paused:
            self.btn_start_pause.setText(f"▶ {self._t('btn_start')}")
            self.btn_start_pause.setObjectName("PrimaryBtn")
        else:
            self.btn_start_pause.setText(f"⏸ {self._t('btn_pause')}")
            self.btn_start_pause.setObjectName("")
        self.btn_start_pause.style().unpolish(self.btn_start_pause)
        self.btn_start_pause.style().polish(self.btn_start_pause)

    def _on_remove_from_queue(self, comment_id: str) -> None:
        if self.db is None:
            return
        if self.db.cancel_queued(comment_id):
            if comment_id == self.active_countdown_id:
                self.worker.cancel_active_countdown(comment_id)
                self.active_countdown_id = None
                self.active_box.hide()
            self._refresh_sidebar()
            if self.current_bundle:
                if any(c["comment_id"] == comment_id for c in self.current_bundle["my_comments"]):
                    self._render_current_post(preserve_media=True, preserve_selection=True)

    def _on_abort_active(self) -> None:
        if self.active_countdown_id:
            self._on_remove_from_queue(self.active_countdown_id)

    def _on_sync_clicked(self, reset_cursor: bool = False) -> None:
        self.btn_sync.setEnabled(False)
        self.btn_done_sync.setEnabled(False)
        pages = int(self.settings.get("sync_pages", 10))
        self.btn_sync.setText(self._t("syncing", p=0, m=pages))
        self.worker.request_sync(max_pages=pages, reset_cursor=reset_cursor)

    def _on_open_settings(self) -> None:
        if self.db is None:
            return
        dlg = SettingsDialog(
            db=self.db,
            account_mgr=self.account_mgr,
            profile_dir=self.profile_dir,
            active_user_id=str((self.active_account or {}).get("user_id") or ""),
            parent=self,
        )
        dlg.clear_cache_requested.connect(self._on_clear_cache_requested)
        dlg.factory_reset_requested.connect(self._on_factory_reset_requested)
        dlg.data_changed.connect(self._on_settings_data_changed)
        if dlg.exec() and dlg.result_settings:
            old_sort = self.settings.get("sort_mode")
            self.settings = self.account_mgr.save_global_settings(dlg.result_settings)
            self.worker.update_delays(
                int(self.settings["min_delay"]),
                int(self.settings["max_delay"]),
            )
            self._apply_theme_and_language()
            self._update_pause_button()
            self._refresh_sidebar()
            if self.settings.get("sort_mode") != old_sort:
                self._load_post_batch()
            elif self.current_bundle:
                self._render_current_post(preserve_media=True, preserve_selection=True)

    def _on_settings_data_changed(self) -> None:
        self._refresh_sidebar()
        self._load_post_batch()

    def _on_clear_cache_requested(self) -> None:
        self._bytes_cache.clear()
        self._slides_cache.clear()
        self._refresh_sidebar()

    def _on_factory_reset_requested(self) -> None:
        self._hydrate_timer.stop()
        self.media_pane.stop_video()
        if self.db is not None:
            self.db.close()
            self.db = None
        self.active_account = None
        self.post_ids.clear()
        self.current_bundle = None
        self.selected_ids.clear()
        self._bytes_cache.clear()
        self._slides_cache.clear()
        self._set_workspace_visible(False)
        self.lbl_loading_title.setText(self._t("loading_reset_title"))
        self.lbl_loading_sub.setText("")
        self.stage_stack.setCurrentIndex(self.STAGE_LOADING)
        if self.worker.isRunning():
            self.worker.request_factory_reset()
        else:
            self.account_mgr.wipe_all_data(self.profile_dir)
            self._on_factory_reset_done()

    def _on_factory_reset_done(self) -> None:
        self.settings = self.account_mgr.get_global_settings()
        self._apply_theme_and_language()
        self._show_welcome_stage("")

    def _connect_worker_signals(self) -> None:
        w = self.worker
        w.account_ready.connect(self._on_account_ready)
        w.session_ready.connect(self._on_session_ready)
        w.auth_required.connect(self._on_auth_required)
        w.login_phase.connect(self._on_login_phase)
        w.login_canceled.connect(self._on_login_canceled)
        w.logged_out.connect(self._on_logged_out)
        w.factory_reset_done.connect(self._on_factory_reset_done)
        w.stats_updated.connect(self._on_stats_updated)
        w.log_message.connect(self.txt_log.appendPlainText)

        w.sync_progress.connect(self._on_sync_progress)
        w.sync_finished.connect(self._on_sync_finished)
        w.post_media_loaded.connect(self._on_post_media_loaded)
        w.post_media_failed.connect(self._on_post_media_failed)
        w.post_hydrated.connect(self._on_post_hydrated)
        w.post_hydrate_failed.connect(self._on_post_hydrate_failed)

        w.countdown_tick.connect(self._on_countdown_tick)
        w.deleting_started.connect(self._on_deleting_started)
        w.comment_deleted.connect(self._on_comment_deleted)
        w.comment_failed.connect(self._on_comment_failed)
        w.watcher_idle.connect(self._on_watcher_idle)
        w.circuit_breaker_tripped.connect(self._on_circuit_breaker)

    def _on_account_ready(self, account_info: dict[str, Any]) -> None:
        self._bind_account_ui(account_info)

    def _on_login_phase(self, phase: str) -> None:
        if phase in ("waiting_login", "loading_account"):
            self._show_loading_stage(phase)

    def _on_login_canceled(self) -> None:
        if self.active_account and self.db is not None:
            self._set_workspace_visible(True)
            self.stage_stack.setCurrentIndex(
                self.STAGE_POST if self.current_bundle else self.STAGE_DONE
            )
        else:
            self._show_welcome_stage()

    def _on_logged_out(self) -> None:
        self._show_welcome_stage()

    def _on_auth_required(self, msg: str) -> None:
        self._show_welcome_stage(msg)

    def _update_connection_badge(self) -> None:
        if self.is_connected:
            self.lbl_conn_dot.setStyleSheet("color: #22c55e; font-size: 11px; background: transparent;")
            self.lbl_conn_text.setText(self._t("connected"))
        else:
            self.lbl_conn_dot.setStyleSheet("color: #f59e0b; font-size: 11px; background: transparent;")
            self.lbl_conn_text.setText(self._t("connecting"))

    def _on_session_ready(self, ready: bool) -> None:
        self.is_connected = ready
        self.btn_refresh_post.setEnabled(ready)
        if ready:
            self.lbl_conn_dot.setStyleSheet("color: #22c55e; font-size: 11px; background: transparent;")
            self.lbl_conn_text.setText(self._t("connected"))
            if self.current_bundle and not self.current_bundle.get("is_hydrated"):
                self._hydrate_timer.start()
        else:
            self.lbl_conn_dot.setStyleSheet("color: #ed4956; font-size: 11px; background: transparent;")
            self.lbl_conn_text.setText(self._t("disconnected"))

    def _on_stats_updated(self, stats: dict[str, int]) -> None:
        if self.db is None:
            return
        self._refresh_sidebar(stats)
        if self.stage_stack.currentIndex() == self.STAGE_DONE:
            self._update_done_stage()

    def _on_sync_progress(self, p: int, m: int, _inserted: int) -> None:
        self.btn_sync.setText(self._t("syncing", p=p, m=m))

    def _on_sync_finished(self, total_inserted: int, _has_more: bool) -> None:
        self.btn_sync.setEnabled(True)
        self.btn_done_sync.setEnabled(True)
        self.btn_sync.setText(self._t("sync"))
        if self.db is None:
            return
        self._refresh_sidebar()
        if self.current_bundle:
            self._render_current_post(preserve_media=True, preserve_selection=True)
        elif not self.post_ids or self.stage_stack.currentIndex() == self.STAGE_DONE:
            if total_inserted > 0:
                self._load_post_batch()
            else:
                self._update_done_stage()

    def _on_countdown_tick(self, item: dict[str, Any], sec_left: int, total_secs: int) -> None:
        self.active_countdown_id = item["comment_id"]
        self.lbl_queue_hint.hide()
        self.active_box.show()
        self.btn_abort_active.setEnabled(True)
        self.lbl_active_status.setText(self._t("deleting_in", s=sec_left))
        snippet = (item.get("comment_text") or "").replace("\n", " ")[:70]
        self.lbl_active_preview.setText(f"@{item['post_author_username']}: {snippet}")
        self.bar_countdown.setMaximum(max(1, total_secs))
        self.bar_countdown.setValue(sec_left)

    def _on_deleting_started(self, item: dict[str, Any]) -> None:
        self.active_countdown_id = None
        self.active_box.show()
        self.btn_abort_active.setEnabled(False)
        self.lbl_active_status.setText(self._t("deleting_now"))
        self.bar_countdown.setMaximum(0)

    def _on_comment_deleted(self, item: dict[str, Any]) -> None:
        self.bar_countdown.setMaximum(100)
        self.bar_countdown.setValue(0)
        self.active_box.hide()
        self._refresh_sidebar()
        if self.current_bundle and self.current_bundle["post_id"] == item.get("post_id"):
            self._render_current_post(preserve_media=True, preserve_selection=True)

    def _on_comment_failed(self, item: dict[str, Any], _err: str) -> None:
        self.bar_countdown.setMaximum(100)
        self.bar_countdown.setValue(0)
        self.active_box.hide()
        self._refresh_sidebar()

    def _on_watcher_idle(self, reason: str) -> None:
        self.active_box.hide()
        self.active_countdown_id = None

        if reason == "empty":
            self._show_queue_hint(self._t("queue_idle_empty"))
        elif reason == "paused":
            self._show_queue_hint(self._t("queue_idle_paused"))
        elif reason == "quota_reached":
            self._show_queue_hint(self._t("queue_idle_quota"))
        elif reason.startswith("cooldown:"):
            self._show_queue_hint(self._t("queue_idle_cooldown"))

        if self.stage_stack.currentIndex() == self.STAGE_DONE:
            self._update_done_stage()
            if self.chk_auto_close.isChecked() and reason in ("empty", "quota_reached"):
                QTimer.singleShot(1800, self.close)

    def _on_circuit_breaker(self, reason: str, until_iso: str) -> None:
        self.lbl_warning.setText(f"⛔ {reason} ({until_iso})")
        self.lbl_warning.show()

    def _show_queue_hint(self, text: str) -> None:
        self.lbl_queue_hint.setText(text)
        self.lbl_queue_hint.show()

    def _refresh_sidebar(self, stats: dict[str, int] | None = None) -> None:
        if self.db is None:
            return
        stats = stats or self.db.get_stats()
        pal = self._palette()
        self.lbl_quota.setText(
            self._t("today_quota", done=stats["deleted_today"], limit=stats["daily_limit"])
        )

        queue_rows = self.db.get_queue_list(limit=100)
        deleted_rows = self.db.get_recent_deleted(limit=60)

        self.lbl_queue_title.setText(self._t("queue_title", n=len(queue_rows)))
        self.btn_tab_queue.setText(self._t("tab_queue", n=len(queue_rows)))
        self.btn_tab_deleted.setText(self._t("tab_deleted", n=len(deleted_rows)))
        self.btn_tab_logs.setText(self._t("tab_logs"))

        def _populate_list(list_widget: QListWidget, rows: list[dict[str, Any]], removable: bool) -> None:
            list_widget.clear()
            for row in rows:
                cid = str(row["comment_id"])
                local_bytes = self._read_local_media_cache(cid)
                media_url = str(row.get("comment_media_url") or "")
                has_media = (local_bytes is not None) or bool(media_url)

                w = QueueItemWidget(
                    row=row,
                    undo_tooltip=self._t("undo_tooltip") if removable else "",
                    palette=pal,
                    can_remove=(removable and row.get("status") != "DELETING"),
                    has_media=has_media,
                )
                if removable:
                    w.remove_clicked.connect(self._on_remove_from_queue)

                li = QListWidgetItem(list_widget)
                w.size_changed.connect(lambda item=li, widget=w: item.setSizeHint(widget.sizeHint()))

                if local_bytes is not None:
                    w.set_media_bytes(local_bytes)
                elif media_url:
                    def _on_side_media(raw: bytes, widget=w, item=li, comment_id=cid):
                        self._save_local_media_cache(comment_id, raw)
                        try:
                            widget.set_media_bytes(raw)
                            item.setSizeHint(widget.sizeHint())
                        except RuntimeError:
                            pass
                    self._fetch_bytes(media_url, _on_side_media)

                w.adjustSize()
                li.setSizeHint(w.sizeHint())
                list_widget.addItem(li)
                list_widget.setItemWidget(li, w)

        _populate_list(self.list_queue, queue_rows, removable=True)
        _populate_list(self.list_deleted, deleted_rows, removable=False)

        in_cd, until = self.db.is_in_cooldown()
        if in_cd and until:
            self.lbl_warning.setText(f"⛔ {self._t('queue_idle_cooldown')} ({until})")
            self.lbl_warning.show()
        else:
            self.lbl_warning.hide()

    def _update_done_stage(self) -> None:
        if self.db is None:
            return
        stats = self.db.get_stats()
        q = stats["queued"]
        done = stats["deleted_today"]
        limit = stats["daily_limit"]
        rem = stats["remaining_today"]

        self.btn_done_back.setVisible(bool(self.post_ids))
        self.btn_done_more.setEnabled(stats["unreviewed"] > 0)

        if q > 0 and rem > 0:
            self.lbl_done_title.setText(self._t("done_title_working"))
            self.lbl_done_sub.setText(self._t("done_sub_working", q=q, done=done, limit=limit))
            self.chk_auto_close.show()
        elif q > 0 and rem == 0:
            self.lbl_done_title.setText(self._t("done_title_quota"))
            self.lbl_done_sub.setText(self._t("done_sub_quota", done=done, q=q))
            self.chk_auto_close.hide()
        else:
            self.lbl_done_title.setText(self._t("done_title_empty"))
            self.lbl_done_sub.setText(self._t("done_sub_empty", u=stats["unreviewed"]))
            self.chk_auto_close.hide()

    def closeEvent(self, event) -> None:
        self._hydrate_timer.stop()
        self.media_pane.stop_video()
        if self.db is not None:
            stats = self.db.get_stats()
            if (
                stats["queued"] > 0
                and stats["remaining_today"] > 0
                and not self.worker.is_paused
                and not self.chk_auto_close.isChecked()
                and self.worker.isRunning()
            ):
                reply = QMessageBox.question(
                    self,
                    self._t("close_warn_title"),
                    self._t("close_warn_body", q=stats["queued"]),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.No:
                    event.ignore()
                    return

        self.worker.stop()
        self.worker.wait(5000)
        if self.db is not None:
            self.db.close()
        event.accept()
