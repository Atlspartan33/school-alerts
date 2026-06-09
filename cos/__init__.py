"""Family Chief of Staff - core package."""

import os

# Secrets set via shell pipes can pick up a UTF-8 BOM (U+FEFF) or stray
# CR/LF; those corrupt HTTP headers in ways that surface as confusing
# connection errors. Every credential read goes through clean_env.
_STRIP_CHARS = "﻿ \t\r\n"


def clean_env(name: str) -> str:
    """Read an env var stripped of whitespace and BOM."""
    return (os.environ.get(name) or "").strip(_STRIP_CHARS)
