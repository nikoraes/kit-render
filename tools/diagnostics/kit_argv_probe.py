"""What does Kit actually pass to a --exec script?

Print sys.argv as early as possible, before any GPU work, then quit. Run the
same way twice -- with and without a `--` separator -- to see which form
delivers arguments to the script.
"""
import sys

print("PROBE argv=%r" % (sys.argv,), flush=True)

import omni.kit.app
omni.kit.app.get_app().post_quit(0)
