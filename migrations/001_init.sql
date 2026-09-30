-- Bookmarks table. Safe to run on every start.
CREATE TABLE IF NOT EXISTS bookmarks (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id       TEXT        NOT NULL,
    url            TEXT        NOT NULL,
    url_normalized TEXT        NOT NULL,
    title          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The rule that makes "create" idempotent: one row per owner per URL.
    -- Enforced by the database, so two concurrent requests cannot both insert.
    CONSTRAINT bookmarks_owner_url_unique UNIQUE (owner_id, url_normalized),
    CONSTRAINT bookmarks_url_length CHECK (char_length(url) BETWEEN 1 AND 2048),
    CONSTRAINT bookmarks_title_length CHECK (title IS NULL OR char_length(title) <= 200)
);

-- Serves "list my bookmarks, newest first".
CREATE INDEX IF NOT EXISTS bookmarks_owner_id_id_desc ON bookmarks (owner_id, id DESC);
