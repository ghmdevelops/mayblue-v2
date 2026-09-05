"""Mais vendidos por periodo e filtro por categoria.

O campo `sales` da API e acumulado desde sempre -- nao existe recorte por
periodo. Saber quanto vendeu na ultima semana exige subtrair o acumulado de
hoje do de sete dias atras, e portanto exige ter coletado naquele dia.

O ponto delicado: quando falta a segunda ponta, e tentador devolver o
acumulado. Isso apresentaria "32.010 vendidos desde sempre" como se fossem
vendas da semana. Estes testes travam esse comportamento.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import categorias, db
from flow02.config import Config, ScoringConfig
from flow02.models import Oferta
from flow02.scoring import avaliar
from flow02.web import api


class BasePeriodo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"
        self.cfg = Config(caminho_banco=self.caminho)

    def tearDown(self):
        self._tmp.cleanup()

    def gravar(self, dia, itens):
        """itens: (item_id, vendas) ou (item_id, vendas, categorias)"""
        with db.conectar(self.caminho) as conexao:
            for item in itens:
                item_id, vendas = item[0], item[1]
                cats = item[2] if len(item) > 2 else ("100630",)
                oferta = Oferta(
                    plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
                    preco=100.0, taxa_comissao=0.2, origem_consulta="t",
                    vendas=vendas, rating=4.5, loja_id="l", loja_nome="L",
                    categoria_ids=tuple(cats),
                )
                db.salvar_ofertas(
                    conexao, [(oferta, avaliar(oferta, ScoringConfig()))], dia)

    def periodo(self, dia, dias):
        with db.conectar(self.caminho) as conexao:
            return db.vendas_no_periodo(conexao, dia, dias)


class TestVendasNoPeriodo(BasePeriodo):
    def test_calcula_o_delta_entre_duas_coletas(self):
        self.gravar("2026-09-01", [("1", 100)])
        self.gravar("2026-09-08", [("1", 340)])
        self.assertEqual(self.periodo("2026-09-08", 7)[("shopee", "1")]["delta"], 240)

    def test_sem_a_ponta_anterior_nao_inventa(self):
        """So um dia coletado: nao ha o que subtrair."""
        self.gravar("2026-09-08", [("1", 340)])
        self.assertEqual(self.periodo("2026-09-08", 7), {})

    def test_usa_a_coleta_mais_proxima_dentro_da_janela(self):
        """Se faltou o dia exato, a anterior mais recente serve de base."""
        self.gravar("2026-08-20", [("1", 50)])
        self.gravar("2026-09-01", [("1", 100)])
        self.gravar("2026-09-08", [("1", 340)])
        resultado = self.periodo("2026-09-08", 7)[("shopee", "1")]
        self.assertEqual(resultado["base"], "2026-09-01")
        self.assertEqual(resultado["delta"], 240)

    def test_produto_novo_fica_de_fora(self):
        """Sem base de comparacao, o acumulado dele inflaria a lista."""
        self.gravar("2026-09-01", [("1", 100)])
        self.gravar("2026-09-08", [("1", 340), ("2", 9999)])
        self.assertNotIn(("shopee", "2"), self.periodo("2026-09-08", 7))

    def test_venda_que_diminuiu_e_descartada(self):
        """Acumulado nao cai: se caiu, o dado nao e comparavel."""
        self.gravar("2026-09-01", [("1", 500)])
        self.gravar("2026-09-08", [("1", 100)])
        self.assertEqual(self.periodo("2026-09-08", 7), {})

    def test_guarda_o_acumulado_junto(self):
        self.gravar("2026-09-01", [("1", 100)])
        self.gravar("2026-09-08", [("1", 340)])
        self.assertEqual(
            self.periodo("2026-09-08", 7)[("shopee", "1")]["acumulado"], 340)

    def test_janelas_diferentes_dao_deltas_diferentes(self):
        self.gravar("2026-08-09", [("1", 10)])
        self.gravar("2026-09-01", [("1", 100)])
        self.gravar("2026-09-08", [("1", 340)])
        semana = self.periodo("2026-09-08", 7)[("shopee", "1")]["delta"]
        mes = self.periodo("2026-09-08", 30)[("shopee", "1")]["delta"]
        self.assertEqual(semana, 240)
        self.assertEqual(mes, 330)


class TestApiComJanela(BasePeriodo):
    def test_sem_historico_a_api_avisa_em_vez_de_fingir(self):
        self.gravar("2026-09-08", [("1", 340)])
        dados = api.top(self.cfg, {"dia": "2026-09-08", "janela": "7"})
        self.assertEqual(dados["com_periodo"], 0)
        self.assertEqual(dados["janela"], 7)
        # A lista continua util, so nao e recortada por periodo.
        self.assertTrue(dados["itens"])

    def test_com_historico_ordena_pelo_periodo(self):
        self.gravar("2026-09-01", [("1", 100), ("2", 100)])
        self.gravar("2026-09-08", [("1", 150), ("2", 900)])
        dados = api.top(self.cfg, {"dia": "2026-09-08", "janela": "7"})
        self.assertEqual(dados["itens"][0]["item_id"], "2")
        self.assertEqual(dados["itens"][0]["no_periodo"]["delta"], 800)

    def test_janela_zero_nao_recorta(self):
        self.gravar("2026-09-08", [("1", 340)])
        dados = api.top(self.cfg, {"dia": "2026-09-08", "janela": "0"})
        self.assertIsNone(dados["itens"][0]["no_periodo"])


class TestTendencia(BasePeriodo):
    """Em alta e crescimento, nao volume: o gigante ja vendia antes."""

    def semear(self):
        # gigante cresce 1%; pequeno cresce 200%
        self.gravar("2026-09-01", [("gigante", 9000), ("pequeno", 20)])
        self.gravar("2026-09-08", [("gigante", 9100), ("pequeno", 60)])

    def top(self, **extra):
        return api.top(self.cfg, {"dia": "2026-09-08", **extra})["itens"]

    def test_ordena_por_crescimento_nao_por_volume(self):
        self.semear()
        itens = self.top(ordenar="tendencia")
        self.assertEqual(itens[0]["item_id"], "pequeno")

    def test_mais_vendidos_ordena_pelo_contrario(self):
        self.semear()
        itens = self.top(ordenar="vendas", janela="7")
        self.assertEqual(itens[0]["item_id"], "gigante")

    def test_calcula_o_percentual(self):
        self.semear()
        porid = {i["item_id"]: i for i in self.top(ordenar="tendencia")}
        self.assertAlmostEqual(porid["pequeno"]["tendencia"], 200.0, places=0)

    def test_crescimento_de_poucas_unidades_e_ruido(self):
        """De 1 para 3 vendas sao 200%, mas nao diz nada."""
        self.gravar("2026-09-01", [("ruido", 1), ("real", 100)])
        self.gravar("2026-09-08", [("ruido", 3), ("real", 200)])
        self.assertEqual(self.top(ordenar="tendencia")[0]["item_id"], "real")

    def test_usa_janela_padrao_quando_nao_informada(self):
        self.semear()
        self.assertEqual(
            api.top(self.cfg, {"dia": "2026-09-08", "ordenar": "tendencia"})["janela"],
            api.JANELA_TENDENCIA)

    def test_sem_historico_avisa_em_vez_de_ordenar_errado(self):
        self.gravar("2026-09-08", [("1", 500)])
        dados = api.top(self.cfg, {"dia": "2026-09-08", "ordenar": "tendencia"})
        self.assertEqual(dados["com_periodo"], 0)
        self.assertIsNone(dados["itens"][0]["tendencia"])


class TestFiltroPorCategoria(BasePeriodo):
    def semear(self):
        self.gravar("2026-09-08", [
            ("1", 100, ("100630",)),
            ("2", 200, ("100664",)),
            ("3", 300, ("100630", "100001")),
        ])

    def test_filtra_pela_categoria(self):
        self.semear()
        dados = api.top(self.cfg, {"dia": "2026-09-08", "categoria": "100664"})
        self.assertEqual([i["item_id"] for i in dados["itens"]], ["2"])

    def test_produto_em_varias_categorias_aparece_em_todas(self):
        self.semear()
        for categoria in ("100630", "100001"):
            dados = api.top(self.cfg, {"dia": "2026-09-08",
                                       "categoria": categoria})
            self.assertIn("3", [i["item_id"] for i in dados["itens"]])

    def test_sem_categoria_traz_tudo(self):
        self.semear()
        self.assertEqual(len(api.top(self.cfg, {"dia": "2026-09-08"})["itens"]), 3)

    def test_categoria_inexistente_traz_vazio(self):
        self.semear()
        dados = api.top(self.cfg, {"dia": "2026-09-08", "categoria": "999"})
        self.assertEqual(dados["itens"], [])

    def test_api_lista_as_categorias_com_contagem(self):
        self.semear()
        dados = api.lista_categorias(self.cfg, {"dia": "2026-09-08"})
        porid = {c["id"]: c for c in dados["itens"]}
        self.assertEqual(porid["100630"]["itens"], 2)
        self.assertEqual(porid["100664"]["itens"], 1)


class TestRotuloDeCategoria(unittest.TestCase):
    """A API so devolve numeros. O rotulo sai das palavras dos produtos."""

    def test_acha_a_palavra_dominante(self):
        rotulo = categorias.rotular([
            "Cortina Sala Blackout 3 metros",
            "Cortina Voil Quarto com Forro",
            "Cortina para Sala Linho 4m",
        ])
        self.assertIn("cortina", rotulo)

    def test_ignora_palavra_de_marketing(self):
        rotulo = categorias.rotular([
            "Kit Original Premium Creatina 300g",
            "Creatina Monohidratada Original Frete Gratis",
        ])
        self.assertIn("creatina", rotulo)
        for lixo in ("original", "premium", "kit", "frete"):
            self.assertNotIn(lixo, rotulo)

    def test_ignora_numero_e_unidade(self):
        rotulo = categorias.rotular(["Whey 900g 100%", "Whey 1kg Concentrado"])
        self.assertIn("whey", rotulo)

    def test_lista_vazia_nao_quebra(self):
        self.assertEqual(categorias.rotular([]), "")

    def test_nomes_sem_palavra_util(self):
        self.assertEqual(categorias.rotular(["kit de 3", "com 2"]), "")


if __name__ == "__main__":
    unittest.main()
