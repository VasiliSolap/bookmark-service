from concurrent.futures import ThreadPoolExecutor

import pytest

URL = "https://example.com/article"


def create(client, body, **kw):
    return client.post("/bookmarks", json=body, **kw)


# ---------- happy path ----------

def test_create_get_list_delete(client):
    r = create(client, {"url": URL, "title": "An article"})
    assert r.status_code == 201
    b = r.json()
    assert b["url"] == URL and b["title"] == "An article"
    assert r.headers["location"] == f"/bookmarks/{b['id']}"

    assert client.get(f"/bookmarks/{b['id']}").json() == b
    assert client.get("/bookmarks").json() == {"items": [b], "next_cursor": None}

    assert client.delete(f"/bookmarks/{b['id']}").status_code == 204
    assert client.get(f"/bookmarks/{b['id']}").status_code == 404
    assert client.delete(f"/bookmarks/{b['id']}").status_code == 404


def test_list_is_newest_first_and_paginates(client):
    ids = [create(client, {"url": f"https://example.com/{i}"}).json()["id"] for i in range(5)]
    page1 = client.get("/bookmarks", params={"limit": 2}).json()
    assert [b["id"] for b in page1["items"]] == ids[::-1][:2]
    page2 = client.get("/bookmarks", params={"limit": 2, "cursor": page1["next_cursor"]}).json()
    assert [b["id"] for b in page2["items"]] == ids[::-1][2:4]


def test_users_only_see_their_own(client):
    b = create(client, {"url": URL}).json()
    bob = {"X-User-Id": "bob"}
    assert client.get("/bookmarks", headers=bob).json()["items"] == []
    assert client.get(f"/bookmarks/{b['id']}", headers=bob).status_code == 404
    assert client.delete(f"/bookmarks/{b['id']}", headers=bob).status_code == 404
    assert client.get(f"/bookmarks/{b['id']}").status_code == 200


# ---------- bad input: always 400 naming the field ----------

@pytest.mark.parametrize("body, message", [
    ({"url": ""}, "url must not be empty"),
    ({"url": "   "}, "url must not be empty"),
    ({"url": 42}, "url must be a string"),
    ({"url": None}, "url must be a string"),
    ({"url": ["https://a.com"]}, "url must be a string"),
    ({"url": {"href": "x"}}, "url must be a string"),
    ({"url": True}, "url must be a string"),
    ({}, "url is required"),
    ({"title": "no url"}, "url is required"),
    ({"url": "https://example.com/" + "a" * 2048}, "url must be at most 2048 characters"),
    ({"url": "example.com"}, "url must start with http:// or https://"),
    ({"url": "ftp://example.com"}, "url must start with http:// or https://"),
    ({"url": "javascript:alert(1)"}, "url must start with http:// or https://"),
    ({"url": "https://"}, "url must include a host, e.g. https://example.com"),
    ({"url": "https://exa mple.com"}, "url must not contain spaces or control characters"),
    ({"url": "https://example.com/\x00"}, "url must not contain spaces or control characters"),
    ({"url": "https://example.com:99999/"}, "url is not a valid URL"),
])
def test_bad_url_is_400_naming_url(client, row_count, body, message):
    r = create(client, body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["field"] == "url"
    assert r.json()["error"]["message"] == message
    assert row_count() == 0


@pytest.mark.parametrize("body, message", [
    ({"url": URL, "title": 5}, "title must be a string"),
    ({"url": URL, "title": "t" * 201}, "title must be at most 200 characters"),
    ({"url": URL, "title": "a\x00b"}, "title must not contain control characters"),
])
def test_bad_title_is_400_naming_title(client, row_count, body, message):
    r = create(client, body)
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "validation_error", "field": "title", "message": message}
    assert row_count() == 0


def test_unknown_field_is_named(client):
    r = create(client, {"ulr": URL})
    assert r.status_code == 400
    assert {"field": "ulr", "message": "ulr is not an allowed field"} in r.json()["error"]["details"]


@pytest.mark.parametrize("raw, headers", [
    (b"{not json", {"Content-Type": "application/json"}),
    (b"", {"Content-Type": "application/json"}),
    (b"\xff\xfe\x00", {"Content-Type": "application/json"}),
    (b"[1, 2]", {"Content-Type": "application/json"}),
    (b'"https://example.com"', {"Content-Type": "application/json"}),
    (b"null", {"Content-Type": "application/json"}),
    (b"url=https://example.com", {"Content-Type": "application/x-www-form-urlencoded"}),
    (b"https://example.com", {"Content-Type": "text/plain"}),
    (b"https://example.com", {}),
])
def test_malformed_body_is_400_naming_body(client, row_count, raw, headers):
    r = client.post("/bookmarks", content=raw, headers=headers)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["field"] == "body"
    assert row_count() == 0


@pytest.mark.parametrize("path, field", [
    ("/bookmarks/abc", "id"),
    ("/bookmarks/0", "id"),
    ("/bookmarks/-1", "id"),
    ("/bookmarks/1.5", "id"),
    ("/bookmarks/99999999999999999999999", "id"),
    ("/bookmarks?limit=0", "limit"),
    ("/bookmarks?limit=1000", "limit"),
    ("/bookmarks?limit=ten", "limit"),
    ("/bookmarks?cursor=abc", "cursor"),
    ("/bookmarks?cursor=99999999999999999999999", "cursor"),
])
def test_bad_path_and_query_are_400(client, path, field):
    r = client.get(path)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["field"] == field


def test_missing_user_is_401(client):
    r = client.post("/bookmarks", json={"url": URL}, headers={"X-User-Id": ""})
    assert r.status_code == 401
    assert r.json()["error"]["field"] == "X-User-Id"


def test_malformed_user_is_400(client):
    r = client.get("/bookmarks", headers={"X-User-Id": "a" * 65})
    assert r.status_code == 400
    assert r.json()["error"]["field"] == "X-User-Id"


def test_unknown_route_and_method_use_the_same_error_shape(client):
    assert client.get("/nope").json()["error"]["code"] == "not_found"
    r = client.put("/bookmarks")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


# ---------- idempotent create ----------

def test_same_create_twice_leaves_one_row(client, row_count):
    first = create(client, {"url": URL, "title": "x"})
    second = create(client, {"url": URL, "title": "x"})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json() == second.json()
    assert row_count() == 1


@pytest.mark.parametrize("variant", [
    "HTTPS://EXAMPLE.COM/article",
    "https://example.com:443/article",
    "https://example.com/article#comments",
    "  https://example.com/article  ",
])
def test_equivalent_urls_are_the_same_bookmark(client, row_count, variant):
    first = create(client, {"url": URL}).json()
    again = create(client, {"url": variant})
    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    assert row_count() == 1


def test_different_path_case_is_a_different_bookmark(client, row_count):
    create(client, {"url": "https://example.com/Article"})
    create(client, {"url": "https://example.com/article"})
    assert row_count() == 2


def test_same_url_for_two_users_is_two_rows(client, row_count):
    create(client, {"url": URL})
    create(client, {"url": URL}, headers={"X-User-Id": "bob"})
    assert row_count() == 2


def test_concurrent_repeats_leave_one_row(client, row_count):
    with ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(lambda _: create(client, {"url": URL}).status_code, range(20)))
    assert sorted(set(codes)) == [200, 201]
    assert codes.count(201) == 1
    assert row_count() == 1


# ---------- fuzz: nothing a caller sends may produce a 500 ----------

def test_random_input_never_500(client):
    import json
    import random
    from urllib.parse import quote

    rng = random.Random(1234)
    atoms = [None, True, 0, -1, 1.5, 10**30, "", " ", "\x00", "https://", "http://a",
             "https://example.com", "x" * 5000, "https://ex.com/" + "é" * 3000, [], {}, [1], {"a": 1}]

    def value(depth=0):
        if depth < 2 and rng.random() < 0.2:
            return {rng.choice(["url", "title", "id", ""]): value(depth + 1)}
        return rng.choice(atoms)

    for _ in range(300):
        body = rng.choice([value(), {"url": value(), "title": value()}, {"url": value()}])
        r = client.post("/bookmarks", content=json.dumps(body), headers={"Content-Type": "application/json"})
        assert r.status_code in (200, 201, 400), (body, r.status_code, r.text)
        path = rng.choice(["/bookmarks/", "/bookmarks?limit=", "/bookmarks?cursor="]) + quote(str(value()), safe="")
        r = client.get(path)
        assert r.status_code < 500, (path, r.text)


# ---------- search ----------

def test_search_matches_title_or_url_case_insensitively(client):
    a = create(client, {"url": "https://docs.python.org/3/", "title": "Python docs"}).json()
    b = create(client, {"url": "https://fastapi.tiangolo.com/", "title": "FastAPI"}).json()
    create(client, {"url": "https://example.com/", "title": "Something else"})

    assert [x["id"] for x in client.get("/bookmarks", params={"q": "PYTHON"}).json()["items"]] == [a["id"]]
    assert [x["id"] for x in client.get("/bookmarks", params={"q": "tiangolo"}).json()["items"]] == [b["id"]]
    assert client.get("/bookmarks", params={"q": "nothing like this"}).json()["items"] == []


def test_search_treats_wildcards_literally(client):
    create(client, {"url": "https://a.com/1", "title": "100% free"})
    create(client, {"url": "https://a.com/2", "title": "1000 things"})
    create(client, {"url": "https://a.com/3", "title": "snake_case"})
    create(client, {"url": "https://a.com/4", "title": "snakeXcase"})

    assert [x["title"] for x in client.get("/bookmarks", params={"q": "100%"}).json()["items"]] == ["100% free"]
    assert [x["title"] for x in client.get("/bookmarks", params={"q": "e_c"}).json()["items"]] == ["snake_case"]


def test_search_only_sees_own_bookmarks(client):
    create(client, {"url": "https://a.com/", "title": "shared word"})
    r = client.get("/bookmarks", params={"q": "shared"}, headers={"X-User-Id": "bob"})
    assert r.json()["items"] == []


def test_search_paginates(client):
    for i in range(5):
        create(client, {"url": f"https://a.com/{i}", "title": f"match {i}"})
    create(client, {"url": "https://b.com/", "title": "other"})
    page1 = client.get("/bookmarks", params={"q": "match", "limit": 3}).json()
    page2 = client.get("/bookmarks", params={"q": "match", "limit": 3, "cursor": page1["next_cursor"]}).json()
    titles = [x["title"] for x in page1["items"] + page2["items"]]
    assert titles == [f"match {i}" for i in range(4, -1, -1)]


@pytest.mark.parametrize("q, message", [
    ("", "q must not be empty"),
    ("   ", "q must not be empty"),
    ("x" * 201, "q must be at most 200 characters"),
    ("a\x01b", "q must not contain control characters"),
])
def test_bad_search_is_400_naming_q(client, q, message):
    r = client.get("/bookmarks", params={"q": q})
    assert r.status_code == 400
    assert r.json()["error"] == {"code": "validation_error", "field": "q", "message": message}


# ---------- editing the title ----------

def test_patch_changes_only_the_title(client):
    b = create(client, {"url": URL, "title": "old"}).json()
    r = client.patch(f"/bookmarks/{b['id']}", json={"title": "  new  "})
    assert r.status_code == 200
    assert r.json() == {**b, "title": "new"}
    assert client.get(f"/bookmarks/{b['id']}").json()["title"] == "new"


def test_patch_null_or_blank_clears_the_title(client):
    b = create(client, {"url": URL, "title": "old"}).json()
    assert client.patch(f"/bookmarks/{b['id']}", json={"title": None}).json()["title"] is None
    client.patch(f"/bookmarks/{b['id']}", json={"title": "x"})
    assert client.patch(f"/bookmarks/{b['id']}", json={"title": "   "}).json()["title"] is None


def test_patch_is_idempotent(client, row_count):
    b = create(client, {"url": URL}).json()
    first = client.patch(f"/bookmarks/{b['id']}", json={"title": "same"}).json()
    second = client.patch(f"/bookmarks/{b['id']}", json={"title": "same"}).json()
    assert first == second
    assert row_count() == 1


def test_patch_someone_elses_bookmark_is_404(client):
    b = create(client, {"url": URL, "title": "mine"}).json()
    r = client.patch(f"/bookmarks/{b['id']}", json={"title": "hacked"}, headers={"X-User-Id": "bob"})
    assert r.status_code == 404
    assert client.get(f"/bookmarks/{b['id']}").json()["title"] == "mine"


def test_patch_missing_bookmark_is_404(client):
    assert client.patch("/bookmarks/999", json={"title": "x"}).status_code == 404


@pytest.mark.parametrize("body, field, message", [
    ({}, "title", "title is required"),
    ({"title": 5}, "title", "title must be a string"),
    ({"title": "t" * 201}, "title", "title must be at most 200 characters"),
    ({"title": "x", "url": "https://other.com"}, "url", "url is not an allowed field"),
])
def test_bad_patch_is_400(client, body, field, message):
    b = create(client, {"url": URL, "title": "keep"}).json()
    r = client.patch(f"/bookmarks/{b['id']}", json=body)
    assert r.status_code == 400
    assert r.json()["error"]["field"] == field
    assert r.json()["error"]["message"] == message
    assert client.get(f"/bookmarks/{b['id']}").json()["title"] == "keep"


# ---------- request ids ----------

def test_every_response_has_a_request_id(client):
    ids = {client.get("/bookmarks").headers["x-request-id"] for _ in range(3)}
    assert len(ids) == 3  # a fresh id per request
    assert client.get("/nope").headers["x-request-id"]                        # 404
    assert client.post("/bookmarks", json={"url": ""}).headers["x-request-id"]  # 400


def test_a_safe_incoming_request_id_is_reused(client):
    r = client.get("/bookmarks", headers={"X-Request-ID": "trace-abc_123.x"})
    assert r.headers["x-request-id"] == "trace-abc_123.x"


@pytest.mark.parametrize("bad", ["has space", "a" * 65, "semi;colon", "new\\nline"])
def test_an_unsafe_incoming_request_id_is_replaced(client, bad):
    r = client.get("/bookmarks", headers={"X-Request-ID": bad})
    assert r.headers["x-request-id"] != bad
    assert len(r.headers["x-request-id"]) == 32


def test_a_500_points_to_its_log_line(client, monkeypatch, caplog):
    import logging

    from app import db

    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(db, "get_for_owner", boom)
    with caplog.at_level(logging.INFO):
        r = client.get("/bookmarks/1", headers={"X-Request-ID": "find-me-42"})

    assert r.status_code == 500
    assert r.headers["x-request-id"] == "find-me-42"
    assert "find-me-42" in r.json()["error"]["message"]
    assert "database exploded" not in r.text  # details stay in the log
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "unhandled error request_id=find-me-42" in logged
    assert "request_id=find-me-42 method=GET path=/bookmarks/1 status=500" in logged


def test_access_log_line_per_request(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="bookmarks.access"):
        client.post("/bookmarks", json={"url": URL}, headers={"X-Request-ID": "log-me"})
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "bookmarks.access"]
    assert len(lines) == 1
    assert lines[0].startswith("request_id=log-me method=POST path=/bookmarks status=201 duration_ms=")
