from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMovie, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    HAS_MULTIMEDIA = True
except Exception:
    HAS_MULTIMEDIA = False

_MENTION_RE = re.compile(r"(@[A-Za-z0-9._]+)")
_LEADING_MENTIONS_RE = re.compile(r"^(?:\s*@[A-Za-z0-9._]+)+\s*")
_MEDIA_PLACEHOLDER_TEXTS = {
    "commented with a gif.",
    "commented with a photo.",
    "commented with an image.",
    "commented with a sticker.",
}

def format_compact_count(n: int | None) -> str:
    if n is None:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if n >= 10_000:
        return f"{n / 1_000:.1f}K".rstrip("0").rstrip(".")
    if n >= 1_000:
        return f"{n:,}"
    return str(n)

def detect_text_is_rtl(text: str) -> bool:
    cleaned = _LEADING_MENTIONS_RE.sub("", text) or text
    for ch in cleaned:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            return True
        if bidi == "L":
            return False
    return False

def format_comment_html(text: str, mention_color: str) -> str:
    escaped = html.escape(text)
    highlighted = _MENTION_RE.sub(
        lambda m: f'\u2066<span style="color:{mention_color}; font-weight:600;">{m.group(1)}</span>\u2069',
        escaped,
    )
    return highlighted.replace("\n", "<br>")

def apply_auto_bidi(label: QLabel, text: str, as_rich_html: str | None = None) -> None:
    is_rtl = detect_text_is_rtl(text)
    if as_rich_html is not None:
        label.setTextFormat(Qt.TextFormat.RichText)
        direction = "rtl" if is_rtl else "ltr"
        label.setText(f'<div dir="{direction}">{as_rich_html}</div>')
    else:
        label.setTextFormat(Qt.TextFormat.PlainText)
        isolated = _MENTION_RE.sub(lambda m: f"\u2066{m.group(1)}\u2069", text)
        label.setText(isolated)

    if is_rtl:
        label.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    else:
        label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

def make_circular_pixmap(pixmap: QPixmap, size: int) -> QPixmap:
    scaled = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    cropped = scaled.copy(x, y, size, size)

    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return out

def make_rounded_pixmap(pixmap: QPixmap, max_w: int = 210, max_h: int = 185, radius: int = 10) -> QPixmap:
    scaled = pixmap.scaled(
        max_w,
        max_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(scaled.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, scaled.width(), scaled.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return out

def make_initial_avatar(
    username: str,
    size: int = 32,
    bg_color: str = "#262626",
    fg_color: str = "#f5f5f5",
) -> QPixmap:
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(bg_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)

    letter = (username.lstrip("@")[:1] or "?").upper()
    painter.setPen(QColor(fg_color))
    font = QFont("Inter", max(9, int(size * 0.42)), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(out.rect(), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return out

class CommentRowWidget(QFrame):
    toggled = pyqtSignal(str, bool)

    def __init__(
        self,
        item: dict[str, Any],
        is_reply_indent: bool,
        status_label: str | None,
        you_tag: str,
        likes_text: str,
        photo_unavailable_text: str,
        palette: dict[str, str],
        has_local_media: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.comment_id = str(item.get("comment_id") or "")
        self.is_mine = bool(item.get("is_mine"))
        self.status = str(item.get("status") or "CONTEXT")
        self.avatar_url = str(item.get("avatar_url") or "")
        self.media_url = str(item.get("media_url") or "")
        self.palette = palette
        self.selectable = self.is_mine and self.status in ("UNREVIEWED", "KEEP", "FAILED")
        self._selected = False

        self._gif_bytes: QByteArray | None = None
        self._gif_buffer: QBuffer | None = None
        self._gif_movie: QMovie | None = None

        self.setObjectName("CommentMine" if self.is_mine else "CommentContext")
        if self.selectable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QHBoxLayout(self)
        left_pad = 28 if is_reply_indent else 10
        outer.setContentsMargins(left_pad, 8, 12, 8)
        outer.setSpacing(10)

        username = str(item.get("username") or "user")
        self.lbl_avatar = QLabel()
        self.lbl_avatar.setFixedSize(28, 28)
        self.lbl_avatar.setPixmap(
            make_initial_avatar(username, 28, palette["avatar_bg"], palette["avatar_fg"])
        )
        outer.addWidget(self.lbl_avatar, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)

        user_color = palette["text"] if self.is_mine else palette["context_user"]
        user_lbl = QLabel(username)
        user_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {user_color}; background: transparent;"
        )
        header.addWidget(user_lbl)

        if self.is_mine:
            you_pill = QLabel(you_tag)
            you_pill.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {palette['blue']}; "
                "background: rgba(0, 149, 246, 0.14); border-radius: 4px; padding: 1px 6px;"
            )
            header.addWidget(you_pill)

        if item.get("time"):
            t_lbl = QLabel(str(item["time"]))
            t_lbl.setStyleSheet(f"color: {palette['subtle']}; font-size: 11px; background: transparent;")
            header.addWidget(t_lbl)

        if status_label:
            st_color = palette["muted"]
            st_bg = "rgba(128, 128, 128, 0.14)"
            if self.status in ("QUEUED_DELETE", "DELETING"):
                st_color = palette["red"]
                st_bg = palette["red_soft"]
            elif self.status == "DELETED":
                st_color = palette["green"]
                st_bg = "rgba(34, 197, 94, 0.15)"
            st_lbl = QLabel(status_label)
            st_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; color: {st_color}; "
                f"background: {st_bg}; border-radius: 4px; padding: 1px 6px;"
            )
            header.addWidget(st_lbl)

        header.addStretch()
        col.addLayout(header)

        show_media = bool(self.media_url) or has_local_media
        raw_text = str(item.get("text") or "").strip()
        hide_placeholder = show_media and raw_text.lower() in _MEDIA_PLACEHOLDER_TEXTS

        if raw_text and not hide_placeholder:
            body = QLabel()
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rich = format_comment_html(raw_text, palette["mention"])
            apply_auto_bidi(body, raw_text, as_rich_html=rich)

            if self.status == "DELETED":
                body.setStyleSheet(
                    f"color: {palette['subtle']}; text-decoration: line-through; font-size: 13px; background: transparent;"
                )
            elif self.is_mine:
                body.setStyleSheet(f"color: {palette['comment_text']}; font-size: 13.5px; background: transparent;")
            else:
                body.setStyleSheet(f"color: {palette['context_text']}; font-size: 13px; background: transparent;")
            col.addWidget(body)

        self.lbl_media: QLabel | None = None
        if show_media:
            self.lbl_media = QLabel("...")
            self.lbl_media.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_media.setStyleSheet(
                f"background-color: {palette['surface_alt']}; border: 1px solid {palette['border']}; "
                "border-radius: 10px; color: #737373; padding: 2px;"
            )
            self.lbl_media.setMinimumSize(110, 80)
            self.lbl_media.setMaximumSize(220, 195)
            col.addWidget(self.lbl_media, 0, Qt.AlignmentFlag.AlignLeft)
        else:
            placeholder = str(item.get("media_placeholder") or "").strip()
            if placeholder or not raw_text:
                ph_lbl = QLabel(f"🖼  {photo_unavailable_text or placeholder or 'Photo unavailable'}")
                ph_lbl.setStyleSheet(
                    f"color: {palette['muted']}; background-color: {palette['surface_alt']}; "
                    f"border: 1px dashed {palette['border']}; border-radius: 8px; "
                    "padding: 6px 10px; font-size: 12px;"
                )
                col.addWidget(ph_lbl, 0, Qt.AlignmentFlag.AlignLeft)

        if likes_text:
            lk_lbl = QLabel(likes_text)
            lk_lbl.setStyleSheet(
                f"color: {palette['subtle']}; font-size: 11px; font-weight: 600; background: transparent;"
            )
            col.addWidget(lk_lbl)

        outer.addLayout(col, 1)

        self.chk_dot: QLabel | None = None
        if self.is_mine:
            self.chk_dot = QLabel()
            self.chk_dot.setFixedSize(20, 20)
            self.chk_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(self.chk_dot, 0, Qt.AlignmentFlag.AlignTop)

        self._update_visual_state()

    def set_avatar_pixmap(self, pix: QPixmap) -> None:
        self.lbl_avatar.setPixmap(make_circular_pixmap(pix, 28))

    def set_media_bytes(self, raw_bytes: bytes) -> None:
        if self.lbl_media is None or not raw_bytes:
            return

        is_gif = raw_bytes[:6] in (b"GIF87a", b"GIF89a")
        if is_gif:
            self._cleanup_movie()
            self._gif_bytes = QByteArray(raw_bytes)
            self._gif_buffer = QBuffer(self._gif_bytes)
            self._gif_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            movie = QMovie(self._gif_buffer, QByteArray(b"gif"), self)
            if movie.isValid():
                movie.jumpToFrame(0)
                orig = movie.currentPixmap().size()
                if orig.width() > 0 and orig.height() > 0:
                    target = orig.scaled(QSize(210, 185), Qt.AspectRatioMode.KeepAspectRatio)
                    movie.setScaledSize(target)
                    self.lbl_media.setFixedSize(target)
                self.lbl_media.setStyleSheet("background: transparent; border: none; padding: 0px;")
                self.lbl_media.setMovie(movie)
                movie.start()
                self._gif_movie = movie
                return

        pix = QPixmap()
        if pix.loadFromData(raw_bytes):
            rounded = make_rounded_pixmap(pix, 210, 185, radius=10)
            self.lbl_media.setStyleSheet("background: transparent; border: none; padding: 0px;")
            self.lbl_media.setFixedSize(rounded.size())
            self.lbl_media.setPixmap(rounded)

    def _cleanup_movie(self) -> None:
        if self._gif_movie is not None:
            self._gif_movie.stop()
            self._gif_movie = None
        if self._gif_buffer is not None:
            self._gif_buffer.close()
            self._gif_buffer = None
        self._gif_bytes = None

    def closeEvent(self, event) -> None:
        self._cleanup_movie()
        super().closeEvent(event)

    @property
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool, emit_signal: bool = True) -> None:
        if not self.selectable or self._selected == selected:
            return
        self._selected = selected
        self._update_visual_state()
        if emit_signal:
            self.toggled.emit(self.comment_id, self._selected)

    def mousePressEvent(self, event) -> None:
        if self.selectable and event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._selected, emit_signal=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def _update_visual_state(self) -> None:
        if self.is_mine:
            self.setObjectName("CommentMineSelected" if self._selected else "CommentMine")
            self.style().unpolish(self)
            self.style().polish(self)

        if self.chk_dot is None:
            return

        if not self.selectable:
            if self.status in ("QUEUED_DELETE", "DELETING"):
                self.chk_dot.setText("•")
                self.chk_dot.setStyleSheet(
                    "border: 1.5px solid #ed4956; border-radius: 10px; color: #ed4956; font-weight: 700; background: transparent;"
                )
            elif self.status == "DELETED":
                self.chk_dot.setText("✓")
                self.chk_dot.setStyleSheet(
                    "border: none; border-radius: 10px; background: #22c55e; color: #000; font-size: 11px; font-weight: 700;"
                )
            else:
                self.chk_dot.setText("")
                self.chk_dot.setStyleSheet("border: none; background: transparent;")
            return

        if self._selected:
            self.chk_dot.setText("✓")
            self.chk_dot.setStyleSheet(
                "border: none; border-radius: 10px; background: #ed4956; color: #ffffff; font-size: 11px; font-weight: 700;"
            )
        else:
            self.chk_dot.setText("")
            self.chk_dot.setStyleSheet(
                f"border: 1.5px solid {self.palette['subtle']}; border-radius: 10px; background: transparent;"
            )

class MediaPaneWidget(QFrame):
    load_media_requested = pyqtSignal()
    slide_image_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MediaPane")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._slides: list[dict[str, str]] = []
        self._slide_index: int = 0
        self._current_pixmap: QPixmap | None = None
        self._load_btn_label: str = "▶ Load post"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()

        self.lbl_image = QLabel()
        self.lbl_image.setObjectName("MediaPlaceholder")
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setMinimumSize(220, 260)
        self.stack.addWidget(self.lbl_image)

        self.player: Any = None
        self.audio_output: Any = None
        self.video_widget: QWidget | None = None

        if HAS_MULTIMEDIA:
            video_wrap = QWidget()
            video_wrap.setObjectName("VideoStage")
            v_layout = QVBoxLayout(video_wrap)
            v_layout.setContentsMargins(0, 0, 0, 0)
            v_layout.setSpacing(0)

            self.video_widget = QVideoWidget()
            v_layout.addWidget(self.video_widget, 1)

            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(0.85)
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video_widget)
            self.player.setLoops(QMediaPlayer.Loops.Infinite)

            ctrl_bar = QFrame()
            ctrl_bar.setObjectName("VideoCtrlBar")
            ctrl_bar.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            c_layout = QHBoxLayout(ctrl_bar)
            c_layout.setContentsMargins(10, 6, 10, 6)
            c_layout.setSpacing(10)

            self.btn_vid_play = QPushButton("⏸")
            self.btn_vid_play.setObjectName("VideoCtrlBtn")
            self.btn_vid_play.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_vid_play.clicked.connect(self._toggle_video_playback)
            c_layout.addWidget(self.btn_vid_play)

            self.slider_vid = QSlider(Qt.Orientation.Horizontal)
            self.slider_vid.setRange(0, 0)
            self.slider_vid.sliderMoved.connect(self.player.setPosition)
            c_layout.addWidget(self.slider_vid, 1)

            self.btn_vid_mute = QPushButton("🔊")
            self.btn_vid_mute.setObjectName("VideoCtrlBtn")
            self.btn_vid_mute.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_vid_mute.clicked.connect(self._toggle_mute)
            c_layout.addWidget(self.btn_vid_mute)

            v_layout.addWidget(ctrl_bar)
            self.stack.addWidget(video_wrap)

            self.player.positionChanged.connect(self.slider_vid.setValue)
            self.player.durationChanged.connect(lambda d: self.slider_vid.setRange(0, d))
            self.player.playbackStateChanged.connect(self._on_playback_state)

        layout.addWidget(self.stack, 1)

        bottom_bar = QFrame()
        bottom_bar.setObjectName("MediaBottomBar")
        b_layout = QHBoxLayout(bottom_bar)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(8)

        self.btn_load_play = QPushButton("▶ Load post")
        self.btn_load_play.setObjectName("MediaOverlayBtn")
        self.btn_load_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load_play.clicked.connect(self._on_play_btn_clicked)
        b_layout.addWidget(self.btn_load_play)

        self.lbl_media_msg = QLabel("")
        self.lbl_media_msg.setObjectName("MediaStatusMsg")
        b_layout.addWidget(self.lbl_media_msg, 1)

        self.btn_slide_prev = QPushButton("‹")
        self.btn_slide_prev.setObjectName("SlideArrowBtn")
        self.btn_slide_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_slide_prev.clicked.connect(lambda: self._step_slide(-1))
        self.btn_slide_prev.hide()

        self.lbl_slide_counter = QLabel("")
        self.lbl_slide_counter.setObjectName("SlideCounter")
        self.lbl_slide_counter.hide()

        self.btn_slide_next = QPushButton("›")
        self.btn_slide_next.setObjectName("SlideArrowBtn")
        self.btn_slide_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_slide_next.clicked.connect(lambda: self._step_slide(1))
        self.btn_slide_next.hide()

        b_layout.addWidget(self.btn_slide_prev)
        b_layout.addWidget(self.lbl_slide_counter)
        b_layout.addWidget(self.btn_slide_next)

        layout.addWidget(bottom_bar)

    def reset_for_post(self, btn_label: str, play_tooltip: str) -> None:
        self.stop_video()
        self._slides = []
        self._slide_index = 0
        self._current_pixmap = None
        self._load_btn_label = btn_label
        self.stack.setCurrentIndex(0)
        self.lbl_image.setPixmap(QPixmap())
        self.lbl_image.setText("...")
        self.lbl_media_msg.setText("")
        self.btn_load_play.setEnabled(True)
        self.btn_load_play.setText(btn_label)
        self.btn_load_play.setToolTip(play_tooltip)
        self.btn_load_play.show()
        self.btn_slide_prev.hide()
        self.lbl_slide_counter.hide()
        self.btn_slide_next.hide()

    def set_loading(self, text: str) -> None:
        self.btn_load_play.setEnabled(False)
        self.btn_load_play.setText(text)

    def set_error(self, msg: str) -> None:
        self.btn_load_play.setEnabled(True)
        self.btn_load_play.setText(self._load_btn_label)
        self.lbl_media_msg.setText(msg)

    def set_still_pixmap(self, pix: QPixmap) -> None:
        self._current_pixmap = pix
        self._rescale_still()

    def set_loaded_slides(self, slides: list[dict[str, str]], unavailable_msg: str) -> None:
        self._slides = slides
        self._slide_index = 0
        self.lbl_media_msg.setText("")

        if not slides:
            self.set_error(unavailable_msg)
            return

        self.btn_load_play.hide()
        if len(slides) > 1:
            self.btn_slide_prev.show()
            self.lbl_slide_counter.show()
            self.btn_slide_next.show()
        else:
            self.btn_slide_prev.hide()
            self.lbl_slide_counter.hide()
            self.btn_slide_next.hide()

        self._show_slide(0, unavailable_msg)

    def _show_slide(self, idx: int, unavailable_msg: str = "") -> None:
        if not self._slides:
            return
        self._slide_index = idx
        slide = self._slides[idx]
        if len(self._slides) > 1:
            self.lbl_slide_counter.setText(f"{idx + 1} / {len(self._slides)}")
            self.btn_slide_prev.setEnabled(idx > 0)
            self.btn_slide_next.setEnabled(idx < len(self._slides) - 1)

        if slide["type"] == "video":
            if HAS_MULTIMEDIA and self.player is not None:
                self.stack.setCurrentIndex(1)
                self.player.setSource(QUrl(slide["url"]))
                self.player.play()
            else:
                self.stack.setCurrentIndex(0)
                self.lbl_media_msg.setText(unavailable_msg)
        else:
            self.stop_video()
            self.stack.setCurrentIndex(0)
            self.slide_image_requested.emit(slide["url"])

    def _step_slide(self, delta: int) -> None:
        new_idx = self._slide_index + delta
        if 0 <= new_idx < len(self._slides):
            self._show_slide(new_idx)

    def _on_play_btn_clicked(self) -> None:
        self.load_media_requested.emit()

    def _toggle_video_playback(self) -> None:
        if not HAS_MULTIMEDIA or self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state(self, state) -> None:
        if not HAS_MULTIMEDIA:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_vid_play.setText("⏸" if playing else "▶")

    def _toggle_mute(self) -> None:
        if not HAS_MULTIMEDIA or self.audio_output is None:
            return
        muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(muted)
        self.btn_vid_mute.setText("🔇" if muted else "🔊")

    def stop_video(self) -> None:
        if HAS_MULTIMEDIA and self.player is not None:
            self.player.stop()

    def _rescale_still(self) -> None:
        if self._current_pixmap is None:
            return
        box = self.lbl_image.size()
        scaled = self._current_pixmap.scaled(
            max(220, box.width()),
            max(260, box.height()),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_image.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale_still()

class QueueItemWidget(QFrame):
    remove_clicked = pyqtSignal(str)
    size_changed = pyqtSignal()

    def __init__(
        self,
        row: dict[str, Any],
        undo_tooltip: str,
        palette: dict[str, str],
        can_remove: bool = True,
        has_media: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.comment_id = str(row["comment_id"])
        self.media_url = str(row.get("comment_media_url") or "")
        self.setObjectName("QueueRow")

        self._gif_bytes: QByteArray | None = None
        self._gif_buffer: QBuffer | None = None
        self._gif_movie: QMovie | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        owner = QLabel(f"@{row.get('post_author_username') or 'unknown'}")
        owner.setStyleSheet(
            f"font-weight: 700; font-size: 12px; color: {palette['text']}; background: transparent;"
        )
        top.addWidget(owner)

        meta_str = row.get("comment_time") or ""
        if row.get("deleted_at"):
            meta_str = str(row["deleted_at"])[11:19]
        if meta_str:
            m_lbl = QLabel(meta_str)
            m_lbl.setStyleSheet(
                f"color: {palette['subtle']}; font-size: 11px; background: transparent;"
            )
            top.addWidget(m_lbl)
        top.addStretch()
        text_col.addLayout(top)

        raw_text = (row.get("comment_text") or "").replace("\n", " ").strip()
        is_gif_placeholder = raw_text.lower() in _MEDIA_PLACEHOLDER_TEXTS
        show_media_box = has_media or bool(self.media_url)

        if raw_text and not (show_media_box and is_gif_placeholder):
            snippet = raw_text[:72] + ("..." if len(raw_text) > 72 else "")
            body = QLabel()
            body.setWordWrap(True)
            apply_auto_bidi(body, snippet)
            body.setStyleSheet(
                f"color: {palette['muted']}; font-size: 12px; background: transparent;"
            )
            text_col.addWidget(body)
        elif not raw_text and not show_media_box:
            body = QLabel("🖼 Photo comment")
            body.setStyleSheet(
                f"color: {palette['subtle']}; font-size: 11.5px; background: transparent;"
            )
            text_col.addWidget(body)

        self.lbl_media: QLabel | None = None
        if show_media_box:
            self.lbl_media = QLabel("GIF...")
            self.lbl_media.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_media.setFixedSize(96, 68)
            self.lbl_media.setStyleSheet(
                f"background-color: {palette['bg']}; border: 1px solid {palette['border']}; "
                "border-radius: 6px; color: #737373; font-size: 10px;"
            )
            text_col.addWidget(self.lbl_media, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(text_col, 1)

        if can_remove:
            btn_x = QPushButton("×")
            btn_x.setObjectName("CloseXBtn")
            btn_x.setToolTip(undo_tooltip)
            btn_x.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_x.clicked.connect(lambda: self.remove_clicked.emit(self.comment_id))
            layout.addWidget(btn_x, 0, Qt.AlignmentFlag.AlignTop)

    def set_media_bytes(self, raw_bytes: bytes) -> None:
        if self.lbl_media is None or not raw_bytes:
            return

        is_gif = raw_bytes[:6] in (b"GIF87a", b"GIF89a")
        if is_gif:
            self._cleanup_movie()
            self._gif_bytes = QByteArray(raw_bytes)
            self._gif_buffer = QBuffer(self._gif_bytes)
            self._gif_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            movie = QMovie(self._gif_buffer, QByteArray(b"gif"), self)
            if movie.isValid():
                movie.jumpToFrame(0)
                orig = movie.currentPixmap().size()
                if orig.width() > 0 and orig.height() > 0:
                    target = orig.scaled(QSize(130, 96), Qt.AspectRatioMode.KeepAspectRatio)
                    movie.setScaledSize(target)
                    self.lbl_media.setFixedSize(target)
                self.lbl_media.setStyleSheet("background: transparent; border: none;")
                self.lbl_media.setMovie(movie)
                movie.start()
                self._gif_movie = movie
                self.size_changed.emit()
                return

        pix = QPixmap()
        if pix.loadFromData(raw_bytes):
            rounded = make_rounded_pixmap(pix, 130, 96, radius=8)
            self.lbl_media.setStyleSheet("background: transparent; border: none;")
            self.lbl_media.setFixedSize(rounded.size())
            self.lbl_media.setPixmap(rounded)
            self.size_changed.emit()

    def _cleanup_movie(self) -> None:
        if self._gif_movie is not None:
            self._gif_movie.stop()
            self._gif_movie = None
        if self._gif_buffer is not None:
            self._gif_buffer.close()
            self._gif_buffer = None
        self._gif_bytes = None

    def closeEvent(self, event) -> None:
        self._cleanup_movie()
        super().closeEvent(event)
