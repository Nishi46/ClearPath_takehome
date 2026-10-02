-- ClearPath Review schema. Every statement is idempotent (IF NOT EXISTS).
-- Blank checks trim spaces, tabs and newlines: SQLite's trim() strips only spaces by default.
-- Reuse the same trim() form for every non-blank CHECK (steps 5 and 6).
-- Foreign keys are enforced per connection (see db.connect), not by this file.

CREATE TABLE IF NOT EXISTS submission (
    id              INTEGER PRIMARY KEY,
    title           TEXT NOT NULL CHECK (length(trim(title, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0),
    product         TEXT NOT NULL CHECK (product IN ('loan', 'card', 'mortgage')),
    channel         TEXT NOT NULL CHECK (channel IN ('email', 'paid_social', 'affiliate_page', 'display')),
    status          TEXT NOT NULL CHECK (status IN ('new', 'in_review', 'changes_requested', 'approved', 'rejected')),
    launch_date     TEXT NOT NULL,
    submitted_by    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    current_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS version (
    id              INTEGER PRIMARY KEY,
    submission_id   INTEGER NOT NULL REFERENCES submission(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    copy            TEXT NOT NULL CHECK (length(trim(copy, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0),
    notes           TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (submission_id, version_number)
);

CREATE TABLE IF NOT EXISTS flag (
    id            INTEGER PRIMARY KEY,
    version_id    INTEGER NOT NULL REFERENCES version(id) ON DELETE CASCADE,
    rule_id       TEXT NOT NULL,
    severity      TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    kind          TEXT NOT NULL CHECK (kind IN ('phrase', 'missing')),
    matched_text  TEXT,
    start_index   INTEGER,
    end_index     INTEGER,
    CHECK (
        (kind = 'phrase' AND matched_text IS NOT NULL AND start_index IS NOT NULL
            AND end_index IS NOT NULL AND start_index < end_index)
        OR
        (kind = 'missing' AND matched_text IS NULL AND start_index IS NULL AND end_index IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS flag_dismissal (
    id            INTEGER PRIMARY KEY,
    version_id    INTEGER NOT NULL REFERENCES version(id) ON DELETE CASCADE,
    rule_id       TEXT NOT NULL,
    note          TEXT NOT NULL CHECK (length(trim(note, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0),
    dismissed_by  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (version_id, rule_id)
);

-- A decision or comment must point at a version that exists: the composite foreign key
-- targets version's UNIQUE (submission_id, version_number) pair.
CREATE TABLE IF NOT EXISTS decision (
    id              INTEGER PRIMARY KEY,
    submission_id   INTEGER NOT NULL REFERENCES submission(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('approved', 'changes_requested', 'rejected')),
    reviewer        TEXT NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (submission_id, version_number),
    FOREIGN KEY (submission_id, version_number)
        REFERENCES version(submission_id, version_number) ON DELETE CASCADE,
    -- Written as "approved OR (not null AND non-blank)": a NULL reason would otherwise pass a bare length check.
    CHECK (outcome = 'approved' OR (reason IS NOT NULL AND length(trim(reason, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0))
);

CREATE TABLE IF NOT EXISTS comment (
    id              INTEGER PRIMARY KEY,
    submission_id   INTEGER NOT NULL REFERENCES submission(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    author          TEXT NOT NULL,
    text            TEXT NOT NULL CHECK (length(trim(text, ' ' || char(9) || char(10) || char(11) || char(12) || char(13))) > 0),
    rule_id         TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (submission_id, version_number)
        REFERENCES version(submission_id, version_number) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_submission_status ON submission(status);
CREATE INDEX IF NOT EXISTS idx_submission_launch_date ON submission(launch_date);
CREATE INDEX IF NOT EXISTS idx_version_submission_id ON version(submission_id);
CREATE INDEX IF NOT EXISTS idx_flag_version_id ON flag(version_id);
CREATE INDEX IF NOT EXISTS idx_comment_submission_id ON comment(submission_id, version_number);

-- Immutable audit rows: a decision, comment or dismissal is never edited (D2, D4, C4). Reset and
-- cascading deletes use DELETE and are unaffected. `flag` is not covered: store_flags replaces it.
CREATE TRIGGER IF NOT EXISTS decision_no_update BEFORE UPDATE ON decision
BEGIN SELECT RAISE(ABORT, 'decision rows cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS comment_no_update BEFORE UPDATE ON comment
BEGIN SELECT RAISE(ABORT, 'comment rows cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS flag_dismissal_no_update BEFORE UPDATE ON flag_dismissal
BEGIN SELECT RAISE(ABORT, 'flag dismissal rows cannot be changed'); END;

PRAGMA user_version = 2;
