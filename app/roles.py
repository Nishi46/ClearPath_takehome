from fastapi import Request

# The role is a demo label, not authentication or authorization (assumption A4).
# Anyone can set it to anything in ROLES, so nothing may rely on it for access control.
# It only changes what the header shows and which view is the default.
ROLES = ("reviewer", "marketer", "affiliate")
DEFAULT_ROLE = ROLES[0]
# The demo reviewer every recorded decision is attributed to. There is no login, so the name
# never comes from the request.
REVIEWER_NAME = "Alex Rivera"
COOKIE_NAME = "role"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def get_role(request: Request) -> str:
    """Return the role from the cookie if it is a known value, else the default."""
    value = request.cookies.get(COOKIE_NAME)
    return value if value in ROLES else DEFAULT_ROLE


# The demo marketer "My submissions" and new submissions belong to. Like the role, it is a label
# picked in the browser, not a login (assumption A4): nothing may rely on it for access control.
# `submitted_by` is always taken from here, never from a form field. Sam Patel owns no seed items,
# so that identity shows the empty state.
MARKETERS = ("Maya Chen", "Jordan Lee", "Sam Patel")
DEFAULT_MARKETER = MARKETERS[0]
MARKETER_COOKIE_NAME = "marketer"


def get_marketer(request: Request) -> str:
    """Return the marketer from the cookie if it is exactly a known name, else the default."""
    value = request.cookies.get(MARKETER_COOKIE_NAME)
    return value if value in MARKETERS else DEFAULT_MARKETER


# Affiliate partners submit their own affiliate_page assets. Same rules as the marketer: a label
# picked in the browser (A4), never a login. Summit Savers owns no seed items, so that identity
# shows the empty state. Names must never overlap MARKETERS, because `submitted_by` is the only
# thing that says whether an item came from a partner.
AFFILIATES = ("Northwind Referrals", "BlueLeaf Media", "Summit Savers")
DEFAULT_AFFILIATE = AFFILIATES[0]
AFFILIATE_COOKIE_NAME = "affiliate"
ALL_SUBMITTERS = MARKETERS + AFFILIATES
# The one channel a partner can submit for.
AFFILIATE_CHANNEL = "affiliate_page"
SUBMITTING_ROLES = ("marketer", "affiliate")


def get_affiliate(request: Request) -> str:
    """Return the affiliate from the cookie if it is exactly a known name, else the default."""
    value = request.cookies.get(AFFILIATE_COOKIE_NAME)
    return value if value in AFFILIATES else DEFAULT_AFFILIATE


def get_submitter(request: Request) -> str:
    """The name `submitted_by` is taken from: the partner for the affiliate role, else the marketer."""
    return get_affiliate(request) if get_role(request) == "affiliate" else get_marketer(request)


def is_partner(name) -> bool:
    return isinstance(name, str) and name in AFFILIATES
