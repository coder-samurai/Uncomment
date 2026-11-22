from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.account_manager import AccountManager, format_bytes
from core.database import CommentDatabase
from ui.i18n import SUPPORT_URL

class SettingsDialog(QDialog):
    clear_cache_requested = pyqtSignal()
    factory_reset_requested = pyqtSignal()
    data_changed = pyqtSignal()

    def __init__(
        self,
        db: CommentDatabase,
        account_mgr: AccountManager,
        profile_dir: Path,
        active_user_id: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.db = db
        self.account_mgr = account_mgr
        self.profile_dir = Path(profile_dir)
        self.active_user_id = active_user_id
        self.settings = db.get_settings()
        self.result_settings: dict[str, Any] | None = None
        is_ar = self.settings.get("language", "en") == "ar"

        self.setWindowTitle("الإعدادات" if is_ar else "Settings")
        self.setMinimumWidth(490)
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_ar else Qt.LayoutDirection.LeftToRight
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._build_safety_tab(is_ar), "الأمان والسرعة" if is_ar else "Safety & Speed")
        tabs.addTab(self._build_review_tab(is_ar), "الفرز والمظهر" if is_ar else "Review & Theme")
        tabs.addTab(self._build_storage_tab(is_ar), "البيانات والتنظيف" if is_ar else "Data & Storage")
        root.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_support = QPushButton("☕ ادعم المشروع" if is_ar else "☕ Support on Ko-fi")
        btn_support.setObjectName("SubtleLinkBtn")
        btn_support.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_support.setToolTip(
            "دعم اختياري لتطوير هذا المشروع مفتوح المصدر"
            if is_ar
            else "Optional tip to support this free open-source project"
        )
        btn_support.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SUPPORT_URL)))
        btn_row.addWidget(btn_support)
        btn_row.addStretch()

        btn_cancel = QPushButton("إلغاء" if is_ar else "Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("حفظ التغييرات" if is_ar else "Save changes")
        btn_save.setObjectName("PrimaryBtn")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        root.addLayout(btn_row)

    def _build_safety_tab(self, is_ar: bool) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)

        preset_row = QHBoxLayout()
        lbl = QLabel("قوالب جاهزة:" if is_ar else "Quick presets:")
        lbl.setObjectName("Muted")
        preset_row.addWidget(lbl)

        btn_safe = QPushButton("آمن جداً (20/يوم)" if is_ar else "Extra Safe (20/day)")
        btn_safe.clicked.connect(lambda: self._apply_preset(20, 18, 35))
        btn_bal = QPushButton("متوازن (30/يوم)" if is_ar else "Balanced (30/day)")
        btn_bal.clicked.connect(lambda: self._apply_preset(30, 12, 26))
        preset_row.addWidget(btn_safe)
        preset_row.addWidget(btn_bal)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        form = QFormLayout()
        form.setSpacing(10)

        self.spin_limit = QSpinBox()
        self.spin_limit.setRange(1, 100)
        self.spin_limit.setValue(int(self.settings["daily_limit"]))
        form.addRow("الحد اليومي للحذف:" if is_ar else "Daily delete limit:", self.spin_limit)

        self.spin_min = QSpinBox()
        self.spin_min.setRange(5, 120)
        self.spin_min.setValue(int(self.settings["min_delay"]))
        self.spin_min.setSuffix(" ث" if is_ar else " s")
        form.addRow("أقل انتظار بين كل حذف:" if is_ar else "Min delay per delete:", self.spin_min)

        self.spin_max = QSpinBox()
        self.spin_max.setRange(6, 180)
        self.spin_max.setValue(int(self.settings["max_delay"]))
        self.spin_max.setSuffix(" ث" if is_ar else " s")
        form.addRow("أقصى انتظار بين كل حذف:" if is_ar else "Max delay per delete:", self.spin_max)

        layout.addLayout(form)

        self.chk_auto_start = QCheckBox(
            "تشغيل مراقب الحذف تلقائياً عند إضافة تعليقات"
            if is_ar
            else "Auto-start delete queue when comments are added"
        )
        self.chk_auto_start.setChecked(bool(self.settings["auto_start_queue"]))
        layout.addWidget(self.chk_auto_start)

        in_cd, until = self.db.is_in_cooldown()
        if in_cd:
            cd_row = QHBoxLayout()
            cd_lbl = QLabel(
                f"قاطع الدائرة نشط حتى: {until}" if is_ar else f"Cooldown active until: {until}"
            )
            cd_lbl.setStyleSheet("color: #ed4956;")
            btn_reset_cd = QPushButton("إلغاء الإيقاف الوقائي" if is_ar else "Reset cooldown")
            btn_reset_cd.clicked.connect(lambda: (self.db.clear_cooldown(), cd_lbl.setText("✓")))
            cd_row.addWidget(cd_lbl)
            cd_row.addWidget(btn_reset_cd)
            layout.addLayout(cd_row)

        layout.addStretch()
        return w

    def _build_review_tab(self, is_ar: bool) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self.cmb_sort = QComboBox()
        self.cmb_sort.addItem("عشوائي (موصى به)" if is_ar else "Random (recommended)", "random")
        self.cmb_sort.addItem("الأقدم أولاً" if is_ar else "Oldest first", "oldest")
        self.cmb_sort.addItem("الأحدث أولاً" if is_ar else "Newest first", "newest")
        idx = self.cmb_sort.findData(self.settings["sort_mode"])
        if idx >= 0:
            self.cmb_sort.setCurrentIndex(idx)
        form.addRow("ترتيب عرض البوستات:" if is_ar else "Post order:", self.cmb_sort)

        self.spin_batch = QSpinBox()
        self.spin_batch.setRange(5, 200)
        self.spin_batch.setValue(int(self.settings["batch_posts"]))
        form.addRow("عدد البوستات في كل دفعة:" if is_ar else "Posts per batch:", self.spin_batch)

        self.spin_sync = QSpinBox()
        self.spin_sync.setRange(1, 40)
        self.spin_sync.setValue(int(self.settings["sync_pages"]))
        form.addRow("الصفحات عند المزامنة:" if is_ar else "Pages per Sync:", self.spin_sync)

        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem("English", "en")
        self.cmb_lang.addItem("العربية", "ar")
        lidx = self.cmb_lang.findData(self.settings["language"])
        if lidx >= 0:
            self.cmb_lang.setCurrentIndex(lidx)
        form.addRow("اللغة / Language:", self.cmb_lang)

        self.cmb_theme = QComboBox()
        self.cmb_theme.addItem("Instagram Dark", "instagram_dark")
        self.cmb_theme.addItem("Pure OLED Black", "oled")
        self.cmb_theme.addItem("Light", "light")
        tidx = self.cmb_theme.findData(self.settings["theme"])
        if tidx >= 0:
            self.cmb_theme.setCurrentIndex(tidx)
        form.addRow("المظهر / Theme:", self.cmb_theme)

        self.chk_dev_logs = QCheckBox(
            "إظهار تبويب السجل التقني" if is_ar else "Show developer logs tab"
        )
        self.chk_dev_logs.setChecked(bool(self.settings["show_dev_logs"]))
        form.addRow("", self.chk_dev_logs)

        return w

    def _build_storage_tab(self, is_ar: bool) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        stats = self.db.get_stats()
        sizes = self.account_mgr.get_storage_sizes(self.profile_dir)

        summary = QLabel(
            f"الحساب الحالي — الإجمالي: {stats['total']} | غير مراجع: {stats['unreviewed']} | مُبقى: {stats['keep']} | محذوف: {stats['deleted']}"
            if is_ar
            else f"Current account — Total: {stats['total']} | Unreviewed: {stats['unreviewed']} | Kept: {stats['keep']} | Deleted: {stats['deleted']}"
        )
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.lbl_disk_usage = QLabel()
        self.lbl_disk_usage.setObjectName("Subtle")
        self._update_disk_label(is_ar, sizes)
        layout.addWidget(self.lbl_disk_usage)

        if stats["failed"] > 0:
            btn_retry = QPushButton(
                f"إعادة محاولة التعليقات الفاشلة ({stats['failed']})"
                if is_ar
                else f"Retry failed comments ({stats['failed']})"
            )
            btn_retry.clicked.connect(lambda: self._on_retry_failed(btn_retry))
            layout.addWidget(btn_retry)

        if stats["keep"] > 0:
            btn_reset_kept = QPushButton(
                f"إعادة مراجعة التعليقات المُبقاة ({stats['keep']})"
                if is_ar
                else f"Reset kept comments ({stats['keep']})"
            )
            btn_reset_kept.clicked.connect(lambda: self._on_reset_kept(is_ar, btn_reset_kept))
            layout.addWidget(btn_reset_kept)

        btn_export = QPushButton(
            "تصدير نسخة احتياطية للتعليقات (JSON)"
            if is_ar
            else "Export comments backup (JSON)"
        )
        btn_export.clicked.connect(lambda: self._on_export(is_ar))
        layout.addWidget(btn_export)

        div = QFrame()
        div.setObjectName("ThreadDivider")
        div.setFixedHeight(1)
        layout.addWidget(div)

        self.btn_clear_cache = QPushButton(
            f"مسح كاش الصور والـ GIF ({format_bytes(sizes['media_cache'])})"
            if is_ar
            else f"Clear media cache ({format_bytes(sizes['media_cache'])})"
        )
        self.btn_clear_cache.clicked.connect(lambda: self._on_clear_cache(is_ar))
        layout.addWidget(self.btn_clear_cache)

        btn_wipe_all = QPushButton(
            f"مسح كل البيانات والكاش وإعادة ضبط التطبيق ({format_bytes(sizes['total'])})"
            if is_ar
            else f"Clear all data, cache & reset app ({format_bytes(sizes['total'])})"
        )
        btn_wipe_all.setObjectName("DangerBtn")
        btn_wipe_all.clicked.connect(lambda: self._on_factory_reset(is_ar))
        layout.addWidget(btn_wipe_all)

        layout.addStretch()
        return w

    def _update_disk_label(self, is_ar: bool, sizes: dict[str, int]) -> None:
        if is_ar:
            self.lbl_disk_usage.setText(
                f"التخزين المحلي — قواعد البيانات: {format_bytes(sizes['databases'])}  •  "
                f"الكاش: {format_bytes(sizes['media_cache'])}  •  "
                f"بيانات المتصفح: {format_bytes(sizes['browser_profile'])}"
            )
        else:
            self.lbl_disk_usage.setText(
                f"Local storage — Databases: {format_bytes(sizes['databases'])}  •  "
                f"Media cache: {format_bytes(sizes['media_cache'])}  •  "
                f"Browser session: {format_bytes(sizes['browser_profile'])}"
            )

    def _apply_preset(self, limit: int, min_d: int, max_d: int) -> None:
        self.spin_limit.setValue(limit)
        self.spin_min.setValue(min_d)
        self.spin_max.setValue(max_d)

    def _on_retry_failed(self, btn: QPushButton) -> None:
        cnt = self.db.retry_failed_comments()
        btn.setEnabled(False)
        btn.setText(f"✓ ({cnt})")
        self.data_changed.emit()

    def _on_reset_kept(self, is_ar: bool, btn: QPushButton) -> None:
        reply = QMessageBox.question(
            self,
            "إعادة تعيين المُبقى" if is_ar else "Reset Kept",
            "ستعود كل التعليقات التي علّمتها كـ «مُبقى» للظهور مجدداً في المراجعة.\nهل تريد المتابعة؟"
            if is_ar
            else "All kept comments will return to unreviewed status.\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            cnt = self.db.reset_kept_comments()
            btn.setEnabled(False)
            btn.setText(f"✓ ({cnt})")
            self.data_changed.emit()

    def _on_export(self, is_ar: bool) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "حفظ النسخة الاحتياطية" if is_ar else "Save backup",
            "uncomment_backup.json",
            "JSON Files (*.json)",
        )
        if not path:
            return
        count = self.db.export_all_json(Path(path))
        QMessageBox.information(
            self,
            "تم التصدير" if is_ar else "Exported",
            f"تم حفظ {count} تعليقاً في:\n{path}" if is_ar else f"Saved {count} comments to:\n{path}",
        )

    def _on_clear_cache(self, is_ar: bool) -> None:
        self.account_mgr.clear_media_cache(user_id=None)
        self.clear_cache_requested.emit()
        sizes = self.account_mgr.get_storage_sizes(self.profile_dir)
        self._update_disk_label(is_ar, sizes)
        self.btn_clear_cache.setEnabled(False)
        self.btn_clear_cache.setText("✓ تم مسح الكاش" if is_ar else "✓ Cache cleared")

    def _on_factory_reset(self, is_ar: bool) -> None:
        reply = QMessageBox.warning(
            self,
            "مسح كل البيانات وإعادة الضبط" if is_ar else "Clear All Data & Reset",
            "سيتم حذف جميع الحسابات المحفوظة، قواعد البيانات، كاش الصور والـ GIF، وجلسة المتصفح بالكامل، والعودة لشاشة البداية.\n\nهل أنت متأكد؟"
            if is_ar
            else "This will permanently delete all saved accounts, local databases, media cache, and browser session data, returning Uncomment to a fresh start.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.factory_reset_requested.emit()
            self.reject()

    def _on_save(self) -> None:
        min_d = self.spin_min.value()
        max_d = max(min_d + 1, self.spin_max.value())
        self.result_settings = self.db.save_settings(
            {
                "daily_limit": self.spin_limit.value(),
                "min_delay": min_d,
                "max_delay": max_d,
                "auto_start_queue": self.chk_auto_start.isChecked(),
                "sort_mode": self.cmb_sort.currentData(),
                "batch_posts": self.spin_batch.value(),
                "sync_pages": self.spin_sync.value(),
                "language": self.cmb_lang.currentData(),
                "theme": self.cmb_theme.currentData(),
                "show_dev_logs": self.chk_dev_logs.isChecked(),
            }
        )
        self.accept()
