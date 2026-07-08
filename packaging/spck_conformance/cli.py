"""
spck-conformance CLI entry point — two-sided (merchant + agent).

Merchant platforms — grade your UCP server against the spec:

    spck-conformance --server https://api.example.com [--config merchant.json]
                     [--json] [--junit report.xml]

Shopping agents — run the agent-conformance lane (the reverse harness: a reference
agent shops an adversarial sandbox; every agent check must pass a conformant agent
and catch its targeted defect):

    spck-conformance --agent [--json]

Merchant exit code is 2 if any MUST deviates, else 0; agent exit code is 1 if any
check is unsound, else 0. Both wrap bundled copies of conformance/ kept in sync via
packaging/sync_bundle.sh. The merchant profile-schema check needs the native
ucp-schema validator (not shipped in the wheel), so it reports `not-tested` here.
"""
import sys, pathlib

BUNDLE = pathlib.Path(__file__).resolve().parent / "_bundle" / "conformance"


def _run_agent():
    agent = BUNDLE / "agent"
    if not agent.is_dir():
        print("spck-conformance: agent lane not bundled; reinstall the package", file=sys.stderr)
        return 1
    sys.path.insert(0, str(agent))       # agent modules add parent (conformance/) for `common`
    try:
        sys.argv.remove("--agent")       # so run_agent's own argparse (--json) is happy
    except ValueError:
        pass
    import run_agent
    return run_agent.main()


def main():
    if "--agent" in sys.argv:
        return _run_agent()
    checks = BUNDLE / "checks"
    if not checks.is_dir():
        print("spck-conformance: bundled runner missing; reinstall the package", file=sys.stderr)
        return 1
    sys.path.insert(0, str(checks))
    import merchant                     # resolves engine/verdict_gate/register via bundle paths
    return merchant.main()


if __name__ == "__main__":
    sys.exit(main())
