#   Copyright 2026 UCP Authors
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""Unit tests for UCP version parsing."""

import datetime
import unittest

from exceptions import UcpVersionError
from ucp_version import parse_ucp_version


class UcpVersionTest(unittest.TestCase):
  """Tests parse_ucp_version behavior."""

  def test_parse_valid_date(self) -> None:
    """Test parsing a valid YYYY-MM-DD date."""
    parsed = parse_ucp_version("2026-01-23")
    self.assertEqual(parsed, datetime.date(2026, 1, 23))

  def test_parse_strips_whitespace(self) -> None:
    """Test that leading/trailing whitespace is stripped before parsing."""
    parsed = parse_ucp_version(" 2026-01-23 ")
    self.assertEqual(parsed, datetime.date(2026, 1, 23))

  def test_parse_rejects_invalid_format(self) -> None:
    """Test that invalid formats raise UcpVersionError."""
    with self.assertRaises(UcpVersionError) as exc:
      parse_ucp_version("2026/01/23")
    self.assertEqual(exc.exception.code, "VERSION_INVALID_FORMAT")

  def test_parse_rejects_invalid_calendar_date(self) -> None:
    """Test that invalid calendar dates (e.g. Feb 30) raise UcpVersionError."""
    with self.assertRaises(UcpVersionError) as exc:
      parse_ucp_version("2026-02-30")
    self.assertEqual(exc.exception.code, "VERSION_INVALID_FORMAT")

  def test_parse_rejects_datetime_format(self) -> None:
    """Test that datetime formats (with time component) are rejected."""
    with self.assertRaises(UcpVersionError) as exc:
      parse_ucp_version("2026-01-23T10:11:12Z")
    self.assertEqual(exc.exception.code, "VERSION_INVALID_FORMAT")


if __name__ == "__main__":
  unittest.main()


# ---------------------------------------------------------------------------
# C3 negotiation (D3-02): the served version set is {server_version} ∪
# supported_versions; anything else is 422 `version_unsupported` (lowercase,
# decision 20). The inherited check only rejected NEWER versions, so an OLDER,
# never-advertised version (2026-01-23) was silently accepted.
# ---------------------------------------------------------------------------


def _booted_app():
  """An IntegrationTest instance booted for its TestClient + payload helpers.
  Imported inside the function on purpose: a module-level import would make
  pytest collect the whole IntegrationTest suite a second time here."""
  from integration_test import IntegrationTest

  t = IntegrationTest()
  t.setUp()
  return t


def _post_create(t, version: str | None):
  payload = t._create_checkout_payload("neg_1", [("rose", "Red Rose", 1000, 1)])
  headers = t._get_headers()
  agent = 'profile="https://agent.example/profile"'
  if version is not None:
    agent += f'; version="{version}"'
  headers["UCP-Agent"] = agent
  with t.client:
    return t.client.post(
      "/checkout-sessions",
      headers=headers,
      json=payload.model_dump(mode="json", exclude_none=True),
    )


def test_unadvertised_version_rejected():
  t = _booted_app()
  try:
    resp = _post_create(t, "2026-01-23")
    assert resp.status_code == 422, (resp.status_code, resp.text)
    body = resp.json()
    assert body["ucp"]["status"] == "error"
    assert body["messages"][0]["code"] == "version_unsupported"

    resp = _post_create(t, "2026-08-25")
    assert resp.status_code == 201, (resp.status_code, resp.text)
  finally:
    t.tearDown()
