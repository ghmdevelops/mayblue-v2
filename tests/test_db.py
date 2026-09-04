from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.config import ScoringConfig
from flow02.models import Oferta
from flow02.scoring import avaliar

CFG = ScoringConfig()


def oferta(item_id="1", **kwargs) -> Oferta:
    base = dict(
        plataforma="shopee",
        item_id=item_id,
        nome=f"produto {item_id}",
        preco=100.0,
        taxa_comissao=0.10,
        origem_consulta="teste",
        vendas=1000,
        rating=4.5,
        desconto_pct=10.0,
        loja_id="loja-a",
        loja_nome="Loja A",
        link_oferta=f"https://s.shopee.com.br/{item_id}",
    )
    base.update(kwargs)
    return Oferta(**base)


class BaseDB(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "sub" / "teste.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def salvar(self, ofertas, dia):
        with db.conectar(self.caminho) as conexao:
            return db.salvar_ofertas(
                conexao, [(o, avaliar(o, CFG)) for o in ofertas], dia
            )


class TestEsquema(BaseDB):
    def test_cria_diretorio_e_tabelas(self):
        with db.conectar(self.caminho) as conexao:
            tabelas = {
                r["name"] for r in conexao.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertTrue(self.caminho.exists())
        self.assertIn("oferta_dia", tabelas)
        self.assertIn("execucao", tabelas)

    def test_conectar_e_idempotente(self):
        self.salvar([oferta()], "2026-09-01")
        self.salvar([oferta("2")], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            total = conexao.execute("SELECT COUNT(*) c FROM oferta_dia").fetchone()["c"]
        self.assertEqual(total, 2)


class TestUpsert(BaseDB):
    def test_mesmo_item_no_mesmo_dia_nao_duplica(self):
        self.salvar([oferta()], "2026-09-01")
        self.salvar([oferta()], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            total = conexao.execute("SELECT COUNT(*) c FROM oferta_dia").fetchone()["c"]
        self.assertEqual(total, 1)

    def test_upsert_mantem_o_maior_score(self):
        self.salvar([oferta(taxa_comissao=0.20)], "2026-09-01")
        self.salvar([oferta(taxa_comissao=0.02)], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            linha = conexao.execute("SELECT taxa_comissao FROM oferta_dia").fetchone()
        self.assertAlmostEqual(linha["taxa_comissao"], 0.20)

    def test_upsert_atualiza_quando_score_melhora(self):
        self.salvar([oferta(taxa_comissao=0.02)], "2026-09-01")
        self.salvar([oferta(taxa_comissao=0.20)], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            linha = conexao.execute("SELECT taxa_comissao FROM oferta_dia").fetchone()
        self.assertAlmostEqual(linha["taxa_comissao"], 0.20)

    def test_dias_diferentes_geram_snapshots_separados(self):
        self.salvar([oferta()], "2026-09-01")
        self.salvar([oferta()], "2026-09-02")
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.dias_disponiveis(conexao), ["2026-09-02", "2026-09-01"])

    def test_lista_vazia_nao_falha(self):
        self.assertEqual(self.salvar([], "2026-09-01"), 0)


class TestRankingDia(BaseDB):
    def test_ordena_por_score_desc(self):
        self.salvar([
            oferta("1", taxa_comissao=0.02),
            oferta("2", taxa_comissao=0.20),
            oferta("3", taxa_comissao=0.10),
        ], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            linhas = db.ranking_dia(conexao, "2026-09-01", 10)
        self.assertEqual([l["item_id"] for l in linhas], ["2", "3", "1"])

    def test_respeita_limite(self):
        self.salvar([oferta(str(i)) for i in range(10)], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(len(db.ranking_dia(conexao, "2026-09-01", 3)), 3)

    def test_diversificacao_por_loja(self):
        ofertas = [
            oferta(str(i), loja_id="loja-a", taxa_comissao=0.20 - i * 0.01)
            for i in range(5)
        ]
        ofertas.append(oferta("99", loja_id="loja-b", taxa_comissao=0.01))
        self.salvar(ofertas, "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            linhas = db.ranking_dia(conexao, "2026-09-01", 10, max_por_loja=2)
        lojas = [l["loja_id"] for l in linhas]
        self.assertEqual(lojas.count("loja-a"), 2)
        self.assertEqual(lojas.count("loja-b"), 1)

    def test_sem_diversificacao_traz_tudo(self):
        self.salvar([oferta(str(i), loja_id="loja-a") for i in range(5)], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(len(db.ranking_dia(conexao, "2026-09-01", 10, 0)), 5)

    def test_filtro_por_plataforma(self):
        self.salvar([
            oferta("1", plataforma="shopee"),
            oferta("2", plataforma="mercadolivre"),
        ], "2026-09-01")
        with db.conectar(self.caminho) as conexao:
            linhas = db.ranking_dia(conexao, "2026-09-01", 10, 0, "mercadolivre")
        self.assertEqual([l["item_id"] for l in linhas], ["2"])

    def test_dia_sem_dados_retorna_vazio(self):
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.ranking_dia(conexao, "1999-01-01", 10), [])


class TestHistorico(BaseDB):
    def test_agrega_por_item_e_conta_dias(self):
        self.salvar([oferta("1")], "2026-09-01")
        self.salvar([oferta("1")], "2026-09-02")
        self.salvar([oferta("2")], "2026-09-02")
        with db.conectar(self.caminho) as conexao:
            registros = {r.item_id: r for r in db.historico(conexao, dias_minimos=1)}
        self.assertEqual(registros["1"].dias_visto, 2)
        self.assertEqual(registros["1"].primeiro_dia, "2026-09-01")
        self.assertEqual(registros["1"].ultimo_dia, "2026-09-02")
        self.assertEqual(registros["2"].dias_visto, 1)

    def test_dias_minimos_filtra_ofertas_pontuais(self):
        self.salvar([oferta("1")], "2026-09-01")
        self.salvar([oferta("1")], "2026-09-02")
        self.salvar([oferta("2")], "2026-09-02")
        with db.conectar(self.caminho) as conexao:
            registros = db.historico(conexao, dias_minimos=2)
        self.assertEqual([r.item_id for r in registros], ["1"])

    def test_vendas_delta_mede_velocidade_real(self):
        """O delta de vendas entre dias e o sinal de venda real do periodo."""
        self.salvar([oferta("1", vendas=1000)], "2026-09-01")
        self.salvar([oferta("1", vendas=1250)], "2026-09-02")
        with db.conectar(self.caminho) as conexao:
            registro = db.historico(conexao, dias_minimos=1)[0]
        self.assertEqual(registro.vendas_delta, 250)


class TestExecucao(BaseDB):
    def test_registra_e_finaliza(self):
        with db.conectar(self.caminho) as conexao:
            execucao_id = db.registrar_execucao(conexao, "shopee")
            db.finalizar_execucao(conexao, execucao_id, brutas=120, salvas=40)
            linha = conexao.execute(
                "SELECT * FROM execucao WHERE id = ?", (execucao_id,)
            ).fetchone()
        self.assertEqual(linha["plataforma"], "shopee")
        self.assertEqual(linha["ofertas_brutas"], 120)
        self.assertEqual(linha["ofertas_salvas"], 40)
        self.assertIsNotNone(linha["finalizado_em"])
        self.assertIsNone(linha["erro"])

    def test_registra_erro(self):
        with db.conectar(self.caminho) as conexao:
            execucao_id = db.registrar_execucao(conexao, "shopee")
            db.finalizar_execucao(conexao, execucao_id, 0, 0, erro="rate limit")
            linha = conexao.execute(
                "SELECT erro FROM execucao WHERE id = ?", (execucao_id,)
            ).fetchone()
        self.assertEqual(linha["erro"], "rate limit")


if __name__ == "__main__":
    unittest.main()
