"""Score de retorno esperado por clique.

A metrica final e "retorno esperado por clique" (EPC) em reais:

    epc = comissao_por_venda * cvr_estimado

`comissao_por_venda` vem direto da plataforma e e um dado duro.
`cvr_estimado` e uma heuristica de bootstrap: a Shopee nao expoe taxa de
conversao por produto, entao ela e derivada de sinais publicos (vendas,
rating, desconto, preco). Isso e uma ESTIMATIVA, nao verdade -- por isso o
campo `calibrado` existe: quando houver historico proprio de conversao vindo
do conversionReport, a estimativa deve ser substituida pelo CVR real.
"""

from __future__ import annotations

import math

from .config import ScoringConfig
from .models import Avaliacao, Oferta

RATING_MAXIMO = 5.0


def _clamp(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


def _popularidade(vendas: int | None, cfg: ScoringConfig) -> float:
    if not vendas or vendas <= 0:
        return cfg.popularidade_min
    bruto = math.log1p(vendas) / math.log1p(cfg.vendas_referencia)
    return _clamp(bruto, cfg.popularidade_min, cfg.popularidade_max)


def _confianca(rating: float | None, cfg: ScoringConfig) -> float:
    if rating is None or rating <= 0:
        return cfg.confianca_sem_rating
    return _clamp(rating / RATING_MAXIMO, 0.0, 1.0)


def _urgencia(desconto_pct: float | None, cfg: ScoringConfig) -> float:
    desconto = _clamp((desconto_pct or 0.0) / 100.0, 0.0, 1.0)
    return 1.0 + cfg.peso_desconto * desconto


def _atrito_preco(preco: float, cfg: ScoringConfig) -> float:
    if preco <= cfg.preco_referencia:
        return 1.0
    return (cfg.preco_referencia / preco) ** cfg.elasticidade_preco


def estimar_cvr(oferta: Oferta, cfg: ScoringConfig) -> tuple[float, dict[str, float]]:
    componentes = {
        "popularidade": _popularidade(oferta.vendas, cfg),
        "confianca": _confianca(oferta.rating, cfg),
        "urgencia": _urgencia(oferta.desconto_pct, cfg),
        "atrito_preco": _atrito_preco(oferta.preco, cfg),
    }
    cvr = cfg.cvr_base
    for fator in componentes.values():
        cvr *= fator
    return _clamp(cvr, 0.0, cfg.cvr_maximo), componentes


def score_historico(
    score_medio: float,
    dias_visto: int,
    dias_totais: int,
    dias_desde_ultimo: int,
    peso_persistencia: float = 0.5,
    meia_vida_dias: float = 7.0,
) -> float:
    """Score acumulado ("de todos"), nao apenas a media.

    Premia dois comportamentos que a media sozinha ignora:
    - persistencia: aparecer no topo em muitos dos dias observados;
    - atualidade: oferta vista ontem vale mais que oferta vista mes passado
      (comissao promocional expira).
    """
    cobertura = dias_visto / max(dias_totais, 1)
    atualidade = 0.5 ** (max(dias_desde_ultimo, 0) / meia_vida_dias)
    return round(score_medio * (1 + peso_persistencia * cobertura) * atualidade, 6)


def avaliar(
    oferta: Oferta,
    cfg: ScoringConfig,
    cvr_calibrado: float | None = None,
) -> Avaliacao:
    """Avalia uma oferta. Se `cvr_calibrado` vier, ele tem precedencia."""
    cvr_est, componentes = estimar_cvr(oferta, cfg)
    if cvr_calibrado is not None and cvr_calibrado >= 0:
        cvr = cvr_calibrado
        calibrado = True
    else:
        cvr = cvr_est
        calibrado = False
    componentes["comissao_valor"] = oferta.comissao_valor
    return Avaliacao(
        cvr_estimado=round(cvr, 8),
        score=round(oferta.comissao_valor * cvr, 6),
        componentes={k: round(v, 6) for k, v in componentes.items()},
        calibrado=calibrado,
    )
