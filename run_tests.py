#!/usr/bin/env python3
"""Run the Plex2iPod test suite.

    python3 run_tests.py              # everything
    python3 run_tests.py -v           # per-test names
    python3 run_tests.py test_sync    # one module
    python3 run_tests.py --no-gui     # skip tests that need a display

Standard library only — no pytest, matching the app itself. Tests that
need Tk are skipped automatically when no display is available.
"""

import argparse
import os
import sys
import unittest

TESTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*",
                        help="specific test modules, e.g. test_sync")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print each test name")
    parser.add_argument("--no-gui", action="store_true",
                        help="skip tests that require a Tk display")
    args = parser.parse_args()

    if args.no_gui:
        # helpers.tk_available() consults this before probing for a display.
        os.environ["PLEX2IPOD_SKIP_GUI"] = "1"

    # tests/ imports its own helpers module by plain name.
    sys.path.insert(0, TESTS_DIR)

    loader = unittest.TestLoader()
    if args.modules:
        names = [m[:-3] if m.endswith(".py") else m for m in args.modules]
        suite = loader.loadTestsFromNames(names)
    else:
        suite = loader.discover(TESTS_DIR, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)

    skipped = len(result.skipped)
    if skipped:
        print("\n%d test(s) skipped:" % skipped)
        seen = set()
        for case, reason in result.skipped:
            if reason not in seen:
                seen.add(reason)
                print("  - %s" % reason)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
