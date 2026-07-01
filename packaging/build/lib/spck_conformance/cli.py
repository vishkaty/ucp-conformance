"""
spck-conformance CLI entry point.

Point it at any UCP server for an honest, capability-scoped conformance report:

    spck-conformance --server https://api.example.com [--config merchant.json]
                     [--json] [--junit report.xml]

Exit code 2 if any MUST deviates, else 0. This wraps the bundled merchant runner
(a copy of conformance/checks/merchant.py, kept in sync via packaging/sync_bundle.sh).
The profile-schema check needs the native ucp-schema validator, which is not shipped
in the wheel, so it reports `not-tested` here — everything else runs.
"""
import sys, pathlib

def main():
    checks = pathlib.Path(__file__).resolve().parent / "_bundle" / "conformance" / "checks"
    if not checks.is_dir():
        print("spck-conformance: bundled runner missing; reinstall the package", file=sys.stderr)
        return 1
    sys.path.insert(0, str(checks))
    import merchant                     # resolves engine/verdict_gate/register via bundle paths
    return merchant.main()

if __name__ == "__main__":
    sys.exit(main())
