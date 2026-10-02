def tamper(conn, sql, params=()):
    """Test setup only: run an UPDATE on an audit table, bypassing the immutability triggers.

    Several tests need a corrupt row (a garbage timestamp, hostile text) to prove a page copes.
    The app itself can never do this; the triggers are restored before returning, so the rest of
    the test still runs against the real schema.
    """
    triggers = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%\\_no\\_update' ESCAPE '\\'"
    ).fetchall()
    for t in triggers:
        conn.execute("DROP TRIGGER " + t["name"])
    try:
        return conn.execute(sql, params)
    finally:
        for t in triggers:
            conn.execute(t["sql"])
