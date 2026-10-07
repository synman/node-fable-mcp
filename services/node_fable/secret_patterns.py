"""High-confidence secret patterns the scrub chokepoint refuses on.

Each entry is (compiled_regex, class_label). The label names the pattern CLASS
only: a refusal never echoes the matched value. The list is deliberately short
and precise; a broad pattern would refuse ordinary prose.

``NODE_FABLE_SECRET_PATTERNS_PATH`` points at a Python file that defines its own
``SECRET_PATTERNS`` list of the same shape. When set, that list replaces this
one, so a team can keep a single shared source of truth.
"""

import re

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "secret API key (sk-...)"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (
        re.compile(
            r"""(?i)\b(password|passwd|secret|api[_-]?key|auth[_-]?token)\b\s*[:=]\s*["'][^"'\s]{8,}["']"""
        ),
        "hardcoded credential literal",
    ),
]
