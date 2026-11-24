"""Bounded display text from explicit user-facing telemetry fields."""

import re


CREDENTIAL_NAME = (
    r"(?:[A-Za-z0-9_-]{0,64}[_-])?(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|session[_ -]?token|token|"
    r"password|passwd|session[_ -]?key|private[_ -]?key|secret|credentials?|authorization)"
)
PRIVATE_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]+-----[\s\S]*?(?:-----END [A-Z0-9 ]+-----|\Z)"
    r"|<(system(?:_prompt)?|developer|internal|thinking|analysis)\b[^>]*>[\s\S]*?(?:</\1\s*>|\Z)",
    re.IGNORECASE,
)
SECRET = re.compile(
    r"(?<![\w-])(?:sk[-_]|gh[pousr]_|github_pat_|glpat-|hf_|xox[baprs]-|AIza|AKIA|eyJ)[A-Za-z0-9._-]{8,}"
    r"|\b(?:authorization|proxy-authorization)\s*[:=][^\r\n]+"
    r"|\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    rf"|(?<![\w-]){CREDENTIAL_NAME}\s*[:=]\s*"
    r"(?:\"[^\n\"]*\"|'[^\n']*'|[^\s;,]+)"
    r"|\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/@]+:[^\s/@]+@[^\s]+",
    re.IGNORECASE,
)
SENSITIVE_LINK = re.compile(r"(https?://[^\s?<>\"']+)\?[^\s<>\"']+", re.IGNORECASE)
LOCAL_PATH = re.compile(r"(?<![\w:/])(?:/(?:home|root|run/user|tmp|var/lib)/[^\s<>\"']+|[A-Z]:\\[^\r\n<>\"']+)")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
UNSAFE_FIELD = re.compile(
    r"(?<![\w-])(?:system[_ -]?prompt|developer[_ -]?message|(?:set[_ -]?)?cookie|passphrase)"
    r"(?:\\?[\"'])?\s*[:=]"
    rf"|(?<![\w-]){CREDENTIAL_NAME}\\?[\"']\s*[:=]"
    r"|(?<![\w-])role(?:\\?[\"'])?\s*:\s*(?:\\?[\"'])?(?:system|developer)\b"
    r"|<\|(?:im_start|im_end|system|developer)\|>",
    re.I,
)


def display_text(value, limit=3600, *, single_line=False):
    if not isinstance(value, str) or len(value) > 262144:
        return None
    content = CONTROL.sub("", value)
    if UNSAFE_FIELD.search(content):
        return "[redacted]"
    content = PRIVATE_BLOCK.sub("[redacted]", content)
    content = SECRET.sub("[redacted]", content)
    content = SENSITIVE_LINK.sub(r"\1?[redacted]", content)
    content = LOCAL_PATH.sub("[local path]", content)
    if single_line:
        content = " ".join(content.split())
    content = content.strip()
    if len(content) > limit:
        return content[:limit].rstrip() + "…"
    return content or None
