import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "interview.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    domain      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id  INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    question      TEXT NOT NULL,
    ideal_answer  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id     INTEGER NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    token            TEXT NOT NULL UNIQUE,
    access_code      TEXT NOT NULL,
    candidate_name   TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'English',
    status           TEXT NOT NULL DEFAULT 'pending',
    current_index    INTEGER NOT NULL DEFAULT 0,
    follow_up_count  INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    started_at       TEXT,
    finished_at      TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_id   INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
    q_index     INTEGER NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    reply       TEXT NOT NULL,
    score       INTEGER NOT NULL,
    decision    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(seed_file: str = "interview_config.json"):
    with get_db() as conn:
        conn.executescript(SCHEMA)
        empty = conn.execute("SELECT COUNT(*) FROM interviews").fetchone()[0] == 0
        if empty and Path(seed_file).exists():
            data = json.loads(Path(seed_file).read_text(encoding="utf-8"))
            create_interview(conn, data.get("domain", "General"), data.get("domain", "General"),
                             data.get("questions", []))


def create_interview(conn, title: str, domain: str, questions: list) -> int:
    cur = conn.execute("INSERT INTO interviews (title, domain) VALUES (?, ?)", (title, domain))
    iid = cur.lastrowid
    for pos, q in enumerate(questions):
        conn.execute(
            "INSERT INTO questions (interview_id, position, question, ideal_answer) VALUES (?,?,?,?)",
            (iid, pos, q["question"].strip(), q["ideal_answer"].strip()),
        )
    return iid


def get_questions(conn, interview_id: int) -> list:
    return conn.execute(
        "SELECT * FROM questions WHERE interview_id = ? ORDER BY position", (interview_id,)
    ).fetchall()


def question_scores(conn, invite_id: int) -> dict:
    latest = {}
    for t in conn.execute("SELECT * FROM turns WHERE invite_id = ? ORDER BY id", (invite_id,)):
        latest[t["q_index"]] = t
    return latest


def average_score(conn, invite_id: int):
    scores = [t["score"] for t in question_scores(conn, invite_id).values()]
    return round(sum(scores) / len(scores), 1) if scores else None
