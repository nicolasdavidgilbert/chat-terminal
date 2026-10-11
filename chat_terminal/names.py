"""Forma común de los apodos para registro, búsqueda y presentación."""

import unicodedata


def normalize_nick(nick: str) -> str:
    return unicodedata.normalize("NFC", nick.lower())
