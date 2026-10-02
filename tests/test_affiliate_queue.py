import re

import pytest

from app import db
from app.queue import FILTER_OPTIONS, PARTNER_LIST, clean_filter, filters_from_query, list_queue, row_view
from app.roles import AFFILIATES

PARTNER_IDS = {3, 12}


def ids(conn, **kw):
    return [r["id"] for r in list_queue(conn, **kw)]


def queue_ids(html):
    return [int(i) for i in re.findall(r'href="/review/(\d+)', html)]


@pytest.fixture
def seeded(client):
    with db.connect() as c:
        yield c


def test_partners_and_internal_partition_the_queue(seeded):
    everything, partners, internal = ids(seeded), ids(seeded, source="partners"), ids(seeded, source="internal")
    assert set(partners) == PARTNER_IDS
    assert set(partners).isdisjoint(internal) and sorted(partners + internal) == sorted(everything)
    assert len(everything) == 14


def test_source_combines_with_other_filters(seeded):
    assert ids(seeded, source="partners", channel="email") == []
    assert set(ids(seeded, source="partners", channel="affiliate_page")) == PARTNER_IDS
    assert ids(seeded, source="internal", channel="affiliate_page") == [8]
    assert ids(seeded, source="partners", status="in_review") == [3]
    assert ids(seeded, source="partners", status="rejected") == []


def test_order_is_unchanged_under_the_filter(seeded):
    got = list_queue(seeded, source="internal")
    assert [(r["launch_date"], r["created_at"], r["id"]) for r in got] == sorted(
        (r["launch_date"], r["created_at"], r["id"]) for r in got)


@pytest.mark.parametrize("value", [None, "", "Partners", "all", "partners ' OR 1=1 --", "x" * 5000, 5, ["partners"], b"partners"])
def test_bad_source_values_mean_all(seeded, value):
    assert clean_filter("source", value) is None
    assert len(ids(seeded, source=value)) == 14


def test_source_filter_options_and_constant_sql():
    assert dict(FILTER_OPTIONS["source"]) == {"partners": "Affiliate partners", "internal": "Internal marketers"}
    assert PARTNER_LIST == "|" + "|".join(AFFILIATES) + "|"


def test_filters_from_query_reads_source_once_only():
    from starlette.datastructures import QueryParams
    assert filters_from_query(QueryParams("source=partners"))["source"] == "partners"
    assert filters_from_query(QueryParams("source=partners&source=internal"))["source"] is None
    assert filters_from_query(QueryParams("source[]=partners"))["source"] is None
    assert filters_from_query(QueryParams("source=bogus"))["source"] is None


def test_queue_page_partner_filter_rows_badges_and_selected_value(client):
    html = client.get("/?source=partners").text
    assert set(queue_ids(html)) == PARTNER_IDS and html.count('class="partner-badge"') == 2
    assert '<option value="partners" selected>' in html and "2 items" in html
    assert 'for="filter-source">Submitted by' in html


def test_badge_appears_only_on_partner_rows(client):
    html = client.get("/").text
    rows = html.split("<tr")[2:]
    assert sum("partner-badge" in r for r in rows) == 2
    assert all(("partner-badge" in r) == any(("/review/%d" % i) in r for i in PARTNER_IDS) for r in rows)


def test_zero_results_show_the_filtered_empty_state(client):
    html = client.get("/?source=partners&channel=email").text
    assert "No items match these filters." in html and "<table" not in html


def test_empty_queue_with_source_filter_is_the_empty_state(client):
    with db.connect() as c:
        c.execute("DELETE FROM submission")
    assert "No submissions yet." in client.get("/?source=partners").text


def test_bad_source_in_the_url_is_not_echoed_and_shows_all(client):
    html = client.get("/?source=<script>alert(1)</script>").text
    assert "<script>alert(1)</script>" not in html and len(queue_ids(html)) == 14


def test_back_link_keeps_the_source_filter(client):
    assert "back=%2F%3Fsource%3Dpartners" in client.get("/?source=partners").text


def test_row_view_marks_partner(seeded):
    rows = {r["id"]: row_view(r) for r in list_queue(seeded)}
    assert rows[3]["is_partner"] and rows[12]["is_partner"] and not rows[8]["is_partner"]


def test_new_partner_submission_shows_up_under_partners(aclient):
    from datetime import timedelta

    from app import clock
    r = aclient.post("/submit", data={"title": "New partner", "product": "card", "channel": "x",
                                      "launch_date": (clock.today() + timedelta(days=9)).isoformat(),
                                      "copy": "Earn cashback on every purchase."}, follow_redirects=False)
    assert r.status_code == 303
    aclient.cookies.set("role", "reviewer")
    assert len(queue_ids(aclient.get("/?source=partners").text)) == 3


def test_partner_without_items_does_not_break_the_filter(client):
    client.cookies.set("affiliate", "Summit Savers")
    assert client.get("/?source=partners").status_code == 200


def test_queue_has_no_console_breaking_inline_script(client):
    html = client.get("/").text
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)


# ---- seed and reset ----

def owners(conn):
    return {r["id"]: r["submitted_by"] for r in conn.execute("SELECT id, submitted_by FROM submission")}


def test_seed_ownership(seeded):
    got = owners(seeded)
    assert got[3] == "Northwind Referrals" and got[12] == "BlueLeaf Media" and got[8] == "Jordan Lee"
    assert sum(1 for n in got.values() if n in AFFILIATES) == 2


def test_reset_restores_exact_partner_ownership(aclient):
    from tests.test_affiliate_submit import good, prep, resubmit
    aclient.post("/submit", data=good(), follow_redirects=False)
    prep(aclient)
    resubmit(aclient, 3)
    aclient.cookies.set("role", "reviewer")
    aclient.post("/review/12/decision", data={"outcome": "approved", "version": "1"}, follow_redirects=False)
    with db.connect() as c:
        assert len(owners(c)) == 15
    r = aclient.post("/reset", data={"confirm": "reset"}, follow_redirects=False)
    assert r.status_code in (200, 303)
    with db.connect() as c:
        after = owners(c)
        assert len(after) == 14 and after[3] == "Northwind Referrals" and after[12] == "BlueLeaf Media"
        assert tuple(c.execute("SELECT status, current_version FROM submission WHERE id = 3").fetchone()) == ("in_review", 1)
        assert c.execute("SELECT status FROM submission WHERE id = 12").fetchone()[0] == "new"
        assert c.execute("SELECT count(*) FROM decision WHERE submission_id IN (3, 12)").fetchone()[0] == 0


# ---- flags, audit trail and old databases on partner items ----

def test_rules_fire_on_the_partner_seed_items(seeded):
    from app import flags
    with db.connect() as c:
        for sid, rules_expected in ((3, {"R3", "R4"}), (12, {"R4"})):
            version = c.execute("SELECT id FROM version WHERE submission_id = ? AND version_number = 1", (sid,)).fetchone()[0]
            got = {r["rule_id"] for r in c.execute("SELECT rule_id FROM flag WHERE version_id = ?", (version,))}
            assert got == rules_expected, sid


def test_dismissal_on_a_partner_item_is_recorded_in_the_audit_trail(client):
    client.cookies.set("role", "reviewer")
    r = client.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "Explanatory use."},
                    follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/review/12").text
    trail = html.split('id="trail-heading"')[1]
    assert "BlueLeaf Media" in trail and "Explanatory use." in trail and "Alex Rivera" in trail
    assert "Flags (0)" in html or "No open flags" in html


def test_a_partner_cannot_dismiss_even_with_a_valid_note(aclient):
    r = aclient.post("/review/12/dismiss", data={"rule_id": "R4", "version": "1", "note": "x"}, follow_redirects=False)
    assert r.status_code == 403
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM flag_dismissal").fetchone()[0] == 1  # the seed's own, on #13


def test_decision_rows_on_partner_items_cannot_be_edited(aclient):
    from tests.test_affiliate_submit import prep
    prep(aclient)
    with db.connect() as c:
        with pytest.raises(Exception):
            c.execute("UPDATE decision SET reason = 'edited' WHERE submission_id = 3")


def test_an_existing_database_from_before_partners_still_boots(db_path):
    """Same schema, old ownership: nothing is migrated, nothing reseeded, and the old rows display."""
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        pass
    with db.connect() as conn:
        conn.execute("UPDATE submission SET submitted_by = 'Maya Chen' WHERE id IN (3, 12)")
    with TestClient(app) as c:
        with db.connect() as conn:
            assert owners(conn)[3] == "Maya Chen"  # startup did not reseed over the old data
        assert c.get("/", headers={"Cookie": "role=reviewer"}).status_code == 200
        assert "Mortgage prequal landing page" in c.get("/mine", headers={"Cookie": "role=marketer"}).text
        # old rows owned by a marketer are simply not partner items
        assert queue_ids(c.get("/?source=partners").text) == []
