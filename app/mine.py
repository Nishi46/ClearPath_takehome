from app import clock
from app.queue import _launch_text, _label, _plural, _urgency_label

# The marketer's view of their own submissions ("My submissions"). Plain functions with no web
# code: list_mine reads (one fixed, parameterized query, nothing is written) and group_mine sorts
# the rows into the three groups the page shows. Everything returned is plain text or numbers;
# the template escapes it. The marketer name is matched exactly, and it is a demo label (A4), so
# this narrows what the page lists, it is not access control.

# One query: each submission with the decision on its CURRENT version (a decision on an older
# version belongs to the history, not to the feedback the marketer must act on now) and the
# distinct open rules on that version, dismissed rules excluded, as the queue counts them.
sql_mine = """
SELECT s.id, s.title, s.product, s.channel, s.status, s.launch_date, s.created_at,
       s.current_version AS version_number,
       d.outcome AS decision_outcome, d.reviewer AS decision_reviewer,
       d.reason AS decision_reason, d.created_at AS decision_at,
       (SELECT count(DISTINCT f.rule_id) FROM flag f JOIN version v ON v.id = f.version_id
         WHERE v.submission_id = s.id AND v.version_number = s.current_version
           AND NOT EXISTS (SELECT 1 FROM flag_dismissal x
                            WHERE x.version_id = f.version_id AND x.rule_id = f.rule_id)) AS flag_count
  FROM submission s
  LEFT JOIN decision d ON d.submission_id = s.id AND d.version_number = s.current_version
 WHERE s.submitted_by = ?
 ORDER BY s.id
"""

# (key, heading, statuses in display order)
GROUPS = (
    ("needs_action", "Needs your action", ("changes_requested", "rejected")),
    ("in_progress", "In progress", ("new", "in_review")),
    ("done", "Done", ("approved",)),
)


def _feedback(row):
    if row["decision_outcome"] is None:
        return None
    from app import review

    return {"outcome": row["decision_outcome"], "outcome_label": _label("status", row["decision_outcome"]),
            "reviewer": row["decision_reviewer"], "reason": row["decision_reason"],
            "at": row["decision_at"], "when": review.day_text(row["decision_at"])}


def list_mine(conn, marketer, today=None):
    """The marketer's submissions as plain dicts, oldest id first; none for an unknown or non-text name.

    `today` (a date) is only used for the urgency label and defaults to the clock's today. Each row
    has `feedback` (the decision on the current version, or None) and `urgency` ("overdue",
    "rush" or None; only open items are ever urgent). A malformed stored date or timestamp
    never raises: the urgency is None and the feedback date shows "unknown date".
    """
    if not isinstance(marketer, str):
        return []
    today = clock.today() if today is None else today
    rows = []
    for r in conn.execute(sql_mine, (marketer,)):
        try:
            urgency = clock.urgency(today, r["launch_date"], r["status"])
            launch = clock.date.fromisoformat(r["launch_date"])
            launch_text = _launch_text(launch)
            urgency_label = _urgency_label(urgency, today, launch) if urgency else ""
        except ValueError:
            urgency, launch_text, urgency_label = None, "unknown date", ""
        rows.append({
            "id": r["id"], "title": r["title"], "product": r["product"], "channel": r["channel"],
            "status": r["status"], "status_label": _label("status", r["status"]),
            "version_number": r["version_number"], "launch_date": r["launch_date"], "urgency": urgency,
            "launch_text": launch_text, "urgency_label": urgency_label,
            "flags_text": _plural(r["flag_count"], "open flag"), "flag_count": r["flag_count"], "feedback": _feedback(r),
            "_decision_at": r["decision_at"] or "",
        })
    return rows


def group_mine(rows):
    """Three groups, always in this order, each {"key", "title", "rows"} (a group may have no rows).

    Needs your action: changes requested first, then rejected, each newest decision first.
    In progress: new and in review, soonest launch first. Done: approved, newest decision first.
    Ties break on the id, so the order never changes between page loads.
    """
    groups = []
    for key, title, statuses in GROUPS:
        members = [r for r in rows if r["status"] in statuses]
        if key == "in_progress":
            members.sort(key=lambda r: (r["launch_date"], r["id"]))
        else:
            members.sort(key=lambda r: (r["_decision_at"], r["id"]), reverse=True)
            members.sort(key=lambda r: statuses.index(r["status"]))  # stable: keeps newest-first within a status
        groups.append({"key": key, "title": title, "rows": members})
    return groups
