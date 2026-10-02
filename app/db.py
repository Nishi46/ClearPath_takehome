import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = "./clearpath.db"
BUSY_TIMEOUT_MS = 5000


class DatabaseError(RuntimeError):
    """The database could not be opened. The message is safe to log, not to show users."""


def get_db_path():
    # Environment only. Never derive this from request data.
    return os.environ.get("CLEARPATH_DB", DEFAULT_DB_PATH)


def _open():
    path = get_db_path()
    parent = Path(path).resolve().parent
    if not parent.is_dir():
        raise DatabaseError("Database folder does not exist: %s" % parent)
    try:
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    except sqlite3.Error as exc:
        raise DatabaseError("Cannot open database %s: %s" % (path, exc)) from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # keep in sync with BUSY_TIMEOUT_MS
    return conn


@contextmanager
def connect():
    """Yield a connection that commits on success, rolls back on error, and always closes."""
    conn = _open()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def enable_wal():
    """Switch the file to WAL so reads do not block while writing. Call once at init."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")


SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def init_schema():
    """Create any missing tables and indexes. Safe to run on every start; never touches data."""
    try:
        enable_wal()
        with connect() as conn:
            conn.executescript(SCHEMA_PATH.read_text())
    except sqlite3.Error as exc:
        raise DatabaseError("Cannot initialise database %s: %s" % (get_db_path(), exc)) from exc
