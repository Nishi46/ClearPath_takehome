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
