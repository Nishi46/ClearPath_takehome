import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app import queue as queue_module
from app.queue import list_queue, row_view
from app.seed import seed_all

NOW = datetime(2026, 10, 1, 12, 30, 15, tzinfo=timezone.utc)
THU = date(2026, 10, 1)
ROOT = Path(queue_module.__file__).resolve().parent.parent


def view(count, severity=None):
    row = {"id": 1, "title": "T", "product": "loan", "channel": "email", "launch_date": "2026-10-20",
           "status": "new", "submitted_by": "Maya", "created_at": "x", "version_number": 1,
           "flag_count": count, "top_severity": severity}
    return row_view(row, THU)


def row_html(html, sid):
    return re.search(rf'<tr[^>]*>(?:(?!</tr>).)*/review/{sid}"(?:(?!</tr>).)*</tr>', html, re.S).group(0)


# ---- the view model ----

@pytest.mark.parametrize("count, severity, label, letter", [
    (0, None, "No flags", ""),
    (1, "low", "1 rule flagged, highest severity Low", "L"),
    (1, "medium", "1 rule flagged, highest severity Medium", "M"),
    (3, "high", "3 rules flagged, highest severity High", "H"),
    (7, "medium", "7 rules flagged, highest severity Medium", "M"),
])
def test_wording_and_letter(count, severity, label, letter):
    v = view(count, severity)
    assert v["flags_label"] == label and v["flags_letter"] == letter and v["flags_text"] == str(count)


def test_singular_and_plural():
    assert "1 rule flagged" in view(1, "high")["flags_label"] and "1 rules" not in view(1, "high")["flags_label"]
    assert "2 rules flagged" in view(2, "high")["flags_label"]


@pytest.mark.parametrize("count, severity", [(0, "high"), (-1, "high"), (0, None)])
def test_a_zero_count_never_shows_a_severity(count, severity):
    v = view(count, severity)
    assert v["flags_label"] == "No flags" and v["flags_letter"] == "" and v["flags_severity"] == ""


@pytest.mark.parametrize("bad", [None, "", "critical", "HIGH", "<script>", "'; DROP TABLE flag; --"])
def test_unknown_or_missing_severity_does_not_crash_and_shows_no_letter(bad):
    v = view(2, bad)
    assert v["flags_letter"] == "" and v["flags_severity"] == "" and v["flags_label"] == "2 rules flagged"


# ---- the query ----

def test_top_severity_for_the_seed(conn):
    seed_all(conn, NOW)
    got = {r["id"]: (r["flag_count"], r["top_severity"]) for r in list_queue(conn)}
    assert got == {1: (3, "high"), 2: (2, "medium"), 3: (2, "high"), 4: (2, "high"), 5: (0, None),
                   6: (0, None), 7: (0, None), 8: (3, "high"), 9: (1, "high"), 10: (0, None),
                   11: (0, None), 12: (1, "medium"), 13: (0, None), 14: (3, "high")}


def test_a_dismissed_high_flag_no_longer_sets_the_top_severity(conn):
    seed_all(conn, NOW)
    vid = conn.execute("SELECT id FROM version WHERE submission_id = 8").fetchone()[0]
    for rule in ("R1",):
        conn.execute("INSERT INTO flag_dismissal (version_id, rule_id, note, dismissed_by, created_at)"
                     " VALUES (?, ?, 'n', 'Alex', '2026-10-01T00:00:00Z')", (vid, rule))
    row = next(r for r in list_queue(conn) if r["id"] == 8)
    assert (row["flag_count"], row["top_severity"]) == (2, "medium")  # R4 medium, R6 low


def test_only_the_current_version_sets_the_severity(conn):
    seed_all(conn, NOW)
    row = next(r for r in list_queue(conn) if r["id"] == 5)  # v1 had two flags, v2 is clean
    assert (row["flag_count"], row["top_severity"]) == (0, None)


# ---- the page ----

def test_every_cell_has_visible_count_hidden_sentence_and_title(client):
    html = client.get("/").text
    cells = re.findall(r'<td data-label="Flags">(.*?)</td>', html, re.S)
    assert len(cells) == 14
    for cell in cells:
        assert re.search(r'title="[^"]+"', cell) and 'class="visually-hidden"' in cell
        assert 'aria-hidden="true"' in cell


def test_item_1_and_clean_items_on_the_page(client):
    html = client.get("/").text
    one = row_html(html, 1)
    assert '<span class="flag-count">3</span>' in one and 'class="sev sev-high">H</span>' in one
    assert 'class="visually-hidden">3 rules flagged, highest severity High</span>' in one
    assert 'title="3 rules flagged, highest severity High"' in one
    for sid in (6, 10, 13):
        clean = row_html(html, sid)
        assert '<span class="flag-count">0</span>' in clean and "No flags" in clean and "sev-" not in clean
    assert 'sev-medium">M</span>' in row_html(html, 2) and "2 rules flagged, highest severity Medium" in row_html(html, 2)


def test_no_dashes_remain_in_the_flag_column(client):
    html = client.get("/").text
    for cell in re.findall(r'<td data-label="Flags">(.*?)</td>', html, re.S):
        assert re.search(r'flag-count">\d+<', cell)
        assert ">-<" not in cell


def test_severity_is_words_not_only_color(client):
    html = client.get("/").text
    for sev, word in (("high", "High"), ("medium", "Medium")):
        assert f"highest severity {word}" in html
    # the letter is real text inside the badge, so removing every stylesheet loses nothing
    assert re.search(r'class="sev sev-high">H</span>', html)


def test_assist_only_note_is_visible_under_the_table(client):
    html = client.get("/").text
    assert "Flags are assist-only" in html
    assert html.index("</table>") < html.index("Flags are assist-only")


def test_note_is_absent_from_the_empty_states(client):
    assert "assist-only" not in client.get("/?status=rejected&product=card").text


def test_label_text_is_escaped_and_the_page_stays_strict(client):
    html = client.get("/").text
    assert "style=" not in html and "<script>" not in re.sub(r'<script src="[^"]*"[^>]*></script>', "", html)


def test_css_defines_the_severity_badges_with_a_non_color_difference():
    css = (ROOT / "app" / "static" / "style.css").read_text()
    for cls in (".sev-high", ".sev-medium", ".sev-low", ".flag-count", ".table-note"):
        assert cls in css
    assert re.search(r"\.sev-low\s*{[^}]*dashed", css) and re.search(r"\.sev-high\s*{[^}]*border-width", css)
    assert re.search(r"\.visually-hidden\s*{", css)
