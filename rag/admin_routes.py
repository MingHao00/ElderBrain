"""管理端接口。会话角色来自环境变量名单，写操作沿用 CSRF。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from admin_queue import list_review_queue
from admin_store import (
    AdminRejected,
    publish_index,
    published_index_version,
    rebuild_index,
    rollback_index,
    set_source_enabled,
)
from auth_app import AuthApp
from auth_constants import (
    ERR_FORBIDDEN,
    ERR_JSON,
    ERR_UNAUTHORIZED,
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    PATH_ADMIN_INDEX_PUBLISH,
    PATH_ADMIN_INDEX_REBUILD,
    PATH_ADMIN_INDEX_ROLLBACK,
    PATH_ADMIN_REVIEW_QUEUE,
    PATH_ADMIN_SOURCE_DISABLE,
    PATH_ADMIN_SOURCE_ENABLE,
    ROLE_ADMIN,
)
from auth_http import Incoming, Outgoing, csrf_ok, json_error, json_response
from auth_session import Visitor, now_seconds, session_visitor

_ADMIN_PATHS = frozenset({
    PATH_ADMIN_SOURCE_DISABLE,
    PATH_ADMIN_SOURCE_ENABLE,
    PATH_ADMIN_INDEX_REBUILD,
    PATH_ADMIN_INDEX_PUBLISH,
    PATH_ADMIN_INDEX_ROLLBACK,
    PATH_ADMIN_REVIEW_QUEUE,
})
_TEXT = "utf-8"


def handle_admin(app: AuthApp, request: Incoming) -> Outgoing | None:
    """停用、重建、发布、回退和审核队列。别的路径返回 None。"""
    if request.path not in _ADMIN_PATHS:
        return None
    visitor = _require_admin(app, request)
    if isinstance(visitor, Outgoing):
        return visitor
    if request.path == PATH_ADMIN_REVIEW_QUEUE:
        return _queue(request, app)
    return _write(app, request, visitor.user_id)


def _require_admin(app: AuthApp, request: Incoming) -> Visitor | Outgoing:
    visitor = session_visitor(app, request, now_seconds())
    if visitor is None:
        return json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
    if visitor.role != ROLE_ADMIN:
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    return visitor


def _queue(request: Incoming, app: AuthApp) -> Outgoing | None:
    if request.method != "GET":
        return None
    return json_response(HTTP_OK, {"items": list_review_queue(app.db)})


def _write(app: AuthApp, request: Incoming, actor_id: str) -> Outgoing | None:
    if request.method != "POST":
        return None
    if not csrf_ok(request):
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    try:
        return _action(app, request, actor_id)
    except AdminRejected as exc:
        return json_error(HTTP_UNPROCESSABLE, str(exc))


def _action(app: AuthApp, request: Incoming, actor_id: str) -> Outgoing | None:
    now = _moment()
    path = request.path
    if path == PATH_ADMIN_SOURCE_DISABLE:
        return _source(app, request, actor_id, False, now)
    if path == PATH_ADMIN_SOURCE_ENABLE:
        return _source(app, request, actor_id, True, now)
    if path == PATH_ADMIN_INDEX_REBUILD:
        return _rebuild(app, actor_id, now)
    if path == PATH_ADMIN_INDEX_PUBLISH:
        return _publish(app, request, actor_id, now)
    if path == PATH_ADMIN_INDEX_ROLLBACK:
        return _rollback(app, actor_id, now)
    return None


def _source(
    app: AuthApp,
    request: Incoming,
    actor_id: str,
    enabled: bool,
    now: datetime,
) -> Outgoing:
    source_id = _source_id(request)
    if isinstance(source_id, Outgoing):
        return source_id
    set_source_enabled(app.db, actor_id, source_id, enabled, now)
    return json_response(HTTP_OK, {"source_id": source_id, "enabled": enabled})


def _rebuild(app: AuthApp, actor_id: str, now: datetime) -> Outgoing:
    version = rebuild_index(app.db, actor_id, app.index_builder, now)
    return json_response(HTTP_OK, _published_body(app, version))


def _publish(
    app: AuthApp,
    request: Incoming,
    actor_id: str,
    now: datetime,
) -> Outgoing:
    version = _requested_version(request)
    if isinstance(version, Outgoing):
        return version
    publish_index(app.db, actor_id, version, now)
    return json_response(HTTP_OK, _published_body(app, None))


def _rollback(app: AuthApp, actor_id: str, now: datetime) -> Outgoing:
    rollback_index(app.db, actor_id, now)
    return json_response(HTTP_OK, _published_body(app, None))


def _published_body(app: AuthApp, built: str | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "published_index_version": published_index_version(app.db),
    }
    if built is not None:
        payload["index_version"] = built
    return payload


def _source_id(request: Incoming) -> str | Outgoing:
    payload = _object(request.body)
    if payload is None:
        return json_error(HTTP_BAD_REQUEST, ERR_JSON)
    source_id = payload.get("source_id")
    if not isinstance(source_id, str):
        return json_error(HTTP_BAD_REQUEST, ERR_JSON)
    return source_id


def _requested_version(request: Incoming) -> str | Outgoing:
    payload = _object(request.body)
    if payload is None:
        return json_error(HTTP_BAD_REQUEST, ERR_JSON)
    version = payload.get("index_version")
    if not isinstance(version, str):
        return json_error(HTTP_BAD_REQUEST, ERR_JSON)
    return version


def _object(body: bytes) -> dict[str, object] | None:
    if body.strip() == b"":
        return {}
    try:
        payload = json.loads(body.decode(_TEXT))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _moment() -> datetime:
    return datetime.fromtimestamp(now_seconds(), timezone.utc)
