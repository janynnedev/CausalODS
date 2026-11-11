"""Credential boundary for presentation exports and live explanations."""
from __future__ import annotations

import os
import re

from platform_v3.serialization import canonical_json


def checked_json(value) -> str:
    text = canonical_json(value)
    secret = os.environ.get("OPENAI_API_KEY")
    if (secret and secret in text) or re.search(
        r"OPENAI_API_KEY|authorization|bearer\s+", text, flags=re.IGNORECASE
    ):
        raise ValueError("Sensitive content rejected")
    return text
