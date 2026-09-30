import os

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://app@localhost:5432/bookmarks_test")


@pytest.fixture(scope="session")
def pool():
    p = db.create_pool(TEST_DB)
    db.migrate(p)
    yield p
    p.close()


@pytest.fixture()
def client(pool):
    with pool.connection() as conn:
        conn.execute("TRUNCATE bookmarks RESTART IDENTITY")
    app.state.pool = pool
    # raise_server_exceptions=False: a 500 shows up as a response the tests can assert on
    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers.update({"X-User-Id": "alice"})
        yield c
    app.state.pool = None


@pytest.fixture()
def row_count(pool):
    def count() -> int:
        with pool.connection() as conn:
            return conn.execute("SELECT count(*) AS n FROM bookmarks").fetchone()["n"]
    return count
