"""SmolVLA live observe-only entrypoint.

The observation and no-motion implementation is shared with the existing ACT
dry-run script; only the server URL needs to point at port 8089 by default.
"""

from __future__ import annotations

import sys

from dry_run_act_observe_infer import main

if __name__ == "__main__":
    if not any(argument == "--infer-url" or argument.startswith("--infer-url=") for argument in sys.argv):
        sys.argv.extend(("--infer-url", "http://127.0.0.1:8089"))
    sys.exit(main())
