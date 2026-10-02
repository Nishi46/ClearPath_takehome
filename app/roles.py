from fastapi import Request

# The role is a demo label, not authentication or authorization (assumption A4).
# Anyone can set it to anything in ROLES, so nothing may rely on it for access control.
# It only changes what the header shows and which view is the default.
ROLES = ("reviewer", "marketer")
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
