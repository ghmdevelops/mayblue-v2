"""Contrato comum das fontes de oferta.

Cada plataforma e um adapter independente. Se uma fonte falhar (o Mercado
Livre e instavel por natureza, ver mercadolivre.py), as outras continuam
rodando -- por isso o orquestrador trata cada fonte isoladamente.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..config import Config, FiltrosConfig
from ..models import Oferta


class FonteIndisponivel(RuntimeError):
    """A fonte nao pode operar (credencial ausente, API restrita, etc)."""


class Fonte(Protocol):
    nome: str

    def disponivel(self, cfg: Config) -> tuple[bool, str]:
        """Retorna (pode_rodar, motivo)."""

    def coletar(self, cfg: Config, cache=None) -> list[Oferta]:
        """`cache` guarda paginas ja obtidas para permitir retomar a coleta.

        Opcional: fonte que nao pagina API (como o CSV do Mercado Livre)
        simplesmente ignora.
        """


def aplicar_filtros(ofertas: Iterable[Oferta], filtros: FiltrosConfig) -> list[Oferta]:
    aprovadas = []
    for oferta in ofertas:
        if oferta.taxa_comissao < filtros.taxa_comissao_min:
            continue
        if not filtros.preco_min <= oferta.preco <= filtros.preco_max:
            continue
        if filtros.vendas_min and (oferta.vendas or 0) < filtros.vendas_min:
            continue
        if filtros.rating_min and oferta.rating is not None and oferta.rating > 0:
            if oferta.rating < filtros.rating_min:
                continue
        if filtros.exigir_link_oferta and not oferta.link_oferta:
            continue
        aprovadas.append(oferta)
    return aprovadas


def deduplicar(ofertas: Iterable[Oferta]) -> list[Oferta]:
    """Mantem uma oferta por chave, preferindo a de maior comissao em reais."""
    melhores: dict[str, Oferta] = {}
    for oferta in ofertas:
        atual = melhores.get(oferta.chave)
        if atual is None or oferta.comissao_valor > atual.comissao_valor:
            melhores[oferta.chave] = oferta
    return list(melhores.values())
