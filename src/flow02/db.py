"""Persistencia em SQLite.

Guarda um snapshot por (dia, plataforma, item_id). Manter o historico diario
permite duas coisas que um snapshot unico nao permite:

- ranking acumulado ("de todos"), ponderado por persistencia no topo;
- velocidade real de venda, via delta do total de vendas entre dias, que e um
  sinal muito melhor do que o total acumulado de vendas do produto.
"""

from __future__ import annotations

import json
import sqlite3
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .models import Avaliacao, Conversao, Oferta
from .tempo import dia_brasil, iso_utc

ESQUEMA = """
CREATE TABLE IF NOT EXISTS oferta_dia (
    dia             TEXT    NOT NULL,
    plataforma      TEXT    NOT NULL,
    item_id         TEXT    NOT NULL,
    coletado_em     TEXT    NOT NULL,
    nome            TEXT    NOT NULL,
    preco           REAL    NOT NULL,
    taxa_comissao   REAL    NOT NULL,
    comissao_valor  REAL    NOT NULL,
    desconto_pct    REAL,
    vendas          INTEGER,
    rating          REAL,
    loja_id         TEXT,
    loja_nome       TEXT,
    categoria_ids   TEXT,
    link_oferta     TEXT,
    link_produto    TEXT,
    imagem          TEXT,
    expira_em       TEXT,
    taxa_vendedor   REAL,
    taxa_shopee     REAL,
    comissao_api    REAL,
    cvr_estimado    REAL    NOT NULL,
    score           REAL    NOT NULL,
    calibrado       INTEGER NOT NULL DEFAULT 0,
    origem_consulta TEXT    NOT NULL,
    componentes     TEXT,
    PRIMARY KEY (dia, plataforma, item_id)
);

CREATE INDEX IF NOT EXISTS idx_oferta_dia_score  ON oferta_dia (dia, score DESC);
CREATE INDEX IF NOT EXISTS idx_oferta_dia_item   ON oferta_dia (plataforma, item_id);

CREATE TABLE IF NOT EXISTS execucao (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_em     TEXT    NOT NULL,
    finalizado_em   TEXT,
    plataforma      TEXT    NOT NULL,
    ofertas_brutas  INTEGER NOT NULL DEFAULT 0,
    ofertas_salvas  INTEGER NOT NULL DEFAULT 0,
    erro            TEXT
);

CREATE TABLE IF NOT EXISTS conversao (
    plataforma      TEXT    NOT NULL,
    conversao_id    TEXT    NOT NULL,
    validada        INTEGER NOT NULL DEFAULT 0,
    cliques         INTEGER NOT NULL DEFAULT 0,
    pedidos         INTEGER NOT NULL DEFAULT 0,
    comissao        REAL    NOT NULL DEFAULT 0,
    item_id         TEXT,
    loja_id         TEXT,
    ocorrido_em     TEXT,
    status          TEXT,
    pedido_id       TEXT,
    clicado_em      TEXT,
    canal           TEXT,
    coletado_em     TEXT    NOT NULL,
    PRIMARY KEY (plataforma, conversao_id, validada)
);

CREATE INDEX IF NOT EXISTS idx_conversao_pedido ON conversao (pedido_id);

CREATE INDEX IF NOT EXISTS idx_conversao_data ON conversao (ocorrido_em);
CREATE INDEX IF NOT EXISTS idx_conversao_item ON conversao (plataforma, item_id);

CREATE TABLE IF NOT EXISTS cvr_calibrado (
    plataforma      TEXT    NOT NULL,
    escopo          TEXT    NOT NULL,
    chave           TEXT    NOT NULL,
    cliques         INTEGER NOT NULL,
    pedidos         INTEGER NOT NULL,
    cvr             REAL    NOT NULL,
    atualizado_em   TEXT    NOT NULL,
    PRIMARY KEY (plataforma, escopo, chave)
);

CREATE TABLE IF NOT EXISTS preco_alvo (
    plataforma      TEXT    NOT NULL,
    item_id         TEXT    NOT NULL,
    alvo            REAL    NOT NULL,
    nome            TEXT,
    criado_em       TEXT    NOT NULL,
    disparado_em    TEXT,
    PRIMARY KEY (plataforma, item_id)
);

-- Registro da rodada inteira, com o resultado de cada etapa.
--
-- Separado de `execucao`, que guarda uma linha por plataforma: aqui interessa
-- se a RODADA foi completa. Existe porque coleta que morre no meio grava
-- parte do trabalho e parece sucesso -- em 2026-09-04 uma rodada salvou as
-- conversoes mas nao as ofertas, e so deu para perceber comparando horarios
-- de gravacao na mao.
CREATE TABLE IF NOT EXISTS rodada (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    comando         TEXT    NOT NULL,
    dia             TEXT    NOT NULL,
    iniciado_em     TEXT    NOT NULL,
    finalizado_em   TEXT,
    estado          TEXT    NOT NULL DEFAULT 'rodando',
    etapas          TEXT,
    erro            TEXT
);

CREATE INDEX IF NOT EXISTS idx_rodada_dia ON rodada (dia, iniciado_em);

-- Paginas ja obtidas da API, para a coleta poder ser retomada.
--
-- Nesta maquina a rede derruba o processo no meio de chamadas de saida, de
-- forma intermitente. Sem checkpoint, uma queda na pagina 7 joga fora as 6
-- que ja tinham vindo e a proxima tentativa recomeca do zero -- o que, com
-- quedas frequentes, significa nunca terminar.
CREATE TABLE IF NOT EXISTS pagina_bruta (
    dia         TEXT    NOT NULL,
    plataforma  TEXT    NOT NULL,
    consulta    TEXT    NOT NULL,
    pagina      INTEGER NOT NULL,
    nodes       TEXT    NOT NULL,
    obtido_em   TEXT    NOT NULL,
    PRIMARY KEY (dia, plataforma, consulta, pagina)
);

CREATE TABLE IF NOT EXISTS vitrine (
    plataforma      TEXT    NOT NULL,
    item_id         TEXT    NOT NULL,
    colecao         TEXT    NOT NULL DEFAULT 'principal',
    posicao         INTEGER NOT NULL DEFAULT 0,
    adicionado_em   TEXT    NOT NULL,
    PRIMARY KEY (plataforma, item_id, colecao)
);

CREATE INDEX IF NOT EXISTS idx_vitrine_colecao ON vitrine (colecao, posicao);

CREATE TABLE IF NOT EXISTS clique (
    plataforma      TEXT    NOT NULL,
    codigo          TEXT    NOT NULL,
    dia             TEXT    NOT NULL,
    item_id         TEXT,
    canal           TEXT,
    total           INTEGER NOT NULL DEFAULT 0,
    importado_em    TEXT    NOT NULL,
    PRIMARY KEY (plataforma, codigo, dia)
);

CREATE INDEX IF NOT EXISTS idx_clique_item ON clique (plataforma, item_id);

CREATE TABLE IF NOT EXISTS link_gerado (
    plataforma      TEXT    NOT NULL,
    url_origem      TEXT    NOT NULL,
    sub_ids         TEXT    NOT NULL DEFAULT '',
    link_curto      TEXT    NOT NULL,
    item_id         TEXT,
    gerado_em       TEXT    NOT NULL,
    PRIMARY KEY (plataforma, url_origem, sub_ids)
);
"""

_UPSERT = """
INSERT INTO oferta_dia (
    dia, plataforma, item_id, coletado_em, nome, preco, taxa_comissao,
    comissao_valor, desconto_pct, vendas, rating, loja_id, loja_nome,
    categoria_ids, link_oferta, link_produto, imagem, expira_em,
    taxa_vendedor, taxa_shopee, comissao_api,
    cvr_estimado, score, calibrado, origem_consulta, componentes
) VALUES (
    :dia, :plataforma, :item_id, :coletado_em, :nome, :preco, :taxa_comissao,
    :comissao_valor, :desconto_pct, :vendas, :rating, :loja_id, :loja_nome,
    :categoria_ids, :link_oferta, :link_produto, :imagem, :expira_em,
    :taxa_vendedor, :taxa_shopee, :comissao_api,
    :cvr_estimado, :score, :calibrado, :origem_consulta, :componentes
)
ON CONFLICT (dia, plataforma, item_id) DO UPDATE SET
    coletado_em     = excluded.coletado_em,
    nome            = excluded.nome,
    preco           = excluded.preco,
    taxa_comissao   = excluded.taxa_comissao,
    comissao_valor  = excluded.comissao_valor,
    desconto_pct    = excluded.desconto_pct,
    vendas          = excluded.vendas,
    rating          = excluded.rating,
    loja_id         = excluded.loja_id,
    loja_nome       = excluded.loja_nome,
    categoria_ids   = excluded.categoria_ids,
    link_oferta     = excluded.link_oferta,
    link_produto    = excluded.link_produto,
    imagem          = excluded.imagem,
    expira_em       = excluded.expira_em,
    taxa_vendedor   = excluded.taxa_vendedor,
    taxa_shopee     = excluded.taxa_shopee,
    comissao_api    = excluded.comissao_api,
    cvr_estimado    = excluded.cvr_estimado,
    score           = excluded.score,
    calibrado       = excluded.calibrado,
    origem_consulta = excluded.origem_consulta,
    componentes     = excluded.componentes
WHERE excluded.score > oferta_dia.score
"""


@dataclass(frozen=True, slots=True)
class LinhaHistorico:
    plataforma: str
    item_id: str
    nome: str
    loja_nome: str | None
    link_oferta: str | None
    dias_visto: int
    primeiro_dia: str
    ultimo_dia: str
    score_medio: float
    score_max: float
    comissao_media: float
    preco_medio: float
    vendas_delta: int | None


# Colunas adicionadas depois que o banco ja existia em campo.
# `CREATE TABLE IF NOT EXISTS` nao altera tabela existente, entao sem isto o
# banco de quem ja usava o programa quebra no primeiro CREATE INDEX novo.
MIGRACOES: tuple[tuple[str, str, str], ...] = (
    ("conversao", "pedido_id", "TEXT"),
    ("conversao", "clicado_em", "TEXT"),
    ("oferta_dia", "expira_em", "TEXT"),
    ("link_gerado", "item_id", "TEXT"),
    ("conversao", "canal", "TEXT"),
    ("oferta_dia", "taxa_vendedor", "REAL"),
    ("oferta_dia", "taxa_shopee", "REAL"),
    ("oferta_dia", "comissao_api", "REAL"),
)


def _migrar(conexao: sqlite3.Connection) -> list[str]:
    """Adiciona colunas que faltam. Roda antes do esquema, para os indices
    novos encontrarem as colunas que referenciam."""
    aplicadas = []
    for tabela, coluna, tipo in MIGRACOES:
        existentes = {
            linha["name"] for linha in conexao.execute(f"PRAGMA table_info({tabela})")
        }
        if existentes and coluna not in existentes:
            conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
            aplicadas.append(f"{tabela}.{coluna}")
    return aplicadas


@contextmanager
def conectar(caminho: Path) -> Iterator[sqlite3.Connection]:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row
    try:
        conexao.execute("PRAGMA journal_mode = WAL")
        conexao.execute("PRAGMA foreign_keys = ON")
        _migrar(conexao)
        conexao.executescript(ESQUEMA)
        yield conexao
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def _para_linha(oferta: Oferta, avaliacao: Avaliacao, dia: str) -> dict:
    return {
        "dia": dia,
        "plataforma": oferta.plataforma,
        "item_id": oferta.item_id,
        "coletado_em": iso_utc(),
        "nome": oferta.nome,
        "preco": oferta.preco,
        "taxa_comissao": oferta.taxa_comissao,
        "comissao_valor": oferta.comissao_valor,
        "desconto_pct": oferta.desconto_pct,
        "vendas": oferta.vendas,
        "rating": oferta.rating,
        "loja_id": oferta.loja_id,
        "loja_nome": oferta.loja_nome,
        "categoria_ids": ",".join(oferta.categoria_ids) or None,
        "link_oferta": oferta.link_oferta,
        "link_produto": oferta.link_produto,
        "imagem": oferta.imagem,
        "expira_em": oferta.expira_em,
        "taxa_vendedor": oferta.taxa_vendedor,
        "taxa_shopee": oferta.taxa_shopee,
        "comissao_api": oferta.comissao_api,
        "cvr_estimado": avaliacao.cvr_estimado,
        "score": avaliacao.score,
        "calibrado": int(avaliacao.calibrado),
        "origem_consulta": oferta.origem_consulta,
        "componentes": json.dumps(avaliacao.componentes, ensure_ascii=False),
    }


def salvar_ofertas(
    conexao: sqlite3.Connection,
    avaliadas: Iterable[tuple[Oferta, Avaliacao]],
    dia: str | None = None,
) -> int:
    dia = dia or dia_brasil()
    linhas = [_para_linha(oferta, avaliacao, dia) for oferta, avaliacao in avaliadas]
    if not linhas:
        return 0
    conexao.executemany(_UPSERT, linhas)
    return len(linhas)


def registrar_execucao(conexao: sqlite3.Connection, plataforma: str) -> int:
    cursor = conexao.execute(
        "INSERT INTO execucao (iniciado_em, plataforma) VALUES (?, ?)",
        (iso_utc(), plataforma),
    )
    return int(cursor.lastrowid)


def finalizar_execucao(
    conexao: sqlite3.Connection,
    execucao_id: int,
    brutas: int,
    salvas: int,
    erro: str | None = None,
) -> None:
    conexao.execute(
        "UPDATE execucao SET finalizado_em = ?, ofertas_brutas = ?, "
        "ofertas_salvas = ?, erro = ? WHERE id = ?",
        (iso_utc(), brutas, salvas, erro, execucao_id),
    )


def dias_disponiveis(conexao: sqlite3.Connection) -> list[str]:
    return [r["dia"] for r in conexao.execute(
        "SELECT DISTINCT dia FROM oferta_dia ORDER BY dia DESC"
    )]


ORDENACOES = {
    "epc": "score",
    "comissao": "comissao_valor",
    "vendas": "vendas",
    "preco": "preco",
    "taxa": "taxa_comissao",
    "nota": "rating",
}


def ranking_dia(
    conexao: sqlite3.Connection,
    dia: str,
    limite: int,
    max_por_loja: int = 0,
    plataforma: str | None = None,
    ordenar: str = "epc",
) -> list[sqlite3.Row]:
    """Top ofertas do dia. `max_por_loja` > 0 aplica diversificacao por loja."""
    coluna = ORDENACOES.get(ordenar, "score")
    sql = ["SELECT * FROM oferta_dia WHERE dia = :dia"]
    params: dict = {"dia": dia}
    if plataforma:
        sql.append("AND plataforma = :plataforma")
        params["plataforma"] = plataforma
    sql.append(f"ORDER BY {coluna} DESC, score DESC")
    linhas = list(conexao.execute(" ".join(sql), params))
    if max_por_loja <= 0:
        return linhas[:limite]

    vistos: dict[str, int] = {}
    selecionadas = []
    for linha in linhas:
        chave = linha["loja_id"] or linha["loja_nome"] or linha["item_id"]
        if vistos.get(chave, 0) >= max_por_loja:
            continue
        vistos[chave] = vistos.get(chave, 0) + 1
        selecionadas.append(linha)
        if len(selecionadas) >= limite:
            break
    return selecionadas


def salvar_conversoes(
    conexao: sqlite3.Connection, conversoes: Iterable[Conversao]
) -> int:
    linhas = [
        {
            "plataforma": c.plataforma,
            "conversao_id": c.conversao_id,
            "validada": int(c.validada),
            "cliques": c.cliques,
            "pedidos": c.pedidos,
            "comissao": c.comissao,
            "item_id": c.item_id,
            "loja_id": c.loja_id,
            "ocorrido_em": c.ocorrido_em,
            "status": c.status,
            "pedido_id": c.pedido_id,
            "clicado_em": c.clicado_em,
            "canal": c.canal,
            "coletado_em": iso_utc(),
        }
        for c in conversoes
    ]
    if not linhas:
        return 0
    conexao.executemany(
        """
        INSERT INTO conversao (
            plataforma, conversao_id, validada, cliques, pedidos, comissao,
            item_id, loja_id, ocorrido_em, status, pedido_id, clicado_em,
            canal, coletado_em
        ) VALUES (
            :plataforma, :conversao_id, :validada, :cliques, :pedidos, :comissao,
            :item_id, :loja_id, :ocorrido_em, :status, :pedido_id, :clicado_em,
            :canal, :coletado_em
        )
        ON CONFLICT (plataforma, conversao_id, validada) DO UPDATE SET
            cliques     = excluded.cliques,
            pedidos     = excluded.pedidos,
            comissao    = excluded.comissao,
            item_id     = COALESCE(excluded.item_id, conversao.item_id),
            loja_id     = COALESCE(excluded.loja_id, conversao.loja_id),
            ocorrido_em = COALESCE(excluded.ocorrido_em, conversao.ocorrido_em),
            status      = excluded.status,
            pedido_id   = COALESCE(excluded.pedido_id, conversao.pedido_id),
            clicado_em  = COALESCE(excluded.clicado_em, conversao.clicado_em),
            canal       = COALESCE(excluded.canal, conversao.canal),
            coletado_em = excluded.coletado_em
        """,
        linhas,
    )
    return len(linhas)


def ganhos(
    conexao: sqlite3.Connection,
    desde: str | None = None,
    plataforma: str | None = None,
    validada: bool | None = None,
) -> list[sqlite3.Row]:
    """Retorno REALIZADO agregado por dia.

    Pedidos e comissao vem da API (tabela `conversao`); cliques vem do
    redirecionador proprio (tabela `clique`), porque a Shopee nao fornece
    esse numero. Sao fontes distintas unidas pelo dia.
    """
    return list(conexao.execute(
        """
        WITH vendas AS (
            SELECT COALESCE(ocorrido_em, substr(coletado_em, 1, 10)) AS dia,
                   plataforma,
                   SUM(pedidos)  AS pedidos,
                   SUM(comissao) AS comissao,
                   MAX(validada) AS validada
            FROM conversao
            WHERE (:plataforma IS NULL OR plataforma = :plataforma)
              AND (:validada IS NULL OR validada = :validada)
            GROUP BY dia, plataforma
        ), trafego AS (
            SELECT dia, plataforma, SUM(total) AS cliques
            FROM clique
            WHERE (:plataforma IS NULL OR plataforma = :plataforma)
            GROUP BY dia, plataforma
        )
        SELECT
            COALESCE(v.dia, t.dia)                 AS dia,
            COALESCE(v.plataforma, t.plataforma)   AS plataforma,
            COALESCE(t.cliques, 0)                 AS cliques,
            COALESCE(v.pedidos, 0)                 AS pedidos,
            COALESCE(v.comissao, 0)                AS comissao,
            COALESCE(v.validada, 0)                AS validada
        FROM vendas v
        LEFT JOIN trafego t ON t.dia = v.dia AND t.plataforma = v.plataforma
        WHERE (:desde IS NULL OR COALESCE(v.dia, t.dia) >= :desde)
        UNION
        SELECT t.dia, t.plataforma, t.cliques, 0, 0, 0
        FROM trafego t
        WHERE NOT EXISTS (
                SELECT 1 FROM vendas v
                 WHERE v.dia = t.dia AND v.plataforma = t.plataforma)
          AND (:desde IS NULL OR t.dia >= :desde)
        ORDER BY dia DESC
        """,
        {"desde": desde, "plataforma": plataforma,
         "validada": None if validada is None else int(validada)},
    ))


def agregar_conversoes(
    conexao: sqlite3.Connection, escopo: str, desde: str | None = None
) -> list[sqlite3.Row]:
    """Soma cliques e pedidos por escopo, base do CVR medido.

    Os cliques nao vem da API da Shopee (que nao os expoe) e sim da tabela
    `clique`, alimentada pelo redirecionador proprio. Por isso o JOIN.
    """
    coluna = {"item": "item_id", "loja": "loja_id", "global": "'global'"}[escopo]
    if escopo == "loja":
        # O clique conhece o item, nao a loja: liga via oferta_dia.
        fonte_cliques = """
            SELECT o.loja_id AS chave, c.plataforma, SUM(c.total) AS cliques
            FROM clique c
            JOIN (SELECT DISTINCT plataforma, item_id, loja_id FROM oferta_dia) o
              ON o.plataforma = c.plataforma AND o.item_id = c.item_id
            WHERE (:desde IS NULL OR c.dia >= :desde) AND o.loja_id IS NOT NULL
            GROUP BY c.plataforma, o.loja_id
        """
    elif escopo == "item":
        fonte_cliques = """
            SELECT item_id AS chave, plataforma, SUM(total) AS cliques
            FROM clique
            WHERE (:desde IS NULL OR dia >= :desde) AND item_id IS NOT NULL
            GROUP BY plataforma, item_id
        """
    else:
        fonte_cliques = """
            SELECT 'global' AS chave, plataforma, SUM(total) AS cliques
            FROM clique
            WHERE (:desde IS NULL OR dia >= :desde)
            GROUP BY plataforma
        """

    return list(conexao.execute(
        f"""
        WITH pedidos AS (
            SELECT plataforma, {coluna} AS chave, SUM(pedidos) AS pedidos
            FROM conversao
            WHERE {coluna} IS NOT NULL
              AND (:desde IS NULL OR COALESCE(ocorrido_em, substr(coletado_em, 1, 10)) >= :desde)
            GROUP BY plataforma, chave
        ), cliques AS ({fonte_cliques})
        SELECT c.plataforma, c.chave,
               c.cliques AS cliques,
               COALESCE(p.pedidos, 0) AS pedidos
        FROM cliques c
        LEFT JOIN pedidos p ON p.plataforma = c.plataforma AND p.chave = c.chave
        WHERE c.cliques > 0
        """,
        {"desde": desde},
    ))


def ganhos_por_item(
    conexao: sqlite3.Connection, desde: str | None = None, limite: int = 50
) -> list[sqlite3.Row]:
    """Comissao realizada por produto -- o que de fato pagou.

    Substitui o CVR como sinal medido: a API de afiliados da Shopee nao expoe
    cliques, entao taxa de conversao e incalculavel. Comissao realizada, sim.
    """
    return list(conexao.execute(
        """
        SELECT
            c.plataforma,
            c.item_id,
            COUNT(*)              AS conversoes,
            SUM(c.pedidos)        AS unidades,
            SUM(c.comissao)       AS comissao,
            MIN(c.ocorrido_em)    AS primeira,
            MAX(c.ocorrido_em)    AS ultima,
            (SELECT o.nome FROM oferta_dia o
              WHERE o.plataforma = c.plataforma AND o.item_id = c.item_id
              ORDER BY o.dia DESC LIMIT 1) AS nome,
            (SELECT o.link_oferta FROM oferta_dia o
              WHERE o.plataforma = c.plataforma AND o.item_id = c.item_id
              ORDER BY o.dia DESC LIMIT 1) AS link
        FROM conversao c
        WHERE c.item_id IS NOT NULL
          AND (:desde IS NULL OR COALESCE(c.ocorrido_em, substr(c.coletado_em, 1, 10)) >= :desde)
        GROUP BY c.plataforma, c.item_id
        ORDER BY comissao DESC
        LIMIT :limite
        """,
        {"desde": desde, "limite": limite},
    ))


def agregar_item_com_loja(
    conexao: sqlite3.Connection, desde: str | None = None
) -> list[sqlite3.Row]:
    """Itens com cliques, pedidos e a loja, para o encolhimento hierarquico.

    Cliques vem da tabela `clique` (redirecionador proprio) e pedidos da
    `conversao` (API da Shopee) -- sao fontes diferentes, unidas pelo item.
    """
    return list(conexao.execute(
        """
        WITH cliques AS (
            SELECT plataforma, item_id, SUM(total) AS cliques
            FROM clique
            WHERE item_id IS NOT NULL AND (:desde IS NULL OR dia >= :desde)
            GROUP BY plataforma, item_id
        ), pedidos AS (
            SELECT plataforma, item_id, MAX(loja_id) AS loja_id,
                   SUM(pedidos) AS pedidos
            FROM conversao
            WHERE item_id IS NOT NULL
              AND (:desde IS NULL OR COALESCE(ocorrido_em, substr(coletado_em, 1, 10)) >= :desde)
            GROUP BY plataforma, item_id
        )
        SELECT c.plataforma, c.item_id, c.cliques,
               COALESCE(p.pedidos, 0) AS pedidos,
               COALESCE(p.loja_id, (SELECT MAX(o.loja_id) FROM oferta_dia o
                   WHERE o.plataforma = c.plataforma AND o.item_id = c.item_id)
               ) AS loja_id
        FROM cliques c
        LEFT JOIN pedidos p ON p.plataforma = c.plataforma AND p.item_id = c.item_id
        WHERE c.cliques > 0
        """,
        {"desde": desde},
    ))


def ofertas_do_dia(
    conexao: sqlite3.Connection, dia: str, plataforma: str | None = None
) -> list[sqlite3.Row]:
    """Snapshot bruto de um dia, sem ordenacao nem corte. Base da comparacao."""
    return list(conexao.execute(
        "SELECT * FROM oferta_dia WHERE dia = :dia "
        "AND (:plataforma IS NULL OR plataforma = :plataforma)",
        {"dia": dia, "plataforma": plataforma},
    ))


def dia_anterior_a(conexao: sqlite3.Connection, dia: str) -> str | None:
    linha = conexao.execute(
        "SELECT MAX(dia) AS dia FROM oferta_dia WHERE dia < ?", (dia,)
    ).fetchone()
    return linha["dia"] if linha else None


def serie_de_precos(
    conexao: sqlite3.Connection,
    plataforma: str | None = None,
    desde: str | None = None,
) -> dict[tuple[str, str], list[float]]:
    """Precos observados por item, para calcular a mediana historica real."""
    series: dict[tuple[str, str], list[float]] = {}
    for linha in conexao.execute(
        "SELECT plataforma, item_id, preco FROM oferta_dia "
        "WHERE (:plataforma IS NULL OR plataforma = :plataforma) "
        "AND (:desde IS NULL OR dia >= :desde) ORDER BY dia",
        {"plataforma": plataforma, "desde": desde},
    ):
        series.setdefault((linha["plataforma"], linha["item_id"]), []).append(
            linha["preco"]
        )
    return series


def serie_de_precos_datada(
    conexao: sqlite3.Connection,
    plataforma: str | None = None,
    desde: str | None = None,
) -> dict[tuple[str, str], list[tuple[str, float]]]:
    """Igual a `serie_de_precos`, mas com o dia -- para achar quando foi o minimo."""
    series: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for linha in conexao.execute(
        "SELECT plataforma, item_id, dia, preco FROM oferta_dia "
        "WHERE (:plataforma IS NULL OR plataforma = :plataforma) "
        "AND (:desde IS NULL OR dia >= :desde) ORDER BY dia",
        {"plataforma": plataforma, "desde": desde},
    ):
        series.setdefault((linha["plataforma"], linha["item_id"]), []).append(
            (linha["dia"], linha["preco"])
        )
    return series


COLECAO_PADRAO = "principal"


def adicionar_vitrine(
    conexao: sqlite3.Connection, plataforma: str, item_id: str,
    colecao: str = COLECAO_PADRAO,
) -> None:
    """Adiciona no fim da colecao, preservando a ordem de curadoria."""
    proxima = conexao.execute(
        "SELECT COALESCE(MAX(posicao), 0) + 1 AS p FROM vitrine WHERE colecao = ?",
        (colecao,),
    ).fetchone()["p"]
    conexao.execute(
        """
        INSERT INTO vitrine (plataforma, item_id, colecao, posicao, adicionado_em)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (plataforma, item_id, colecao) DO NOTHING
        """,
        (plataforma, item_id, colecao, proxima, iso_utc()),
    )


def remover_vitrine(
    conexao: sqlite3.Connection, plataforma: str, item_id: str,
    colecao: str | None = None,
) -> bool:
    if colecao:
        cursor = conexao.execute(
            "DELETE FROM vitrine WHERE plataforma = ? AND item_id = ? AND colecao = ?",
            (plataforma, item_id, colecao),
        )
    else:
        cursor = conexao.execute(
            "DELETE FROM vitrine WHERE plataforma = ? AND item_id = ?",
            (plataforma, item_id),
        )
    return cursor.rowcount > 0


def esta_na_vitrine(conexao: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (r["plataforma"], r["item_id"])
        for r in conexao.execute("SELECT plataforma, item_id FROM vitrine")
    }


def listar_vitrine(
    conexao: sqlite3.Connection, colecao: str | None = None
) -> list[sqlite3.Row]:
    """Itens curados com o snapshot mais recente de cada um."""
    return list(conexao.execute(
        """
        SELECT
            v.plataforma, v.item_id, v.colecao, v.posicao, v.adicionado_em,
            o.nome, o.preco, o.taxa_comissao, o.comissao_valor, o.score AS epc,
            o.vendas, o.rating, o.desconto_pct, o.loja_nome, o.imagem,
            o.expira_em, o.link_oferta, o.link_produto, o.dia AS dia_preco
        FROM vitrine v
        LEFT JOIN oferta_dia o
          ON o.plataforma = v.plataforma AND o.item_id = v.item_id
         AND o.dia = (SELECT MAX(dia) FROM oferta_dia x
                       WHERE x.plataforma = v.plataforma AND x.item_id = v.item_id)
        WHERE (:colecao IS NULL OR v.colecao = :colecao)
        ORDER BY v.colecao, v.posicao
        """,
        {"colecao": colecao},
    ))


def colecoes(conexao: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conexao.execute(
        "SELECT colecao, COUNT(*) AS itens FROM vitrine "
        "GROUP BY colecao ORDER BY colecao"
    ))


ESTADO_OK = "ok"
ESTADO_PARCIAL = "parcial"
ESTADO_ERRO = "erro"


class CachePaginas:
    """Guarda as páginas já obtidas para que a coleta possa ser retomada.

    Fica aqui, e não na fonte, para a fonte não depender do banco: ela só
    recebe algo com `obter` e `guardar`. Em teste, um dicionário serve.
    """

    def __init__(self, conexao: sqlite3.Connection, dia: str, plataforma: str):
        self._conexao = conexao
        self._dia = dia
        self._plataforma = plataforma
        self.reaproveitadas = 0

    def obter(self, consulta: str, pagina: int) -> list[dict] | None:
        linha = self._conexao.execute(
            "SELECT nodes FROM pagina_bruta WHERE dia = ? AND plataforma = ? "
            "AND consulta = ? AND pagina = ?",
            (self._dia, self._plataforma, consulta, pagina),
        ).fetchone()
        if linha is None:
            return None
        self.reaproveitadas += 1
        return json.loads(linha["nodes"])

    def guardar(self, consulta: str, pagina: int, nodes: list[dict]) -> None:
        self._conexao.execute(
            """
            INSERT INTO pagina_bruta
                (dia, plataforma, consulta, pagina, nodes, obtido_em)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (dia, plataforma, consulta, pagina) DO UPDATE SET
                nodes = excluded.nodes, obtido_em = excluded.obtido_em
            """,
            (self._dia, self._plataforma, consulta, pagina,
             json.dumps(nodes, ensure_ascii=False), iso_utc()),
        )
        self._conexao.commit()  # aguenta o processo morrer no meio


def limpar_paginas(conexao: sqlite3.Connection, dia: str,
                   plataforma: str | None = None) -> int:
    """Descarta o checkpoint. Chamar depois de uma rodada completa: manter
    paginas velhas faria a proxima coleta devolver dado do dia anterior."""
    cursor = conexao.execute(
        "DELETE FROM pagina_bruta WHERE dia = :dia "
        "AND (:plataforma IS NULL OR plataforma = :plataforma)",
        {"dia": dia, "plataforma": plataforma},
    )
    return cursor.rowcount


def paginas_guardadas(conexao: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conexao.execute(
        "SELECT dia, plataforma, consulta, COUNT(*) paginas FROM pagina_bruta "
        "GROUP BY dia, plataforma, consulta ORDER BY dia DESC"
    ))


def abrir_rodada(conexao: sqlite3.Connection, comando: str, dia: str) -> int:
    cursor = conexao.execute(
        "INSERT INTO rodada (comando, dia, iniciado_em) VALUES (?, ?, ?)",
        (comando, dia, iso_utc()),
    )
    return int(cursor.lastrowid)


def fechar_rodada(
    conexao: sqlite3.Connection, rodada_id: int, etapas: dict,
    erro: str | None = None,
) -> str:
    """Estado derivado das etapas: tudo ok, parcial, ou erro.

    `etapas` mapeia nome -> quantidade gravada, ou None quando a etapa nem
    chegou a rodar. Uma etapa que falhou vira `parcial`, nao sucesso.
    """
    executadas = [v for v in etapas.values() if v is not None]
    if erro and not executadas:
        estado = ESTADO_ERRO
    elif erro or any(v is None for v in etapas.values()):
        estado = ESTADO_PARCIAL
    else:
        estado = ESTADO_OK
    conexao.execute(
        "UPDATE rodada SET finalizado_em = ?, estado = ?, etapas = ?, erro = ? "
        "WHERE id = ?",
        (iso_utc(), estado, json.dumps(etapas, ensure_ascii=False),
         (erro or "")[:400] or None, rodada_id),
    )
    return estado


def ultima_rodada(conexao: sqlite3.Connection) -> sqlite3.Row | None:
    """Desempata pelo id: `iniciado_em` tem resolucao de segundo, e duas
    rodadas no mesmo segundo devolveriam ordem arbitraria."""
    return conexao.execute(
        "SELECT * FROM rodada WHERE comando = 'coletar' "
        "ORDER BY iniciado_em DESC, id DESC LIMIT 1"
    ).fetchone()


def ultima_rodada_completa(conexao: sqlite3.Connection) -> sqlite3.Row | None:
    return conexao.execute(
        "SELECT * FROM rodada WHERE comando = 'coletar' AND estado = ? "
        "ORDER BY iniciado_em DESC, id DESC LIMIT 1",
        (ESTADO_OK,),
    ).fetchone()


def velocidade_vendas(
    conexao: sqlite3.Connection, dia: str, plataforma: str | None = None
) -> dict[tuple[str, str], dict]:
    """Quantas unidades cada produto vendeu desde a coleta anterior.

    A API nao expoe estoque (confirmado por introspecao: os 25 campos de
    productOfferV2 nao tem nada de inventario). O que da para medir e o
    movimento: comparando o total de vendas entre dois retratos, aparece o
    que esta acelerando e o que parou.

    Produto acelerando e o que tem mais chance de esgotar -- e a informacao
    mais proxima de estoque que existe aqui.
    """
    resultado: dict[tuple[str, str], dict] = {}
    for linha in conexao.execute(
        """
        WITH anterior AS (
            SELECT o.plataforma, o.item_id, o.vendas, o.dia,
                   ROW_NUMBER() OVER (
                       PARTITION BY o.plataforma, o.item_id ORDER BY o.dia DESC
                   ) AS ordem
            FROM oferta_dia o
            WHERE o.dia < :dia
              AND (:plataforma IS NULL OR o.plataforma = :plataforma)
        )
        SELECT
            h.plataforma, h.item_id,
            h.vendas - a.vendas AS delta,
            julianday(:dia) - julianday(a.dia) AS dias,
            a.dia AS dia_base
        FROM oferta_dia h
        JOIN anterior a
          ON a.plataforma = h.plataforma AND a.item_id = h.item_id AND a.ordem = 1
        WHERE h.dia = :dia
          AND (:plataforma IS NULL OR h.plataforma = :plataforma)
        """,
        {"dia": dia, "plataforma": plataforma},
    ):
        dias = max(1.0, linha["dias"] or 1.0)
        delta = linha["delta"] or 0
        resultado[(linha["plataforma"], linha["item_id"])] = {
            "delta": delta,
            "dias": round(dias),
            "por_dia": round(delta / dias, 1),
            "desde": linha["dia_base"],
        }
    return resultado


def dias_sem_aparecer(
    conexao: sqlite3.Connection, hoje: str
) -> dict[tuple[str, str], int | None]:
    """Ha quantos dias cada item nao aparece na coleta.

    Item que some da API geralmente esgotou ou saiu do ar -- e link morto na
    sua vitrine e no seu post. `None` significa que nunca foi coletado.
    """
    resultado: dict[tuple[str, str], int | None] = {}
    for linha in conexao.execute(
        """
        SELECT v.plataforma, v.item_id,
               (SELECT MAX(o.dia) FROM oferta_dia o
                 WHERE o.plataforma = v.plataforma AND o.item_id = v.item_id) AS visto
        FROM (SELECT plataforma, item_id FROM vitrine
              UNION SELECT plataforma, item_id FROM preco_alvo) v
        """
    ):
        visto = linha["visto"]
        if not visto:
            resultado[(linha["plataforma"], linha["item_id"])] = None
            continue
        atraso = (date.fromisoformat(hoje) - date.fromisoformat(visto)).days
        resultado[(linha["plataforma"], linha["item_id"])] = atraso
    return resultado


def pares_comprados_juntos(
    conexao: sqlite3.Connection, minimo: int = 2
) -> list[sqlite3.Row]:
    """Itens que apareceram no mesmo pedido.

    `minimo` existe porque um par que aconteceu uma vez e coincidencia, nao
    padrao. Com pouca amostra a lista sai vazia -- e melhor vazia do que
    sugerindo combinacao inventada.
    """
    # Conversoes gravadas antes de `pedido_id` existir ainda podem ser
    # agrupadas: o conversao_id foi montado como "{conversionId}:{itemId}",
    # entao o prefixo identifica o mesmo pedido. Sem esse resgate a analise
    # so funcionaria para dados coletados dali em diante.
    return list(conexao.execute(
        """
        WITH itens AS (
            SELECT
                plataforma, item_id, comissao,
                CASE
                    WHEN pedido_id IS NOT NULL AND pedido_id <> '' THEN pedido_id
                    WHEN instr(conversao_id, ':') > 1
                        THEN substr(conversao_id, 1, instr(conversao_id, ':') - 1)
                END AS grupo
            FROM conversao
            WHERE item_id IS NOT NULL
        )
        SELECT
            a.item_id AS item_a,
            b.item_id AS item_b,
            COUNT(*)  AS juntos,
            SUM(a.comissao + b.comissao) AS comissao,
            (SELECT o.nome FROM oferta_dia o WHERE o.item_id = a.item_id
              ORDER BY o.dia DESC LIMIT 1) AS nome_a,
            (SELECT o.nome FROM oferta_dia o WHERE o.item_id = b.item_id
              ORDER BY o.dia DESC LIMIT 1) AS nome_b
        FROM itens a
        JOIN itens b
          ON a.grupo = b.grupo
         AND a.plataforma = b.plataforma
         AND a.item_id < b.item_id
        WHERE a.grupo IS NOT NULL
        GROUP BY a.item_id, b.item_id
        HAVING COUNT(*) >= :minimo
        ORDER BY juntos DESC, comissao DESC
        """,
        {"minimo": minimo},
    ))


def tempo_ate_comprar(conexao: sqlite3.Connection) -> list[int]:
    """Dias entre o clique e a compra, por conversao.

    O cookie da Shopee dura 7 dias. Saber onde a maioria cai nesse intervalo
    diz quando vale repostar antes de perder a atribuicao.
    """
    dias = []
    for linha in conexao.execute(
        "SELECT clicado_em, ocorrido_em FROM conversao "
        "WHERE clicado_em IS NOT NULL AND ocorrido_em IS NOT NULL"
    ):
        try:
            delta = (date.fromisoformat(linha["ocorrido_em"])
                     - date.fromisoformat(linha["clicado_em"])).days
        except ValueError:
            continue
        if 0 <= delta <= 30:
            dias.append(delta)
    return sorted(dias)


def definir_alvo(
    conexao: sqlite3.Connection, plataforma: str, item_id: str,
    alvo: float, nome: str | None = None,
) -> None:
    conexao.execute(
        """
        INSERT INTO preco_alvo (plataforma, item_id, alvo, nome, criado_em)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (plataforma, item_id) DO UPDATE SET
            alvo = excluded.alvo, nome = COALESCE(excluded.nome, preco_alvo.nome),
            disparado_em = NULL
        """,
        (plataforma, item_id, alvo, nome, iso_utc()),
    )


def remover_alvo(conexao: sqlite3.Connection, plataforma: str, item_id: str) -> bool:
    cursor = conexao.execute(
        "DELETE FROM preco_alvo WHERE plataforma = ? AND item_id = ?",
        (plataforma, item_id),
    )
    return cursor.rowcount > 0


def alvos(conexao: sqlite3.Connection) -> list[sqlite3.Row]:
    """Alvos com o preco mais recente observado de cada item."""
    return list(conexao.execute(
        """
        SELECT
            a.plataforma, a.item_id, a.alvo, a.disparado_em,
            COALESCE(a.nome, o.nome) AS nome,
            o.preco AS preco_atual,
            o.dia AS dia_preco,
            o.link_oferta AS link
        FROM preco_alvo a
        LEFT JOIN oferta_dia o
          ON o.plataforma = a.plataforma AND o.item_id = a.item_id
         AND o.dia = (SELECT MAX(dia) FROM oferta_dia x
                       WHERE x.plataforma = a.plataforma AND x.item_id = a.item_id)
        ORDER BY a.criado_em DESC
        """
    ))


def marcar_alvo_disparado(
    conexao: sqlite3.Connection, plataforma: str, item_id: str
) -> None:
    conexao.execute(
        "UPDATE preco_alvo SET disparado_em = ? WHERE plataforma = ? AND item_id = ?",
        (iso_utc(), plataforma, item_id),
    )


def salvar_cliques(conexao: sqlite3.Connection, registros: Iterable[dict]) -> int:
    linhas = [{**r, "importado_em": iso_utc()} for r in registros]
    if not linhas:
        return 0
    conexao.executemany(
        """
        INSERT INTO clique (plataforma, codigo, dia, item_id, canal, total, importado_em)
        VALUES (:plataforma, :codigo, :dia, :item_id, :canal, :total, :importado_em)
        ON CONFLICT (plataforma, codigo, dia) DO UPDATE SET
            total = excluded.total,
            item_id = COALESCE(excluded.item_id, clique.item_id),
            canal = COALESCE(excluded.canal, clique.canal),
            importado_em = excluded.importado_em
        """,
        linhas,
    )
    return len(linhas)


def cliques_por_item(
    conexao: sqlite3.Connection, desde: str | None = None
) -> dict[tuple[str, str], int]:
    return {
        (r["plataforma"], r["item_id"]): r["total"]
        for r in conexao.execute(
            "SELECT plataforma, item_id, SUM(total) AS total FROM clique "
            "WHERE item_id IS NOT NULL AND (:desde IS NULL OR dia >= :desde) "
            "GROUP BY plataforma, item_id",
            {"desde": desde},
        )
    }


def cliques_por_canal(
    conexao: sqlite3.Connection, desde: str | None = None
) -> list[sqlite3.Row]:
    return list(conexao.execute(
        "SELECT COALESCE(canal, '(sem canal)') AS canal, SUM(total) AS cliques, "
        "COUNT(DISTINCT item_id) AS itens FROM clique "
        "WHERE (:desde IS NULL OR dia >= :desde) GROUP BY canal ORDER BY cliques DESC",
        {"desde": desde},
    ))


def categorias_vistas(
    conexao: sqlite3.Connection, plataforma: str = "shopee"
) -> list[dict]:
    """Categorias que apareceram na coleta, com volume e comissao media.

    Serve para descobrir os productCatId reais da sua conta em vez de
    adivinhar IDs de catalogo.
    """
    contagem: dict[str, dict] = {}
    for linha in conexao.execute(
        "SELECT categoria_ids, taxa_comissao, score FROM oferta_dia "
        "WHERE plataforma = ? AND categoria_ids IS NOT NULL AND categoria_ids != ''",
        (plataforma,),
    ):
        for categoria in str(linha["categoria_ids"]).split(","):
            categoria = categoria.strip()
            if not categoria:
                continue
            atual = contagem.setdefault(
                categoria, {"categoria_id": categoria, "itens": 0,
                            "soma_comissao": 0.0, "melhor_epc": 0.0}
            )
            atual["itens"] += 1
            atual["soma_comissao"] += linha["taxa_comissao"]
            atual["melhor_epc"] = max(atual["melhor_epc"], linha["score"])

    resultado = []
    for dados in contagem.values():
        resultado.append({
            "categoria_id": dados["categoria_id"],
            "itens": dados["itens"],
            "comissao_media": dados["soma_comissao"] / dados["itens"],
            "melhor_epc": dados["melhor_epc"],
        })
    return sorted(resultado, key=lambda d: d["melhor_epc"], reverse=True)


def salvar_cvr_calibrado(conexao: sqlite3.Connection, registros: Iterable[dict]) -> int:
    linhas = [{**r, "atualizado_em": iso_utc()} for r in registros]
    if not linhas:
        return 0
    conexao.executemany(
        """
        INSERT INTO cvr_calibrado
            (plataforma, escopo, chave, cliques, pedidos, cvr, atualizado_em)
        VALUES (:plataforma, :escopo, :chave, :cliques, :pedidos, :cvr, :atualizado_em)
        ON CONFLICT (plataforma, escopo, chave) DO UPDATE SET
            cliques = excluded.cliques, pedidos = excluded.pedidos,
            cvr = excluded.cvr, atualizado_em = excluded.atualizado_em
        """,
        linhas,
    )
    return len(linhas)


def carregar_cvr_calibrado(conexao: sqlite3.Connection) -> dict[tuple[str, str, str], float]:
    return {
        (r["plataforma"], r["escopo"], r["chave"]): r["cvr"]
        for r in conexao.execute("SELECT * FROM cvr_calibrado")
    }


def buscar_links(
    conexao: sqlite3.Connection, plataforma: str, sub_ids: str = ""
) -> dict[str, str]:
    """Links curtos ja gerados, para nao gastar chamada de API de novo."""
    return {
        r["url_origem"]: r["link_curto"]
        for r in conexao.execute(
            "SELECT url_origem, link_curto FROM link_gerado "
            "WHERE plataforma = ? AND sub_ids = ?",
            (plataforma, sub_ids),
        )
    }


def item_id_da_url(url: str) -> str | None:
    """Extrai o item da URL de produto da Shopee.

    O formato e .../product/{lojaId}/{itemId} ou .../{slug}.{lojaId}.{itemId}.
    Serve para saber QUAL produto foi compartilhado -- o link curto sozinho
    nao diz, e sem isso nao da para cruzar com cliques e vendas.
    """
    if not url:
        return None
    caminho = url.split("?")[0].rstrip("/")
    fatias = caminho.rsplit("/", 1)
    if len(fatias) != 2:
        return None
    final = fatias[1]
    if final.isdigit():
        return final
    # slug.lojaId.itemId
    partes = final.split(".")
    if len(partes) >= 3 and partes[-1].isdigit():
        return partes[-1]
    return None


def salvar_link(conexao: sqlite3.Connection, plataforma: str, registro: dict) -> None:
    item_id = registro.get("item_id") or item_id_da_url(registro["url_origem"])
    conexao.execute(
        """
        INSERT INTO link_gerado
            (plataforma, url_origem, sub_ids, link_curto, item_id, gerado_em)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (plataforma, url_origem, sub_ids) DO UPDATE SET
            link_curto = excluded.link_curto,
            item_id    = COALESCE(excluded.item_id, link_gerado.item_id),
            gerado_em  = excluded.gerado_em
        """,
        (plataforma, registro["url_origem"], registro.get("sub_ids", ""),
         registro["link_curto"], item_id, registro["gerado_em"]),
    )


def vendas_por_canal(
    conexao: sqlite3.Connection, desde: str | None = None
) -> list[sqlite3.Row]:
    """Vendas agrupadas pela marcacao que veio no link.

    Vem do `utmContent` do relatorio de conversao -- e o unico dado de
    atribuicao que a Shopee fornece. Funciona para qualquer sub-id, inclusive
    os criados fora daqui, como campanhas de trafego pago.
    """
    return list(conexao.execute(
        """
        SELECT
            COALESCE(NULLIF(canal, ''), '(sem marcacao)') AS canal,
            COUNT(*)                 AS vendas,
            SUM(pedidos)             AS unidades,
            SUM(comissao)            AS comissao,
            AVG(comissao)            AS ticket,
            COUNT(DISTINCT item_id)  AS produtos,
            MIN(ocorrido_em)         AS primeira,
            MAX(ocorrido_em)         AS ultima
        FROM conversao
        WHERE (:desde IS NULL OR COALESCE(ocorrido_em, substr(coletado_em, 1, 10)) >= :desde)
        GROUP BY canal
        ORDER BY comissao DESC
        """,
        {"desde": desde},
    ))


def compartilhados(
    conexao: sqlite3.Connection, desde: str | None = None
) -> list[sqlite3.Row]:
    """O que voce divulgou, com cliques e vendas de cada item.

    Junta tres fontes: o link que voce gerou (aqui), os cliques do
    redirecionador e as conversoes da API. Cada uma sozinha conta metade da
    historia -- gerar link nao e divulgar, e venda sem clique nao diz o
    caminho que o comprador fez.
    """
    return list(conexao.execute(
        """
        WITH cliques AS (
            SELECT item_id, canal, SUM(total) AS total, MAX(dia) AS ultimo
            FROM clique WHERE item_id IS NOT NULL
            GROUP BY item_id, canal
        ), vendas AS (
            SELECT item_id, COUNT(*) AS pedidos, SUM(comissao) AS comissao,
                   MAX(ocorrido_em) AS ultima
            FROM conversao WHERE item_id IS NOT NULL
            GROUP BY item_id
        )
        SELECT
            g.plataforma,
            g.item_id,
            g.sub_ids                          AS canal,
            g.link_curto,
            g.gerado_em,
            COALESCE(c.total, 0)               AS cliques,
            c.ultimo                           AS ultimo_clique,
            COALESCE(v.pedidos, 0)             AS pedidos,
            COALESCE(v.comissao, 0)            AS comissao,
            v.ultima                           AS ultima_venda,
            (SELECT o.nome FROM oferta_dia o
              WHERE o.plataforma = g.plataforma AND o.item_id = g.item_id
              ORDER BY o.dia DESC LIMIT 1)     AS nome,
            (SELECT o.imagem FROM oferta_dia o
              WHERE o.plataforma = g.plataforma AND o.item_id = g.item_id
              ORDER BY o.dia DESC LIMIT 1)     AS imagem,
            (SELECT o.comissao_valor FROM oferta_dia o
              WHERE o.plataforma = g.plataforma AND o.item_id = g.item_id
              ORDER BY o.dia DESC LIMIT 1)     AS comissao_prevista
        FROM link_gerado g
        LEFT JOIN cliques c
               ON c.item_id = g.item_id
              AND COALESCE(c.canal, '') = g.sub_ids
        LEFT JOIN vendas v ON v.item_id = g.item_id
        WHERE g.item_id IS NOT NULL
          AND (:desde IS NULL OR substr(g.gerado_em, 1, 10) >= :desde)
        ORDER BY g.gerado_em DESC
        """,
        {"desde": desde},
    ))


def historico(
    conexao: sqlite3.Connection,
    dias_minimos: int = 1,
    plataforma: str | None = None,
) -> list[LinhaHistorico]:
    sql = """
    SELECT
        plataforma,
        item_id,
        MAX(nome)                       AS nome,
        MAX(loja_nome)                  AS loja_nome,
        MAX(link_oferta)                AS link_oferta,
        COUNT(DISTINCT dia)             AS dias_visto,
        MIN(dia)                        AS primeiro_dia,
        MAX(dia)                        AS ultimo_dia,
        AVG(score)                      AS score_medio,
        MAX(score)                      AS score_max,
        AVG(comissao_valor)             AS comissao_media,
        AVG(preco)                      AS preco_medio,
        MAX(vendas) - MIN(vendas)       AS vendas_delta
    FROM oferta_dia
    WHERE (:plataforma IS NULL OR plataforma = :plataforma)
    GROUP BY plataforma, item_id
    HAVING COUNT(DISTINCT dia) >= :dias_minimos
    """
    linhas = conexao.execute(
        sql, {"plataforma": plataforma, "dias_minimos": dias_minimos}
    )
    return [LinhaHistorico(**dict(linha)) for linha in linhas]
