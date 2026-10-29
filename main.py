# AceNix — keep the original root entry point working.
import sys
from core.main import main

# MAIN — use the native NixOS target; there is no separate default rule file.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["preview"]))

# EXTRAS
# Fastfetch: acenix setr:fastfetch status
