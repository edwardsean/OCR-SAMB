"""Run phase 2 on hand-labelled pages and print the raw metrics used to set thresholds.

python -m worker.calibrate <batch> <pages, e.g. 1,4,15>"""
import io, re, sys, json
import numpy as np
from PIL import Image
from common import config, storage
from worker import enhance

BATCH = sys.argv[1]
pages = [int(x) for x in sys.argv[2].split(",")]
PREFIX = config.STORAGE_PREFIX
print(f"{'pg':>3} {'rot':>4} {'osd':>5} {'skew':>5} {'band':>5} {'speck':>5} {'variant':>11} {'conf':>5} {'chars':>5}  flags / checks                    secs")
for n in pages:
    o = storage.client().get_object(storage.bucket(), f"{PREFIX}pages/{BATCH}/original/p{n:03d}.png")
    a = np.array(Image.open(io.BytesIO(o.read())).convert("L")); o.close()
    _, _, r = enhance.process(a)
    flat = re.sub(r"[^A-Z0-9]", "", r["classical_text"].upper())
    checks = []
    if "FAKTURPENJUALAN" in flat: checks.append("title")
    m = re.search(r"SOR2611\d{7}", flat)
    if m: checks.append(m.group(0))
    if r["qr_text"]: checks.append("QR=" + r["qr_text"][:40])
    print(f"{n:>3} {r['rotation']:>4} {r['osd_conf']:>5} {r['skew_angle']:>5} {r['dark_band_ratio']:>5} {r['speckle_ratio']:>5} "
          f"{r['ocr_variant']:>11} {r['ocr_conf']:>5.0f} {r['confident_chars']:>5}  {','.join(r['quality_flags']) or '-':24} {' '.join(checks):32} {r['ms_enhance_ocr']/1000:.1f}")
