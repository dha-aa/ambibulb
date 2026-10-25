"""Enable `python3 -m ambibulb ...`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
