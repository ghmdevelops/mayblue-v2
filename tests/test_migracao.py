"""Migracao de banco que ja existe em campo.

Este arquivo existe por causa de um bug real: eu adicionei colunas ao esquema
e um indice sobre elas. Todos os testes passaram, porque todos criam banco
novo -- e `CREATE TABLE IF NOT EXISTS` cria a tabela ja com as colunas.

No banco de quem ja usava o programa, a tabela existia SEM as colunas, o
`CREATE INDEX` referenciava coluna inexistente e o programa parava de abrir.
Suite verde e usuario com o banco quebrado.

A licao: teste de esquema precisa partir de um banco ANTIGO, nao de um vazio.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db

# Esquema anterior a introducao de pedido_id e clicado_em.
ESQUEMA_ANTIGO = """
CREATE TABLE conversao (
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
    coletado_em     TEXT    NOT NULL,
    PRIMARY KEY (plataforma, conversao_id, validada)
);
"""


class TestMigracao(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "antigo.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def criar_banco_antigo(self, com_dados=True):
        conexao = sqlite3.connect(self.caminho)
        conexao.executescript(ESQUEMA_ANTIGO)
        if com_dados:
            conexao.execute(
                "INSERT INTO conversao (plataforma, conversao_id, validada, "
                "pedidos, comissao, item_id, ocorrido_em, coletado_em) "
                "VALUES ('shopee', 'c1', 0, 1, 5.5, '123', '2026-08-01', 'x')"
            )
        conexao.commit()
        conexao.close()

    def colunas(self, tabela="conversao"):
        conexao = sqlite3.connect(self.caminho)
        nomes = {linha[1] for linha in conexao.execute(f"PRAGMA table_info({tabela})")}
        conexao.close()
        return nomes

    def test_banco_antigo_abre_sem_erro(self):
        """Era exatamente isto que quebrava: OperationalError no CREATE INDEX."""
        self.criar_banco_antigo()
        with db.conectar(self.caminho) as conexao:
            self.assertIsNotNone(conexao)

    def test_colunas_novas_sao_adicionadas(self):
        self.criar_banco_antigo()
        self.assertNotIn("pedido_id", self.colunas())
        with db.conectar(self.caminho):
            pass
        self.assertIn("pedido_id", self.colunas())
        self.assertIn("clicado_em", self.colunas())

    def test_dados_existentes_sobrevivem(self):
        self.criar_banco_antigo()
        with db.conectar(self.caminho) as conexao:
            linha = conexao.execute(
                "SELECT item_id, comissao, pedido_id FROM conversao"
            ).fetchone()
        self.assertEqual(linha["item_id"], "123")
        self.assertAlmostEqual(linha["comissao"], 5.5)
        self.assertIsNone(linha["pedido_id"])

    def test_migracao_e_idempotente(self):
        self.criar_banco_antigo()
        for _ in range(3):
            with db.conectar(self.caminho):
                pass
        self.assertIn("pedido_id", self.colunas())

    def test_banco_novo_nao_precisa_migrar(self):
        with db.conectar(self.caminho) as conexao:
            aplicadas = db._migrar(conexao)
        self.assertEqual(aplicadas, [])
        self.assertIn("pedido_id", self.colunas())

    def test_relata_o_que_migrou(self):
        """Lista so as colunas que faltavam na tabela que existe.

        Nao fixa a lista inteira de MIGRACOES de proposito: colunas novas
        entram com o tempo, e o teste quebraria a cada uma sem apontar
        defeito nenhum.
        """
        self.criar_banco_antigo(com_dados=False)
        conexao = sqlite3.connect(self.caminho)
        conexao.row_factory = sqlite3.Row
        try:
            aplicadas = db._migrar(conexao)
        finally:
            conexao.close()

        esperadas = [f"conversao.{coluna}" for tabela, coluna, _ in db.MIGRACOES
                     if tabela == "conversao"]
        self.assertEqual(aplicadas, esperadas)
        self.assertIn("conversao.pedido_id", aplicadas)

    def test_indices_novos_funcionam_depois_da_migracao(self):
        self.criar_banco_antigo()
        with db.conectar(self.caminho) as conexao:
            indices = {
                linha["name"] for linha in conexao.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertIn("idx_conversao_pedido", indices)

    def test_consultas_novas_rodam_em_banco_migrado(self):
        self.criar_banco_antigo()
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.pares_comprados_juntos(conexao), [])
            self.assertEqual(db.tempo_ate_comprar(conexao), [])
            self.assertEqual(db.dias_sem_aparecer(conexao, "2026-09-04"), {})


if __name__ == "__main__":
    unittest.main()
