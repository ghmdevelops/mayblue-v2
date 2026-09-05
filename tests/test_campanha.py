"""Viabilidade de trafego pago.

Para trafego organico o que manda e volume. Para trafego PAGO a conta
inverte: cada clique custa, entao comissao alta tolera conversao baixa.
Errar esse raciocinio custa dinheiro real do usuario, nao so um ranking
mal ordenado.
"""

from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02 import campanha


class TestConversaoNecessaria(unittest.TestCase):
    def test_comissao_alta_exige_menos_conversao(self):
        caro = campanha.avaliar(comissao=254.91, cpc=1.0)
        barato = campanha.avaliar(comissao=36.75, cpc=1.0)
        self.assertLess(caro.conversao_necessaria, barato.conversao_necessaria)

    def test_conta_confere(self):
        """0,39% = R$ 1,00 / R$ 254,91"""
        aval = campanha.avaliar(comissao=254.91, cpc=1.0)
        self.assertAlmostEqual(aval.conversao_necessaria, 0.003923, places=5)
        self.assertAlmostEqual(aval.cliques_por_venda, 254.91, places=1)

    def test_cpc_maior_exige_mais_conversao(self):
        barato = campanha.avaliar(comissao=50.0, cpc=0.5)
        caro = campanha.avaliar(comissao=50.0, cpc=2.0)
        self.assertLess(barato.conversao_necessaria, caro.conversao_necessaria)

    def test_comissao_zero_nao_tem_conta(self):
        self.assertIsNone(campanha.avaliar(comissao=0, cpc=1.0))

    def test_cpc_zero_nao_tem_conta(self):
        self.assertIsNone(campanha.avaliar(comissao=50.0, cpc=0))

    def test_comissao_negativa_nao_tem_conta(self):
        self.assertIsNone(campanha.avaliar(comissao=-5, cpc=1.0))


class TestVeredito(unittest.TestCase):
    def test_comissao_muito_alta_tem_folga(self):
        aval = campanha.avaliar(comissao=254.91, cpc=1.0)
        self.assertIn("folga", aval.veredito)
        self.assertTrue(aval.viavel)

    def test_dentro_do_tipico_e_viavel(self):
        aval = campanha.avaliar(comissao=100.0, cpc=1.0)  # 1%
        self.assertIn("viavel", aval.veredito)
        self.assertTrue(aval.viavel)

    def test_acima_do_tipico_e_apertado(self):
        aval = campanha.avaliar(comissao=30.0, cpc=1.0)  # 3,3%
        self.assertIn("apertado", aval.veredito)
        self.assertFalse(aval.viavel)

    def test_comissao_de_centavos_e_prejuizo(self):
        """R$ 1,36 de comissao a R$ 1,00 o clique: precisaria de 74%."""
        aval = campanha.avaliar(comissao=1.36, cpc=1.0)
        self.assertIn("prejuizo", aval.veredito)
        self.assertFalse(aval.viavel)


class TestSimulacao(unittest.TestCase):
    def test_empata_exatamente_na_conversao_minima(self):
        aval = campanha.avaliar(comissao=100.0, cpc=1.0)
        r = aval.resultado(cliques=1000, conversao=aval.conversao_necessaria)
        self.assertAlmostEqual(r["lucro"], 0.0, places=2)

    def test_acima_do_minimo_da_lucro(self):
        aval = campanha.avaliar(comissao=100.0, cpc=1.0)
        self.assertGreater(aval.resultado(1000, 0.02)["lucro"], 0)

    def test_abaixo_do_minimo_da_prejuizo(self):
        aval = campanha.avaliar(comissao=100.0, cpc=1.0)
        self.assertLess(aval.resultado(1000, 0.005)["lucro"], 0)

    def test_relata_custo_e_receita_separados(self):
        r = campanha.avaliar(comissao=50.0, cpc=1.0).resultado(500, 0.01)
        self.assertAlmostEqual(r["custo"], 500.0)
        self.assertAlmostEqual(r["receita"], 250.0)
        self.assertAlmostEqual(r["lucro"], -250.0)


class TestOrcamentoDeTeste(unittest.TestCase):
    def test_orcamento_cobre_tres_vendas(self):
        """Testar com menos que isso nao distingue produto ruim de azar."""
        orcamento = campanha.orcamento_de_teste(comissao=100.0, cpc=1.0)
        self.assertAlmostEqual(orcamento, 300.0)

    def test_comissao_maior_exige_orcamento_maior(self):
        """Contraintuitivo: mais comissao = mais cliques ate a venda."""
        self.assertGreater(
            campanha.orcamento_de_teste(254.91, 1.0),
            campanha.orcamento_de_teste(50.0, 1.0),
        )

    def test_permite_escolher_quantas_vendas(self):
        uma = campanha.orcamento_de_teste(100.0, 1.0, vendas_desejadas=1)
        tres = campanha.orcamento_de_teste(100.0, 1.0, vendas_desejadas=3)
        self.assertAlmostEqual(tres, uma * 3)

    def test_sem_dado_devolve_zero(self):
        self.assertEqual(campanha.orcamento_de_teste(0, 1.0), 0.0)


class TestCasoRealDoUsuario(unittest.TestCase):
    """Numeros medidos na conta dele em 2026-09-04."""

    def test_comissao_media_realizada_nao_paga_anuncio(self):
        """R$ 1,36 foi a media real das 57 vendas dele."""
        self.assertFalse(campanha.avaliar(1.36, 1.0).viavel)

    def test_balsamo_aguenta_trafego_pago_apesar_de_vender_pouco(self):
        """57 vendas acumuladas e pouco, mas R$ 254,91 muda a conta."""
        aval = campanha.avaliar(254.91, 1.0)
        self.assertTrue(aval.viavel)
        self.assertLess(aval.conversao_necessaria, 0.005)

    def test_celimax_e_mais_apertado_no_pago_apesar_de_vender_muito(self):
        """18.765 vendas, mas R$ 36,75 exige quase 3% de conversao."""
        aval = campanha.avaliar(36.75, 1.0)
        self.assertGreater(aval.conversao_necessaria, 0.02)


if __name__ == "__main__":
    unittest.main()
