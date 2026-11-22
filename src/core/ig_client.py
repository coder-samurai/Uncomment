from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from playwright.sync_api import (
    BrowserContext,
    Page,
    Playwright,
    Request,
    Response,
    sync_playwright,
)

from core.bloks_parser import BloksParseResult, parse_wbloks_response

LOGIN_URL = "https://www.instagram.com/accounts/login/"
COMMENTS_URL = "https://www.instagram.com/your_activity/interactions/comments/"
WBLOKS_PATH = "/async/wbloks/fetch/"

RATE_LIMIT_SIGNATURES = (
    "feedback_required",
    "please wait a few minutes",
    "try again later",
    "action_blocked",
    "too many requests",
    "challenge_required",
    "checkpoint_required",
)

LOGIN_SIGNATURES = (
    "login_required",
    "/accounts/login",
)

AUTH_PENDING_PATHS = (
    "/accounts/login",
    "two_factor",
    "challenge",
    "checkpoint",
)

class InstagramClientError(Exception):
    pass

class LoginRequiredError(InstagramClientError):
    pass

class LoginCanceledError(InstagramClientError):
    pass

class RateLimitError(InstagramClientError):
    pass

@dataclass
class SessionTemplate:
    bkv: str
    form_fields: dict[str, str]
    container_meta: dict[str, int] = field(default_factory=dict)
    req_counter: int = 3

    def next_req_id(self) -> str:
        self.req_counter += 1
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        n = self.req_counter
        out = ""
        while n:
            n, r = divmod(n, 36)
            out = chars[r] + out
        return out or "1"

def _clean_meta_json(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.lstrip()
    if cleaned.startswith("for (;;);"):
        cleaned = cleaned[len("for (;;);"):].lstrip()
    return json.loads(cleaned)

def _detect_system_browser() -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for binary, kwargs in (
        ("google-chrome", {"channel": "chrome"}),
        ("google-chrome-stable", {"channel": "chrome"}),
        ("microsoft-edge", {"channel": "msedge"}),
        ("chromium", {"executable_path": shutil.which("chromium")}),
    ):
        found = shutil.which(binary)
        if found:
            hints.append({"executable_path": found} if "executable_path" in kwargs else kwargs)

    for channel in ("chrome", "msedge"):
        item = {"channel": channel}
        if item not in hints:
            hints.append(item)
    return hints

class InstagramWBloksClient:
    def __init__(self, profile_dir: Path):
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.template: SessionTemplate | None = None
        self.initial_parse: BloksParseResult | None = None
        self.is_headless: bool = True

    @property
    def is_ready(self) -> bool:
        return self._page is not None and self.template is not None

    def _launch_context(
        self,
        headless: bool,
        viewport: dict[str, int],
        extra_args: list[str] | None = None,
    ) -> BrowserContext:
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        if extra_args:
            args.extend(extra_args)

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": headless,
            "viewport": viewport,
            "args": args,
        }
        for hint in _detect_system_browser():
            try:
                return self._pw.chromium.launch_persistent_context(**launch_kwargs, **hint)
            except Exception:
                continue
        return self._pw.chromium.launch_persistent_context(**launch_kwargs)

    def _get_auth_cookies(self) -> tuple[str, str]:
        if self._context is None:
            return "", ""
        try:
            cookies = self._context.cookies("https://www.instagram.com")
        except Exception:
            return "", ""
        sid = ""
        uid = ""
        for c in cookies:
            name = c.get("name")
            if name == "sessionid":
                sid = str(c.get("value") or "")
            elif name == "ds_user_id":
                uid = str(c.get("value") or "")
        return sid, uid

    def get_current_user_id(self) -> str:
        _, uid = self._get_auth_cookies()
        return uid

    def clear_saved_session(self) -> None:
        self.close()
        try:
            self._context = self._launch_context(headless=True, viewport={"width": 800, "height": 600})
            self._context.clear_cookies()
        except Exception:
            pass
        finally:
            self.close()

    def interactive_login(
        self,
        clear_cookies: bool = True,
        prefill_username: str = "",
        timeout_seconds: int = 300,
    ) -> str:
        self.close()
        self.is_headless = False

        try:
            self._context = self._launch_context(
                headless=False,
                viewport={"width": 440, "height": 680},
                extra_args=["--window-size=460,740"],
            )
            if clear_cookies:
                try:
                    self._context.clear_cookies()
                except Exception:
                    pass

            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)

            if prefill_username:
                try:
                    inp = self._page.locator('input[name="username"], input[name="email"]').first
                    inp.wait_for(state="visible", timeout=4000)
                    inp.fill(prefill_username.lstrip("@"))
                except Exception:
                    pass

            deadline = time.monotonic() + float(timeout_seconds)
            while time.monotonic() < deadline:
                if self._page.is_closed():
                    raise LoginCanceledError("تم إغلاق نافذة تسجيل الدخول قبل إتمام العملية.")

                sid, uid = self._get_auth_cookies()
                curr_url = self._page.url.lower()
                in_auth_flow = any(p in curr_url for p in AUTH_PENDING_PATHS)

                if sid and uid and not in_auth_flow:
                    self._page.wait_for_timeout(600)
                    return uid

                try:
                    self._page.wait_for_timeout(250)
                except Exception as exc:
                    raise LoginCanceledError("تم إغلاق نافذة تسجيل الدخول.") from exc

            raise LoginRequiredError("LOGIN_TIMEOUT")
        finally:
            self.close()

    def start(self, headless: bool = True, login_wait_seconds: int = 180) -> BloksParseResult:
        self.close()
        self.is_headless = headless
        self.template = None
        self.initial_parse = None

        self._context = self._launch_context(
            headless=headless,
            viewport={"width": 1280, "height": 860},
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        sid, uid = self._get_auth_cookies()
        if headless and (not sid or not uid):
            raise LoginRequiredError("AUTH_NEEDED")

        captured_req: dict[str, Any] = {}
        captured_resp: dict[str, Any] = {}

        def on_request(req: Request) -> None:
            if (
                req.method == "POST"
                and WBLOKS_PATH in req.url
                and "com.instagram.privacy.activity_center.comments_screen" in req.url
            ):
                parsed = urlparse(req.url)
                qs = parse_qs(parsed.query)
                bkv = qs.get("__bkv", [""])[0]
                raw_post = req.post_data or ""
                form_raw = parse_qs(raw_post, keep_blank_values=True)
                form_fields = {k: v[0] for k, v in form_raw.items() if k != "params"}
                captured_req["bkv"] = bkv
                captured_req["form_fields"] = form_fields

        def on_response(resp: Response) -> None:
            if (
                resp.request.method == "POST"
                and WBLOKS_PATH in resp.url
                and "com.instagram.privacy.activity_center.comments_screen" in resp.url
            ):
                try:
                    captured_resp["status"] = resp.status
                    captured_resp["json"] = _clean_meta_json(resp.text())
                except Exception as exc:
                    captured_resp["error"] = str(exc)

        self._page.on("request", on_request)
        self._page.on("response", on_response)

        self._page.goto(COMMENTS_URL, wait_until="domcontentloaded", timeout=45_000)

        max_wait = 25.0 if headless else float(login_wait_seconds)
        deadline = time.monotonic() + max_wait

        while time.monotonic() < deadline:
            if captured_req and "json" in captured_resp:
                break

            curr_url = self._page.url
            if any(sig in curr_url for sig in LOGIN_SIGNATURES) and headless:
                raise LoginRequiredError("SESSION_EXPIRED")

            if "challenge" in curr_url or "checkpoint" in curr_url:
                if headless:
                    raise RateLimitError("طلب إنستقرام تحقق أمان (Checkpoint). افتح الحساب يدوياً.")

            self._page.wait_for_timeout(250)

        if not captured_req or "json" not in captured_resp:
            if any(sig in self._page.url for sig in LOGIN_SIGNATURES):
                raise LoginRequiredError("انتهت مهلة الجلسة قبل فتح صفحة التعليقات.")
            raise InstagramClientError("تعذر تهيئة جلسة إنستقرام. تأكد من الاتصال وصلاحية الجلسة.")

        parsed_initial = parse_wbloks_response(captured_resp["json"])
        self.template = SessionTemplate(
            bkv=captured_req["bkv"],
            form_fields=captured_req["form_fields"],
            container_meta=dict(parsed_initial.container_meta),
        )
        self.initial_parse = parsed_initial
        return parsed_initial

    def fetch_account_profile(
        self,
        user_id: str,
        fallback_parse: BloksParseResult | None = None,
    ) -> tuple[str, bytes | None]:
        username = ""
        avatar_url = ""

        if user_id and self.is_ready:
            try:
                data = self._ig_api_get(f"/api/v1/users/{user_id}/info/")
                user = data.get("user") or {}
                username = str(user.get("username") or "").strip()
                avatar_url = str(user.get("profile_pic_url") or "").strip()
            except Exception:
                pass

        if not username and fallback_parse and fallback_parse.comments:
            for c in fallback_parse.comments:
                if c.comment_author_username:
                    username = c.comment_author_username.strip()
                    break

        if not avatar_url and fallback_parse and fallback_parse.comments:
            for c in fallback_parse.comments:
                for item in c.thread_items:
                    if item.get("is_mine") and item.get("avatar_url"):
                        avatar_url = str(item["avatar_url"])
                        break
                if avatar_url:
                    break

        avatar_bytes: bytes | None = None
        if avatar_url and self._context is not None:
            try:
                resp = self._context.request.get(avatar_url, timeout=10_000)
                if resp.ok:
                    avatar_bytes = resp.body()
            except Exception:
                pass

        return username or f"user_{user_id}", avatar_bytes

    def pump_events(self, ms: int = 100) -> None:
        if self._page is not None:
            try:
                self._page.wait_for_timeout(ms)
                return
            except Exception:
                pass
        time.sleep(ms / 1000.0)

    def _wbloks_post(
        self,
        appid: str,
        params_obj: dict[str, Any],
        req_type: str = "action",
    ) -> dict[str, Any]:
        if not self.is_ready or self._page is None or self.template is None:
            raise InstagramClientError("العميل غير جاهز.")

        url = f"{WBLOKS_PATH}?appid={appid}&type={req_type}"
        if self.template.bkv:
            url += f"&__bkv={self.template.bkv}"

        form = dict(self.template.form_fields)
        form["__req"] = self.template.next_req_id()
        form["params"] = json.dumps(params_obj, separators=(",", ":"), ensure_ascii=False)

        result = self._page.evaluate(
            """async ([targetUrl, formData]) => {
                const body = new URLSearchParams(formData).toString();
                const resp = await fetch(targetUrl, {
                    method: "POST",
                    credentials: "include",
                    headers: {
                        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                        "x-requested-with": "XMLHttpRequest"
                    },
                    body
                });
                const text = await resp.text();
                return { status: resp.status, text };
            }""",
            [url, form],
        )

        status = int(result["status"])
        raw_text = str(result["text"])
        lowered = raw_text.lower()

        if status == 429 or any(sig in lowered for sig in RATE_LIMIT_SIGNATURES):
            raise RateLimitError(f"توقف احترازي (HTTP {status}): إنستقرام فرض حداً مؤقتاً.")

        if status in (401, 403) or any(sig in lowered for sig in LOGIN_SIGNATURES):
            raise LoginRequiredError(f"انتهت صلاحية الجلسة (HTTP {status}).")

        if status != 200:
            raise InstagramClientError(f"استجابة غير متوقعة من الخادم: HTTP {status}")

        try:
            data = _clean_meta_json(raw_text)
        except json.JSONDecodeError as exc:
            raise InstagramClientError(f"فشل تحليل الرد: {exc}") from exc

        if isinstance(data, dict) and data.get("error"):
            err_msg = (
                data.get("errorDescription")
                or data.get("errorSummary")
                or str(data.get("error"))
            )
            if any(sig in str(err_msg).lower() for sig in RATE_LIMIT_SIGNATURES):
                raise RateLimitError(f"رفض مؤقت: {err_msg}")
            raise InstagramClientError(f"خطأ من الخادم: {err_msg}")

        return data

    def fetch_next_page(self, next_page_params: dict[str, Any]) -> BloksParseResult:
        data = self._wbloks_post(
            appid="com.instagram.privacy.activity_center.comments_next",
            params_obj=next_page_params,
            req_type="action",
        )
        parsed = parse_wbloks_response(data)
        if parsed.container_meta and self.template:
            self.template.container_meta.update(parsed.container_meta)
        return parsed

    def delete_comment(self, delete_key: str) -> bool:
        if not self.template:
            raise InstagramClientError("قالب الجلسة غير متاح.")

        meta = self.template.container_meta
        container_id = meta.get("content_container_id", 324320943)
        element_id = meta.get("content_element_id", 324320944)
        spinner_id = meta.get("content_spinner_id") or (element_id + 1)

        params = {
            "content_container_id": container_id,
            "content_element_id": element_id,
            "content_spinner_id": spinner_id,
            "main_order_state_value": True,
            "main_attribute_order_state_value": "newest_to_oldest",
            "main_date_start_state_value": -1,
            "main_date_end_state_value": -1,
            "main_authors_state_value": "",
            "main_filter_to_visible_on_facebook_value": False,
            "main_includes_location_value": False,
            "main_liked_privately_value": False,
            "main_content_type_value": 0,
            "main_content_types_value": "Posts, Reels",
            "main_account_history_events_state_value": "",
            "entrypoint": "",
            "shared_user_id": "",
            "main_filter_to_visible_from_facebook_value": False,
            "items_for_action": delete_key,
            "number_of_items": 1,
        }

        data = self._wbloks_post(
            appid="com.instagram.privacy.activity_center.comments_delete",
            params_obj=params,
            req_type="action",
        )

        if isinstance(data, dict) and data.get("__ar") == 1 and "payload" in data:
            return True
        raise InstagramClientError("رد الحذف لم يحتوي على تأكيد payload متوقع.")

    def _ig_api_get(self, path: str) -> dict[str, Any]:
        if not self.is_ready or self._page is None:
            raise InstagramClientError("الجلسة غير متصلة حالياً.")

        result = self._page.evaluate(
            """async (targetPath) => {
                const resp = await fetch(targetPath, {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "x-ig-app-id": "936619743392459",
                        "x-requested-with": "XMLHttpRequest"
                    }
                });
                const text = await resp.text();
                return { status: resp.status, text };
            }""",
            path,
        )

        status = int(result["status"])
        raw_text = str(result["text"])
        lowered = raw_text.lower()

        if status == 429 or any(sig in lowered for sig in RATE_LIMIT_SIGNATURES):
            raise RateLimitError(f"تم تقييد الطلبات مؤقتاً (HTTP {status}).")
        if status in (401, 403):
            raise LoginRequiredError(f"انتهت صلاحية الجلسة (HTTP {status}).")
        if status != 200:
            raise InstagramClientError(f"استجابة غير متوقعة من الخادم (HTTP {status}).")

        return _clean_meta_json(raw_text)

    @staticmethod
    def _extract_slides_from_item(item: dict[str, Any]) -> list[dict[str, str]]:
        slides: list[dict[str, str]] = []

        def _extract_single(obj: dict[str, Any]) -> dict[str, str] | None:
            vids = obj.get("video_versions") or []
            imgs = (obj.get("image_versions2") or {}).get("candidates") or []
            poster = str(imgs[0].get("url") or "") if imgs else ""
            if vids and vids[0].get("url"):
                return {"type": "video", "url": str(vids[0]["url"]), "poster": poster}
            if poster:
                return {"type": "image", "url": poster, "poster": poster}
            return None

        carousel = item.get("carousel_media") or []
        if carousel:
            for c_item in carousel:
                s = _extract_single(c_item)
                if s:
                    slides.append(s)
        else:
            s = _extract_single(item)
            if s:
                slides.append(s)
        return slides

    @staticmethod
    def _extract_api_comment_media(c: dict[str, Any]) -> tuple[str, str]:
        giphy = c.get("giphy_media_info")
        if isinstance(giphy, dict):
            images = giphy.get("images") or {}
            for variant in ("fixed_height", "original", "downsized"):
                v = images.get(variant)
                if isinstance(v, dict) and v.get("url"):
                    return str(v["url"]), ""

        for key in ("media_comment_info", "media_info", "comment_media", "sticker_media_info"):
            obj = c.get(key)
            if isinstance(obj, dict):
                cands = (obj.get("image_versions2") or {}).get("candidates") or []
                if cands and cands[0].get("url"):
                    return str(cands[0]["url"]), ""
                if obj.get("url"):
                    return str(obj["url"]), ""
                if obj.get("placeholder_text"):
                    return "", str(obj["placeholder_text"])

        cands = (c.get("image_versions2") or {}).get("candidates") or []
        if cands and cands[0].get("url"):
            return str(cands[0]["url"]), ""

        return "", ""

    @staticmethod
    def _format_relative_time(ts: Any) -> str:
        try:
            diff = max(1, int(time.time()) - int(ts))
        except (TypeError, ValueError):
            return ""
        if diff < 60:
            return f"{diff}s"
        if diff < 3600:
            return f"{diff // 60}m"
        if diff < 86400:
            return f"{diff // 3600}h"
        if diff < 604800:
            return f"{diff // 86400}d"
        return f"{diff // 604800}w"

    def fetch_post_media(self, post_id: str) -> list[dict[str, str]]:
        data = self._ig_api_get(f"/api/v1/media/{post_id}/info/")
        items = data.get("items") or []
        if not items:
            raise InstagramClientError("المنشور غير متاح أو محذوف.")
        return self._extract_slides_from_item(items[0])

    def _fetch_child_replies(
        self,
        post_id: str,
        root_pk: str,
        target_cids: set[str],
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        all_raw: list[dict[str, Any]] = []
        seen_pks: set[str] = set()
        cursor_param = ""

        for _ in range(max_pages):
            path = f"/api/v1/media/{post_id}/comments/{root_pk}/child_comments/{cursor_param}"
            ch_data = self._ig_api_get(path)
            batch = ch_data.get("child_comments") or []
            for ch in batch:
                pk = str(ch.get("pk") or "")
                if pk and pk not in seen_pks:
                    seen_pks.add(pk)
                    all_raw.append(ch)

            next_max = ch_data.get("next_max_child_cursor")
            next_min = ch_data.get("next_min_child_cursor")
            has_more = bool(
                ch_data.get("has_more_tail_child_comments")
                or ch_data.get("has_more_head_child_comments")
            )
            if not has_more:
                break
            if len(all_raw) >= 20 and target_cids.issubset(seen_pks):
                break
            if next_max:
                cursor_param = f"?max_id={quote(str(next_max), safe='')}"
            elif next_min:
                cursor_param = f"?min_id={quote(str(next_min), safe='')}"
            else:
                break

        return all_raw

    def hydrate_post(
        self,
        post_id: str,
        my_comment_ids: list[str],
        my_username: str = "",
    ) -> dict[str, Any]:
        like_count: int | None = None
        comment_count: int | None = None
        owner_avatar = ""
        media_type: int | None = None
        slides: list[dict[str, str]] = []
        warnings: list[str] = []

        try:
            info_data = self._ig_api_get(f"/api/v1/media/{post_id}/info/")
            items = info_data.get("items") or []
            if items:
                item = items[0]
                like_count = item.get("like_count")
                comment_count = item.get("comment_count")
                media_type = item.get("media_type")
                owner_avatar = str((item.get("user") or {}).get("profile_pic_url") or "")
                slides = self._extract_slides_from_item(item)
        except RateLimitError:
            raise
        except Exception as exc:
            warnings.append(f"/info/ failed: {exc}")

        my_id_set = {str(cid) for cid in my_comment_ids if cid}
        my_user_lower = my_username.lower().lstrip("@")

        def _norm_comment(c: dict[str, Any]) -> dict[str, Any]:
            pk = str(c.get("pk") or "")
            user = c.get("user") or {}
            uname = str(user.get("username") or "")
            is_mine = (pk in my_id_set) or (
                bool(my_user_lower) and uname.lower() == my_user_lower
            )
            media_url, placeholder = self._extract_api_comment_media(c)
            return {
                "comment_id": pk,
                "username": uname,
                "avatar_url": str(user.get("profile_pic_url") or ""),
                "text": str(c.get("text") or ""),
                "time": self._format_relative_time(c.get("created_at") or c.get("created_at_utc")),
                "created_at": int(c.get("created_at") or c.get("created_at_utc") or 0),
                "likes": c.get("comment_like_count"),
                "media_url": media_url,
                "media_placeholder": placeholder,
                "is_mine": is_mine,
            }

        roots_map: dict[str, dict[str, Any]] = {}
        resolved_mine: set[str] = set()

        for cid in list(my_comment_ids)[:6]:
            if cid in resolved_mine:
                continue

            try:
                c_data = self._ig_api_get(
                    f"/api/v1/media/{post_id}/comments/"
                    f"?can_support_threading=true&permalink_enabled=true&target_comment_id={cid}"
                )
            except RateLimitError:
                raise
            except Exception as exc:
                warnings.append(f"/comments/ ({cid}) failed: {exc}")
                continue

            comments_list = c_data.get("comments") or []
            if not comments_list:
                continue

            matched_top: dict[str, Any] | None = None
            for cand in comments_list:
                cand_pk = str(cand.get("pk") or "")
                cand_user = str((cand.get("user") or {}).get("username") or "").lower()
                cand_previews = cand.get("preview_child_comments") or []
                cand_preview_pks = {str(p.get("pk") or "") for p in cand_previews}
                if (
                    cand_pk == cid
                    or cid in cand_preview_pks
                    or (my_user_lower and cand_user == my_user_lower and cand_pk in my_id_set)
                ):
                    matched_top = cand
                    break

            if matched_top is None:
                for cand in comments_list[:5]:
                    if (cand.get("child_comment_count") or 0) > 0:
                        matched_top = cand
                        break

            if matched_top is None:
                continue

            root_pk = str(matched_top.get("pk") or "")
            if not root_pk:
                continue

            if root_pk not in roots_map:
                root_norm = _norm_comment(matched_top)
                roots_map[root_pk] = {
                    "root": root_norm,
                    "children": {},
                    "fetched_replies": False,
                    "child_total": int(matched_top.get("child_comment_count") or 0),
                }
                if root_norm["is_mine"]:
                    resolved_mine.add(root_pk)

            entry = roots_map[root_pk]
            child_by_pk: dict[str, dict[str, Any]] = entry["children"]

            for p in matched_top.get("preview_child_comments") or []:
                np = _norm_comment(p)
                if np["comment_id"]:
                    child_by_pk[np["comment_id"]] = np
                    if np["is_mine"]:
                        resolved_mine.add(np["comment_id"])

            if not entry["fetched_replies"] and entry["child_total"] > 0:
                entry["fetched_replies"] = True
                try:
                    raw_children = self._fetch_child_replies(
                        post_id=post_id,
                        root_pk=root_pk,
                        target_cids=my_id_set,
                        max_pages=3,
                    )
                    for ch in raw_children:
                        nch = _norm_comment(ch)
                        if nch["comment_id"] and nch["comment_id"] not in child_by_pk:
                            child_by_pk[nch["comment_id"]] = nch
                            if nch["is_mine"]:
                                resolved_mine.add(nch["comment_id"])
                except RateLimitError:
                    raise
                except Exception as exc:
                    warnings.append(f"/child_comments/ ({root_pk}) failed: {exc}")

        threads: list[list[dict[str, Any]]] = []
        for entry in roots_map.values():
            root_norm = entry["root"]
            child_by_pk = entry["children"]

            has_my_comment = root_norm["is_mine"] or any(
                ch["is_mine"] for ch in child_by_pk.values()
            )
            if not has_my_comment:
                continue

            all_children = sorted(child_by_pk.values(), key=lambda x: x["created_at"])

            if len(all_children) > 12 and not root_norm["is_mine"]:
                mentioned_by_me: set[str] = set()
                for ch in all_children:
                    if ch["is_mine"]:
                        for token in ch["text"].split():
                            if token.startswith("@"):
                                mentioned_by_me.add(token.lstrip("@").rstrip(".,!?:;").lower())

                filtered: list[dict[str, Any]] = []
                for ch in all_children:
                    u_low = ch["username"].lower()
                    mentions_me = bool(my_user_lower) and f"@{my_user_lower}" in ch["text"].lower()
                    if ch["is_mine"] or u_low in mentioned_by_me or mentions_me:
                        filtered.append(ch)
                all_children = filtered

            threads.append([root_norm, *all_children])

        threads.sort(key=lambda grp: grp[0].get("created_at", 0))

        return {
            "post_id": post_id,
            "like_count": like_count,
            "comment_count": comment_count,
            "owner_avatar_url": owner_avatar,
            "media_type": media_type,
            "slides": slides,
            "threads": threads,
            "warnings": warnings,
        }

    def close(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self.template = None
