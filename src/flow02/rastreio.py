"""Codigos curtos para o redirecionador que conta cliques.

O codigo e deterministico: o mesmo (plataforma, item, canal) sempre gera o
mesmo codigo. Isso importa porque o link ja pode estar publicado num post
antigo -- se o codigo mudasse a cada sincronizacao, os links parariam de
funcionar.
"""

from __future__ import annotations

import hashlib

ALFABETO = "23456789abcdefghijkmnpqrstuvwxyz"  # sem 0/O/1/l, que confundem
TAMANHO = 8


def codigo_curto(plataforma: str, item_id: str, canal: str = "") -> str:
    semente = f"{plataforma}|{item_id}|{canal}".encode("utf-8")
    digest = hashlib.sha256(semente).digest()
    valor = int.from_bytes(digest[:8], "big")
    letras = []
    for _ in range(TAMANHO):
        valor, resto = divmod(valor, len(ALFABETO))
        letras.append(ALFABETO[resto])
    return "".join(letras)


def url_rastreada(base: str, codigo: str) -> str:
    """Monta a URL publica do redirecionador."""
    return f"{base.rstrip('/')}/r/{codigo}"
