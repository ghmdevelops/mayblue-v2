from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02 import analise
from flow02.config import AlertasConfig, PrecoConfig

CFG_ALERTAS = AlertasConfig()
CFG_PRECO = PrecoConfig()


def oferta(item_id="1", **kwargs) -> dict:
    base = {
        "plataforma": "shopee", "item_id": item_id, "nome": f"produto {item_id}",
        "preco": 100.0, "taxa_comissao": 0.05, "score": 0.1,
        "desconto_pct": 20.0, "link_oferta": "https://s/x", "link_produto": None,
    }
    base.update(kwargs)
    return base


class TestComissao(unittest.TestCase):
    def test_alta_relevante_dispara(self):
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.15)], [oferta(taxa_comissao=0.04)], CFG_ALERTAS
        )
        self.assertEqual(alertas[0].tipo, analise.COMISSAO_SUBIU)
        self.assertAlmostEqual(alertas[0].variacao, 2.75)

    def test_queda_relevante_dispara(self):
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.04)], [oferta(taxa_comissao=0.15)], CFG_ALERTAS
        )
        self.assertEqual(alertas[0].tipo, analise.COMISSAO_CAIU)

    def test_variacao_pequena_e_ignorada(self):
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.051)], [oferta(taxa_comissao=0.05)], CFG_ALERTAS
        )
        self.assertEqual(alertas, [])

    def test_limiar_configuravel(self):
        cfg = AlertasConfig(variacao_comissao_min=0.01)
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.051)], [oferta(taxa_comissao=0.05)], cfg
        )
        self.assertEqual(len(alertas), 1)

    def test_comissao_anterior_zero_nao_divide_por_zero(self):
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.10)], [oferta(taxa_comissao=0.0)], CFG_ALERTAS
        )
        self.assertEqual([a.tipo for a in alertas], [])


class TestPreco(unittest.TestCase):
    def test_queda_dispara(self):
        alertas = analise.detectar_alertas(
            [oferta(preco=70.0)], [oferta(preco=100.0)], CFG_ALERTAS
        )
        self.assertEqual(alertas[0].tipo, analise.PRECO_CAIU)
        self.assertAlmostEqual(alertas[0].variacao, -0.30)

    def test_alta_dispara(self):
        alertas = analise.detectar_alertas(
            [oferta(preco=130.0)], [oferta(preco=100.0)], CFG_ALERTAS
        )
        self.assertEqual(alertas[0].tipo, analise.PRECO_SUBIU)

    def test_oscilacao_pequena_ignorada(self):
        alertas = analise.detectar_alertas(
            [oferta(preco=101.0)], [oferta(preco=100.0)], CFG_ALERTAS
        )
        self.assertEqual(alertas, [])


class TestEntradaESaida(unittest.TestCase):
    def test_novo_no_top_dispara(self):
        alertas = analise.detectar_alertas([oferta("novo")], [], CFG_ALERTAS)
        self.assertEqual(alertas[0].tipo, analise.NOVO_NO_TOP)

    def test_novo_fora_do_top_nao_dispara(self):
        cfg = AlertasConfig(top_referencia=1)
        atual = [oferta("a", score=0.9), oferta("b", score=0.1)]
        alertas = analise.detectar_alertas(atual, [oferta("a", score=0.9)], cfg)
        self.assertEqual(alertas, [])

    def test_sumico_do_top_dispara(self):
        alertas = analise.detectar_alertas([], [oferta("sumido")], CFG_ALERTAS)
        self.assertEqual(alertas[0].tipo, analise.SUMIU)

    def test_sumico_fora_do_top_nao_dispara(self):
        cfg = AlertasConfig(top_referencia=1)
        anterior = [oferta("a", score=0.9), oferta("b", score=0.1)]
        alertas = analise.detectar_alertas([oferta("a", score=0.9)], anterior, cfg)
        self.assertEqual(alertas, [])

    def test_plataformas_diferentes_nao_colidem(self):
        alertas = analise.detectar_alertas(
            [oferta("1", plataforma="shopee")],
            [oferta("1", plataforma="mercadolivre")],
            CFG_ALERTAS,
        )
        tipos = {a.tipo for a in alertas}
        self.assertEqual(tipos, {analise.NOVO_NO_TOP, analise.SUMIU})


class TestOrdenacao(unittest.TestCase):
    def test_comissao_subiu_vem_primeiro(self):
        atual = [oferta("a", preco=70.0), oferta("b", taxa_comissao=0.15)]
        anterior = [oferta("a", preco=100.0), oferta("b", taxa_comissao=0.04)]
        alertas = analise.detectar_alertas(atual, anterior, CFG_ALERTAS)
        self.assertEqual(alertas[0].tipo, analise.COMISSAO_SUBIU)

    def test_maior_variacao_primeiro_dentro_do_tipo(self):
        atual = [oferta("a", taxa_comissao=0.10), oferta("b", taxa_comissao=0.30)]
        anterior = [oferta("a", taxa_comissao=0.05), oferta("b", taxa_comissao=0.05)]
        alertas = analise.detectar_alertas(atual, anterior, CFG_ALERTAS)
        self.assertEqual(alertas[0].item_id, "b")

    def test_rotulo_legivel(self):
        alertas = analise.detectar_alertas(
            [oferta(taxa_comissao=0.15)], [oferta(taxa_comissao=0.04)], CFG_ALERTAS
        )
        self.assertEqual(alertas[0].rotulo, "COMISSAO SUBIU")


class TestDescontoReal(unittest.TestCase):
    def test_historico_curto_nao_avalia(self):
        resultado = analise.avaliar_desconto(80.0, 50.0, [100.0, 100.0], CFG_PRECO)
        self.assertFalse(resultado.confiavel)
        self.assertIsNone(resultado.desconto_real)

    def test_calcula_contra_a_mediana(self):
        resultado = analise.avaliar_desconto(
            80.0, 50.0, [100.0] * 10, CFG_PRECO
        )
        self.assertTrue(resultado.confiavel)
        self.assertAlmostEqual(resultado.mediana_historica, 100.0)
        self.assertAlmostEqual(resultado.desconto_real, 20.0)

    def test_detecta_desconto_inflado(self):
        """Anuncia 50% mas o preco so caiu 20% contra a mediana praticada."""
        resultado = analise.avaliar_desconto(80.0, 50.0, [100.0] * 10, CFG_PRECO)
        self.assertAlmostEqual(resultado.exagero_pp, 30.0)
        self.assertTrue(analise.desconto_inflado(resultado, CFG_PRECO))

    def test_desconto_honesto_nao_e_marcado(self):
        resultado = analise.avaliar_desconto(50.0, 50.0, [100.0] * 10, CFG_PRECO)
        self.assertAlmostEqual(resultado.desconto_real, 50.0)
        self.assertFalse(analise.desconto_inflado(resultado, CFG_PRECO))

    def test_tolerancia_configuravel(self):
        resultado = analise.avaliar_desconto(80.0, 50.0, [100.0] * 10, CFG_PRECO)
        tolerante = PrecoConfig(tolerancia_pp=40.0)
        self.assertFalse(analise.desconto_inflado(resultado, tolerante))

    def test_preco_acima_da_mediana_da_desconto_zero(self):
        resultado = analise.avaliar_desconto(150.0, 30.0, [100.0] * 10, CFG_PRECO)
        self.assertEqual(resultado.desconto_real, 0.0)

    def test_usa_mediana_e_nao_media(self):
        """Mediana resiste a um outlier de preco; media nao."""
        precos = [100.0, 100.0, 100.0, 100.0, 100.0, 1000.0]
        resultado = analise.avaliar_desconto(80.0, 20.0, precos, CFG_PRECO)
        self.assertAlmostEqual(resultado.mediana_historica, 100.0)

    def test_ignora_precos_invalidos(self):
        precos = [0.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        resultado = analise.avaliar_desconto(80.0, 20.0, precos, CFG_PRECO)
        self.assertEqual(resultado.dias_observados, 5)


if __name__ == "__main__":
    unittest.main()
