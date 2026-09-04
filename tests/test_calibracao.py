from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import calibracao, db
from flow02.config import CalibracaoConfig, ScoringConfig
from flow02.models import Conversao, Oferta
from flow02.scoring import avaliar

CFG_SCORE = ScoringConfig()
CFG_CAL = CalibracaoConfig(cliques_minimos=20, janela_dias=0, peso_prior=150.0)


def conversao(conversao_id, cliques, pedidos, item_id=None, loja_id=None, **kwargs):
    """`cliques` fica no argumento por compatibilidade, mas a API da Shopee
    nao fornece esse dado -- ele entra pela tabela `clique`, alimentada pelo
    redirecionador proprio. Ver `BaseCalibracao.gravar_cliques`."""
    return Conversao(
        plataforma="shopee",
        conversao_id=conversao_id,
        cliques=0,
        pedidos=pedidos,
        comissao=kwargs.pop("comissao", 10.0),
        item_id=item_id,
        loja_id=loja_id,
        ocorrido_em=kwargs.pop("ocorrido_em", "2026-09-01"),
        **kwargs,
    )


def oferta(item_id="1", loja_id="loja-a"):
    return Oferta(
        plataforma="shopee", item_id=item_id, nome="p", preco=100.0,
        taxa_comissao=0.10, origem_consulta="t", loja_id=loja_id,
        vendas=1000, rating=4.5, link_oferta="https://x",
    )


class BaseCalibracao(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "teste.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def gravar(self, conversoes):
        with db.conectar(self.caminho) as conexao:
            return db.salvar_conversoes(conexao, conversoes)

    def gravar_cliques(self, *entradas):
        """(item_id, loja_id, total) -- grava o clique e a oferta que o liga
        a loja, que e como o dado existe na pratica."""
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(conexao, [
                (oferta(item_id, loja_id), avaliar(oferta(item_id, loja_id), CFG_SCORE))
                for item_id, loja_id, _ in entradas
            ], "2026-09-01")
            db.salvar_cliques(conexao, [
                {"plataforma": "shopee", "codigo": f"c{item_id}",
                 "dia": "2026-09-01", "item_id": item_id, "canal": "teste",
                 "total": total}
                for item_id, _, total in entradas
            ])

    def calibrar(self, cfg):
        with db.conectar(self.caminho) as conexao:
            return calibracao.recalcular(conexao, cfg)

    def calibrador(self, cfg=CalibracaoConfig(janela_dias=0)):
        with db.conectar(self.caminho) as conexao:
            return calibracao.carregar(conexao, cfg)


class TestPersistenciaConversoes(BaseCalibracao):
    def test_grava_e_deduplica_por_id(self):
        self.gravar([conversao("c1", 0, 5)])
        self.gravar([conversao("c1", 0, 8)])
        with db.conectar(self.caminho) as conexao:
            linhas = list(conexao.execute("SELECT * FROM conversao"))
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["pedidos"], 8)

    def test_estimada_e_validada_convivem(self):
        self.gravar([
            conversao("c1", 100, 5),
            Conversao("shopee", "c1", 100, 5, 90.0, validada=True),
        ])
        with db.conectar(self.caminho) as conexao:
            total = conexao.execute("SELECT COUNT(*) c FROM conversao").fetchone()["c"]
        self.assertEqual(total, 2)

    def test_ganhos_agrega_por_dia(self):
        self.gravar([
            conversao("c1", 100, 5, comissao=50.0, ocorrido_em="2026-09-01"),
            conversao("c2", 200, 9, comissao=70.0, ocorrido_em="2026-09-01"),
            conversao("c3", 10, 1, comissao=5.0, ocorrido_em="2026-09-02"),
        ])
        with db.conectar(self.caminho) as conexao:
            linhas = {l["dia"]: l for l in db.ganhos(conexao)}
        self.assertAlmostEqual(linhas["2026-09-01"]["comissao"], 120.0)
        self.assertEqual(linhas["2026-09-01"]["pedidos"], 14)
        self.assertEqual(linhas["2026-09-02"]["pedidos"], 1)

    def test_ganhos_filtra_por_validada(self):
        self.gravar([
            conversao("c1", 10, 1, comissao=5.0),
            Conversao("shopee", "c2", 10, 1, 4.0, ocorrido_em="2026-09-01", validada=True),
        ])
        with db.conectar(self.caminho) as conexao:
            linhas = db.ganhos(conexao, validada=True)
        self.assertEqual(len(linhas), 1)
        self.assertAlmostEqual(linhas[0]["comissao"], 4.0)


class TestSuavizacao(unittest.TestCase):
    """A funcao de encolhimento, isolada."""

    def test_sem_amostra_retorna_o_prior(self):
        self.assertAlmostEqual(calibracao.suavizar(0, 0, 0.05, 150), 0.05)

    def test_peso_prior_zero_devolve_a_razao_crua(self):
        self.assertAlmostEqual(calibracao.suavizar(50, 100, 0.02, 0), 0.5)

    def test_amostra_pequena_fica_perto_do_prior(self):
        """3 cliques e 1 pedido dariam 33% cru; encolhido fica perto de 2%."""
        cvr = calibracao.suavizar(1, 3, 0.02, 150)
        self.assertLess(cvr, 0.03)

    def test_amostra_grande_domina_o_prior(self):
        cvr = calibracao.suavizar(4000, 100000, 0.02, 150)
        self.assertAlmostEqual(cvr, 0.04, places=3)

    def test_valor_fica_entre_o_prior_e_a_razao_crua(self):
        cvr = calibracao.suavizar(40, 1000, 0.02, 150)
        self.assertGreater(cvr, 0.02)
        self.assertLess(cvr, 0.04)


class TestRecalculo(BaseCalibracao):
    def test_ignora_amostra_ridicula(self):
        self.gravar_cliques(("1", "loja-a", 3))
        self.gravar([conversao("c1", 0, 1, item_id="1", loja_id="loja-a")])
        resumo = self.calibrar(CalibracaoConfig(cliques_minimos=20, janela_dias=0))
        self.assertEqual(resumo["total"], 0)

    def test_sem_cliques_nao_calibra(self):
        """Conversao sozinha nao basta: sem cliques nao ha taxa de conversao."""
        self.gravar([conversao("c1", 0, 40, item_id="1", loja_id="loja-a")])
        self.assertEqual(self.calibrar(CFG_CAL)["total"], 0)

    def test_aceita_amostra_suficiente(self):
        self.gravar_cliques(("1", "loja-a", 1000))
        self.gravar([conversao("c1", 0, 40, item_id="1", loja_id="loja-a")])
        resumo = self.calibrar(CFG_CAL)
        self.assertEqual(resumo["item"], 1)
        self.assertEqual(resumo["loja"], 1)
        self.assertEqual(resumo["global"], 1)

    def test_cadeia_completa_item_loja_global(self):
        """Confere a aritmetica dos tres niveis de encolhimento."""
        self.gravar_cliques(("1", "loja-a", 1000))
        self.gravar([conversao("c1", 0, 40, item_id="1", loja_id="loja-a")])
        self.calibrar(CFG_CAL)
        tabela = self.calibrador().tabela

        esperado_global = calibracao.suavizar(40, 1000, 0.02, 150.0)
        esperado_loja = calibracao.suavizar(40, 1000, esperado_global, 150.0)
        esperado_item = calibracao.suavizar(40, 1000, esperado_loja, 150.0)

        self.assertAlmostEqual(tabela[("shopee", "global", "global")], esperado_global)
        self.assertAlmostEqual(tabela[("shopee", "loja", "loja-a")], esperado_loja)
        self.assertAlmostEqual(tabela[("shopee", "item", "1")], esperado_item)

    def test_soma_varias_conversoes_do_mesmo_item(self):
        self.gravar_cliques(("1", "loja-a", 1000))
        self.gravar([
            conversao("c1", 0, 10, item_id="1", loja_id="loja-a"),
            conversao("c2", 0, 30, item_id="1", loja_id="loja-a"),
        ])
        self.calibrar(CFG_CAL)
        somado = self.calibrador().tabela[("shopee", "item", "1")]
        self.assertGreater(somado, 0.02)
        self.assertLess(somado, 0.04)

    def test_item_ruidoso_e_puxado_para_a_loja(self):
        """Amostra pequena entra no calculo em vez de ser descartada."""
        self.gravar_cliques(("1", "loja-a", 1000), ("2", "loja-a", 25))
        self.gravar([
            conversao("c1", 0, 40, item_id="1", loja_id="loja-a"),
            conversao("c2", 0, 5, item_id="2", loja_id="loja-a"),
        ])
        self.calibrar(CFG_CAL)
        tabela = self.calibrador().tabela
        ruidoso = tabela[("shopee", "item", "2")]
        loja = tabela[("shopee", "loja", "loja-a")]

        self.assertLess(ruidoso, 0.10)
        self.assertGreater(ruidoso, loja)

    def test_recalcular_e_idempotente(self):
        self.gravar_cliques(("1", "loja-a", 1000))
        self.gravar([conversao("c1", 0, 40, item_id="1", loja_id="loja-a")])
        self.calibrar(CFG_CAL)
        segundo = self.calibrar(CFG_CAL)
        self.assertEqual(segundo["total"], 3)
        self.assertEqual(len(self.calibrador().tabela), 3)


class TestBuscaHierarquica(BaseCalibracao):
    def _preparar(self, cliques, conversoes):
        self.gravar_cliques(*cliques)
        self.gravar(conversoes)
        self.calibrar(CFG_CAL)
        return self.calibrador()

    def test_item_tem_precedencia_sobre_loja(self):
        calibrador = self._preparar(
            [("1", "loja-a", 1000), ("2", "loja-a", 1000)],
            [conversao("c1", 0, 10, item_id="1", loja_id="loja-a"),
             conversao("c2", 0, 80, item_id="2", loja_id="loja-a")],
        )
        fraco = calibrador.cvr_para(oferta("1", "loja-a"))
        forte = calibrador.cvr_para(oferta("2", "loja-a"))

        self.assertLess(fraco, forte)
        self.assertGreater(fraco, 0.01)
        self.assertLess(forte, 0.08)

    def test_cai_para_loja_quando_item_nao_tem_amostra(self):
        calibrador = self._preparar(
            [("1", "loja-a", 1000)],
            [conversao("c1", 0, 50, item_id="1", loja_id="loja-a")],
        )
        self.assertAlmostEqual(
            calibrador.cvr_para(oferta("item-novo", "loja-a")),
            calibrador.tabela[("shopee", "loja", "loja-a")],
        )

    def test_cai_para_global_quando_a_loja_e_desconhecida(self):
        calibrador = self._preparar(
            [("1", "loja-a", 1000)],
            [conversao("c1", 0, 50, item_id="1", loja_id="loja-a")],
        )
        self.assertAlmostEqual(
            calibrador.cvr_para(oferta("item-novo", "loja-desconhecida")),
            calibrador.tabela[("shopee", "global", "global")],
        )

    def test_sem_dado_nenhum_retorna_none(self):
        self.assertIsNone(self.calibrador().cvr_para(oferta()))

    def test_desabilitada_ignora_a_tabela(self):
        self.gravar_cliques(("1", "loja-a", 1000))
        self.gravar([conversao("c1", 0, 40, item_id="1", loja_id="loja-a")])
        self.calibrar(CFG_CAL)
        vazio = self.calibrador(CalibracaoConfig(habilitada=False))
        self.assertIsNone(vazio.cvr_para(oferta("1", "loja-a")))


class TestEfeitoNoScore(BaseCalibracao):
    def test_score_calibrado_substitui_a_heuristica(self):
        self.gravar_cliques(("1", "loja-a", 5000))
        self.gravar([conversao("c1", 0, 500, item_id="1", loja_id="loja-a")])
        self.calibrar(CFG_CAL)
        calibrador = self.calibrador()
        item = oferta("1", "loja-a")
        cvr = calibrador.cvr_para(item)

        heuristico = avaliar(item, CFG_SCORE)
        medido = avaliar(item, CFG_SCORE, cvr)

        self.assertFalse(heuristico.calibrado)
        self.assertTrue(medido.calibrado)
        self.assertAlmostEqual(medido.cvr_estimado, cvr, places=6)
        self.assertAlmostEqual(medido.score, item.comissao_valor * cvr, places=5)
        self.assertNotAlmostEqual(medido.score, heuristico.score)

    def test_amostra_grande_aproxima_a_razao_crua(self):
        self.gravar_cliques(("1", "loja-a", 100000))
        self.gravar([conversao("c1", 0, 10000, item_id="1", loja_id="loja-a")])
        self.calibrar(CFG_CAL)
        cvr = self.calibrador().cvr_para(oferta("1", "loja-a"))
        self.assertAlmostEqual(cvr, 0.10, places=2)


if __name__ == "__main__":
    unittest.main()
