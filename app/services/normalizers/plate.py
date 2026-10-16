r"""Turkish vehicle registration plate normalizer.

Adheres to Karayolları Trafik Yönetmeliği plate format:
- 2 digits province code: 01-81 (and general 2-digit patterns \d{2})
- 1 to 3 letters: [A-Z]{1,3}
- 2 to 5 digits: \d{2,5}

Examples:
- "34  HO  3448" -> "34 HO 3448"
- "34TN2229"    -> "34 TN 2229"
- "34-tn-2229"  -> "34 TN 2229"
- "41BR123"     -> "41 BR 123"
- "06BJK123"    -> "06 BJK 123"
- "34a12345"    -> "34 A 12345"
- "34AAA12"     -> "34 AAA 12"
"""

from __future__ import annotations

import re
from typing import Any

# Standard Turkish plate pattern: 2 digits + 1-3 letters + 2-5 digits
_TR_PLATE_RE = re.compile(r"^(\d{2})([A-Z]{1,3})(\d{2,5})$")


def normalize_plate(raw: Any) -> str | None:
    """Normalize raw license plate string to canonical single-space format.

    Returns canonical plate (e.g. '34 HO 3448', '34 TN 2229') or sanitized
    fallback for non-standard inputs, or None if input is empty/missing.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Handle Turkish dotless/dotted i before uppercase conversion:
    # 'i' -> 'I', 'ı' -> 'I', 'İ' -> 'I'
    clean = s.replace("i", "I").replace("ı", "I").replace("İ", "I").replace("I\u0307", "I").upper()
    # Strip any spaces, hyphens, dots, or non-alphanumeric chars
    alphanumeric = re.sub(r"[^A-Z0-9]", "", clean)

    match = _TR_PLATE_RE.match(alphanumeric)
    if match:
        return f"{match.group(1)} {match.group(2)} {match.group(3)}"

    # Fallback for non-standard, custom, or test strings (e.g. "TEST 1")
    collapsed = re.sub(r"\s+", " ", clean).strip()
    return collapsed or None
