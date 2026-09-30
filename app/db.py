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


def list_for_owner(pool, owner_id: str, limit: int, cursor: int | None):
    with pool.connection() as conn:
        if cursor is None:
            return conn.execute(
                f"SELECT {COLUMNS} FROM bookmarks WHERE owner_id = %s ORDER BY id DESC LIMIT %s",
                (owner_id, limit),
            ).fetchall()
        return conn.execute(
            f"""SELECT {COLUMNS} FROM bookmarks
                WHERE owner_id = %s AND id < %s ORDER BY id DESC LIMIT %s""",
            (owner_id, cursor, limit),
        ).fetchall()


def get_for_owner(pool, owner_id: str, bookmark_id: int):
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT {COLUMNS} FROM bookmarks WHERE id = %s AND owner_id = %s",
            (bookmark_id, owner_id),
        ).fetchone()


def delete_for_owner(pool, owner_id: str, bookmark_id: int) -> bool:
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM bookmarks WHERE id = %s AND owner_id = %s", (bookmark_id, owner_id)
        )
        return cur.rowcount == 1
