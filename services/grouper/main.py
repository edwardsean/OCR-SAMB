"""Grouping worker. Phase 0: reports health. Fan-in + grouping arrive in phase 6."""
import time
from common import health

if __name__ == "__main__":
    health.serve("grouper", role="per batch: pages → docs → SOR")
    print("grouper ready (phase 0: idle)")
    while True:
        time.sleep(3600)
