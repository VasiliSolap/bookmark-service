import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Path, Query, Request, Response, status

from . import db, errors
from .errors import ApiError
from .schemas import Bookmark, BookmarkCreate, BookmarkList
from .urls import normalize_url

MAX_ID = 2**63 - 1  # Postgres BIGINT; larger ids are a 400, not a database error
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    owns_pool = getattr(app.state, "pool", None) is None  # tests inject their own pool
    if owns_pool:
        app.state.pool = db.create_pool()
    db.migrate(app.state.pool)
    yield
    if owns_pool:
        app.state.pool.close()
        app.state.pool = None


app = FastAPI(title="Bookmark service", version="1.0.0", lifespan=lifespan)
errors.install(app)


def current_user(request: Request) -> str:
    """Stand-in for real authentication: the caller names themselves in X-User-Id."""
    user_id = request.headers.get("x-user-id")
    if user_id is None or not user_id.strip():
        raise ApiError(401, "unauthenticated", "X-User-Id header is required", "X-User-Id")
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ApiError(400, "validation_error",
                       "X-User-Id must be 1-64 characters: letters, digits, '-' or '_'", "X-User-Id")
    return user_id


User = Annotated[str, Depends(current_user)]
BookmarkId = Annotated[int, Path(ge=1, le=MAX_ID)]


@app.get("/health")
def health(request: Request):
    with request.app.state.pool.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post(
    "/bookmarks",
    response_model=Bookmark,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Already saved: the existing bookmark is returned"}},
)
def create_bookmark(payload: Annotated[BookmarkCreate, Body()], user: User,
                    request: Request, response: Response):
    row, created = db.insert_or_get(
        request.app.state.pool, user, payload.url, normalize_url(payload.url), payload.title
    )
    response.headers["Location"] = f"/bookmarks/{row['id']}"
    if not created:
        response.status_code = status.HTTP_200_OK
    return row


@app.get("/bookmarks", response_model=BookmarkList)
def list_bookmarks(user: User, request: Request,
                   limit: Annotated[int, Query(ge=1, le=100)] = 50,
                   cursor: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
                   q: Annotated[str | None, Query(max_length=200)] = None):
    if q is not None:
        q = q.strip()
        if not q:
            raise ApiError(400, "validation_error", "q must not be empty", "q")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in q):
            raise ApiError(400, "validation_error", "q must not contain control characters", "q")
    rows = db.list_for_owner(request.app.state.pool, user, limit, cursor, q)
    next_cursor = rows[-1]["id"] if len(rows) == limit else None
    return {"items": rows, "next_cursor": next_cursor}


@app.get("/bookmarks/{bookmark_id}", response_model=Bookmark)
def get_bookmark(bookmark_id: BookmarkId, user: User, request: Request):
    row = db.get_for_owner(request.app.state.pool, user, bookmark_id)
    if row is None:
        # Same answer for "does not exist" and "belongs to someone else".
        raise ApiError(404, "not_found", f"bookmark {bookmark_id} not found", "id")
    return row


@app.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bookmark(bookmark_id: BookmarkId, user: User, request: Request):
    if not db.delete_for_owner(request.app.state.pool, user, bookmark_id):
        raise ApiError(404, "not_found", f"bookmark {bookmark_id} not found", "id")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
