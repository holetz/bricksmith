"""The kebab identity key — the one text transform every front shares.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import re
import unicodedata


def slugify(name: str) -> str:
    """A kebab identity key, in the language the repo actually writes in.

    UNICODE IS NORMALISED AND STRIPPED FIRST, and that is the whole of it. Without it the
    `[^a-z0-9]+` below treats every accented letter as a SEPARATOR, so `criação` reduced to
    `cria-o` and `avaliar-o-fluxo-de-criacao-de-specs` came out
    `avaliar-o-fluxo-de-cria-o-de-specs` — an identity key that is neither readable nor
    guessable, in a repository whose declared harness language is pt-BR. NFD splits a letter
    from its combining marks and the marks are then dropped, so `ç` becomes `c` and `ã`
    becomes `a`: exactly the transliteration a human types when the accent is unavailable.

    A character that does not decompose (`ß`, `ø`) still falls to the separator rule. That is
    a real limit and it is left alone rather than papered over with a lookup table nobody
    maintains — the languages this front is used in are covered, and a slug that loses a
    letter is visible the moment it is printed."""
    folded = unicodedata.normalize("NFD", name.strip().lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return re.sub(r"-{2,}", "-", s)
