"""Deprecated compatibility wrapper for the demonstration collector.

Use ``scripts/collect_demonstrations.py`` for both no-motion previews and
safety-gated real collection. This wrapper preserves existing operator
commands while the public entry point transitions to the clearer name.
"""

from __future__ import annotations

import sys

if __package__:
    from scripts.collect_demonstrations import main
else:
    from collect_demonstrations import main


if __name__ == "__main__":
    print(
        "DEPRECATED: use scripts/collect_demonstrations.py; forwarding all arguments.",
        file=sys.stderr,
    )
    raise SystemExit(main())
