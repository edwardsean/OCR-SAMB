"""The 288-page sample scan (real customer documents, never committed), mounted at /data/sample.pdf (SAMPLE_PDF in
.env). A fresh clone doesn't have it: the tests that read it skip (needs_sample) instead of failing to load."""
import hashlib
import os

import pytest

SAMPLE = "/data/sample.pdf"
HAVE = os.path.isfile(SAMPLE)
BID = "b-" + hashlib.sha256(open(SAMPLE, "rb").read()).hexdigest()[:10] if HAVE else None
needs_sample = pytest.mark.skipif(not HAVE, reason="needs the sample scan (SAMPLE_PDF): real customer documents, "
                                                   "not in the repository")
