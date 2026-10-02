"""The submit page at laptop and phone widths, measured in headless Chrome (skipped without Chrome)."""
import pytest

from tests.chrome_layout import CHROME, measure
from pathlib import Path

pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome is not installed here")

LONG = "Unbroken" * 625  # 5,000 characters, no spaces


def pages(client):
    plain = client.get("/submit").text
    flagged = client.post("/submit/check", data={"product": "loan", "channel": "email", "title": "T" * 120,
                                                 "launch_date": "2000-01-01", "notes": LONG,
                                                 "copy": "Guaranteed approval, rates as low as 5.99%.\n" + LONG}).text
    failed = client.post("/submit", data={"title": LONG, "product": "x", "copy": LONG, "notes": LONG}).text
    return {"plain": plain, "flagged": flagged, "failed": failed}


@pytest.mark.parametrize("size", [(1366, 768), (1440, 900), (390, 844)])
def test_no_horizontal_scroll_at_any_width(mclient, size):
    for name, html in pages(mclient).items():
        m = measure(html, *size)
        assert m["scrollWidth"] <= m["clientWidth"] + 1, (name, size, m["scrollWidth"], m["clientWidth"])
        assert m["wide"] == [], (name, size, m["wide"])


def test_the_precheck_sits_beside_the_form_on_a_laptop_and_below_it_on_a_phone(mclient):
    html = mclient.get("/submit").text
    wide = measure(html, 1366, 768)
    narrow = measure(html, 390, 844)
    assert wide["scrollHeight"] > 0 and narrow["scrollHeight"] > wide["scrollHeight"]  # stacked: taller on a phone
