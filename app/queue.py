from datetime import date

from app import clock
from app.roles import AFFILIATES, is_partner

# Queue filters and the queue query. FILTER_OPTIONS is the one list of allowed filter values
# and their display names; the filter form, the route and the query all use it.

FILTER_OPTIONS = {
    "status": (
        ("new", "New"),
        ("in_review", "In review"),
        ("changes_requested", "Changes requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ),
    "product": (
        ("loan", "Loan"),
        ("card", "Card"),
        ("mortgage", "Mortgage"),
    ),
    "channel": (
        ("email", "Email"),
        ("paid_social", "Paid social"),
        ("affiliate_page", "Affiliate page"),
        ("display", "Display"),
    ),
    "source": (
        ("partners", "Affiliate partners"),
        ("internal", "Internal marketers"),
    ),
}

# Sort is not a filter: it never hides rows, and it always has a value. The first option is the
# default and is left out of URLs. Ties always fall back to earliest launch date.
SORT_OPTIONS = (
    ("launch_asc", "Launch date: earliest first"),
    ("launch_desc", "Launch date: latest first"),
    ("flags_desc", "Flags: most first"),
    ("flags_asc", "Flags: fewest first"),
)
DEFAULT_SORT = SORT_OPTIONS[0][0]

# The partner names as one bound text value for the query, so the SQL itself never changes.
PARTNER_LIST = "|" + "|".join(AFFILIATES) + "|"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# One constant query. Each filter is "no filter (NULL) or an exact match", so a request value is
# only ever a bound parameter and no SQL text is assembled at run time.
sql_queue = """
SELECT s.id, s.title, s.product, s.channel, s.launch_date, s.status, s.submitted_by,
       s.created_at, s.current_version AS version_number,
       (SELECT count(DISTINCT f.rule_id) FROM flag f JOIN version v ON v.id = f.version_id
         WHERE v.submission_id = s.id AND v.version_number = s.current_version
           AND NOT EXISTS (SELECT 1 FROM flag_dismissal d
                            WHERE d.version_id = f.version_id AND d.rule_id = f.rule_id)) AS flag_count,
       (SELECT CASE min(CASE f.severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END)
                    WHEN 1 THEN 'high' WHEN 2 THEN 'medium' WHEN 3 THEN 'low' END
          FROM flag f JOIN version v ON v.id = f.version_id
         WHERE v.submission_id = s.id AND v.version_number = s.current_version
           AND NOT EXISTS (SELECT 1 FROM flag_dismissal d
                            WHERE d.version_id = f.version_id AND d.rule_id = f.rule_id)) AS top_severity
  FROM submission s
 WHERE (? IS NULL OR s.status = ?)
   AND (? IS NULL OR s.product = ?)
   AND (? IS NULL OR s.channel = ?)
   AND (? IS NULL OR (instr(?, '|' || s.submitted_by || '|') > 0) = (? = 'partners'))
 ORDER BY CASE WHEN ? = 'launch_desc' THEN s.launch_date END DESC,
          CASE WHEN ? = 'flags_desc' THEN flag_count END DESC,
          CASE WHEN ? = 'flags_asc' THEN flag_count END ASC,
          s.launch_date ASC, s.created_at ASC, s.id ASC
"""


def clean_filter(name, value):
    """Return `value` if it is an allowed choice for filter `name`, else None (meaning "All").

    Anything else, including odd types, wrong case, SQL-looking text and over-long strings, is
    treated as "All" instead of raising or being passed on.
    """
    if not isinstance(value, str):
        return None
    return value if value in dict(FILTER_OPTIONS[name]) else None


def clean_sort(value):
    """Return `value` if it is an allowed sort, else the default."""
    return value if isinstance(value, str) and value in dict(SORT_OPTIONS) else DEFAULT_SORT


def sort_from_query(query_params):
    """The sort from a request's query string; missing, repeated or unknown means the default."""
    values = query_params.getlist("sort")
    return clean_sort(values[0]) if len(values) == 1 else DEFAULT_SORT


def sort_controls(filters, sort):
    """The two sort arrows in the table header (Launch date, Flags): where each links and which way it points now.

    One link per column. Clicking it flips that column's direction; any other current sort moves to that
    column's first direction (launch: earliest first, flags: most first). Filters stay in the link.
    """
    from urllib.parse import urlencode

    sort = clean_sort(sort)
    kept = [(k, v) for k, v in filters.items() if v]

    def href(target):
        query = kept + ([("sort", target)] if target != DEFAULT_SORT else [])
        return "/?" + urlencode(query) if query else "/"

    labels = dict(SORT_OPTIONS)
    out = {}
    for column, first, second in (("launch", "launch_asc", "launch_desc"), ("flags", "flags_desc", "flags_asc")):
        current = sort if sort in (first, second) else None
        target = second if current == first else first
        out[column] = {"href": href(target), "label": "Sort by " + labels[target][0].lower() + labels[target][1:],
                       "state": ("up" if current == "launch_asc" or current == "flags_asc" else "down") if current else ""}
    return out


def list_queue(conn, status=None, product=None, channel=None, source=None, sort=DEFAULT_SORT):
    """One row per submission, showing its current version, earliest launch first by default.

    Ties break on created_at then id, so the order is always the same. Unknown filter values
    mean "All". Runs a single query.
    """
    args = []
    for name, value in (("status", status), ("product", product), ("channel", channel)):
        value = clean_filter(name, value)
        args += [value, value]
    source = clean_filter("source", source)
    sort = clean_sort(sort)
    args += [source, PARTNER_LIST, source, sort, sort, sort]
    return [dict(r) for r in conn.execute(sql_queue, args).fetchall()]


def _label(name, value):
    """Display name for a filter value; an unlisted value is shown readable rather than crashing the page."""
    labels = dict(FILTER_OPTIONS[name])
    return labels.get(value) or str(value).replace("_", " ").capitalize()


# Severity shown as a letter plus words, never color alone.
SEVERITY_DISPLAY = {"high": ("H", "High"), "medium": ("M", "Medium"), "low": ("L", "Low")}


def _flags_view(count, severity):
    """Visible text, severity letter and the full sentence for the Flags column."""
    if count <= 0:
        return {"flags_text": "0", "flags_label": "No flags", "flags_severity": "", "flags_letter": ""}
    letter, word = SEVERITY_DISPLAY.get(severity, ("", ""))
    label = "%s flagged" % _plural(count, "rule")
    if word:
        label += ", highest severity " + word
    return {"flags_text": str(count), "flags_label": label,
            "flags_severity": severity if word else "", "flags_letter": letter}


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _launch_text(launch):
    return "%s %d, %d" % (MONTHS[launch.month - 1], launch.day, launch.year)


def _urgency_label(kind, today, launch):
    if kind == "overdue":
        return "Overdue by " + _plural((today - launch).days, "day")
    days = (launch - today).days
    if days == 0:
        return "Rush: launches today"
    if days == 1:
        return "Rush: launches tomorrow"
    business = clock.business_days_until(today, launch)
    if business == 0:
        return "Rush: launches this weekend"
    return "Rush: launches in " + _plural(business, "business day")


def row_view(row, today=None):
    """Turn a list_queue row into the plain-text values the queue page shows.

    Everything here is a plain string; the template escapes it. Nothing raises on odd data:
    an unknown status or an unreadable launch date just loses its label or urgency.
    """
    today = today or clock.today()
    try:
        launch = date.fromisoformat(row["launch_date"])
        launch_text = _launch_text(launch)
    except (ValueError, TypeError):
        launch, launch_text = None, str(row["launch_date"])

    kind = None
    if launch is not None:
        try:
            kind = clock.urgency(today, launch, row["status"])
        except ValueError:
            kind = None

    return {
        "id": row["id"],
        "title": row["title"],
        "product": row["product"],
        "product_label": _label("product", row["product"]),
        "channel": row["channel"],
        "channel_label": _label("channel", row["channel"]),
        "status": row["status"],
        "status_label": _label("status", row["status"]),
        "submitted_by": row["submitted_by"],
        "is_partner": is_partner(row["submitted_by"]),
        "version_number": row["version_number"],
        "version_text": "v%d" % row["version_number"],
        "launch_date": row["launch_date"],
        "launch_text": launch_text,
        "urgency": kind,
        "urgency_label": _urgency_label(kind, today, launch) if kind else "",
        "urgency_class": "urgency-" + kind if kind else "",
        "needs_attention": kind is not None,
        **_flags_view(row["flag_count"], row["top_severity"]),
    }


# Same left-to-right order as the table columns: Product, Channel, Status, Submitter.
FILTER_FIELDS = (("product", "Product"), ("channel", "Channel"), ("status", "Status"), ("source", "Submitted by"))


def filters_from_query(query_params):
    """Read the filters from a request's query string; anything unusable means "All".

    A filter given more than once is ambiguous, so it also means "All". Only the exact key
    names are read (status[] is a different key and is ignored).
    """
    chosen = {}
    for name, _ in FILTER_FIELDS:
        values = query_params.getlist(name)
        chosen[name] = clean_filter(name, values[0]) if len(values) == 1 else None
    return chosen


def summary_text(views):
    """"N items" plus how many of them need attention; plain words, no color."""
    n = len(views)
    items = "%d item%s" % (n, "" if n == 1 else "s")
    if n == 0:
        return items
    attention = sum(1 for v in views if v["needs_attention"])
    if attention == 0:
        return items + " \u00b7 none need attention"
    return items + " \u00b7 %d %s attention" % (attention, "needs" if attention == 1 else "need")


def has_submissions(conn):
    return bool(conn.execute("SELECT EXISTS (SELECT 1 FROM submission)").fetchone()[0])


def empty_kind(conn, views, filters):
    """Why the table has no rows: None (it has rows), "filtered" (filters hide everything) or "empty".

    An empty database says "empty" even when filters are set, so the page never blames
    the filters for a queue that has nothing in it.
    """
    if views:
        return None
    if any(filters.values()) and has_submissions(conn):
        return "filtered"
    return "empty"
