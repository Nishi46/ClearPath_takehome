from fastapi import Request

# The role is a demo label, not authentication or authorization (assumption A4).
# Anyone can set it to anything in ROLES, so nothing may rely on it for access control.
# It only changes what the header shows and which view is the default.
ROLES = ("reviewer", "marketer")
DEFAULT_ROLE = ROLES[0]
COOKIE_NAME = "role"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def get_role(request: Request) -> str:
    """Return the role from the cookie if it is a known value, else the default."""
    value = request.cookies.get(COOKIE_NAME)
    return value if value in ROLES else DEFAULT_ROLE
