"""Request IDs: one id per request, returned in X-Request-ID and written to every log line.

When a caller reports "I got a 500", the id in the response is enough to find the exact
request in the logs. A caller (or a proxy in front of the service) may send its own
X-Request-ID; it is reused if it looks safe, otherwise a new one is generated.
"""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request

HEADER = "X-Request-ID"
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

access_log = logging.getLogger("bookmarks.access")


def new_id() -> str:
    return uuid.uuid4().hex


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get(HEADER, "")
        # Never trust arbitrary text into logs: an unsafe value is replaced, not echoed.
        request_id = incoming if SAFE_ID.fullmatch(incoming) else new_id()
        request.state.request_id = request_id

        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[HEADER] = request_id
            return response
        finally:
            access_log.info(
                "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id, request.method, request.url.path, status,
                (time.perf_counter() - started) * 1000,
            )
