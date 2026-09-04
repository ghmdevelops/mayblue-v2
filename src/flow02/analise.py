"""Analise sobre os snapshots diarios.

Duas coisas que o ranking sozinho nao mostra:

1. MUDANCA. O ranking diz o que e bom hoje; o alerta diz o que MUDOU hoje.
   Um produto cuja comissao saltou de 4% para 15% e o sinal mais acionavel
   que existe -- e campanha nova, tem prazo, e quase ninguem viu ainda. O
   dado ja estava no banco desde o primeiro dia, so nao era comparado.

2. DESCONTO REAL. O `priceDiscountRate` da Shopee e calculado contra o
   "preco original" declarado pelo vendedor, que costuma ser inflado. Como
   guardamos o preco praticado todo dia, da para comparar o desconto
   anunciado com o desconto de verdade contra a mediana historica.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

from .config import AlertasConfig, PrecoConfig

COMISSAO_SUBIU = "comissao_subiu"
COMISSAO_CAIU = "comissao_caiu"
PRECO_CAIU = "preco_caiu"
PRECO_SUBIU = "preco_subiu"
NOVO_NO_TOP = "novo_no_top"
SUMIU = "sumiu"

ROTULOS = {
    COMISSAO_SUBIU: "COMISSAO SUBIU",
    COMISSAO_CAIU: "comissao caiu",
    PRECO_CAIU: "PRECO CAIU",
    PRECO_SUBIU: "preco subiu",
    NOVO_NO_TOP: "NOVO NO TOP",
    SUMIU: "sumiu",
}
PRIORIDADE = {
    COMISSAO_SUBIU: 0, PRECO_CAIU: 1, NOVO_NO_TOP: 2,
    COMISSAO_CAIU: 3, PRECO_SUBIU: 4, SUMIU: 5,
}


@dataclass(frozen=True, slots=True)
class Alerta:
    tipo: str
    plataforma: str
    item_id: str
    nome: str
    antes: float | None
    depois: float | None
    variacao: float | None
    link: str | None

    @property
    def rotulo(self) -> str:
        return ROTULOS[self.tipo]

    @property
    def ordem(self) -> tuple[int, float]:
        return (PRIORIDADE[self.tipo], -abs(self.variacao or 0.0))


@dataclass(frozen=True, slots=True)
class PosicaoPreco:
    """Onde o preco de hoje esta na historia do produto.

    Detectar desconto inflado protege sua credibilidade; detectar o menor
    preco constroi autoridade. Sao os dois lados do mesmo dado, e so o
    segundo faz o seguidor comprar agora.
    """

    preco_atual: float
    minimo: float | None
    maximo: float | None
    dia_minimo: str | None
    dias_observados: int

    @property
    def confiavel(self) -> bool:
        return self.minimo is not None

    @property
    def e_minimo(self) -> bool:
        return self.confiavel and self.preco_atual <= self.minimo * 1.005

    @property
    def acima_do_minimo_pct(self) -> float | None:
        if not self.confiavel or not self.minimo:
            return None
        return (self.preco_atual - self.minimo) / self.minimo * 100


def avaliar_posicao_preco(
    preco_atual: float,
    historico: Sequence[tuple[str, float]],
    dias_minimos: int,
) -> PosicaoPreco:
    """`historico` e uma sequencia de (dia, preco), incluindo hoje."""
    validos = [(dia, preco) for dia, preco in historico if preco and preco > 0]
    if len(validos) < dias_minimos:
        return PosicaoPreco(preco_atual, None, None, None, len(validos))
    dia_min, minimo = min(validos, key=lambda par: par[1])
    maximo = max(preco for _, preco in validos)
    return PosicaoPreco(preco_atual, minimo, maximo, dia_min, len(validos))


@dataclass(frozen=True, slots=True)
class DescontoAvaliado:
    preco_atual: float
    mediana_historica: float | None
    desconto_declarado: float
    desconto_real: float | None
    dias_observados: int

    @property
    def confiavel(self) -> bool:
        return self.mediana_historica is not None

    @property
    def exagero_pp(self) -> float | None:
        """Quantos pontos percentuais o anuncio infla o desconto."""
        if self.desconto_real is None:
            return None
        return self.desconto_declarado - self.desconto_real


def _variacao(antes: float, depois: float) -> float | None:
    if not antes:
        return None
    return (depois - antes) / antes


def _indexar(linhas: Iterable) -> dict[tuple[str, str], dict]:
    return {
        (linha["plataforma"], linha["item_id"]): dict(linha)
        for linha in linhas
    }


def detectar_alertas(
    atual: Sequence,
    anterior: Sequence,
    cfg: AlertasConfig,
) -> list[Alerta]:
    """Compara dois snapshots e devolve so o que mudou de forma relevante."""
    hoje = _indexar(atual)
    ontem = _indexar(anterior)
    top_hoje = {
        (l["plataforma"], l["item_id"])
        for l in sorted(atual, key=lambda l: l["score"], reverse=True)[
            : cfg.top_referencia
        ]
    }
    top_ontem = {
        (l["plataforma"], l["item_id"])
        for l in sorted(anterior, key=lambda l: l["score"], reverse=True)[
            : cfg.top_referencia
        ]
    }

    alertas: list[Alerta] = []
    for chave, item in hoje.items():
        antigo = ontem.get(chave)
        if antigo is None:
            if chave in top_hoje:
                alertas.append(Alerta(
                    NOVO_NO_TOP, item["plataforma"], item["item_id"], item["nome"],
                    None, item["score"], None,
                    item["link_oferta"] or item["link_produto"],
                ))
            continue

        var_comissao = _variacao(antigo["taxa_comissao"], item["taxa_comissao"])
        if var_comissao is not None and abs(var_comissao) >= cfg.variacao_comissao_min:
            alertas.append(Alerta(
                COMISSAO_SUBIU if var_comissao > 0 else COMISSAO_CAIU,
                item["plataforma"], item["item_id"], item["nome"],
                antigo["taxa_comissao"], item["taxa_comissao"], var_comissao,
                item["link_oferta"] or item["link_produto"],
            ))

        var_preco = _variacao(antigo["preco"], item["preco"])
        if var_preco is not None and abs(var_preco) >= cfg.variacao_preco_min:
            alertas.append(Alerta(
                PRECO_CAIU if var_preco < 0 else PRECO_SUBIU,
                item["plataforma"], item["item_id"], item["nome"],
                antigo["preco"], item["preco"], var_preco,
                item["link_oferta"] or item["link_produto"],
            ))

    for chave, antigo in ontem.items():
        if chave not in hoje and chave in top_ontem:
            alertas.append(Alerta(
                SUMIU, antigo["plataforma"], antigo["item_id"], antigo["nome"],
                antigo["score"], None, None,
                antigo["link_oferta"] or antigo["link_produto"],
            ))

    return sorted(alertas, key=lambda a: a.ordem)


def avaliar_desconto(
    preco_atual: float,
    desconto_declarado: float | None,
    precos_historicos: Sequence[float],
    cfg: PrecoConfig,
) -> DescontoAvaliado:
    """Compara o desconto anunciado com o desconto contra a mediana real."""
    declarado = desconto_declarado or 0.0
    anteriores = [p for p in precos_historicos if p > 0]
    if len(anteriores) < cfg.dias_minimos:
        return DescontoAvaliado(preco_atual, None, declarado, None, len(anteriores))

    referencia = median(anteriores)
    real = (referencia - preco_atual) / referencia * 100 if referencia else 0.0
    return DescontoAvaliado(
        preco_atual, referencia, declarado, max(real, 0.0), len(anteriores)
    )


def desconto_inflado(avaliacao: DescontoAvaliado, cfg: PrecoConfig) -> bool:
    exagero = avaliacao.exagero_pp
    return exagero is not None and exagero > cfg.tolerancia_pp
