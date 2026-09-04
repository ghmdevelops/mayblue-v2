"""O EPC previsto acerta o que de fato vendeu?

Esta e a pergunta que o projeto inteiro evitou responder ate aqui. O ranking
ordena por EPC estimado, mas EPC estimado usa uma taxa de conversao
heuristica -- um palpite fundamentado. Se esse palpite nao tiver relacao com
o resultado real, o ranking e enfeite.

O metodo:

1. pega os produtos que ja tiveram venda registrada;
2. compara a posicao deles no ranking com a comissao que renderam;
3. mede a correlacao de postos (Spearman), que so olha ORDEM.

Spearman e nao Pearson porque o que importa e a ordenacao: se o ranking
coloca na frente quem rende mais, esta cumprindo o papel, mesmo que a escala
esteja errada. Spearman tambem aguenta os poucos itens que rendem muito e
distorcem qualquer media.

O resultado vem com aviso de amostra: com 20 produtos, uma correlacao de
0,3 pode ser sorte. Reportar o numero sem isso seria pior que nao reportar.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

AMOSTRA_MINIMA = 8
AMOSTRA_CONFIAVEL = 30


@dataclass(frozen=True, slots=True)
class Resultado:
    pares: int
    correlacao: float | None
    previstos_no_topo: int
    acertos_no_topo: int
    comissao_topo: float
    comissao_resto: float

    @property
    def confiavel(self) -> bool:
        return self.pares >= AMOSTRA_CONFIAVEL

    @property
    def suficiente(self) -> bool:
        return self.pares >= AMOSTRA_MINIMA

    @property
    def veredito(self) -> str:
        if not self.suficiente:
            return "amostra insuficiente"
        if self.correlacao is None:
            return "sem variacao para comparar"
        if self.correlacao >= 0.5:
            return "o ranking preve bem"
        if self.correlacao >= 0.2:
            return "o ranking preve fracamente"
        if self.correlacao > -0.2:
            return "o ranking NAO preve -- os pesos precisam de revisao"
        return "o ranking preve ao CONTRARIO -- ha erro de sinal em algum peso"


def _postos(valores: list[float]) -> list[float]:
    """Postos com media em caso de empate, como Spearman exige."""
    ordenados = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0.0] * len(valores)
    indice = 0
    while indice < len(ordenados):
        fim = indice
        while (fim + 1 < len(ordenados)
               and valores[ordenados[fim + 1]] == valores[ordenados[indice]]):
            fim += 1
        media = (indice + fim) / 2 + 1
        for k in range(indice, fim + 1):
            postos[ordenados[k]] = media
        indice = fim + 1
    return postos


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Correlacao de postos. `None` quando um dos lados nao varia."""
    if len(xs) < 2:
        return None
    px, py = _postos(xs), _postos(ys)
    n = len(px)
    media_x, media_y = sum(px) / n, sum(py) / n
    numerador = sum((a - media_x) * (b - media_y) for a, b in zip(px, py))
    var_x = sum((a - media_x) ** 2 for a in px)
    var_y = sum((b - media_y) ** 2 for b in py)
    if var_x == 0 or var_y == 0:
        return None
    return numerador / (var_x * var_y) ** 0.5


def coletar_pares(conexao: sqlite3.Connection) -> list[tuple[str, float, float]]:
    """(item, EPC previsto, comissao realizada) para quem tem os dois lados.

    Usa o MAIOR EPC ja registrado para o item, nao o de hoje: a decisao de
    divulgar foi tomada em algum momento do passado, com o numero daquele
    dia. Comparar a venda de junho com o EPC de setembro nao mediria nada.
    """
    return [
        (linha["item_id"], linha["epc"], linha["comissao"])
        for linha in conexao.execute(
            """
            SELECT c.item_id,
                   MAX(o.score)  AS epc,
                   SUM(c.comissao) AS comissao
            FROM conversao c
            JOIN oferta_dia o
              ON o.plataforma = c.plataforma AND o.item_id = c.item_id
            WHERE c.item_id IS NOT NULL AND c.comissao > 0
            GROUP BY c.item_id
            """
        )
    ]


def avaliar(conexao: sqlite3.Connection, fatia_topo: float = 0.3) -> Resultado:
    pares = coletar_pares(conexao)
    if not pares:
        return Resultado(0, None, 0, 0, 0.0, 0.0)

    epcs = [p[1] for p in pares]
    ganhos = [p[2] for p in pares]

    # Quantos dos que o ranking colocaria no topo realmente renderam acima
    # da mediana. E a leitura pratica: "seguir o ranking teria dado certo?"
    corte = max(1, int(len(pares) * fatia_topo))
    por_epc = sorted(pares, key=lambda p: -p[1])
    topo = por_epc[:corte]
    resto = por_epc[corte:]
    mediana_ganho = sorted(ganhos)[len(ganhos) // 2]
    acertos = sum(1 for p in topo if p[2] >= mediana_ganho)

    return Resultado(
        pares=len(pares),
        correlacao=spearman(epcs, ganhos),
        previstos_no_topo=len(topo),
        acertos_no_topo=acertos,
        comissao_topo=sum(p[2] for p in topo) / len(topo),
        comissao_resto=(sum(p[2] for p in resto) / len(resto)) if resto else 0.0,
    )
