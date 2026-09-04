"""Agrupa variacoes do mesmo produto.

Motivo concreto: na lista de oportunidades de 2026-09-04, quatro das vagas
eram o mesmo item:

    Celimax The Vita A Retinal Shot Tightening Booster 15ml
    Celimax the vita A Retinol Shot Tightening Serum 30ml
    Celimax Retinol The Vita A Retinal Shot Tightening
    CELIMAX The Vita A Retinal Shot Tightening Booster, 15

A diversificacao existente e por LOJA, entao nao pega isso: sao anuncios da
mesma loja ou de lojas diferentes para o mesmo produto. O resultado e um
ranking que parece variado e nao e.

A abordagem e deliberadamente simples -- normaliza o nome e compara as
primeiras palavras significativas. Nao tenta ser esperta: agrupar demais
esconderia produto legitimo, o que e pior do que mostrar repetido.
"""

from __future__ import annotations

import re
import unicodedata

# Palavras que nao ajudam a distinguir produto: aparecem em quase todo anuncio
# de marketplace e so atrapalham a comparacao.
RUIDO = frozenset("""
de da do das dos e com para por em no na nos nas o a os as um uma
kit novo nova original oficial premium promocao frete gratis envio
unidades unidade pecas peca pcs und un ml mg kg gr cm mm litros litro
""".split())

PALAVRAS_CHAVE = 4  # quantas palavras significativas formam a assinatura


def normalizar(nome: str) -> str:
    """Minusculas, sem acento e sem pontuacao."""
    texto = unicodedata.normalize("NFD", nome or "")
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", texto.lower())


def assinatura(nome: str, palavras: int = PALAVRAS_CHAVE) -> str:
    """Primeiras palavras significativas, que costumam ser marca e modelo.

    Numero puro entra como ruido porque quase sempre e volume ou quantidade
    ("15ml", "3 unidades") -- justamente o que varia entre variantes do
    mesmo produto.
    """
    significativas = [
        p for p in normalizar(nome).split()
        if p not in RUIDO and not p.isdigit() and len(p) > 2
    ]
    return " ".join(significativas[:palavras])


def agrupar(itens, chave_nome="nome", chave_ordem="epc", palavras=PALAVRAS_CHAVE):
    """Mantem so o melhor de cada grupo, preservando a ordem de entrada.

    Devolve (lista_filtrada, quantos_foram_escondidos). Cada item mantido
    ganha `variantes`, com quantos anuncios ele representa -- a informacao
    nao se perde, so deixa de ocupar espaco.
    """
    melhores: dict[str, dict] = {}
    ordem: list[str] = []

    for item in itens:
        chave = assinatura(item.get(chave_nome) or "", palavras)
        if not chave:  # nome curto demais para agrupar: fica sozinho
            chave = f"__unico__{len(ordem)}"
        atual = melhores.get(chave)
        if atual is None:
            melhores[chave] = dict(item, variantes=1)
            ordem.append(chave)
            continue
        atual["variantes"] += 1
        if (item.get(chave_ordem) or 0) > (atual.get(chave_ordem) or 0):
            # O melhor do grupo assume, sem perder a contagem acumulada.
            melhores[chave] = dict(item, variantes=atual["variantes"])

    resultado = [melhores[c] for c in ordem]
    return resultado, sum(i["variantes"] - 1 for i in resultado)
