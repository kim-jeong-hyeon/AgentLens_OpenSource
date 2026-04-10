# Copyright 2026 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities for agents."""

import ast
import json
import re
from typing import Any


def extract_json(s: str) -> dict[str, Any] | None:
  """Extracts JSON from string using brace-depth matching.

  Handles nested objects (e.g. visualization dicts inside action dicts).

  Args:
    s: A string with a JSON in it. E.g., "{'hello': 'world'}" or from CoT:
      "let's think step-by-step, ..., {'hello': 'world'}".

  Returns:
    JSON object.
  """
  start = s.find('{')
  if start == -1:
    return None
  depth = 0
  for i in range(start, len(s)):
    if s[i] == '{':
      depth += 1
    elif s[i] == '}':
      depth -= 1
      if depth == 0:
        candidate = s[start:i + 1]
        try:
          return ast.literal_eval(candidate)
        except (SyntaxError, ValueError):
          try:
            return json.loads(candidate)
          except (json.JSONDecodeError, ValueError) as error:
            print(
                f'Cannot extract JSON, skipping due to error {error}'
            )
            return None
  return None
