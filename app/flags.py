from app.rules import Flag, evaluate

# Database side of the rules engine. app/rules.py stays pure (no database); this module reads a
# version's copy, runs the engine and writes the `flag` rows. Every statement binds its values as
# parameters. None of these functions commit: the caller owns the transaction.


def _check_version_id(version_id):
    if type(version_id) is not int:
        raise TypeError("version_id must be an integer")


def evaluate_version(conn, version_id):
    """Run the engine on a stored version's product, channel and copy. Does not write."""
    _check_version_id(version_id)
    row = conn.execute(
        "SELECT s.product, s.channel, v.copy FROM version v"
        " JOIN submission s ON s.id = v.submission_id WHERE v.id = ?", (version_id,)).fetchone()
    if row is None:
        raise ValueError("No such version")
    return evaluate(row[0], row[1], row[2])


def store_flags(conn, version_id, flags):
    """Make the stored flags of one version equal to `flags`.

    Existing rows for the version are replaced, so calling it twice leaves the same rows and an
    empty list clears stale ones. All or nothing: a failure part-way leaves the old rows.
    """
    _check_version_id(version_id)
    flags = tuple(flags)
    if not all(isinstance(f, Flag) for f in flags):
        raise TypeError("flags must be Flag objects")
    if conn.execute("SELECT 1 FROM version WHERE id = ?", (version_id,)).fetchone() is None:
        raise ValueError("No such version")
    if not conn.in_transaction:
        # An outermost SAVEPOINT would start its own transaction and RELEASE would commit it.
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT store_flags")
    try:
        conn.execute("DELETE FROM flag WHERE version_id = ?", (version_id,))
        conn.executemany(
            "INSERT INTO flag (version_id, rule_id, severity, kind, matched_text, start_index, end_index)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(version_id, f.rule_id, f.severity, f.kind, f.matched_text, f.start, f.end) for f in flags])
    except BaseException:
        conn.execute("ROLLBACK TO store_flags")
        conn.execute("RELEASE store_flags")
        raise
    conn.execute("RELEASE store_flags")
