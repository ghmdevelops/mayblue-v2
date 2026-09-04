"""Copia de seguranca do banco.

Duas camadas, porque protegem contra coisas diferentes:

- copia local rotativa: protege contra corrupcao do arquivo e contra a propria
  ferramenta gravar besteira. Nao protega contra perder a maquina.
- copia no Firebase: protege contra perder a maquina. Vai so o que nao da
  para refazer -- as conversoes, que a Shopee devolve numa janela limitada,
  e a curadoria, que e trabalho manual seu. Ofertas nao vao: sao recolhiveis
  a qualquer momento e ocupariam espaco a toa.

A copia local usa a API de backup do proprio SQLite em vez de copiar o
arquivo: copiar arquivo aberto em modo WAL pode gerar copia inconsistente.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .tempo import iso_utc

SUFIXO = ".bak.sqlite3"


@dataclass(frozen=True, slots=True)
class ResultadoBackup:
    caminho: Path
    bytes_: int
    removidos: int


def copiar_local(
    origem: Path, pasta: Path | None = None, manter: int = 7
) -> ResultadoBackup:
    """Copia consistente do banco, mantendo apenas as N mais recentes."""
    pasta = pasta or (origem.parent / "backups")
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = iso_utc().replace(":", "").replace("-", "")[:15]
    destino = pasta / f"{origem.stem}-{carimbo}{SUFIXO}"

    fonte = sqlite3.connect(origem)
    try:
        alvo = sqlite3.connect(destino)
        try:
            fonte.backup(alvo)
        finally:
            alvo.close()
    finally:
        fonte.close()

    antigos = sorted(pasta.glob(f"*{SUFIXO}"), reverse=True)[max(0, manter):]
    for arquivo in antigos:
        arquivo.unlink(missing_ok=True)

    return ResultadoBackup(destino, destino.stat().st_size, len(antigos))


def exportar_para_nuvem(conexao: sqlite3.Connection) -> dict:
    """Monta o pacote que vai para o Firebase.

    Chaves com ponto, cifrao, colchete, barra ou # sao invalidas na Realtime
    Database, entao ids viram chaves saneadas.
    """
    def limpar(chave) -> str:
        texto = str(chave)
        for proibido in ".$#[]/":
            texto = texto.replace(proibido, "_")
        return texto or "_"

    conversoes = {
        limpar(linha["conversao_id"]): dict(linha)
        for linha in conexao.execute("SELECT * FROM conversao")
    }
    vitrine = {
        f"{limpar(linha['colecao'])}__{limpar(linha['item_id'])}": dict(linha)
        for linha in conexao.execute("SELECT * FROM vitrine")
    }
    alvos = {
        limpar(linha["item_id"]): dict(linha)
        for linha in conexao.execute("SELECT * FROM preco_alvo")
    }
    cliques = {
        f"{limpar(linha['dia'])}__{limpar(linha['codigo'])}": dict(linha)
        for linha in conexao.execute("SELECT * FROM clique")
    }
    return {
        "em": iso_utc(),
        "conversoes": conversoes,
        "vitrine": vitrine,
        "alvos": alvos,
        "cliques": cliques,
        "contagem": {
            "conversoes": len(conversoes), "vitrine": len(vitrine),
            "alvos": len(alvos), "cliques": len(cliques),
        },
    }


def restaurar_da_nuvem(conexao: sqlite3.Connection, pacote: dict) -> dict:
    """Regrava no banco o que veio do Firebase, sem apagar o que ja existe."""
    from . import db

    contagem = {}
    conversoes = list((pacote.get("conversoes") or {}).values())
    if conversoes:
        conexao.executemany(
            """
            INSERT OR REPLACE INTO conversao
              (plataforma, conversao_id, validada, cliques, pedidos, comissao,
               item_id, loja_id, ocorrido_em, status, pedido_id, clicado_em,
               coletado_em)
            VALUES (:plataforma, :conversao_id, :validada, :cliques, :pedidos,
                    :comissao, :item_id, :loja_id, :ocorrido_em, :status,
                    :pedido_id, :clicado_em, :coletado_em)
            """,
            [{**{c: None for c in (
                "pedido_id", "clicado_em", "item_id", "loja_id", "ocorrido_em",
                "status")}, **linha} for linha in conversoes],
        )
    contagem["conversoes"] = len(conversoes)

    for linha in (pacote.get("vitrine") or {}).values():
        db.adicionar_vitrine(conexao, linha["plataforma"], linha["item_id"],
                             linha.get("colecao") or db.COLECAO_PADRAO)
    contagem["vitrine"] = len(pacote.get("vitrine") or {})

    for linha in (pacote.get("alvos") or {}).values():
        db.definir_alvo(conexao, linha["plataforma"], linha["item_id"],
                        float(linha["alvo"]), linha.get("nome"))
    contagem["alvos"] = len(pacote.get("alvos") or {})

    cliques = list((pacote.get("cliques") or {}).values())
    if cliques:
        db.salvar_cliques(conexao, cliques)
    contagem["cliques"] = len(cliques)
    return contagem
