from __future__ import annotations

import queue
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from core.account_manager import AccountManager
from core.database import CommentDatabase
from core.ig_client import (
    InstagramClientError,
    InstagramWBloksClient,
    LoginCanceledError,
    LoginRequiredError,
    RateLimitError,
)

class UncommentWorker(QThread):
    account_ready = pyqtSignal(dict)
    session_ready = pyqtSignal(bool)
    auth_required = pyqtSignal(str)
    login_phase = pyqtSignal(str)
    login_canceled = pyqtSignal()
    logged_out = pyqtSignal()
    factory_reset_done = pyqtSignal()
    stats_updated = pyqtSignal(dict)
    log_message = pyqtSignal(str)

    sync_progress = pyqtSignal(int, int, int)
    sync_finished = pyqtSignal(int, bool)

    post_media_loaded = pyqtSignal(str, list)
    post_media_failed = pyqtSignal(str, str)
    post_hydrated = pyqtSignal(str, list)
    post_hydrate_failed = pyqtSignal(str)

    countdown_tick = pyqtSignal(dict, int, int)
    deleting_started = pyqtSignal(dict)
    comment_deleted = pyqtSignal(dict)
    comment_failed = pyqtSignal(dict, str)
    watcher_idle = pyqtSignal(str)
    circuit_breaker_tripped = pyqtSignal(str, str)

    def __init__(
        self,
        data_dir: Path,
        profile_dir: Path,
        min_delay: int = 12,
        max_delay: int = 26,
        start_paused: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.data_dir = Path(data_dir)
        self.profile_dir = Path(profile_dir)
        self.account_mgr = AccountManager(self.data_dir)
        self.min_delay = min_delay
        self.max_delay = max_delay

        self._cmd_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._interactive_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        if start_paused:
            self._pause_event.set()

        self._cancel_id_lock = threading.Lock()
        self._canceled_comment_id: str | None = None

        self._db: CommentDatabase | None = None
        self._client: InstagramWBloksClient | None = None
        self._last_idle_reason: str | None = None

    def update_delays(self, min_delay: int, max_delay: int) -> None:
        self.min_delay = max(5, int(min_delay))
        self.max_delay = max(self.min_delay + 1, int(max_delay))

    def request_interactive_login(
        self,
        clear_cookies: bool = True,
        prefill_username: str = "",
    ) -> None:
        self._cmd_queue.put(
            (
                "interactive_login",
                {"clear_cookies": clear_cookies, "prefill_username": prefill_username},
            )
        )

    def request_logout(self) -> None:
        self._cmd_queue.put(("logout", {}))

    def request_factory_reset(self) -> None:
        self._cmd_queue.put(("factory_reset", {}))

    def request_sync(self, max_pages: int = 10, reset_cursor: bool = False) -> None:
        self._cmd_queue.put(("sync", {"max_pages": max_pages, "reset_cursor": reset_cursor}))

    def request_post_media(self, post_id: str) -> None:
        self._interactive_queue.put(("fetch_media", {"post_id": post_id}))

    def request_hydrate_post(
        self,
        post_id: str,
        comment_ids: list[str],
        my_username: str = "",
        force: bool = False,
    ) -> None:
        self._interactive_queue.put(
            (
                "hydrate",
                {
                    "post_id": post_id,
                    "comment_ids": comment_ids,
                    "my_username": my_username,
                    "force": force,
                },
            )
        )

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_event.set()
            self._last_idle_reason = None
        else:
            self._pause_event.clear()
            self._last_idle_reason = None

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def cancel_active_countdown(self, comment_id: str) -> None:
        with self._cancel_id_lock:
            self._canceled_comment_id = comment_id

    def stop(self) -> None:
        self._stop_event.set()
        self._cmd_queue.put(("stop", {}))

    def _switch_worker_db(self, db_path: Path) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        self._db = CommentDatabase(db_path)
        self._db.save_settings(self.account_mgr.get_global_settings())

    def _drain_interactive_queue(self) -> None:
        while not self._interactive_queue.empty():
            try:
                self._interactive_queue.get_nowait()
            except queue.Empty:
                break

    def run(self) -> None:
        self._client = InstagramWBloksClient(self.profile_dir)

        try:
            self._connect_headless()

            while not self._stop_event.is_set():
                try:
                    if self._process_pending_commands():
                        continue
                    if self._process_interactive_commands():
                        continue

                    if self._db is None or not self._client.is_ready:
                        self._emit_idle_once("disconnected")
                        self._sleep_responsive(0.35, allow_when_paused=True)
                        continue

                    if self._pause_event.is_set():
                        self._emit_idle_once("paused")
                        self._sleep_responsive(0.25, allow_when_paused=True)
                        continue

                    in_cooldown, until = self._db.is_in_cooldown()
                    if in_cooldown:
                        self._emit_idle_once(f"cooldown:{until}")
                        self._sleep_responsive(1.0, allow_when_paused=True)
                        continue

                    stats = self._db.get_stats()
                    if stats["remaining_today"] <= 0:
                        self._emit_idle_once("quota_reached")
                        self._sleep_responsive(1.0, allow_when_paused=True)
                        continue

                    next_item = self._db.peek_next_queued()
                    if not next_item:
                        self._emit_idle_once("empty")
                        self._sleep_responsive(0.35, allow_when_paused=True)
                        continue

                    self._last_idle_reason = None
                    self._handle_single_deletion(next_item)
                except Exception as exc:
                    self.log_message.emit(f"[x] Worker loop error: {exc}")
                    self._sleep_responsive(0.5, allow_when_paused=True)

        finally:
            if self._client is not None:
                self._client.close()
            if self._db is not None:
                self._db.close()

    def _process_interactive_commands(self) -> bool:
        try:
            cmd, kwargs = self._interactive_queue.get_nowait()
        except queue.Empty:
            return False

        if cmd == "fetch_media":
            self._run_fetch_media(str(kwargs.get("post_id", "")))
            return True

        if cmd == "hydrate":
            latest = kwargs
            pending_other: list[tuple[str, dict[str, Any]]] = []
            while not self._interactive_queue.empty():
                try:
                    c2, k2 = self._interactive_queue.get_nowait()
                    if c2 == "hydrate":
                        latest = k2
                    else:
                        pending_other.append((c2, k2))
                except queue.Empty:
                    break
            for item in pending_other:
                self._interactive_queue.put(item)

            self._run_hydrate(
                post_id=str(latest.get("post_id", "")),
                comment_ids=list(latest.get("comment_ids") or []),
                my_username=str(latest.get("my_username") or ""),
                force=bool(latest.get("force", False)),
            )
            return True

        return False

    def _process_pending_commands(self) -> bool:
        try:
            cmd, kwargs = self._cmd_queue.get_nowait()
        except queue.Empty:
            return False

        if cmd == "stop":
            self._stop_event.set()
            return True
        if cmd == "interactive_login":
            self._run_interactive_login(
                clear_cookies=bool(kwargs.get("clear_cookies", True)),
                prefill_username=str(kwargs.get("prefill_username", "")),
            )
            return True
        if cmd == "logout":
            self._run_logout()
            return True
        if cmd == "factory_reset":
            self._run_factory_reset()
            return True
        if cmd == "sync":
            self._run_sync(
                max_pages=int(kwargs.get("max_pages", 10)),
                reset_cursor=bool(kwargs.get("reset_cursor", False)),
            )
            return True
        return False

    def _run_interactive_login(self, clear_cookies: bool, prefill_username: str) -> None:
        assert self._client is not None
        self._drain_interactive_queue()
        self.session_ready.emit(False)
        self.login_phase.emit("waiting_login")

        if self._db is not None:
            self._db.close()
            self._db = None

        self.log_message.emit(
            f"[*] Waiting for interactive login (prefill={prefill_username or '-'})..."
        )
        try:
            self._client.interactive_login(
                clear_cookies=clear_cookies,
                prefill_username=prefill_username,
            )
            self.login_phase.emit("loading_account")
            self._connect_headless()
        except LoginCanceledError:
            self.log_message.emit("[!] Login canceled by user.")
            self.login_phase.emit("idle")
            if clear_cookies:
                self.account_mgr.set_active_user_id("")
            self.login_canceled.emit()
        except LoginRequiredError as exc:
            self.login_phase.emit("idle")
            self.auth_required.emit(str(exc))
            self.log_message.emit(f"[!] {exc}")
        except Exception as exc:
            self.login_phase.emit("idle")
            self.auth_required.emit(str(exc))
            self.log_message.emit(f"[x] Login error: {exc}")

    def _run_logout(self) -> None:
        assert self._client is not None
        self._drain_interactive_queue()
        if self._db is not None:
            self._db.close()
            self._db = None
        self._client.clear_saved_session()
        self.account_mgr.set_active_user_id("")
        self.session_ready.emit(False)
        self.logged_out.emit()
        self.log_message.emit("[*] Logged out and cleared active session.")

    def _run_factory_reset(self) -> None:
        assert self._client is not None
        self._drain_interactive_queue()
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
            self._client.close()
            self.account_mgr.wipe_all_data(self.profile_dir)
            self.log_message.emit("[*] Factory reset completed: all local data & cache cleared.")
        except Exception as exc:
            self.log_message.emit(f"[x] Factory reset error: {exc}")
        finally:
            self.session_ready.emit(False)
            self.factory_reset_done.emit()

    def _connect_headless(self) -> None:
        assert self._client is not None
        self.log_message.emit("[*] Connecting (headless)...")

        try:
            initial = self._client.start(headless=True)
            uid = self._client.get_current_user_id()
            if not uid:
                raise LoginRequiredError("AUTH_NEEDED")

            username, avatar_bytes = self._client.fetch_account_profile(
                uid, fallback_parse=initial
            )
            self.account_mgr.migrate_legacy_if_needed(uid, username)
            account_info = self.account_mgr.register_account(
                uid, username, avatar_bytes=avatar_bytes
            )

            self._switch_worker_db(Path(account_info["db_path"]))
            assert self._db is not None

            ins, upd = self._db.upsert_comments(initial.comments)
            if initial.next_page_params and not self._db.get_json_state("sync_next_params"):
                self._db.set_state("sync_next_params", initial.next_page_params)

            self.login_phase.emit("idle")
            self.account_ready.emit(account_info)
            self.session_ready.emit(True)
            self.stats_updated.emit(self._db.get_stats())
            self.log_message.emit(
                f"[✓] Ready as @{username} (fetched={len(initial.comments)}, new={ins}, updated={upd})."
            )
        except LoginRequiredError as exc:
            self.login_phase.emit("idle")
            self.session_ready.emit(False)
            self.auth_required.emit(str(exc))
            self.log_message.emit(f"[!] {exc}")
        except RateLimitError as exc:
            self.login_phase.emit("idle")
            self._trip_circuit_breaker(str(exc))
        except Exception as exc:
            self.login_phase.emit("idle")
            self.session_ready.emit(False)
            self.log_message.emit(f"[x] Connect error: {exc}")

    def _run_sync(self, max_pages: int, reset_cursor: bool) -> None:
        assert self._client is not None
        if not self._client.is_ready or self._db is None:
            self._connect_headless()
            if not self._client.is_ready or self._db is None:
                self.sync_finished.emit(0, True)
                return

        total_inserted = 0
        if reset_cursor and self._client.initial_parse and self._client.initial_parse.next_page_params:
            next_params = self._client.initial_parse.next_page_params
        else:
            next_params = self._db.get_json_state("sync_next_params")
            if not next_params and self._client.initial_parse:
                next_params = self._client.initial_parse.next_page_params

        if not next_params:
            self.sync_finished.emit(0, False)
            return

        has_more = True
        for page_idx in range(1, max_pages + 1):
            if self._stop_event.is_set():
                break

            try:
                parsed = self._client.fetch_next_page(next_params)
                ins, upd = self._db.upsert_comments(parsed.comments)
                total_inserted += ins

                self.sync_progress.emit(page_idx, max_pages, total_inserted)
                self.stats_updated.emit(self._db.get_stats())
                self.log_message.emit(
                    f"[Sync {page_idx}/{max_pages}] fetched={len(parsed.comments)} new={ins} upd={upd}"
                )

                if parsed.next_page_params:
                    next_params = parsed.next_page_params
                    self._db.set_state("sync_next_params", next_params)
                else:
                    has_more = False
                    self._db.set_state("sync_next_params", "")
                    break

                if page_idx < max_pages:
                    self._sleep_responsive(random.uniform(2.5, 5.0), allow_when_paused=True)

            except RateLimitError as exc:
                self._trip_circuit_breaker(str(exc))
                break
            except LoginRequiredError as exc:
                self.session_ready.emit(False)
                self.auth_required.emit(str(exc))
                break
            except Exception as exc:
                self.log_message.emit(f"[x] Sync stopped at page {page_idx}: {exc}")
                if self._client.initial_parse and self._client.initial_parse.next_page_params:
                    self._db.set_state("sync_next_params", self._client.initial_parse.next_page_params)
                break

        self.sync_finished.emit(total_inserted, has_more)

    def _run_fetch_media(self, post_id: str) -> None:
        assert self._client is not None
        if not self._client.is_ready:
            self.post_media_failed.emit(post_id, "Not connected to Instagram.")
            return
        try:
            slides = self._client.fetch_post_media(post_id)
            self.post_media_loaded.emit(post_id, slides)
        except RateLimitError as exc:
            self._trip_circuit_breaker(str(exc))
            self.post_media_failed.emit(post_id, str(exc))
        except Exception as exc:
            self.log_message.emit(f"[x] Media fetch failed for {post_id}: {exc}")
            self.post_media_failed.emit(post_id, str(exc))

    def _run_hydrate(
        self,
        post_id: str,
        comment_ids: list[str],
        my_username: str,
        force: bool = False,
    ) -> None:
        assert self._client is not None
        if not self._client.is_ready or self._db is None or not post_id:
            self.post_hydrate_failed.emit(post_id)
            return

        if force:
            self._db.clear_post_hydration(post_id)
        else:
            meta = self._db.get_post_meta(post_id)
            if meta and meta.get("hydrated_at") and (
                meta.get("like_count") is not None or bool(meta.get("hydrated_threads_json"))
            ):
                return

        try:
            res = self._client.hydrate_post(post_id, comment_ids, my_username=my_username)
            for w in res.get("warnings") or []:
                self.log_message.emit(f"[!] Hydrate warning ({post_id}): {w}")

            if res["like_count"] is None and not res["threads"]:
                self.log_message.emit(f"[!] Hydrate returned empty data for {post_id} (not cached).")
                self.post_hydrate_failed.emit(post_id)
                return

            self._db.save_post_hydration(
                post_id=post_id,
                like_count=res["like_count"],
                comment_count=res["comment_count"],
                owner_avatar_url=res["owner_avatar_url"],
                media_type=res["media_type"],
                threads=res["threads"],
            )
            self.post_hydrated.emit(post_id, res["slides"])
            self.log_message.emit(
                f"[Hydrate] post={post_id} likes={res['like_count']} threads={len(res['threads'])}"
            )
        except RateLimitError as exc:
            self._trip_circuit_breaker(str(exc))
            self.post_hydrate_failed.emit(post_id)
        except Exception as exc:
            self.log_message.emit(f"[x] Hydrate failed for {post_id}: {exc}")
            self.post_hydrate_failed.emit(post_id)

    def _handle_single_deletion(self, item: dict[str, Any]) -> None:
        assert self._client is not None and self._db is not None
        cid = item["comment_id"]
        total_secs = random.randint(self.min_delay, self.max_delay)

        for sec_left in range(total_secs, 0, -1):
            if self._stop_event.is_set() or self._pause_event.is_set() or not self._cmd_queue.empty():
                return

            if self._was_canceled(cid):
                self.log_message.emit(f"[↩] Canceled {cid} before deletion.")
                self.stats_updated.emit(self._db.get_stats())
                return

            still_queued = self._db.peek_next_queued()
            if not still_queued or still_queued["comment_id"] != cid:
                return

            self.countdown_tick.emit(item, sec_left, total_secs)

            if not self._sleep_responsive(1.0, watch_cancel_id=cid):
                return

        if (
            self._stop_event.is_set()
            or self._pause_event.is_set()
            or self._was_canceled(cid)
            or not self._cmd_queue.empty()
        ):
            return

        self._db.set_status(cid, "DELETING")
        self.deleting_started.emit(item)

        try:
            self._client.delete_comment(item["delete_key"])
            self._db.set_status(cid, "DELETED")
            self.comment_deleted.emit(item)
            self.stats_updated.emit(self._db.get_stats())
            self.log_message.emit(f"[✓] Deleted {cid} on @{item['post_author_username']}")

        except RateLimitError as exc:
            self._db.set_status(cid, "QUEUED_DELETE")
            self._trip_circuit_breaker(str(exc))

        except LoginRequiredError as exc:
            self._db.set_status(cid, "QUEUED_DELETE")
            self.session_ready.emit(False)
            self.auth_required.emit(str(exc))

        except InstagramClientError as exc:
            self._db.set_status(cid, "FAILED", error_message=str(exc))
            self.comment_failed.emit(item, str(exc))
            self.stats_updated.emit(self._db.get_stats())
            self.log_message.emit(f"[x] Failed {cid}: {exc}")

    def _was_canceled(self, cid: str) -> bool:
        with self._cancel_id_lock:
            if self._canceled_comment_id == cid:
                self._canceled_comment_id = None
                return True
        return False

    def _sleep_responsive(
        self,
        seconds: float,
        watch_cancel_id: str | None = None,
        allow_when_paused: bool = False,
    ) -> bool:
        steps = max(1, int(seconds * 10))
        for _ in range(steps):
            if self._stop_event.is_set():
                return False
            if not allow_when_paused and self._pause_event.is_set():
                return False
            if watch_cancel_id and self._was_canceled(watch_cancel_id):
                return False
            if not self._cmd_queue.empty():
                return False
            if not self._interactive_queue.empty():
                self._process_interactive_commands()
            if self._client is not None:
                self._client.pump_events(100)
        return True

    def _trip_circuit_breaker(self, reason: str) -> None:
        until = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds")
        if self._db is not None:
            self._db.set_state("cooldown_until", until)
        self.circuit_breaker_tripped.emit(reason, until)
        self.log_message.emit(f"[⛔] Circuit breaker tripped until {until}: {reason}")

    def _emit_idle_once(self, reason: str) -> None:
        if self._last_idle_reason != reason:
            self._last_idle_reason = reason
            self.watcher_idle.emit(reason)
