"""Capture the Faktur Penjualan layout template from one clear FP page.

    python -m worker.make_fp_template <batch_id> <page_no>

SAMB prints every FP from the same template, so this is a fixed property of SAMB's own document —
in production it comes from SAMB's print layout, not from customer scans. Writes worker/fp_layout.json.
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image

from common import storage
from worker import layout

batch, page = sys.argv[1], int(sys.argv[2])
o = storage.client().get_object(storage.bucket(), f"pages/{batch}/upright/p{page:03d}.png")
fp = layout.fingerprint(np.array(Image.open(io.BytesIO(o.read())).convert("L")))
out = {"source": f"{batch} page {page}", "v": [float(x) for x in fp["v"]], "h": [float(x) for x in fp["h"]]}
path = os.path.join(os.path.dirname(__file__), "fp_layout.json")
json.dump(out, open(path, "w"), indent=1)
print(f"wrote {path}: {len(out['v'])} column lines")
