# AceNix — keep the original root entry point working.
from pathlib import Path
import sys
from core.main import main, name, version

# MAIN — read the test configuration next to this Python file when launched
# without CLI arguments, preserving the original prototype preview workflow.
if __name__ == '__main__':
    arguments = sys.argv[1:] or ['--rules', str(Path(__file__).with_name('config.sys')), 'preview']
    raise SystemExit(main(arguments))

# EXTRAS
# Fastfetch: python3 main.py setr:fastfetch status
