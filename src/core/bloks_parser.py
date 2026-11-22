from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class ParsedComment:
    comment_id: str
    post_id: str
    delete_key: str
    comment_text: str
    comment_time: str
    comment_author_username: str
    post_author_username: str
    post_text: str
    post_time: str
    post_media_code: str
    post_url: str
    post_image_url: str
    post_owner_avatar_url: str = ""
    comment_media_url: str = ""
    reply_to_username: str | None = None
    reply_to_text: str | None = None
    thread_items: list[dict[str, Any]] = field(default_factory=list)

@dataclass(slots=True)
class BloksParseResult:
    comments: list[ParsedComment]
    next_page_params: dict[str, Any] | None
    container_meta: dict[str, int]

def _tokenize_lispy(expr: str) -> list[Any]:
    tokens: list[Any] = []
    n = len(expr)
    i = 0

    while i < n:
        ch = expr[i]

        if ch in " \t\r\n,":
            i += 1
            continue

        if ch in "()":
            tokens.append(ch)
            i += 1
            continue

        if ch == '"':
            start = i
            i += 1
            escaped = False
            while i < n:
                c = expr[i]
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    i += 1
                    break
                i += 1
            raw_str = expr[start:i]
            try:
                tokens.append(json.loads(raw_str))
            except json.JSONDecodeError:
                tokens.append(raw_str[1:-1])
            continue

        start = i
        while i < n and expr[i] not in " \t\r\n,()":
            i += 1
        atom = expr[start:i]

        if atom == "true":
            tokens.append(True)
        elif atom == "false":
            tokens.append(False)
        elif atom == "null":
            tokens.append(None)
        else:
            try:
                tokens.append(int(atom))
            except ValueError:
                try:
                    tokens.append(float(atom))
                except ValueError:
                    tokens.append(atom)

    return tokens

def _parse_ast(tokens: list[Any]) -> Any:
    root: list[Any] = []
    stack: list[list[Any]] = [root]

    for tok in tokens:
        if tok == "(":
            node: list[Any] = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(tok)

    return root[0] if len(root) == 1 else root

def _eval_bloks_primitives(node: Any) -> Any:
    if not isinstance(node, list) or not node:
        return node

    op = node[0]

    if op == "dqp" and len(node) == 2:
        return bool(node[1])

    if op == "ety" and len(node) == 2:
        return node[1]

    if op == "dkc":
        return [_eval_bloks_primitives(item) for item in node[1:]]

    if op == "f4i" and len(node) == 3:
        keys = _eval_bloks_primitives(node[1])
        vals = _eval_bloks_primitives(node[2])
        if isinstance(keys, list) and isinstance(vals, list):
            return dict(zip(keys, vals))

    return [_eval_bloks_primitives(item) for item in node]

def parse_lispy(expr: str) -> Any:
    return _eval_bloks_primitives(_parse_ast(_tokenize_lispy(expr)))

def _iter_strings(obj: Any):
    stack = [obj]
    while stack:
        curr = stack.pop()
        if isinstance(curr, str):
            yield curr
        elif isinstance(curr, dict):
            stack.extend(curr.values())
        elif isinstance(curr, list):
            stack.extend(curr)

def _find_comment_entries(ast_obj: Any, out: list[dict[str, Any]]) -> None:
    stack = [ast_obj]
    while stack:
        curr = stack.pop()
        if isinstance(curr, dict):
            if "post_info" in curr and "comments_info" in curr:
                out.append(curr)
            else:
                stack.extend(curr.values())
        elif isinstance(curr, list):
            stack.extend(curr)

def _find_next_page_params(ast_obj: Any) -> dict[str, Any] | None:
    stack = [ast_obj]
    while stack:
        curr = stack.pop()
        if isinstance(curr, list):
            if (
                len(curr) >= 3
                and curr[0] == "dmi"
                and curr[1] == "com.instagram.privacy.activity_center.comments_next"
                and isinstance(curr[2], dict)
            ):
                return curr[2]
            stack.extend(curr)
        elif isinstance(curr, dict):
            stack.extend(curr.values())
    return None

def _extract_comment_media_url(c: dict[str, Any]) -> str:
    for key in ("comment_url", "media_url", "comment_image_url", "image_url", "gif_url"):
        val = c.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val.strip()
    return ""

def parse_wbloks_response(response_json: dict[str, Any]) -> BloksParseResult:
    comments_by_id: dict[str, ParsedComment] = {}
    next_page_params: dict[str, Any] | None = None
    container_meta: dict[str, int] = {}

    payload = response_json.get("payload", response_json)

    for text in _iter_strings(payload):
        if "comments_set_info" in text and "post_info" in text:
            try:
                evaluated = parse_lispy(text)
            except Exception:
                continue

            entries: list[dict[str, Any]] = []
            _find_comment_entries(evaluated, entries)

            for entry in entries:
                post_list = entry.get("post_info") or []
                post = (
                    post_list[0]
                    if (isinstance(post_list, list) and post_list and isinstance(post_list[0], dict))
                    else {}
                )

                post_id = str(post.get("post_id") or "")
                post_code = str(post.get("post_media_code") or "")
                post_url = f"https://www.instagram.com/p/{post_code}/" if post_code else ""
                post_owner_avatar = str(post.get("user_image_url") or "")

                comments_info = entry.get("comments_info") or []
                for group in comments_info:
                    if not isinstance(group, dict):
                        continue
                    cset = group.get("comments_set_info") or []
                    if not isinstance(cset, list):
                        continue

                    thread_items: list[dict[str, Any]] = []
                    parent_user: str | None = None
                    parent_text: str | None = None

                    for c in cset:
                        if not isinstance(c, dict):
                            continue
                        is_mine = bool(c.get("user_created_this_comment"))
                        media_url = _extract_comment_media_url(c)
                        item_dict = {
                            "comment_id": str(c.get("comment_id") or "").strip(),
                            "username": str(c.get("comment_author_username") or ""),
                            "text": str(c.get("comment_text") or ""),
                            "time": str(c.get("comment_time") or ""),
                            "is_mine": is_mine,
                            "avatar_url": str(c.get("user_image_url") or ""),
                            "media_url": media_url,
                        }
                        if item_dict["comment_id"] or item_dict["text"] or media_url:
                            thread_items.append(item_dict)
                        if not is_mine and parent_user is None:
                            parent_user = item_dict["username"] or None
                            parent_text = item_dict["text"] or None

                    for c in cset:
                        if not isinstance(c, dict) or not c.get("user_created_this_comment"):
                            continue

                        cid = str(c.get("comment_id") or "").strip()
                        pid = str(c.get("post_id") or post_id).strip()
                        if not cid or not pid:
                            continue

                        code = str(c.get("post_media_code") or post_code)
                        url = f"https://www.instagram.com/p/{code}/" if code else post_url
                        c_media = _extract_comment_media_url(c)

                        comments_by_id[cid] = ParsedComment(
                            comment_id=cid,
                            post_id=pid,
                            delete_key=f"{pid}:{cid}",
                            comment_text=str(c.get("comment_text") or ""),
                            comment_time=str(c.get("comment_time") or ""),
                            comment_author_username=str(c.get("comment_author_username") or ""),
                            post_author_username=str(
                                c.get("post_author_username") or post.get("post_author_username") or ""
                            ),
                            post_text=str(post.get("post_text") or ""),
                            post_time=str(post.get("post_time") or ""),
                            post_media_code=code,
                            post_url=url,
                            post_image_url=str(post.get("post_image_url") or ""),
                            post_owner_avatar_url=post_owner_avatar,
                            comment_media_url=c_media,
                            reply_to_username=parent_user,
                            reply_to_text=parent_text,
                            thread_items=thread_items,
                        )

        if next_page_params is None and "com.instagram.privacy.activity_center.comments_next" in text:
            try:
                evaluated = parse_lispy(text)
                found = _find_next_page_params(evaluated)
                if found:
                    next_page_params = found
                    raw_ac = found.get("activity_center_params")
                    if isinstance(raw_ac, str):
                        try:
                            ac_dict = json.loads(raw_ac)
                            for k in ("content_container_id", "content_element_id", "content_spinner_id"):
                                if isinstance(ac_dict.get(k), int):
                                    container_meta[k] = ac_dict[k]
                        except json.JSONDecodeError:
                            pass
            except Exception:
                continue

    return BloksParseResult(
        comments=list(comments_by_id.values()),
        next_page_params=next_page_params,
        container_meta=container_meta,
    )
