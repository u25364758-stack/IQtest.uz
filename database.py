"""SQLite persistence for profiles, test sessions and question history."""
import json
import random
import sqlite3
import time
from contextlib import contextmanager

from config import DATABASE_PATH


@contextmanager
def connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATABASE_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with connection() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            finished_at INTEGER,
            duration INTEGER,
            correct INTEGER,
            iq_score INTEGER,
            question_ids TEXT NOT NULL,
            answers TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        );
        CREATE TABLE IF NOT EXISTS used_questions (
            telegram_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            used_at INTEGER NOT NULL,
            PRIMARY KEY (telegram_id, question_id)
        );
        CREATE INDEX IF NOT EXISTS attempts_user_idx ON attempts(telegram_id, started_at);
        """)
        # Migrate the earlier MVP schema without dropping user data.
        cols = {row[1] for row in con.execute("PRAGMA table_info(users)")}
        if "updated_at" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN updated_at INTEGER")
            con.execute("UPDATE users SET updated_at = COALESCE(created_at, ?) ", (int(time.time()),))
        cols = {row[1] for row in con.execute("PRAGMA table_info(attempts)")}
        if "answers" not in cols:
            con.execute("ALTER TABLE attempts ADD COLUMN answers TEXT")
        cols = {row[1] for row in con.execute("PRAGMA table_info(used_questions)")}
        if "used_at" not in cols:
            con.execute("ALTER TABLE used_questions ADD COLUMN used_at INTEGER NOT NULL DEFAULT 0")


def save_profile(telegram_id, full_name, age, gender):
    now = int(time.time())
    with connection() as con:
        con.execute("""INSERT INTO users(telegram_id,full_name,age,gender,created_at,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
            full_name=excluded.full_name, age=excluded.age, gender=excluded.gender,
            updated_at=excluded.updated_at""", (telegram_id, full_name, age, gender, now, now))


def reserve_questions(telegram_id, questions):
    """Atomically choose and reserve 25 questions, preventing concurrent duplicates."""
    now = int(time.time())
    with connection() as con:
        con.execute("BEGIN IMMEDIATE")
        used = {r[0] for r in con.execute(
            "SELECT question_id FROM used_questions WHERE telegram_id=?", (telegram_id,))}
        available = [q for q in questions if q["id"] not in used]
        if not available:
            con.execute("DELETE FROM used_questions WHERE telegram_id=?", (telegram_id,))
            available = questions
        if len(available) < 25:
            return None
        selected = random.sample(available, 25)
        question_ids = [q["id"] for q in selected]
        cur = con.execute("INSERT INTO attempts(telegram_id,started_at,question_ids) VALUES(?,?,?)",
                           (telegram_id, now, json.dumps(question_ids)))
        con.executemany("INSERT INTO used_questions(telegram_id,question_id,used_at) VALUES(?,?,?)",
                        [(telegram_id, qid, now) for qid in question_ids])
        return cur.lastrowid, now, selected


def get_attempt(attempt_id, telegram_id):
    with connection() as con:
        return con.execute("SELECT * FROM attempts WHERE id=? AND telegram_id=?", (attempt_id, telegram_id)).fetchone()


def finish_attempt(attempt_id, telegram_id, answers, correct, iq_score):
    now = int(time.time())
    with connection() as con:
        row = con.execute("SELECT started_at FROM attempts WHERE id=? AND telegram_id=? AND finished_at IS NULL",
                          (attempt_id, telegram_id)).fetchone()
        if row is None:
            return None
        duration = max(0, now - row["started_at"])
        con.execute("UPDATE attempts SET finished_at=?,duration=?,correct=?,iq_score=?,answers=? WHERE id=?",
                    (now, duration, correct, iq_score, json.dumps(answers), attempt_id))
        return duration
