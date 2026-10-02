# The fixed lists the whole app agrees on. They live here, with no imports, so the seed loader,
# the rules engine and the flag storage can all use them without importing each other.
PRODUCTS = ("loan", "card", "mortgage")
CHANNELS = ("email", "paid_social", "affiliate_page", "display")
STATUSES = ("new", "in_review", "changes_requested", "approved", "rejected")
OUTCOMES = ("approved", "changes_requested", "rejected")
