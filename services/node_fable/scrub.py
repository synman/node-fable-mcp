"""Scrub chokepoint — secret refusal + jargon neutralization + aliasing.

``scan_or_refuse(text)`` runs immediately before EVERY subprocess spawn
(fresh, resume, any account/tier) on the fully assembled prompt (constructed
system prompt + caller brief). A high-confidence secret-pattern hit REFUSES
the whole call with a structured error naming the pattern CLASS only — the
matched value is withheld and never redact-and-sent (a secret is
never a candidate for output; the leak vector is selection, not emission).

Secret patterns live in node_fable/secret_patterns.py. Set
NODE_FABLE_SECRET_PATTERNS_PATH to a JSON file (a list of {"pattern", "label"}
objects) to replace them with a shared source.
"""

from __future__ import annotations

import ipaddress
import json
import re

from node_fable import config
from node_fable.errors import FableError

# Vocabulary the PROMPT BUILDER must not emit into constructed role prompts sent
# to an external consumer account is the ``jargon_map`` setting: term ->
# neutral replacement (empty by default). The caller's own brief text is
# NOT rewritten — only refused on secrets. Applied only to builder-constructed
# prompt text (templates + fragments + the plan role's scope param), never to
# the caller's brief or to material/target_repo path listings.

_patterns = None


def _load_pattern_file(path) -> list:
    """Compile a JSON pattern file: a list of ``{"pattern": <regex>, "label": <class>}``
    objects (inline regex flags such as ``(?i)`` go inside the pattern). Any problem
    is a structured error: a half-read list would be a weaker scan, never a guess."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FableError(
            "missing_dependency",
            f"failed to read the secret-pattern file at {path}: {type(exc).__name__}",
        ) from exc
    if not isinstance(raw, list):
        raise FableError(
            "missing_dependency", f"the secret-pattern file at {path} must be a JSON list"
        )
    patterns = []
    for index, entry in enumerate(raw):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("pattern"), str)
            or not isinstance(entry.get("label"), str)
        ):
            raise FableError(
                "missing_dependency",
                f"entry {index} of the secret-pattern file at {path} needs a string "
                '"pattern" and a string "label"',
            )
        try:
            patterns.append((re.compile(entry["pattern"]), entry["label"]))
        except re.error as exc:
            raise FableError(
                "missing_dependency",
                f"entry {index} of the secret-pattern file at {path} is not a valid regex",
            ) from exc
    return patterns


def _load_patterns():
    """The (compiled_regex, label) list: the built-in one, or the override file's. Fail closed."""
    global _patterns
    if _patterns is not None:
        return _patterns
    path = config.secret_patterns_path()
    if path is None:
        from node_fable import secret_patterns

        patterns = secret_patterns.SECRET_PATTERNS
    else:
        if not path.is_file():
            raise FableError(
                "missing_dependency",
                f"secret-pattern file not found at {path}; fix or unset "
                f"{config.ENV_SECRET_PATTERNS_PATH}.",
            )
        patterns = _load_pattern_file(path)
    if not patterns:
        raise FableError(
            "missing_dependency",
            "the secret-pattern list is empty; refusing to run with an empty scan surface (non-vacuous guard).",
        )
    _patterns = patterns
    return patterns


def secret_patterns():
    """The (compiled_regex, label) list the scan uses."""
    return _load_patterns()


def scan_or_refuse(text: str) -> None:
    """Refuse the whole call on any high-confidence secret hit.

    Raises FableError("secret_refused", ...) naming the pattern CLASS (label)
    only; the matched value is withheld. Returns None when clean. Callers
    must invoke this through the module attribute
    (``scrub.scan_or_refuse``) so the chokepoint stays monkeypatchable and
    auditable at one seam.
    """
    if not isinstance(text, str):
        raise FableError("secret_refused", "scrub received a non-string prompt")
    for pattern, label in secret_patterns():
        if pattern.search(text):
            raise FableError(
                "secret_refused",
                f"assembled prompt matches secret pattern class '{label}' — "
                "the call is refused outright (never redact-and-send). Remove "
                "the secret from the brief/material and retry. When quoting a "
                "buggy credential line for diagnosis, replace the value with "
                "a SHORT placeholder like \"***\" — a placeholder of 8+ "
                "unspaced characters still matches the credential shape and "
                "still refuses. The matched value is withheld from this "
                "error.",
                pattern_class=label,
            )
    return None


def scan_mcp_servers_or_refuse(mcp_servers: dict) -> None:
    """Refuse the call if any granted server's own registration matches a
    secret pattern — the ``--mcp-config`` payload's refuse-on-hit gate.

    Same fail-closed posture as ``scan_or_refuse`` (a secret
    is never redacted-and-sent: secret-pattern hits REFUSE the call outright).
    Applied to the same DIRECTION as the assembled prompt — content heading
    INTO the subprocess on argv — never the ECHOED SUBPROCESS OUTPUT
    ``redact_secrets`` exists for (a fundamentally different case: refusing
    there would hide a real error instead of reporting it).

    Scanned PER SERVER, not as one opaque blob, so a hit can be attributed
    to the offending registration by name. These are a registered MCP server's
    OWN config entries (read from the ``mcp_registry_paths`` files,
    forwarded verbatim by ``mcp_grant.generate_grant()`` with no content
    validation) — not something THIS call's caller chose or can fix by
    rewording a brief, so a refusal that could not say WHICH registration
    tripped it would be undiagnosable. Naming the server does not weaken
    the withhold: the matched value itself never appears in the error,
    exactly like ``scan_or_refuse``.
    """
    for name in sorted(mcp_servers):
        text = json.dumps(mcp_servers[name], separators=(",", ":"))
        for pattern, label in secret_patterns():
            if pattern.search(text):
                raise FableError(
                    "secret_refused",
                    f"MCP server '{name}' registration matches secret "
                    f"pattern class '{label}' — the call is refused "
                    "outright (never redact-and-send). "
                    f"Fix the '{name}' server's registration (most likely "
                    "its env block) to use vault-resolved credentials "
                    "instead of an embedded literal, then retry. The "
                    "matched value is withheld from this error.",
                    pattern_class=label,
                    server=name,
                )
    return None


def redact_secrets(text: str) -> str:
    """Redact (not refuse) secret-pattern matches in ECHOED subprocess output.

    Different posture from scan_or_refuse, deliberately: the inbound prompt
    is REFUSED on a hit (the caller chose to send it), but stdout/stderr the
    subprocess emitted on its own error path is not caller-chosen — refusing
    would hide the error, so matches are substituted with their pattern
    class before the text can land in a transcript.
    """
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern, label in secret_patterns():
        out = pattern.sub(f"[redacted:{label}]", out)
    return out


def neutralize_jargon(text: str) -> str:
    """Replace the configured vocabulary in BUILDER-constructed prompt text.

    Case-insensitive, word-ish boundary, terms from the ``jargon_map`` setting
    (term -> replacement). Only the prompt builder calls this.
    "Builder text" includes the role templates, the guard/right-sizing
    fragments, AND the caller's ``scope`` parameter (substituted into the
    plan template before this runs — it is treated as builder text by
    design). The caller's BRIEF and the material/target_repo PATH LISTINGS
    are never rewritten: the brief is refused on secrets only, and paths are
    operational addressing Fable must receive verbatim (a neutralized path
    is a nonexistent path — review finding).
    """
    out = text
    for term, neutral in config.jargon_map().items():
        out = re.sub(
            r"(?i)\b" + re.escape(term.lower()).replace(r"\ ", r"\s+") + r"\b",
            lambda _m, _n=neutral: _n,
            out,
        )
    return out


_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_INTERNAL_TLDS = ("local", "internal", "corp", "lan")


def _internal_host_re() -> re.Pattern:
    tlds = "|".join(re.escape(t) for t in _INTERNAL_TLDS + tuple(config.extra_internal_tlds()))
    return re.compile(
        r"\b[a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9-]+)*\.(?:" + tlds + r")\b",
        re.IGNORECASE,
    )


def alias_internal_names(text: str) -> str:
    """Optional simple hostname/IP aliasing for the caller's brief.

    Off by default (fable_consult alias_internal_names=False). Deterministic
    within one call: each distinct IPv4 / internal hostname maps to a stable
    placeholder (host-ip-N / internal-host-N) in first-seen order. This is a
    courtesy de-identification, NOT a security control — the scrub refusal
    above is the enforcement surface.
    """
    mapping: dict[str, str] = {}

    def _ip_sub(m: re.Match) -> str:
        raw = m.group(0)
        try:
            ipaddress.IPv4Address(raw)
        except ValueError:
            return raw
        if raw not in mapping:
            mapping[raw] = f"host-ip-{sum(1 for v in mapping.values() if v.startswith('host-ip-')) + 1}"
        return mapping[raw]

    def _host_sub(m: re.Match) -> str:
        raw = m.group(0)
        if raw not in mapping:
            mapping[raw] = (
                f"internal-host-{sum(1 for v in mapping.values() if v.startswith('internal-host-')) + 1}"
            )
        return mapping[raw]

    out = _IPV4_RE.sub(_ip_sub, text)
    out = _internal_host_re().sub(_host_sub, out)
    return out
