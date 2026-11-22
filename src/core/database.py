from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.bloks_parser import ParsedComment

DEFAULT_SETTINGS: dict[str, Any] = {
    "daily_limit": 30,
    "min_delay": 12,
    "max_delay": 26,
    "batch_posts": 25,
    "sync_pages": 10,
    "sort_mode": "random",
    "language": "en",
    "theme": "instagram_dark",
    "auto_start_queue": True,
    "auto_close_done": False,
    "show_dev_logs": False,
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS comments (
    comment_id              TEXT PRIMARY KEY,
    post_id                 TEXT NOT NULL,
    delete_key              TEXT NOT NULL,
    comment_text            TEXT NOT NULL,
    comment_time            TEXT,
    comment_author_username TEXT,
    post_author_username    TEXT,
    post_text               TEXT,
    post_time               TEXT,
    post_media_code         TEXT,
    post_url                TEXT,
    post_image_url          TEXT,
    post_owner_avatar_url   TEXT,
    comment_media_url       TEXT,
    reply_to_username       TEXT,
    reply_to_text           TEXT,
    thread_json             TEXT,
    status                  TEXT NOT NULL DEFAULT 'UNREVIEWED'
                            CHECK (status IN (
                                'UNREVIEWED', 'KEEP', 'QUEUED_DELETE',
                                'DELETING', 'DELETED', 'FAILED'
                            )),
    queued_at               TEXT,
    deleted_at              TEXT,
    error_message           TEXT,
    fetched_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(status);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_queued ON comments(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_comments_deleted_at ON comments(deleted_at);

CREATE TABLE IF NOT EXISTS post_meta (
    post_id               TEXT PRIMARY KEY,
    like_count            INTEGER,
    comment_count         INTEGER,
    owner_avatar_url      TEXT,
    media_type            INTEGER,
    hydrated_threads_json TEXT,
    hydrated_at           TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_TIME_UNIT_MINUTES = {
    "s": 1 / 60,
    "m": 1,
    "h": 60,
    "d": 60 * 24,
    "w": 60 * 24 * 7,
    "y": 60 * 24 * 365,
}

def _age_minutes(time_str: str | None) -> float:
    if not time_str:
        return 0.0
    m = re.match(r"^\s*(\d+)\s*([smhdwy])", time_str.lower())
    if not m:
        return 0.0
    return int(m.group(1)) * _TIME_UNIT_MINUTES.get(m.group(2), 1.0)

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def _today_prefix() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")

class CommentDatabase:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        with conn:
            conn.executescript(SCHEMA_SQL)

            cols = {r["name"] for r in conn.execute("PRAGMA table_info(comments)").fetchall()}
            if "post_owner_avatar_url" not in cols:
                conn.execute("ALTER TABLE comments ADD COLUMN post_owner_avatar_url TEXT")
            if "comment_media_url" not in cols:
                conn.execute("ALTER TABLE comments ADD COLUMN comment_media_url TEXT")
            if "thread_json" not in cols:
                conn.execute("ALTER TABLE comments ADD COLUMN thread_json TEXT")

            pm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(post_meta)").fetchall()}
            for col_name, col_type in (
                ("like_count", "INTEGER"),
                ("comment_count", "INTEGER"),
                ("owner_avatar_url", "TEXT"),
                ("media_type", "INTEGER"),
                ("hydrated_threads_json", "TEXT"),
                ("hydrated_at", "TEXT"),
            ):
                if col_name not in pm_cols:
                    conn.execute(f"ALTER TABLE post_meta ADD COLUMN {col_name} {col_type}")

            conn.execute(
                "UPDATE comments SET status = 'QUEUED_DELETE' WHERE status = 'DELETING'"
            )
            conn.execute(
                """
                DELETE FROM post_meta
                WHERE like_count IS NULL
                  AND (hydrated_threads_json IS NULL OR hydrated_threads_json = '')
                """
            )

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def get_settings(self) -> dict[str, Any]:
        stored = self.get_json_state("user_settings")
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(stored, dict):
            merged.update(stored)
        legacy_limit = self.get_state("daily_limit")
        if legacy_limit and "daily_limit" not in (stored or {}):
            try:
                merged["daily_limit"] = int(legacy_limit)
            except ValueError:
                pass
        return merged

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.get_settings()
        merged.update(settings)
        merged["daily_limit"] = max(1, min(100, int(merged["daily_limit"])))
        merged["min_delay"] = max(5, int(merged["min_delay"]))
        merged["max_delay"] = max(merged["min_delay"] + 1, int(merged["max_delay"]))
        merged["batch_posts"] = max(5, min(200, int(merged["batch_posts"])))
        merged["sync_pages"] = max(1, min(50, int(merged["sync_pages"])))
        self.set_state("user_settings", merged)
        self.set_state("daily_limit", str(merged["daily_limit"]))
        return merged

    def upsert_comments(self, comments: list[ParsedComment]) -> tuple[int, int]:
        if not comments:
            return 0, 0

        now = _now_iso()
        conn = self._conn()
        inserted = 0
        updated = 0

        with conn:
            existing_ids = {
                row[0]
                for row in conn.execute(
                    f"SELECT comment_id FROM comments WHERE comment_id IN ({','.join('?' * len(comments))})",
                    [c.comment_id for c in comments],
                )
            }

            conn.executemany(
                """
                INSERT INTO comments (
                    comment_id, post_id, delete_key, comment_text, comment_time,
                    comment_author_username, post_author_username, post_text,
                    post_time, post_media_code, post_url, post_image_url,
                    post_owner_avatar_url, comment_media_url,
                    reply_to_username, reply_to_text, thread_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(comment_id) DO UPDATE SET
                    comment_time          = excluded.comment_time,
                    post_time             = excluded.post_time,
                    post_image_url        = excluded.post_image_url,
                    post_owner_avatar_url = COALESCE(NULLIF(excluded.post_owner_avatar_url, ''), comments.post_owner_avatar_url),
                    comment_media_url     = COALESCE(NULLIF(excluded.comment_media_url, ''), comments.comment_media_url),
                    reply_to_username     = COALESCE(excluded.reply_to_username, comments.reply_to_username),
                    reply_to_text         = COALESCE(excluded.reply_to_text, comments.reply_to_text),
                    thread_json           = COALESCE(NULLIF(excluded.thread_json, ''), comments.thread_json),
                    fetched_at            = excluded.fetched_at
                """,
                [
                    (
                        c.comment_id,
                        c.post_id,
                        c.delete_key,
                        c.comment_text,
                        c.comment_time,
                        c.comment_author_username,
                        c.post_author_username,
                        c.post_text,
                        c.post_time,
                        c.post_media_code,
                        c.post_url,
                        c.post_image_url,
                        c.post_owner_avatar_url,
                        c.comment_media_url,
                        c.reply_to_username,
                        c.reply_to_text,
                        json.dumps(c.thread_items, ensure_ascii=False) if c.thread_items else "",
                        now,
                    )
                    for c in comments
                ],
            )

            for c in comments:
                if c.comment_id in existing_ids:
                    updated += 1
                else:
                    inserted += 1

        return inserted, updated

    def get_post_meta(self, post_id: str) -> dict[str, Any] | None:
        row = self._conn().execute(
            "SELECT * FROM post_meta WHERE post_id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None

    def clear_post_hydration(self, post_id: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM post_meta WHERE post_id = ?", (post_id,))

    def save_post_hydration(
        self,
        post_id: str,
        like_count: int | None,
        comment_count: int | None,
        owner_avatar_url: str,
        media_type: int | None,
        threads: list[list[dict[str, Any]]],
    ) -> None:
        if like_count is None and not threads:
            return

        now = _now_iso()
        threads_str = json.dumps(threads, ensure_ascii=False) if threads else ""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO post_meta (
                    post_id, like_count, comment_count, owner_avatar_url,
                    media_type, hydrated_threads_json, hydrated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    like_count            = COALESCE(excluded.like_count, post_meta.like_count),
                    comment_count         = COALESCE(excluded.comment_count, post_meta.comment_count),
                    owner_avatar_url      = COALESCE(NULLIF(excluded.owner_avatar_url, ''), post_meta.owner_avatar_url),
                    media_type            = COALESCE(excluded.media_type, post_meta.media_type),
                    hydrated_threads_json = COALESCE(NULLIF(excluded.hydrated_threads_json, ''), post_meta.hydrated_threads_json),
                    hydrated_at           = excluded.hydrated_at
                """,
                (
                    post_id,
                    like_count,
                    comment_count,
                    owner_avatar_url,
                    media_type,
                    threads_str,
                    now,
                ),
            )
            if owner_avatar_url:
                conn.execute(
                    """
                    UPDATE comments
                    SET post_owner_avatar_url = ?
                    WHERE post_id = ? AND (post_owner_avatar_url IS NULL OR post_owner_avatar_url = '')
                    """,
                    (owner_avatar_url, post_id),
                )

            template_row = conn.execute(
                """
                SELECT post_author_username, post_text, post_time, post_media_code,
                       post_url, post_image_url, post_owner_avatar_url
                FROM comments WHERE post_id = ? LIMIT 1
                """,
                (post_id,),
            ).fetchone()

            if template_row and threads:
                for grp in threads:
                    for item in grp:
                        if not isinstance(item, dict) or not item.get("is_mine"):
                            continue
                        cid = str(item.get("comment_id") or "").strip()
                        if not cid:
                            continue
                        media_u = str(item.get("media_url") or "")
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO comments (
                                comment_id, post_id, delete_key, comment_text, comment_time,
                                comment_author_username, post_author_username, post_text,
                                post_time, post_media_code, post_url, post_image_url,
                                post_owner_avatar_url, comment_media_url, fetched_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cid,
                                post_id,
                                f"{post_id}:{cid}",
                                str(item.get("text") or ""),
                                str(item.get("time") or ""),
                                str(item.get("username") or ""),
                                template_row["post_author_username"],
                                template_row["post_text"],
                                template_row["post_time"],
                                template_row["post_media_code"],
                                template_row["post_url"],
                                template_row["post_image_url"],
                                owner_avatar_url or template_row["post_owner_avatar_url"],
                                media_u,
                                now,
                            ),
                        )
                        if media_u:
                            conn.execute(
                                "UPDATE comments SET comment_media_url = ? WHERE comment_id = ?",
                                (media_u, cid),
                            )

    def get_unreviewed_post_ids(
        self,
        sort_mode: str = "random",
        limit: int = 25,
        search_query: str = "",
    ) -> list[str]:
        sql = """
            SELECT post_id, comment_time, comment_text, post_author_username, post_text
            FROM comments
            WHERE status = 'UNREVIEWED'
        """
        params: list[Any] = []
        q = search_query.strip()
        if q:
            like = f"%{q}%"
            sql += " AND (comment_text LIKE ? OR post_author_username LIKE ? OR post_text LIKE ?)"
            params.extend([like, like, like])

        rows = self._conn().execute(sql, params).fetchall()

        post_ages: dict[str, float] = {}
        for r in rows:
            pid = r["post_id"]
            age = _age_minutes(r["comment_time"])
            if pid not in post_ages or age > post_ages[pid]:
                post_ages[pid] = age

        pids = list(post_ages.keys())
        if sort_mode == "oldest":
            pids.sort(key=lambda p: post_ages[p], reverse=True)
        elif sort_mode == "newest":
            pids.sort(key=lambda p: post_ages[p])
        else:
            random.shuffle(pids)

        return pids[:limit]

    def get_post_bundle(self, post_id: str) -> dict[str, Any] | None:
        rows = self._conn().execute(
            "SELECT rowid, * FROM comments WHERE post_id = ?",
            (post_id,),
        ).fetchall()
        if not rows:
            return None

        sorted_rows = sorted(
            rows,
            key=lambda r: (_age_minutes(r["comment_time"]), int(r["rowid"])),
            reverse=True,
        )

        my_comments_map: dict[str, dict[str, Any]] = {r["comment_id"]: dict(r) for r in sorted_rows}
        first = dict(sorted_rows[0])
        meta = self.get_post_meta(post_id)

        post_image_url = next((r["post_image_url"] for r in sorted_rows if r["post_image_url"]), "")
        owner_avatar = (
            (meta.get("owner_avatar_url") if meta else "")
            or next((r["post_owner_avatar_url"] for r in sorted_rows if r["post_owner_avatar_url"]), "")
        )

        seen_ids: set[str] = set()
        threads: list[list[dict[str, Any]]] = []

        hydrated_raw = (meta.get("hydrated_threads_json") if meta else "") or ""
        if hydrated_raw:
            try:
                hydrated_groups = json.loads(hydrated_raw)
            except json.JSONDecodeError:
                hydrated_groups = []

            for grp in hydrated_groups:
                if not isinstance(grp, list):
                    continue
                built_grp: list[dict[str, Any]] = []
                for item in grp:
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("comment_id") or "")
                    dedup_key = cid or f"{item.get('username')}:{item.get('text')}"
                    if dedup_key in seen_ids:
                        continue
                    seen_ids.add(dedup_key)

                    if cid in my_comments_map:
                        db_row = my_comments_map[cid]
                        built_grp.append(
                            {
                                "comment_id": cid,
                                "username": item.get("username") or db_row["comment_author_username"] or "you",
                                "text": item.get("text") if item.get("text") is not None else db_row["comment_text"],
                                "time": item.get("time") or db_row["comment_time"] or "",
                                "is_mine": True,
                                "status": db_row["status"],
                                "avatar_url": item.get("avatar_url") or "",
                                "media_url": item.get("media_url") or db_row.get("comment_media_url") or "",
                                "media_placeholder": str(item.get("media_placeholder") or ""),
                                "likes": item.get("likes"),
                            }
                        )
                    else:
                        built_grp.append(
                            {
                                "comment_id": cid,
                                "username": item.get("username") or "",
                                "text": item.get("text") or "",
                                "time": item.get("time") or "",
                                "is_mine": bool(item.get("is_mine")),
                                "status": "CONTEXT",
                                "avatar_url": item.get("avatar_url") or "",
                                "media_url": item.get("media_url") or "",
                                "media_placeholder": str(item.get("media_placeholder") or ""),
                                "likes": item.get("likes"),
                            }
                        )
                if built_grp:
                    threads.append(built_grp)

        if not threads:
            for r in sorted_rows:
                raw_thread = r["thread_json"]
                group_items: list[dict[str, Any]] = []

                if raw_thread:
                    try:
                        parsed_items = json.loads(raw_thread)
                    except json.JSONDecodeError:
                        parsed_items = []

                    for item in parsed_items:
                        cid = str(item.get("comment_id") or "")
                        dedup_key = cid or f"{item.get('username')}:{item.get('text')}"
                        if dedup_key in seen_ids:
                            continue
                        seen_ids.add(dedup_key)

                        if cid in my_comments_map:
                            db_row = my_comments_map[cid]
                            group_items.append(
                                {
                                    "comment_id": cid,
                                    "username": db_row["comment_author_username"] or item.get("username") or "you",
                                    "text": db_row["comment_text"],
                                    "time": db_row["comment_time"] or item.get("time") or "",
                                    "is_mine": True,
                                    "status": db_row["status"],
                                    "avatar_url": item.get("avatar_url") or "",
                                    "media_url": item.get("media_url") or db_row.get("comment_media_url") or "",
                                    "media_placeholder": "",
                                    "likes": None,
                                }
                            )
                        else:
                            group_items.append(
                                {
                                    "comment_id": cid,
                                    "username": item.get("username") or "",
                                    "text": item.get("text") or "",
                                    "time": item.get("time") or "",
                                    "is_mine": False,
                                    "status": "CONTEXT",
                                    "avatar_url": item.get("avatar_url") or "",
                                    "media_url": item.get("media_url") or "",
                                    "media_placeholder": "",
                                    "likes": None,
                                }
                            )
                else:
                    cid = r["comment_id"]
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)

                    if r["reply_to_username"] and r["reply_to_text"]:
                        parent_key = f"parent:{r['reply_to_username']}:{r['reply_to_text']}"
                        if parent_key not in seen_ids:
                            seen_ids.add(parent_key)
                            group_items.append(
                                {
                                    "comment_id": "",
                                    "username": r["reply_to_username"],
                                    "text": r["reply_to_text"],
                                    "time": "",
                                    "is_mine": False,
                                    "status": "CONTEXT",
                                    "avatar_url": "",
                                    "media_url": "",
                                    "media_placeholder": "",
                                    "likes": None,
                                }
                            )

                    group_items.append(
                        {
                            "comment_id": cid,
                            "username": r["comment_author_username"] or "you",
                            "text": r["comment_text"],
                            "time": r["comment_time"] or "",
                            "is_mine": True,
                            "status": r["status"],
                            "avatar_url": "",
                            "media_url": r["comment_media_url"] or "",
                            "media_placeholder": "",
                            "likes": None,
                        }
                    )

                if group_items:
                    threads.append(group_items)

            merged_threads: list[list[dict[str, Any]]] = []
            for grp in threads:
                if (
                    merged_threads
                    and grp
                    and grp[0].get("text", "").lstrip().startswith("@")
                    and any(
                        m.get("username")
                        and grp[0]["text"].lstrip().lower().startswith(f"@{m['username'].lower()}")
                        for m in merged_threads[-1]
                    )
                ):
                    merged_threads[-1].extend(grp)
                else:
                    merged_threads.append(grp)
            threads = merged_threads

        for cid, db_row in my_comments_map.items():
            if cid not in seen_ids:
                threads.append(
                    [
                        {
                            "comment_id": cid,
                            "username": db_row["comment_author_username"] or "you",
                            "text": db_row["comment_text"],
                            "time": db_row["comment_time"] or "",
                            "is_mine": True,
                            "status": db_row["status"],
                            "avatar_url": "",
                            "media_url": db_row.get("comment_media_url") or "",
                            "media_placeholder": "",
                            "likes": None,
                        }
                    ]
                )

        return {
            "post_id": post_id,
            "post_author_username": first.get("post_author_username") or "unknown",
            "post_text": first.get("post_text") or "",
            "post_time": first.get("post_time") or "",
            "post_url": first.get("post_url") or "",
            "post_image_url": post_image_url,
            "post_owner_avatar_url": owner_avatar,
            "like_count": meta.get("like_count") if meta else None,
            "comment_count": meta.get("comment_count") if meta else None,
            "media_type": meta.get("media_type") if meta else None,
            "is_hydrated": bool(
                meta
                and meta.get("hydrated_at")
                and (meta.get("like_count") is not None or bool(meta.get("hydrated_threads_json")))
            ),
            "threads": threads,
            "my_comments": list(my_comments_map.values()),
        }

    def set_status(
        self,
        comment_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        conn = self._conn()
        now = _now_iso()
        with conn:
            if status == "QUEUED_DELETE":
                conn.execute(
                    """
                    UPDATE comments
                    SET status = 'QUEUED_DELETE', queued_at = ?, error_message = NULL
                    WHERE comment_id = ? AND status != 'DELETED'
                    """,
                    (now, comment_id),
                )
            elif status == "DELETED":
                conn.execute(
                    """
                    UPDATE comments
                    SET status = 'DELETED', deleted_at = ?, error_message = NULL
                    WHERE comment_id = ?
                    """,
                    (now, comment_id),
                )
            elif status == "FAILED":
                conn.execute(
                    """
                    UPDATE comments
                    SET status = 'FAILED', error_message = ?
                    WHERE comment_id = ?
                    """,
                    (error_message, comment_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE comments
                    SET status = ?, error_message = NULL
                    WHERE comment_id = ? AND status != 'DELETED'
                    """,
                    (status, comment_id),
                )

    def set_many_status(self, comment_ids: list[str], status: str) -> None:
        for cid in comment_ids:
            self.set_status(cid, status)

    def cancel_queued(self, comment_id: str) -> bool:
        conn = self._conn()
        with conn:
            cur = conn.execute(
                """
                UPDATE comments
                SET status = 'UNREVIEWED', queued_at = NULL
                WHERE comment_id = ? AND status = 'QUEUED_DELETE'
                """,
                (comment_id,),
            )
            return cur.rowcount > 0

    def clear_cooldown(self) -> None:
        self.set_state("cooldown_until", "")

    def retry_failed_comments(self) -> int:
        conn = self._conn()
        now = _now_iso()
        with conn:
            cur = conn.execute(
                """
                UPDATE comments
                SET status = 'QUEUED_DELETE', queued_at = ?, error_message = NULL
                WHERE status = 'FAILED'
                """,
                (now,),
            )
            return cur.rowcount

    def reset_kept_comments(self) -> int:
        conn = self._conn()
        with conn:
            cur = conn.execute(
                "UPDATE comments SET status = 'UNREVIEWED' WHERE status = 'KEEP'"
            )
            return cur.rowcount

    def peek_next_queued(self) -> dict[str, Any] | None:
        row = self._conn().execute(
            """
            SELECT * FROM comments
            WHERE status = 'QUEUED_DELETE'
            ORDER BY queued_at ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def get_queue_list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            """
            SELECT comment_id, post_id, comment_text, comment_media_url,
                   post_author_username, comment_time, status, queued_at
            FROM comments
            WHERE status IN ('DELETING', 'QUEUED_DELETE')
            ORDER BY CASE status WHEN 'DELETING' THEN 0 ELSE 1 END, queued_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_deleted(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            """
            SELECT comment_id, post_id, comment_text, comment_media_url,
                   post_author_username, comment_time, deleted_at
            FROM comments
            WHERE status = 'DELETED'
            ORDER BY deleted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_deleted_today(self) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) FROM comments WHERE status = 'DELETED' AND deleted_at LIKE ?",
            (f"{_today_prefix()}%",),
        ).fetchone()
        return int(row[0]) if row else 0

    def get_stats(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) AS cnt FROM comments GROUP BY status"
        ).fetchall()
        counts = {r["status"]: int(r["cnt"]) for r in rows}
        unreviewed_posts = self._conn().execute(
            "SELECT COUNT(DISTINCT post_id) FROM comments WHERE status = 'UNREVIEWED'"
        ).fetchone()[0]

        settings = self.get_settings()
        daily_limit = int(settings["daily_limit"])
        deleted_today = self.count_deleted_today()
        return {
            "total": sum(counts.values()),
            "unreviewed": counts.get("UNREVIEWED", 0),
            "unreviewed_posts": int(unreviewed_posts),
            "keep": counts.get("KEEP", 0),
            "queued": counts.get("QUEUED_DELETE", 0) + counts.get("DELETING", 0),
            "deleted": counts.get("DELETED", 0),
            "failed": counts.get("FAILED", 0),
            "deleted_today": deleted_today,
            "daily_limit": daily_limit,
            "remaining_today": max(0, daily_limit - deleted_today),
        }

    def export_all_json(self, output_path: Path) -> int:
        rows = self._conn().execute("SELECT * FROM comments ORDER BY rowid ASC").fetchall()
        payload = [dict(r) for r in rows]
        Path(output_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return len(payload)

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._conn().execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: Any) -> None:
        serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, serialized),
            )

    def get_json_state(self, key: str) -> Any:
        raw = self.get_state(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def is_in_cooldown(self) -> tuple[bool, str | None]:
        until = self.get_state("cooldown_until")
        if not until:
            return False, None
        try:
            dt = datetime.fromisoformat(until)
            if datetime.now(timezone.utc) < dt.astimezone(timezone.utc):
                return True, until
        except ValueError:
            pass
        return False, None
