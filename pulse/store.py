"""수집한 글을 SQLite에 저장한다 (작성자 정보 없음)."""
import json
import os
import sqlite3

DB = os.environ.get("PULSE_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "pulse.db"))


def _conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS posts (id TEXT PRIMARY KEY, source TEXT, title TEXT, text TEXT, url TEXT,
                 created TEXT, views INT, recommends INT, comments INT, vote TEXT)""")
    return c


def save(posts):
    with _conn() as c:
        for p in posts:
            # 같은 글은 조회·댓글 수만 최신으로 갱신
            c.execute("""INSERT INTO posts VALUES (:id,:source,:title,:text,:url,:created,:views,:recommends,:comments,:vote)
                         ON CONFLICT(id) DO UPDATE SET views=excluded.views, recommends=excluded.recommends, comments=excluded.comments""", p)
    return len(posts)


def between(start, end):
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM posts WHERE created >= ? AND created < ? ORDER BY created DESC",
                         (start.isoformat(), end.isoformat())).fetchall()
    return [dict(r) for r in rows]


def prune(before):
    with _conn() as c:
        c.execute("DELETE FROM posts WHERE created < ?", (before.isoformat(),))


def oldest():
    with _conn() as c:
        r = c.execute("SELECT MIN(created) FROM posts").fetchone()
    return r[0] if r else None


def newest(source_prefix="dc-"):
    with _conn() as c:
        r = c.execute("SELECT MAX(created) FROM posts WHERE id LIKE ?", (source_prefix + "%",)).fetchone()
    return r[0] if r else None


def get_many(ids):
    if not ids:
        return []
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = {r["id"]: dict(r) for r in c.execute(f"SELECT * FROM posts WHERE id IN ({','.join('?' * len(ids))})", list(ids))}
    return [rows[i] for i in ids if i in rows]
