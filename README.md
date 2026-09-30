# Bookmark service

A small HTTP API for saving bookmarks, built with FastAPI and PostgreSQL.
The happy path is simple on purpose; the work is in what happens with bad input and repeated requests.

## Run it

With Docker:

```bash
docker compose up --build
# API on http://localhost:8000, interactive docs on http://localhost:8000/docs
```

Without Docker (needs a running PostgreSQL):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://app:app@localhost:5432/bookmarks
uvicorn app.main:app --reload
```

The table is created on startup from `migrations/001_init.sql`.

## Tests

```bash
export TEST_DATABASE_URL=postgresql://app:app@localhost:5432/bookmarks_test
pytest -q
```

72 tests run against a real PostgreSQL (also in GitHub Actions on every push). They cover every
malformed input listed below, repeated and concurrent creates, access to another user's rows, and a
fuzz test that sends 300 random bodies and paths and fails on any 5xx.

## Who is calling

Every request must carry an `X-User-Id` header (1–64 characters: letters, digits, `-`, `_`).
It is a stand-in for real authentication, which is out of scope for this task. It exists so
"list **their own**" means something: each caller only ever sees, fetches and deletes their own rows.

## Endpoints

| Method | Path | Success | Errors |
|---|---|---|---|
| `POST` | `/bookmarks` | **201** created, or **200** if the caller already saved this URL (the existing bookmark is returned) | 400, 401 |
| `GET` | `/bookmarks?limit=50&cursor=<id>&q=<text>` | **200** `{"items": [...], "next_cursor": id or null}`, newest first | 400, 401 |
| `GET` | `/bookmarks/{id}` | **200** the bookmark | 400, 401, 404 |
| `PATCH` | `/bookmarks/{id}` `{"title": "..."}` | **200** the updated bookmark; `{"title": null}` clears it | 400, 401, 404 |
| `DELETE` | `/bookmarks/{id}` | **204** no body | 400, 401, 404 |
| `GET` | `/health` | **200** `{"status": "ok"}` (checks the database) | — |

Request body for `POST /bookmarks`:

```json
{ "url": "https://example.com/article", "title": "optional, up to 200 characters" }
```

`limit` is 1–100 (default 50). `cursor` is the `next_cursor` from the previous page.
`q` (optional, 1–200 characters) keeps only bookmarks whose title or URL contains the text,
ignoring case. `%` and `_` in `q` are matched literally, not as SQL wildcards, so searching for
`100%` does not match `1000`. Search and pagination combine: pass the same `q` with each `cursor`.
Cursor pagination is used instead of offsets so a bookmark added while you page does not shift or repeat entries.

`PATCH` changes only the title. The URL cannot be edited because it is the bookmark's identity
(see [How repeats are recognised](#how-repeats-are-recognised)): changing it could silently turn
one bookmark into a duplicate of another. To bookmark a different URL, create a new one. Sending
the same `PATCH` twice leaves the same result.

A bookmark owned by someone else returns **404**, the same as one that does not exist,
so the API does not reveal which ids are taken.

## Errors

Every error, from any endpoint, has the same shape:

```json
{ "error": { "code": "validation_error", "field": "url", "message": "url must not be empty" } }
```

| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | Anything the caller sent is wrong. `field` names what: `url`, `title`, `body`, `id`, `limit`, `cursor`, `q` or `X-User-Id` |
| 401 | `unauthenticated` | `X-User-Id` is missing |
| 404 | `not_found` | No such bookmark for this caller, or no such route |
| 405 | `method_not_allowed` | e.g. `PUT /bookmarks` |
| 500 | `internal_error` | A bug on our side. The caller gets a reference id; details go to the server log only |

What the `url` field rejects, each with its own message:

| Input | Message |
|---|---|
| missing | `url is required` |
| `""` or only spaces | `url must not be empty` |
| a number, boolean, `null`, list or object | `url must be a string` |
| longer than 2048 characters | `url must be at most 2048 characters` |
| `example.com`, `ftp://…`, `javascript:…` | `url must start with http:// or https://` |
| `https://` | `url must include a host, e.g. https://example.com` |
| spaces, NUL or other control characters | `url must not contain spaces or control characters` |
| `https://example.com:99999/` | `url is not a valid URL` |

A body that is not JSON, is not a JSON object, or cannot be decoded is a 400 with `field: "body"`.
Unknown fields (a typo like `"ulr"`) are rejected rather than silently ignored.

**Why no input can cause a 500:** everything is validated before it reaches the database.
Strings are type-checked strictly (no silent `42` → `"42"`), lengths are capped below the column
constraints, NUL bytes (which PostgreSQL rejects) are refused up front, and path/query integers are
bounded to the `BIGINT` range. All SQL is parameterised.

## How repeats are recognised

**Rule: one bookmark per caller per normalised URL.** Sending the same create request twice
returns the first bookmark with **200** instead of creating a second row.

Before comparing, the URL is normalised:

- scheme and host are lower-cased — they are case-insensitive by definition
- the default port is dropped — `https://a.com:443/x` is `https://a.com/x`
- an empty path becomes `/` — `https://a.com` is `https://a.com/`
- the `#fragment` is dropped — browsers never send it to the server, so it is the same page
- surrounding whitespace is trimmed

Path and query are kept exactly as sent, because on most servers `/Article` and `/article` are
different pages and `?page=2` is different content. Merging them would lose bookmarks the user meant
to keep. The original URL is stored and returned; the normalised one is used only for matching.

**Why the URL and not an `Idempotency-Key` header.** An idempotency key protects against a
*retry of the same request*. But the realistic duplicate here is a user saving the same page twice,
from two tabs or a day apart, and those are different requests with different keys. Using the URL as
the identity covers both: a network retry and a genuine second save both land on the same row.
The trade-off is that a user cannot keep two bookmarks for one URL with different titles; for a
bookmark list that is the behaviour people expect.

**Why the database enforces it.** The rule is a `UNIQUE (owner_id, url_normalized)` constraint,
and the insert is `INSERT … ON CONFLICT DO NOTHING RETURNING …`, followed by a lookup when nothing
was inserted. A "check first, then insert" in application code would let two simultaneous requests
both pass the check. With the constraint, the database picks exactly one winner; the test
`test_concurrent_repeats_leave_one_row` fires 20 parallel creates and asserts one row, one 201.

If the repeat carries a different `title`, the stored bookmark is returned unchanged:
a create is not an update. Use `PATCH` to change the title.

## Layout

```
app/main.py        routes and the X-User-Id check
app/schemas.py     request/response models and field validation
app/urls.py        URL validation and normalisation
app/errors.py      one error shape for 400/401/404/405/500
app/db.py          SQL (psycopg 3, connection pool)
migrations/        schema
tests/             pytest suite against PostgreSQL
```
