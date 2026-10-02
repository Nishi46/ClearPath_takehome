import re
from html.parser import HTMLParser

from tests.test_queue_page import EXPECTED_ORDER


class Focusables(HTMLParser):
    """Collect the elements a keyboard user reaches with Tab, in document order."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.positive_tabindex = []
        self.main_seen = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main":
            self.main_seen = True
        if a.get("tabindex") not in (None, "0", "-1"):
            self.positive_tabindex.append((tag, a["tabindex"]))
        if a.get("tabindex") == "-1" or "disabled" in a:
            return
        if tag == "a" and "href" in a:
            self.items.append(("a", a["href"], self.main_seen))
        elif tag == "button":
            self.items.append((tag, a.get("value") or "", self.main_seen))   # the role buttons carry their role as the value
        elif tag in ("select", "textarea"):
            self.items.append((tag, a.get("name") or "", self.main_seen))
        elif tag == "input" and a.get("type") != "hidden":
            self.items.append(("input", a.get("name", ""), self.main_seen))


def focusables(html):
    p = Focusables()
    p.feed(html)
    return p


def test_tab_order_on_the_queue(client):
    p = focusables(client.get("/").text)
    assert p.positive_tabindex == []           # a positive tabindex would reorder the page
    items = p.items
    kinds = [(k, v) for k, v, _ in items]
    # Skip link first, then the header, then the main content.
    assert kinds[0] == ("a", "#main")
    header = kinds[1:items.index(next(i for i in items if i[2]))]
    assert header == [("a", "/"), ("a", "/"), ("a", "/submit"),
                      ("button", "reviewer"), ("button", "marketer"), ("button", "affiliate"),
                      ("a", "/reset/confirm")]
    main = [(k, v) for k, v, in_main in items if in_main]
    assert main[:7] == [("select", "status"), ("select", "product"), ("select", "channel"), ("select", "source"),
                        ("button", ""), ("a", "/"), ("a", "/review/11")]
    titles = [v for k, v in main if k == "a" and v.startswith("/review/")]
    assert titles == ["/review/%d" % i for i in EXPECTED_ORDER]


def test_skip_link_target_exists(client):
    html = client.get("/").text
    assert re.search(r'<main id="main"', html)


def test_apply_comes_after_all_three_filters_and_before_the_rows(client):
    kinds = [k for k, v, m in focusables(client.get("/").text).items if m]
    assert kinds.index("button") > max(i for i, k in enumerate(kinds) if k == "select")
    assert kinds.index("button") < kinds.index("a", kinds.index("button") + 2)


def test_empty_state_link_is_reachable_after_the_filters(client):
    items = focusables(client.get("/?status=rejected&product=card").text).items
    main = [(k, v) for k, v, m in items if m]
    assert main[-1] == ("a", "/") and not any(v.startswith("/review/") for _, v in main)


def test_confirm_page_cancel_and_submit_are_both_reachable(client):
    main = [(k, v) for k, v, m in focusables(client.get("/reset/confirm").text).items if m]
    assert ("button", "") in main and ("a", "/") in main
