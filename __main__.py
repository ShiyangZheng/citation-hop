"""Allow ``python -m citation_hop`` to launch the tray app."""

import sys

from .tray import main

if __name__ == "__main__":
    sys.exit(main())
