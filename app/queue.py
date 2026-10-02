from datetime import date

from app import clock

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
}

# Phase 3 turns this on once flags are computed. Until then a dash is honest; a "0" would claim
# the item was checked and found clean.
FLAGS_READY = False
FLAGS_PENDING_TITLE = "Flags are computed in phase 3"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# One constant query. Each filter is "no filter (NULL) or an exact match", so a request value is
# only ever a bound parameter and no SQL text is assembled at run time.
sql_queue = """
SELECT s.id, s.title, s.product, s.channel, s.launch_date, s.status, s.submitted_by,
       s.created_at, s.current_version AS version_number,
       (SELECT count(*) FROM flag f JOIN version v ON v.id = f.version_id
         WHERE v.submission_id = s.id AND v.version_number = s.current_version) AS flag_count
  FROM submission s
 WHERE (? IS NULL OR s.status = ?)
   AND (? IS NULL OR s.product = ?)
   AND (? IS NULL OR s.channel = ?)
 ORDER BY s.launch_date ASC, s.created_at ASC, s.id ASC
"""


def clean_filter(name, value):
    """Return `value` if it is an allowed choice for filter `name`, else None (meaning "All").

    Anything else, including odd types, wrong case, SQL-looking text and over-long strings, is
    treated as "All" instead of raising or being passed on.
    """
    if not isinstance(value, str):
        return None
    return value if value in dict(FILTER_OPTIONS[name]) else None


def list_queue(conn, status=None, product=None, channel=None):
    """One row per submission, showing its current version, earliest launch first.

    Ties break on created_at then id, so the order is always the same. Unknown filter values
    mean "All". Runs a single query.
    """
    args = []
    for name, value in (("status", status), ("product", product), ("channel", channel)):
        value = clean_filter(name, value)
        args += [value, value]
    return [dict(r) for r in conn.execute(sql_queue, args).fetchall()]


def _label(name, value):
    """Display name for a filter value; an unlisted value is shown readable rather than crashing the page."""
    labels = dict(FILTER_OPTIONS[name])
    return labels.get(value) or str(value).replace("_", " ").capitalize()


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _launch_text(launch):
    return "%s %d, %d" % (MONTHS[launch.month - 1], launch.day, launch.year)


def _urgency_label(kind, today, launch):
    if kind == "overdue":
        return "Overdue by " + _plural((today - launch).days, "day")
    days = (launch - today).days
    if days == 0:
        return "Launches today"
    if days == 1:
        return "Launches tomorrow"
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

    if FLAGS_READY:
        flags_text, flags_title = str(row["flag_count"]), ""
    else:
        flags_text, flags_title = "-", FLAGS_PENDING_TITLE

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
        "version_number": row["version_number"],
        "launch_date": row["launch_date"],
        "launch_text": launch_text,
        "urgency": kind,
        "urgency_label": _urgency_label(kind, today, launch) if kind else "",
        "urgency_class": "urgency-" + kind if kind else "",
        "needs_attention": kind is not None,
        "flags_text": flags_text,
        "flags_title": flags_title,
    }


FILTER_FIELDS = (("status", "Status"), ("product", "Product"), ("channel", "Channel"))


def filters_from_query(query_params):
    """Read the three filters from a request's query string; anything unusable means "All".

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
