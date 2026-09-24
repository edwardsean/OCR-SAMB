"""Publisher. Phase 0: reports health. Satellite rows + SOR PDF arrive in phase 8."""
import time
from common import health

if __name__ == "__main__":
    health.serve("publisher", role="typed rows + SOR PDF")
    print("publisher ready (phase 0: idle)")
    while True:
        time.sleep(3600)
