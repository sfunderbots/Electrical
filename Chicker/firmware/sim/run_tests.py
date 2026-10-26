"""Run the simulator test suite (no pytest needed).

    python -m sim.run_tests                 # everything
    python -m sim.run_tests charge kick     # only files / tests whose name contains a filter
    python -m sim.run_tests --fast          # skip tests marked slow

Files run in level order (low level first). Ends with a transition-coverage report for both
state machines: every row of their TRANSITIONS tables should be exercised by some test.
"""

import importlib
import os
import sys
import time
import traceback

ORDER = ("test_l0_flowcharts", "test_l1_sensors_protocol", "test_l2_kicker_maths", "test_l3_kicker_pio", "test_l4_power_stage",
         "test_l5_charge", "test_l6_modes", "test_l6b_matrix", "test_l7_system", "test_l8_soak", "test_l9_legacy", "test_l10_bench")


def slow(fn):
    fn.slow = True
    return fn


class Skip(Exception):
    pass


def skip(reason):
    """Mark a test as skipped (it is listed with the reason instead of being run)."""
    def wrap(fn):
        def skipped():
            raise Skip(reason)
        skipped.__name__ = fn.__name__
        skipped.__code__ = skipped.__code__.replace(co_firstlineno=fn.__code__.co_firstlineno)
        return skipped
    return wrap


def discover(filters):
    here = os.path.join(os.path.dirname(__file__), "tests")
    names = sorted(f[:-3] for f in os.listdir(here) if f.startswith("test_") and f.endswith(".py"))
    names.sort(key=lambda n: ORDER.index(n) if n in ORDER else 99)
    for mod_name in names:
        mod = importlib.import_module("sim.tests." + mod_name)
        for attr in sorted(vars(mod), key=lambda a: getattr(getattr(mod, a), "__code__", None)
                           and getattr(mod, a).__code__.co_firstlineno or 0):
            fn = getattr(mod, attr)
            if attr.startswith("test_") and callable(fn):
                full = "%s.%s" % (mod_name, attr)
                if not filters or any(f in full for f in filters):
                    yield full, fn


def coverage_report():
    from sim.harness import ALL_COVERAGE
    import charge_sm
    import mode_sm
    lines, missing = [], 0
    for name, mod in (("ChargeSM", charge_sm), ("ModeSM", mode_sm)):
        rows = mod.TRANSITIONS
        hit = 0
        for frm, to, why, desc in rows:
            covered = any(k[0] == name and k[2] == to and k[3] == why and (frm == -1 or k[1] == frm)
                          for k in ALL_COVERAGE)
            if covered:
                hit += 1
            else:
                missing += 1
                fn = "ANY" if frm == -1 else mod.NAMES[frm]
                lines.append("    not covered: %s %s -> %s (%s)" % (name, fn, mod.NAMES[to], why))
        lines.insert(0, "  %s: %d / %d transitions exercised" % (name, hit, len(rows)))
    return lines, missing


def main(argv):
    fast = "--fast" in argv
    filters = [a for a in argv if not a.startswith("--")]
    passed, failed, skipped = 0, [], 0
    t_all = time.time()
    for full, fn in discover(filters):
        if fast and getattr(fn, "slow", False):
            skipped += 1
            continue
        t0 = time.time()
        try:
            fn()
            passed += 1
            print("PASS %-62s %5.1fs" % (full, time.time() - t0), flush=True)
        except Exception as e:      # noqa
            if type(e).__name__ == "Skip":   # by name: the tests import sim.run_tests, this file runs as __main__
                skipped += 1
                print("SKIP %-62s %s" % (full, e), flush=True)
                continue
            failed.append(full)
            print("FAIL %-62s %5.1fs" % (full, time.time() - t0), flush=True)
            traceback.print_exc(limit=6)
    print("-" * 78)
    print("%d passed, %d failed, %d skipped in %.0fs" % (passed, len(failed), skipped, time.time() - t_all))
    if not filters:
        lines, _ = coverage_report()
        print("Transition coverage:")
        print("\n".join(lines))
    for f in failed:
        print("  failed:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
