"""列出、读取和删除当前用户的对话。查询密钥不走这里。"""

from __future__ import annotations

from auth_app import AuthApp
from auth_constants import (
    ERR_FORBIDDEN,
    ERR_UNAUTHORIZED,
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
    PATH_CONVERSATIONS,
)
from auth_http import Incoming, Outgoing, csrf_ok, json_error, json_response
from auth_session import now_seconds, session_visitor
from conversation_store import (
    conversation_messages,
    delete_conversation,
    list_conversations,
)


def handle_conversations(app: AuthApp, request: Incoming) -> Outgoing | None:
    """只认网页登录会话。不是这组路径就交给后面的提问。"""
    if request.path == PATH_CONVERSATIONS and request.method == "GET":
        return _list(app, request)
    conversation_id = _item_id(request.path)
    if conversation_id is None:
        return None
    if request.method == "GET":
        return _read(app, request, conversation_id)
    if request.method == "DELETE":
        return _delete(app, request, conversation_id)
    return None


def _list(app: AuthApp, request: Incoming) -> Outgoing:
    visitor = _visitor(app, request)
    if visitor is None:
        return json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
    rows = list_conversations(app.db, visitor)
    return json_response(HTTP_OK, {"conversations": rows})


def _read(app: AuthApp, request: Incoming, conversation_id: str) -> Outgoing:
    visitor = _visitor(app, request)
    if visitor is None:
        return json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
    messages = conversation_messages(app.db, visitor, conversation_id)
    if messages is None:
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    payload = {"conversation_id": conversation_id, "messages": messages}
    return json_response(HTTP_OK, payload)


def _delete(app: AuthApp, request: Incoming, conversation_id: str) -> Outgoing:
    visitor = _visitor(app, request)
    if visitor is None:
        return json_error(HTTP_UNAUTHORIZED, ERR_UNAUTHORIZED)
    if not csrf_ok(request):
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    removed = delete_conversation(app.db, visitor, conversation_id)
    if not removed:
        return json_error(HTTP_FORBIDDEN, ERR_FORBIDDEN)
    return json_response(HTTP_OK, {"deleted": True})


def _visitor(app: AuthApp, request: Incoming) -> str | None:
    visitor = session_visitor(app, request, now_seconds())
    if visitor is None:
        return None
    return visitor.user_id


def _item_id(path: str) -> str | None:
    prefix = PATH_CONVERSATIONS + "/"
    if not path.startswith(prefix):
        return None
    item = path[len(prefix) :]
    if not item or "/" in item:
        return None
    return item
