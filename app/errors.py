"""One error shape for every failure:

    {"error": {"code": "validation_error", "field": "url", "message": "url must not be empty"}}
"""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("bookmarks")

# Path/query/header parameter names as the caller sees them.
PUBLIC_NAMES = {"bookmark_id": "id", "x-user-id": "X-User-Id"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        self.status, self.code, self.message, self.field = status, code, message, field


def error_body(code: str, message: str, field: str | None = None) -> dict:
    body = {"code": code, "message": message}
    if field is not None:
        body["field"] = field
    return {"error": body}


def _field_from_loc(loc: tuple) -> str:
    # ("body", "url") -> "url"; ("body",) or ("body", 17) -> "body"; ("path", "bookmark_id") -> "id"
    names = [str(part) for part in loc[1:] if isinstance(part, str)]
    name = ".".join(names) if names else "body"
    return PUBLIC_NAMES.get(name, name)


def _message(err: dict, field: str) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "json_invalid":
        return "request body is not valid JSON"
    if kind == "missing":
        return "request body is required" if field == "body" else f"{field} is required"
    if kind == "model_attributes_type" or (kind == "dict_type" and field == "body"):
        return "request body must be a JSON object"
    if kind == "string_type":
        return f"{field} must be a string"
    if kind == "string_too_long" and "max_length" in ctx:
        return f"{field} must be at most {ctx['max_length']} characters"
    if kind == "extra_forbidden":
        return f"{field} is not an allowed field"
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return f"{field} must be an integer"
    if kind in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
        return f"{field} is out of range"
    if kind == "value_error" and "error" in ctx:
        return f"{field} {ctx['error']}"
    return f"{field}: {err.get('msg', 'is invalid')}"


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        # Report the first problem; a body that is not JSON at all takes priority.
        first = next((e for e in errors if e.get("type") == "json_invalid"), errors[0])
        field = _field_from_loc(tuple(first.get("loc", ("body",))))
        details = [
            {"field": _field_from_loc(tuple(e.get("loc", ("body",)))),
             "message": _message(e, _field_from_loc(tuple(e.get("loc", ("body",)))))}
            for e in errors
        ]
        body = error_body("validation_error", _message(first, field), field)
        if len(details) > 1:
            body["error"]["details"] = details
        return JSONResponse(status_code=400, content=body)

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, exc.field))

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 400:
            # FastAPI raises this when the body cannot even be decoded (e.g. invalid UTF-8).
            return JSONResponse(status_code=400, content=error_body(
                "validation_error", "request body could not be read as JSON", "body"))
        codes = {404: "not_found", 405: "method_not_allowed"}
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(codes.get(exc.status_code, "http_error"), message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        # Full detail goes to the log; the caller gets a reference, never a stack trace.
        # The reference is the request id, so the caller's error and the log line match.
        ref = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        log.exception("unhandled error request_id=%s %s %s", ref, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", f"something went wrong on our side (ref {ref})"),
            headers={"X-Request-ID": ref},
        )
