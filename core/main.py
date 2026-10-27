# AceNix — the original prototype now delegates to reusable components.
from pathlib import Path
import sys

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acenix.cli import main

# VERSION
from acenix import __version__ as version
name = 'AceNix'

# MAIN — read the selected config.sys and actual NixOS configuration.
# VALUES and READING AND VALIDATING now live in acenix/rules.py.
# NIXOS INTEGRATION uses native evaluation in acenix/nixos.py.

# EXTRAS
# Fastfetch stays accessible: python3 core/main.py setr:fastfetch status
# Its user-only backup/restore code lives in acenix/fastfetch.py.

if __name__ == '__main__':
    raise SystemExit(main())
