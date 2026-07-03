#!/usr/bin/env python3
"""
run_schema_04_08.py — the `schema-04-08` gate runner: discovers every
schema_check_04_08*.py sibling module (the base file plus per-area modules added by
parallel work) and runs BOTH check kinds each may export:
  run()                 payload checks (valid + per-defect negatives = kill proof)
  run_resolve_checks()  resolver-level checks (lifecycle-omit annotations)

Exit codes: 0 = every check clean-pass + kill-safe; 1 = any failure;
2 = schema oracle unavailable (honest skip).
"""
import sys, glob, pathlib, importlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "selfcheck"))


def main():
    allok, any_ran = True, False
    for f in sorted(glob.glob(str(HERE / "schema_check_04_08*.py"))):
        name = pathlib.Path(f).stem
        mod = importlib.import_module(name)
        results = []
        for runner in ("run", "run_resolve_checks"):
            fn = getattr(mod, runner, None)
            if not fn:
                continue
            res, avail = fn()
            if not avail:
                print("oracle unavailable — skip")
                return 2
            results += res
        if results:
            any_ran = True
            print(f"[{name}]")
        for c, ok, detail in results:
            print(f"  {'✓' if ok else '✗'} {c.id} ({','.join(c.req_ids)}): {detail}")
            allok = allok and ok
    if not any_ran:
        print("no schema_check_04_08 modules found — nothing to gate")
        return 1
    print("PASS" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
