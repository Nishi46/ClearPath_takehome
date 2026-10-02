import re

import pytest

from app.queue import FILTER_OPTIONS
from app.templating import APP_DIR

CSS = (APP_DIR / "static" / "style.css").read_text()


def tokens():
    root = re.search(r":root\s*{(.*?)}", CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", root))


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


T = tokens()

# Every text/background pairing the stylesheet actually uses. All must reach WCAG AA for normal text.
PAIRS = [
    ("text", "bg"), ("text", "surface"), ("text-muted", "bg"), ("text-muted", "surface"),
    ("accent", "surface"), ("accent", "bg"), ("accent-text", "accent"),
    ("overdue-text", "overdue-bg"), ("rush-text", "rush-bg"),
    ("overdue-text", "surface"), ("rush-text", "surface"),
    ("text-muted", "overdue-bg"), ("text-muted", "rush-bg"), ("text", "overdue-bg"), ("text", "rush-bg"),
    ("accent", "overdue-bg"), ("accent", "rush-bg"),
] + [(f"status-{v}-text", f"status-{v}-bg") for v, _ in FILTER_OPTIONS["status"]]


@pytest.mark.parametrize("fg,bg", PAIRS)
def test_text_contrast_meets_aa(fg, bg):
    assert fg in T and bg in T, "token missing"
    assert contrast(T[fg], T[bg]) >= 4.5, (fg, bg, round(contrast(T[fg], T[bg]), 2))


def test_contrast_helper_is_correct():
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
    assert contrast("#777777", "#ffffff") < 4.6   # a known borderline grey


def test_focus_color_is_visible_against_the_page():
    assert contrast(T["focus"], T["bg"]) >= 3.0 and contrast(T["focus"], T["surface"]) >= 3.0


def test_border_colors_for_urgency_are_visible_non_text_graphics():
    for name in ("overdue-border", "rush-border"):
        assert contrast(T[name], T["surface"]) >= 3.0


# ---- the stylesheet covers the markup ----

@pytest.mark.parametrize("selector", [
    "table.queue", ".urgency-overdue", ".urgency-rush", ".urgency", ".status", ".filters", ".filter",
    ".filter-apply", ".summary", ".empty-state", ".banner", ".visually-hidden", ".launch",
])
def test_class_used_by_the_queue_page_is_styled(selector):
    assert selector in CSS


@pytest.mark.parametrize("value", [v for v, _ in FILTER_OPTIONS["status"]])
def test_every_status_has_a_chip_style(value):
    assert f".status-{value}" in CSS


def test_status_chip_has_a_border_so_it_is_not_color_alone():
    block = re.search(r"\.status\s*{(.*?)}", CSS, re.S).group(1)
    assert "border" in block


def test_overdue_has_a_non_color_marker():
    assert re.search(r'\.urgency-overdue \.urgency::before\s*{[^}]*content:\s*"! "', CSS)


def test_focus_styles_exist_for_every_interactive_element():
    for sel in ("a:focus-visible", "button:focus-visible", "select:focus-visible"):
        assert sel in CSS
    assert not re.search(r"outline\s*:\s*(none|0)", CSS)


# ---- the narrow layout ----

def narrow_block():
    m = re.search(r"@media \(max-width:\s*40rem\)\s*{(.*)}\s*$", CSS, re.S)
    return m.group(1)


def test_narrow_layout_stacks_rows_and_labels_values():
    block = narrow_block()
    assert re.search(r"table\.queue, table\.queue tbody, table\.queue tr, table\.queue td\s*{[^}]*display:\s*block", block)
    assert "attr(data-label)" in block
    assert re.search(r"table\.queue thead\s*{[^}]*clip", block)       # header hidden visually, kept for readers
    assert "display: none" not in block                               # display:none would hide it from screen readers


def test_every_cell_label_in_the_markup_matches_the_columns(client):
    row = re.search(r"<tbody>.*?</tr>", client.get("/").text, re.S).group(0)
    assert re.findall(r'data-label="([^"]+)"', row) == [
        "Title", "Product", "Channel", "Launch date", "Status", "Submitter", "Flags"]


def test_free_text_columns_can_break_long_words():
    assert re.search(r"nth-child\(1\).*?overflow-wrap:\s*anywhere", CSS, re.S)


def test_layout_has_a_viewport_meta_and_no_fixed_widths(client):
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in client.get("/").text
    assert not re.search(r"(?<!max-)(?<!min-)width:\s*\d{2,}px", CSS)   # no pixel-fixed widths (the 1px hidden-text utility is fine)


# ---- CSP safety ----

def test_stylesheet_loads_nothing_external():
    assert "@import" not in CSS
    assert not re.search(r"url\(\s*['\"]?(https?:|//)", CSS)
    assert "http://" not in CSS and "https://" not in CSS


def test_stylesheet_is_served_with_a_css_content_type(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


def test_urgency_and_status_are_in_the_markup_as_words(client):
    html = client.get("/").text
    for word in ("Overdue by 1 day", "Launches tomorrow", "In review", "Changes requested"):
        assert word in html


def test_launch_wrapper_keeps_date_and_label_together(client):
    row = re.search(r'<tr class="urgency-overdue">.*?</tr>', client.get("/").text, re.S).group(0)
    assert re.search(r'<div class="launch">\s*<time[^>]*>[^<]+</time>\s*<span class="urgency">Overdue', row)
