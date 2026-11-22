from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from core.database import DEFAULT_SETTINGS

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total

def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"

class AccountManager:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir = self.data_dir / "accounts"
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_file = self.data_dir / "accounts.json"
        self.config_file = self.data_dir / "config.json"

    def _read_accounts_doc(self) -> dict[str, Any]:
        if not self.accounts_file.is_file():
            return {"active_user_id": "", "accounts": []}
        try:
            doc = json.loads(self.accounts_file.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("active_user_id", "")
                doc.setdefault("accounts", [])
                return doc
        except (OSError, json.JSONDecodeError):
            pass
        return {"active_user_id": "", "accounts": []}

    def _write_accounts_doc(self, doc: dict[str, Any]) -> None:
        _atomic_write_json(self.accounts_file, doc)

    def get_global_settings(self) -> dict[str, Any]:
        merged = dict(DEFAULT_SETTINGS)
        if self.config_file.is_file():
            try:
                stored = json.loads(self.config_file.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    merged.update(stored)
            except (OSError, json.JSONDecodeError):
                pass
        return merged

    def save_global_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        merged = self.get_global_settings()
        merged.update(settings)
        _atomic_write_json(self.config_file, merged)
        return merged

    def get_account_dir(self, user_id: str) -> Path:
        d = self.accounts_dir / str(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_db_path(self, user_id: str) -> Path:
        return self.get_account_dir(user_id) / "uncomment.db"

    def get_media_cache_dir(self, user_id: str) -> Path:
        d = self.get_account_dir(user_id) / "media_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_avatar_path(self, user_id: str) -> Path:
        return self.get_account_dir(user_id) / "avatar.jpg"

    def list_accounts(self) -> list[dict[str, Any]]:
        doc = self._read_accounts_doc()
        out: list[dict[str, Any]] = []
        for acc in doc.get("accounts") or []:
            if not isinstance(acc, dict) or not acc.get("user_id"):
                continue
            uid = str(acc["user_id"])
            item = dict(acc)
            item["db_path"] = str(self.get_db_path(uid))
            item["media_cache_dir"] = str(self.get_media_cache_dir(uid))
            av = self.get_avatar_path(uid)
            item["avatar_path"] = str(av) if av.is_file() else ""
            out.append(item)
        return out

    def get_active_user_id(self) -> str:
        return str(self._read_accounts_doc().get("active_user_id") or "")

    def set_active_user_id(self, user_id: str) -> None:
        doc = self._read_accounts_doc()
        doc["active_user_id"] = str(user_id)
        self._write_accounts_doc(doc)

    def get_active_account(self) -> dict[str, Any] | None:
        uid = self.get_active_user_id()
        if not uid:
            return None
        for acc in self.list_accounts():
            if acc["user_id"] == uid:
                return acc
        return None

    def register_account(
        self,
        user_id: str,
        username: str,
        avatar_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        uid = str(user_id).strip()
        uname = str(username).strip().lstrip("@") or f"user_{uid}"
        self.get_account_dir(uid)

        av_path = self.get_avatar_path(uid)
        if avatar_bytes:
            try:
                av_path.write_bytes(avatar_bytes)
            except OSError:
                pass

        doc = self._read_accounts_doc()
        accounts: list[dict[str, Any]] = [
            a for a in (doc.get("accounts") or []) if str(a.get("user_id")) != uid
        ]
        entry = {
            "user_id": uid,
            "username": uname,
            "last_used": _now_iso(),
        }
        accounts.insert(0, entry)
        doc["accounts"] = accounts
        doc["active_user_id"] = uid
        self._write_accounts_doc(doc)

        result = dict(entry)
        result["db_path"] = str(self.get_db_path(uid))
        result["media_cache_dir"] = str(self.get_media_cache_dir(uid))
        result["avatar_path"] = str(av_path) if av_path.is_file() else ""
        return result

    def remove_account_entry(self, user_id: str, delete_data: bool = False) -> None:
        uid = str(user_id)
        doc = self._read_accounts_doc()
        doc["accounts"] = [
            a for a in (doc.get("accounts") or []) if str(a.get("user_id")) != uid
        ]
        if doc.get("active_user_id") == uid:
            doc["active_user_id"] = ""
        self._write_accounts_doc(doc)
        if delete_data:
            shutil.rmtree(self.accounts_dir / uid, ignore_errors=True)

    def migrate_legacy_if_needed(self, user_id: str, username: str) -> bool:
        target_db = self.get_db_path(user_id)
        if target_db.exists():
            return False

        legacy_db: Path | None = None
        for name in ("uncomment.db", "sweeper.db"):
            cand = self.data_dir / name
            if cand.is_file():
                legacy_db = cand
                break
        if legacy_db is None:
            return False

        uname_lower = username.strip().lstrip("@").lower()
        try:
            conn = sqlite3.connect(legacy_db)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            row = conn.execute(
                """
                SELECT LOWER(comment_author_username) AS u, COUNT(*) AS cnt
                FROM comments
                WHERE comment_author_username IS NOT NULL AND comment_author_username != ''
                GROUP BY LOWER(comment_author_username)
                ORDER BY cnt DESC
                LIMIT 1
                """
            ).fetchone()
            dominant_user = str(row[0] or "") if row else ""

            if dominant_user and uname_lower and dominant_user != uname_lower:
                conn.close()
                return False

            if uname_lower:
                with conn:
                    conn.execute(
                        """
                        DELETE FROM comments
                        WHERE comment_author_username IS NOT NULL
                          AND comment_author_username != ''
                          AND LOWER(comment_author_username) != ?
                        """,
                        (uname_lower,),
                    )
            conn.close()
        except Exception:
            return False

        target_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_db), str(target_db))
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(legacy_db) + suffix)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)

        legacy_cache = self.data_dir / "media_cache"
        target_cache = self.get_media_cache_dir(user_id)
        if legacy_cache.is_dir():
            for f in legacy_cache.iterdir():
                if f.is_file():
                    dest = target_cache / f.name
                    if not dest.exists():
                        shutil.move(str(f), str(dest))
            try:
                legacy_cache.rmdir()
            except OSError:
                pass

        return True

    def get_storage_sizes(self, profile_dir: Path) -> dict[str, int]:
        media_bytes = 0
        for p in self.accounts_dir.glob("*/media_cache"):
            media_bytes += _dir_size_bytes(p)
        media_bytes += _dir_size_bytes(self.data_dir / "media_cache")

        total_data = _dir_size_bytes(self.data_dir)
        db_bytes = max(0, total_data - media_bytes)
        browser_bytes = _dir_size_bytes(Path(profile_dir))
        return {
            "media_cache": media_bytes,
            "databases": db_bytes,
            "browser_profile": browser_bytes,
            "total": total_data + browser_bytes,
        }

    def clear_media_cache(self, user_id: str | None = None) -> int:
        targets = (
            [self.get_media_cache_dir(user_id)]
            if user_id
            else list(self.accounts_dir.glob("*/media_cache")) + [self.data_dir / "media_cache"]
        )
        removed = 0
        for d in targets:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed

    def wipe_all_data(self, profile_dir: Path) -> None:
        for target in (self.data_dir, Path(profile_dir)):
            if target.exists():
                for child in list(target.iterdir()):
                    try:
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink(missing_ok=True)
                    except OSError:
                        pass
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
