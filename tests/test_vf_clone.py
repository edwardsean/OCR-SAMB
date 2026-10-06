"""vlm-first: the cloned pages point at the same original images as v1, labels keep v1's pile,
and v1's database can only be read from here."""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from common import config, db

pytestmark = [pytest.mark.skipif(os.environ.get("PIPELINE") != "vlm-first", reason="vlm-first only")]
from _sample import BID, needs_sample  # noqa: E402  (skips without the sample scan)
pytestmark.append(needs_sample)


def main_db():
    return psycopg.connect(config.required("MAIN_DATABASE_URL"), row_factory=dict_row)


def test_same_original_images_as_v1():
    with db.connect() as c:
        vf = {r["page_no"]: r for r in c.execute(
            "SELECT page_no, image_path, original_path FROM staging.page WHERE batch_id=%s", (BID,))}
    with main_db() as m:
        v1 = {r["page_no"]: r for r in m.execute(
            "SELECT page_no, image_path, original_path FROM staging.page WHERE batch_id=%s AND page_no = ANY(%s)",
            (BID, list(vf)))}
    assert set(range(1, 32)) <= set(vf), "pages 1-31 must all be cloned"
    assert {n: (r["image_path"], r["original_path"]) for n, r in vf.items()} == \
           {n: (r["image_path"], r["original_path"]) for n, r in v1.items()}


def test_labels_keep_their_pile():
    with db.connect() as c:
        vf = {r["page_no"]: (r["label"], r["pile"]) for r in c.execute(
            "SELECT page_no, label::text, pile FROM staging.type_label WHERE batch_id=%s", (BID,))}
    with main_db() as m:
        v1 = {r["page_no"]: (r["label"], r["pile"]) for r in m.execute(
            "SELECT page_no, label::text, pile FROM staging.type_label WHERE batch_id=%s AND page_no = ANY(%s)",
            (BID, list(vf) or [0]))}
    for n, (label, pile) in v1.items():
        assert vf[n][1] == pile, f"page {n}: pile changed"


def test_v1_database_is_read_only_from_here():
    with main_db() as m:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            m.execute("UPDATE staging.page SET page_no = page_no WHERE false")
