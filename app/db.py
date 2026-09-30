import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://app@localhost:5432/bookmarks")


def create_pool(url: str | None = None) -> ConnectionPool:
    pool = ConnectionPool(url or database_url(), min_size=1, max_size=10,
                          kwargs={"row_factory": dict_row}, open=True)
    pool.wait(timeout=10)
    return pool


def migrate(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        for sql_file in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(sql_file.read_text())


COLUMNS = "id, url, title, created_at"


def insert_or_get(pool, owner_id: str, url: str, url_normalized: str, title: str | None):
    """Return (row, created). The unique constraint decides, so concurrent repeats are safe."""
    with pool.connection() as conn:
        row = conn.execute(
            f"""INSERT INTO bookmarks (owner_id, url, url_normalized, title)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (owner_id, url_normalized) DO NOTHING
                RETURNING {COLUMNS}""",
            (owner_id, url, url_normalized, title),
        ).fetchone()
        if row is not None:
            return row, True
        existing = conn.execute(
            f"SELECT {COLUMNS} FROM bookmarks WHERE owner_id = %s AND url_normalized = %s",
            (owner_id, url_normalized),
        ).fetchone()
        return existing, False


def _like_pattern(text: str) -> str:
    """Escape LIKE wildcards so a search for "100%" matches the literal text."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_for_owner(pool, owner_id: str, limit: int, cursor: int | None, q: str | None = None):
    conditions = ["owner_id = %(owner)s"]
    params = {"owner": owner_id, "limit": limit}
    if cursor is not None:
        conditions.append("id < %(cursor)s")
        params["cursor"] = cursor
    if q is not None:
        # Case-insensitive substring match on the title or the URL.
        conditions.append("(title ILIKE %(pattern)s OR url ILIKE %(pattern)s)")
        params["pattern"] = _like_pattern(q)
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT {COLUMNS} FROM bookmarks WHERE {' AND '.join(conditions)} "
            "ORDER BY id DESC LIMIT %(limit)s",
            params,
        ).fetchall()


def get_for_owner(pool, owner_id: str, bookmark_id: int):
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT {COLUMNS} FROM bookmarks WHERE id = %s AND owner_id = %s",
            (bookmark_id, owner_id),
        ).fetchone()


def update_title(pool, owner_id: str, bookmark_id: int, title: str | None):
    with pool.connection() as conn:
        return conn.execute(
            f"""UPDATE bookmarks SET title = %s WHERE id = %s AND owner_id = %s
                RETURNING {COLUMNS}""",
            (title, bookmark_id, owner_id),
        ).fetchone()


def delete_for_owner(pool, owner_id: str, bookmark_id: int) -> bool:
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM bookmarks WHERE id = %s AND owner_id = %s", (bookmark_id, owner_id)
        )
        return cur.rowcount == 1
