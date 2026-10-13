"""Load Satellite's sales-order export into satellite.sor and satellite.sor_item. The work is common/satellite_feed.py
(which column comes from which field, and the upsert a live feed from Satellite uses too).

The export is real customer data: it stays on this machine (*.csv is git-ignored) and goes only into the pipeline's
database (PIPELINE_DB). Re-running updates every SO it contains and replaces their lines; SOs it doesn't contain keep
theirs. Afterwards: python -m grouper.group <batch> --recheck, and python -m common.efaktur relink (e-Fakturs waiting
for a billing number; the scheduler also does it every EFAKTUR_EVERY_MINUTES).

  docker compose run --rm -v <folder with the CSVs>:/data/so:ro -v ./scripts:/scripts:ro rtm-api \
      python /scripts/load_satellite.py /data/so
"""
import os
import sys
import time

for path in ("/app", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "services")):
    if os.path.isdir(os.path.join(path, "common")) and path not in sys.path:     # the services' code, in the container
        sys.path.insert(0, path)                                                  # or next to this folder

from common import satellite_feed  # noqa: E402


def main(folder):
    t0 = time.time()
    n = satellite_feed.load_folder(folder)
    print(f"{n['orders']} SOs ({n['with_po_number']} with a customer PO number) and {n['lines']} SO lines loaded; "
          f"{n['lines_without_order']} lines without their SO; {n['only_in_po_file']} SOs only in the PO file (skipped) · "
          f"{time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1])
