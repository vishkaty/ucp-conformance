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

"""UCP version string parsing (YYYY-MM-DD).

Error codes are lowercase (`version_invalid_format`): error_code.json is an open
string whose examples are all lowercase and the 08-25 NEG table names
`version_unsupported` in lowercase -- the inherited upper-case codes were a golden
bug (decision 20, D3-02).
"""

import datetime
import re

from exceptions import UcpVersionError

_UCP_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# `version=` in UCP-Agent, quoted or bare, at the start or after a `;`, any case.
_AGENT_VERSION_RE = re.compile(
  r"(?:^|;)\s*version=(?:\"([^\"]+)\"|([^;]+))", re.IGNORECASE
)


def extract_agent_version(ucp_agent: str | None) -> str | None:
  """The `version=` parameter of a UCP-Agent header value, or None when the
  header or the parameter is absent. Shared by request-time negotiation
  (dependencies.validate_ucp_headers) and the version-projection middleware
  (server.py) so both agree on what the platform asked for."""
  if not ucp_agent:
    return None
  match = _AGENT_VERSION_RE.search(ucp_agent)
  if not match:
    return None
  return (match.group(1) or match.group(2)).strip()


def parse_ucp_version(version: str) -> datetime.date:
  """Parse a UCP version string in YYYY-MM-DD format.

  Args:
    version: The version string to parse.

  Returns:
    A datetime.date representing the version.

  Raises:
    UcpVersionError: If the string is not a valid YYYY-MM-DD calendar date.
    No provision for other formats supported like YYYY-MM-DDTHH:MM:SSZ

  """
  version = version.strip()
  if not _UCP_VERSION_RE.fullmatch(version):
    raise UcpVersionError(
      f"Version '{version}' is invalid. Expected YYYY-MM-DD.",
      code="version_invalid_format",
    )

  try:
    return datetime.date.fromisoformat(version)
  except ValueError as exc:
    raise UcpVersionError(
      f"Version '{version}' is invalid. Expected YYYY-MM-DD.",
      code="version_invalid_format",
    ) from exc
