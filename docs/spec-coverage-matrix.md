# UCP Conformance Coverage Matrix

_Every MUST is CHECK (has a kill-rate check), EXEMPT (documented), or GAP (unaccounted)._

_CHECK is split by EVIDENCE CLASS — live-wire (kill-tested against an independently-authored server), fixture-schema (our fixture through the official ucp-schema oracle), fixture-crypto (self-signed primitive self-test), self-referenced (only our own fixture; no independent oracle or target — the fixture-circularity class, conformance#79)._


## 2026-01-11 — 89% accounted (121 check · 38 exempt · 19 gap of 178 MUSTs)

- CHECK by evidence: live-wire 49 · fixture-schema 16 · fixture-crypto 0 · self-referenced 56
- GAP/manual: ERR-008, FUL-017
- GAP/needs-receiver: A2A-001, CHK-015, CHK-039, MCP-002, NEG-009, ORD-011, ORD-017, PAY-018, PAY-022, PAY-024, PAY-025, PAY-028, PAY-030, PAY-031, PAY-032, PAY-033, PAY-034

## 2026-01-23 — 89% accounted (129 check · 39 exempt · 20 gap of 188 MUSTs)

- CHECK by evidence: live-wire 53 · fixture-schema 22 · fixture-crypto 0 · self-referenced 54
- GAP/manual: ERR-008, FUL-017
- GAP/needs-receiver: A2A-001, CHK-015, CHK-039, MCP-002, MCP-003, NEG-009, ORD-011, ORD-017, PAY-018, PAY-022, PAY-024, PAY-025, PAY-028, PAY-030, PAY-031, PAY-032, PAY-033, PAY-034

## 2026-04-08 — 98% accounted (276 check · 81 exempt · 7 gap of 364 MUSTs)

- CHECK by evidence: live-wire 48 · fixture-schema 73 · fixture-crypto 4 · self-referenced 151
- GAP/manual: CAT-035, ERR-006, ERR-034, FUL-017, IDL-062, OVR-013, OVR-014
