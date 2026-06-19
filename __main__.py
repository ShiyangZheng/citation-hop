"""Allow ``python -m citation_hop`` to launch the menu-bar app."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
