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

"""server_state.py — the single, lazily-constructed DefectsEngine instance,
shared between the defects middleware (server.py) and the test-only fixture
route (routes/defect_fixtures.py) so both read the same arm state without
loading defects_config.json twice.

Lazy on purpose: constructing DefectsEngine reads config.FLAGS.defects_config,
which absl only finishes parsing once app.main() runs -- building it at import
time would always observe the flag's declared default (None/disabled), even
when a caller passed --defects_config on the command line.
"""
import config
import defects

_ENGINE: defects.DefectsEngine | None = None


def defects_engine() -> defects.DefectsEngine:
  global _ENGINE
  if _ENGINE is None:
    _ENGINE = defects.DefectsEngine(
        config.FLAGS.defects_config, config.FLAGS.defects_state_file
    )
  return _ENGINE
