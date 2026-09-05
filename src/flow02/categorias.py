"""Rotulos legiveis para as categorias da Shopee.

A API devolve `productCatIds` -- so numeros, como 100630. Escolher entre
"100630" e "100664" nao ajuda ninguem, e nao existe endpoint que traduza.

Em vez de chutar nomes de um catalogo que eu nao conheco, o rotulo e
derivado do proprio dado: a palavra mais frequente nos nomes dos produtos
daquela categoria. Se 40 dos 75 itens de uma categoria tem "cortina" ou
"almofada" no nome, "cortina" descreve melhor do que qualquer palpite meu.

O rotulo e uma dica, nao o nome oficial -- por isso aparece junto do id.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Palavras que aparecem em qualquer anuncio e nao distinguem categoria.
RUIDO = frozenset("""
de da do das dos e com para por em no na nos nas o a os as um uma
kit novo nova original oficial premium promocao frete gratis envio full
unidades unidade pecas peca pcs und un ml mg kg gr cm mm litros litro
tamanho cor modelo tipo super mega ultra top qualidade produto item
masculino feminino infantil adulto grande pequeno medio alta baixo
"""  .split())

MINIMO_LETRAS = 4


def _palavras(nome: str):
    texto = unicodedata.normalize("NFD", nome or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    for palavra in re.split(r"[^a-z0-9]+", texto.lower()):
        if len(palavra) >= MINIMO_LETRAS and palavra not in RUIDO \
                and not palavra.isdigit():
            yield palavra


def rotular(nomes) -> str:
    """Duas palavras mais frequentes, que costumam descrever a categoria."""
    contagem = Counter(p for nome in nomes for p in set(_palavras(nome)))
    comuns = [p for p, _ in contagem.most_common(2)]
    return " / ".join(comuns) if comuns else ""


def resumir(linhas) -> list[dict]:
    """Agrupa por categoria e devolve rotulo, contagem e melhor comissao.

    `linhas` sao registros com categoria_ids, nome e comissao_valor.
    """
    grupos: dict[str, list] = {}
    for linha in linhas:
        for identificador in (linha["categoria_ids"] or "").split(","):
            identificador = identificador.strip()
            if identificador and identificador != "0":
                grupos.setdefault(identificador, []).append(linha)

    resumo = []
    for identificador, itens in grupos.items():
        comissoes = [i["comissao_valor"] or 0 for i in itens]
        resumo.append({
            "id": identificador,
            "rotulo": rotular(i["nome"] for i in itens),
            "itens": len(itens),
            "melhor_comissao": round(max(comissoes), 2) if comissoes else 0.0,
            "comissao_media": round(sum(comissoes) / len(comissoes), 2)
            if comissoes else 0.0,
        })
    return sorted(resumo, key=lambda c: -c["itens"])
