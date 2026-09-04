"""Calibracao do CVR: troca a heuristica pelo dado medido.

O score de descoberta usa uma conversao ESTIMADA, porque a Shopee nao expoe
taxa de conversao por produto. Assim que existe historico proprio de
conversao (conversionReport), da para medir o CVR real.

O problema de usar `pedidos / cliques` cru: um item com 3 cliques e 1 pedido
daria 33% de conversao. Corte duro por amostra minima resolve isso mal --
trata um item com 201 cliques e outro com 50.000 como igualmente confiaveis,
e joga fora todo o dado abaixo do corte.

A solucao usada aqui e encolhimento bayesiano (beta-binomial) hierarquico:

    cvr = (pedidos + peso_prior * cvr_do_pai) / (cliques + peso_prior)

Cada escopo encolhe em direcao ao pai: item -> loja -> global -> cvr_base.
`peso_prior` funciona como pseudo-contagem: com poucos cliques o valor fica
proximo do pai; conforme a amostra cresce, o dado proprio domina. Assim
amostra pequena entra no calculo sem virar ruido, em vez de ser descartada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from . import db
from .config import CalibracaoConfig
from .models import Oferta

ESCOPO_GLOBAL = "global"
ESCOPO_LOJA = "loja"
ESCOPO_ITEM = "item"


@dataclass(frozen=True, slots=True)
class Calibrador:
    """Consulta o CVR medido para uma oferta, ou None se nao houver amostra."""

    tabela: dict[tuple[str, str, str], float]

    def cvr_para(self, oferta: Oferta) -> float | None:
        candidatos = (
            (ESCOPO_ITEM, oferta.item_id),
            (ESCOPO_LOJA, oferta.loja_id or ""),
            (ESCOPO_GLOBAL, ESCOPO_GLOBAL),
        )
        for escopo, chave in candidatos:
            if not chave:
                continue
            valor = self.tabela.get((oferta.plataforma, escopo, chave))
            if valor is not None:
                return valor
        return None

    def __bool__(self) -> bool:
        return bool(self.tabela)


def suavizar(pedidos: int, cliques: int, cvr_prior: float, peso_prior: float) -> float:
    """Media posterior de um beta-binomial com prior centrado em `cvr_prior`."""
    if cliques <= 0 and peso_prior <= 0:
        return cvr_prior
    return (pedidos + peso_prior * cvr_prior) / (cliques + peso_prior)


def _desde(janela_dias: int) -> str | None:
    if janela_dias <= 0:
        return None
    return (date.today() - timedelta(days=janela_dias)).isoformat()


def ha_cliques(conexao: sqlite3.Connection) -> bool:
    """A calibracao depende de cliques, que nem toda plataforma fornece.

    Confirmado por introspecao do schema: a API de afiliados da Shopee nao
    tem relatorio de trafego -- so oferta, conversao e pedido. Sem cliques,
    `pedidos / cliques` e incalculavel e a tabela fica vazia. Detectar isso
    aqui permite explicar o motivo em vez de dizer "amostra insuficiente".
    """
    linha = conexao.execute("SELECT COUNT(*) AS n FROM clique").fetchone()
    return bool(linha and linha["n"])


def recalcular(conexao: sqlite3.Connection, cfg: CalibracaoConfig) -> dict[str, int]:
    """Recalcula a tabela de CVR encolhendo item -> loja -> global."""
    desde = _desde(cfg.janela_dias)
    peso = cfg.peso_prior
    registros: list[dict] = []
    resumo = {ESCOPO_ITEM: 0, ESCOPO_LOJA: 0, ESCOPO_GLOBAL: 0}

    globais: dict[str, float] = {}
    for linha in db.agregar_conversoes(conexao, ESCOPO_GLOBAL, desde):
        cliques, pedidos = linha["cliques"] or 0, linha["pedidos"] or 0
        if cliques < cfg.cliques_minimos:
            continue
        cvr = suavizar(pedidos, cliques, cfg.cvr_prior_padrao, peso)
        globais[linha["plataforma"]] = cvr
        registros.append({
            "plataforma": linha["plataforma"], "escopo": ESCOPO_GLOBAL,
            "chave": ESCOPO_GLOBAL, "cliques": cliques, "pedidos": pedidos, "cvr": cvr,
        })
        resumo[ESCOPO_GLOBAL] += 1

    lojas: dict[tuple[str, str], float] = {}
    for linha in db.agregar_conversoes(conexao, ESCOPO_LOJA, desde):
        cliques, pedidos = linha["cliques"] or 0, linha["pedidos"] or 0
        if cliques < cfg.cliques_minimos:
            continue
        plataforma = linha["plataforma"]
        prior = globais.get(plataforma, cfg.cvr_prior_padrao)
        cvr = suavizar(pedidos, cliques, prior, peso)
        lojas[(plataforma, str(linha["chave"]))] = cvr
        registros.append({
            "plataforma": plataforma, "escopo": ESCOPO_LOJA,
            "chave": str(linha["chave"]), "cliques": cliques,
            "pedidos": pedidos, "cvr": cvr,
        })
        resumo[ESCOPO_LOJA] += 1

    for linha in db.agregar_item_com_loja(conexao, desde):
        cliques, pedidos = linha["cliques"] or 0, linha["pedidos"] or 0
        if cliques < cfg.cliques_minimos:
            continue
        plataforma = linha["plataforma"]
        prior = lojas.get(
            (plataforma, str(linha["loja_id"] or "")),
            globais.get(plataforma, cfg.cvr_prior_padrao),
        )
        registros.append({
            "plataforma": plataforma, "escopo": ESCOPO_ITEM,
            "chave": str(linha["item_id"]), "cliques": cliques, "pedidos": pedidos,
            "cvr": suavizar(pedidos, cliques, prior, peso),
        })
        resumo[ESCOPO_ITEM] += 1

    db.salvar_cvr_calibrado(conexao, registros)
    resumo["total"] = len(registros)
    return resumo


def carregar(conexao: sqlite3.Connection, cfg: CalibracaoConfig) -> Calibrador:
    if not cfg.habilitada:
        return Calibrador({})
    return Calibrador(db.carregar_cvr_calibrado(conexao))
