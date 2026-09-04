"""Identificacao de oportunidade: quando vale divulgar agora.

O ponto do modulo e que sinal isolado engana. Estes testes existem para
garantir que um sinal muito bom nunca carregue os ruins -- que e exatamente
o erro que uma nota unica ponderada cometeria.
"""

from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02.oportunidade import avaliar_oportunidade, corte_de_epc

# Corte, nao maximo: o criterio compara com o percentil do dia.
MELHOR_EPC = 0.5


def item(**trocas) -> dict:
    base = {
        "comissao_valor": 30.0,
        "epc": 0.9,
        "menor_preco": True,
        "preco_minimo": 50.0,
        "velocidade": {"por_dia": 40.0, "delta": 40, "dias": 1},
        "indisponivel": False,
        "dias_ate_expirar": 20,
        "desconto_confiavel": True,
        "desconto_inflado": False,
    }
    base.update(trocas)
    return base


class TestOportunidadeForte(unittest.TestCase):
    def test_tudo_bom_e_oportunidade(self):
        self.assertTrue(avaliar_oportunidade(item(), MELHOR_EPC).forte)

    def test_lista_os_motivos(self):
        aval = avaliar_oportunidade(item(), MELHOR_EPC)
        self.assertEqual(len(aval.motivos), aval.total)
        self.assertTrue(any("menor preco" in m for m in aval.motivos))


class TestUmSinalRuimDerruba(unittest.TestCase):
    """Nenhum sinal isolado pode carregar os outros."""

    def test_comissao_de_centavos_derruba(self):
        aval = avaliar_oportunidade(item(comissao_valor=0.60), MELHOR_EPC)
        self.assertFalse(aval.forte)
        self.assertTrue(any("0.60" in i for i in aval.impedimentos))

    def test_epc_muito_abaixo_do_melhor_derruba(self):
        self.assertFalse(avaliar_oportunidade(item(epc=0.01), MELHOR_EPC).forte)

    def test_preco_longe_do_minimo_derruba(self):
        self.assertFalse(
            avaliar_oportunidade(item(menor_preco=False), MELHOR_EPC).forte)

    def test_produto_parado_derruba(self):
        aval = avaliar_oportunidade(
            item(velocidade={"por_dia": 0.5, "delta": 1, "dias": 2}), MELHOR_EPC)
        self.assertFalse(aval.forte)

    def test_indisponivel_derruba(self):
        aval = avaliar_oportunidade(item(indisponivel=True), MELHOR_EPC)
        self.assertFalse(aval.forte)
        self.assertTrue(any("sumiu" in i for i in aval.impedimentos))

    def test_oferta_expirando_derruba(self):
        self.assertFalse(
            avaliar_oportunidade(item(dias_ate_expirar=1), MELHOR_EPC).forte)

    def test_oferta_expirada_derruba(self):
        aval = avaliar_oportunidade(item(dias_ate_expirar=-3), MELHOR_EPC)
        self.assertFalse(aval.forte)
        self.assertTrue(any("expirou" in i for i in aval.impedimentos))

    def test_desconto_inflado_derruba(self):
        aval = avaliar_oportunidade(item(desconto_inflado=True), MELHOR_EPC)
        self.assertFalse(aval.forte)
        self.assertTrue(any("inflado" in i for i in aval.impedimentos))


class TestCorteDeEpc(unittest.TestCase):
    """O corte e percentil, nao fracao do maximo.

    Medido no dado real: o melhor EPC era 1,5659 e a mediana 0,1100. Com
    "metade do maximo", so 3 de 400 produtos passavam -- um unico item
    excepcional definia o criterio para todos.
    """

    def test_ignora_o_outlier(self):
        itens = [{"epc": 0.1}] * 99 + [{"epc": 50.0}]
        self.assertLess(corte_de_epc(itens), 1.0)

    def test_acompanha_a_distribuicao(self):
        baixa = corte_de_epc([{"epc": v / 100} for v in range(1, 101)])
        alta = corte_de_epc([{"epc": v} for v in range(1, 101)])
        self.assertLess(baixa, alta)

    def test_lista_vazia(self):
        self.assertEqual(corte_de_epc([]), 0.0)

    def test_ignora_epc_zero(self):
        self.assertGreater(corte_de_epc([{"epc": 0}] * 50 + [{"epc": 2.0}]), 0)

    def test_um_item_so(self):
        self.assertAlmostEqual(corte_de_epc([{"epc": 0.7}]), 0.7)

    def test_deixa_passar_uma_fatia_util(self):
        """Corte que aprova 1 em 400 nao serve como filtro."""
        itens = [{"epc": v / 1000} for v in range(1, 401)]
        corte = corte_de_epc(itens)
        passam = sum(1 for i in itens if i["epc"] >= corte)
        self.assertGreater(passam, len(itens) * 0.15)
        self.assertLess(passam, len(itens) * 0.40)


class TestQuaseOportunidade(unittest.TestCase):
    def test_falta_um_criterio(self):
        aval = avaliar_oportunidade(item(indisponivel=True), MELHOR_EPC)
        self.assertTrue(aval.quase)
        self.assertFalse(aval.forte)

    def test_faltam_dois_nao_e_quase(self):
        aval = avaliar_oportunidade(
            item(indisponivel=True, comissao_valor=0.5), MELHOR_EPC)
        self.assertFalse(aval.quase)

    def test_completo_nao_e_quase(self):
        self.assertFalse(avaliar_oportunidade(item(), MELHOR_EPC).quase)


class TestCriterioNaoAvaliavel(unittest.TestCase):
    """Falta de dado nao conta a favor nem contra -- fica fora da conta."""

    def test_sem_historico_de_preco_nao_avalia_preco(self):
        aval = avaliar_oportunidade(
            item(preco_minimo=None, menor_preco=False), MELHOR_EPC)
        self.assertTrue(aval.forte)
        self.assertNotIn("preco", [c.nome for c in aval.criterios])

    def test_sem_velocidade_nao_avalia_tracao(self):
        aval = avaliar_oportunidade(item(velocidade=None), MELHOR_EPC)
        self.assertTrue(aval.forte)
        self.assertNotIn("tracao", [c.nome for c in aval.criterios])

    def test_sem_validade_nao_avalia(self):
        aval = avaliar_oportunidade(item(dias_ate_expirar=None), MELHOR_EPC)
        self.assertNotIn("validade", [c.nome for c in aval.criterios])

    def test_desconto_nao_conferido_nao_avalia_honestidade(self):
        aval = avaliar_oportunidade(item(desconto_confiavel=False), MELHOR_EPC)
        self.assertNotIn("honestidade", [c.nome for c in aval.criterios])

    def test_poucos_criterios_nao_viram_oportunidade(self):
        """Produto do qual mal se sabe algo nao pode ser recomendado."""
        aval = avaliar_oportunidade(
            {"comissao_valor": 50.0, "epc": 0.9}, MELHOR_EPC)
        self.assertLess(aval.total, 3)
        self.assertFalse(aval.forte)

    def test_sem_melhor_epc_nao_avalia_epc(self):
        aval = avaliar_oportunidade(item(), 0.0)
        self.assertNotIn("epc", [c.nome for c in aval.criterios])


class TestIntegracaoComOPainel(unittest.TestCase):
    def test_api_entrega_o_campo(self):
        import tempfile
        from pathlib import Path

        from flow02 import db
        from flow02.config import Config, ScoringConfig
        from flow02.models import Oferta
        from flow02.scoring import avaliar
        from flow02.web import api

        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "t.sqlite3"
            oferta = Oferta(
                plataforma="shopee", item_id="1", nome="p", preco=100.0,
                taxa_comissao=0.30, origem_consulta="t", vendas=500,
                rating=4.8, loja_id="l", loja_nome="L",
            )
            with db.conectar(caminho) as conexao:
                db.salvar_ofertas(
                    conexao, [(oferta, avaliar(oferta, ScoringConfig()))],
                    "2026-09-04")
            dados = api.top(Config(caminho_banco=caminho), {"dia": "2026-09-04"})

        self.assertIn("oportunidade", dados["itens"][0])
        self.assertIn("oportunidades", dados)


if __name__ == "__main__":
    unittest.main()
