"""Operações e campos da API da Shopee que estavam ociosos.

As formas vieram de introspecção do schema em 2026-09-04, não de suposição:

    shopOfferV2(keyword, shopType, sortType, sellerCommCoveRatio, page,
                limit, shopId, isKeySeller)
    generateBatchShortLink(input: BatchShortLinkInput { links })
      -> BatchShortLinkResult { links, total, successCount }

A verificação contra a API real ficou pendente: a rede da máquina passou a
derrubar toda conexão de saída. Estes testes cobrem o que não depende de rede
-- montagem da query e interpretação da resposta.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.config import Config, ConsultaConfig, ScoringConfig
from flow02.scoring import avaliar
from flow02.sources import shopee
from flow02.web import api


class TestComissaoSeparada(unittest.TestCase):
    """A taxa total se divide entre o que o vendedor banca e o que a Shopee
    banca. A parte do vendedor pode sumir sem aviso."""

    def no(self, **trocas) -> dict:
        base = {
            "itemId": 1, "productName": "p", "priceMin": "100",
            "commissionRate": "0.17", "sellerCommissionRate": "0.14",
            "shopeeCommissionRate": "0.03", "commission": "16.66",
            "offerLink": "https://s/1",
        }
        base.update(trocas)
        return base

    def test_le_as_duas_partes(self):
        oferta = shopee.para_oferta(self.no(), "t")
        self.assertAlmostEqual(oferta.taxa_vendedor, 0.14)
        self.assertAlmostEqual(oferta.taxa_shopee, 0.03)

    def test_guarda_a_comissao_calculada_pela_shopee(self):
        """A Shopee aplica teto de comissao; preco x taxa ignora isso."""
        self.assertAlmostEqual(shopee.para_oferta(self.no(), "t").comissao_api, 16.66)

    def test_ausencia_dos_campos_nao_quebra(self):
        magro = {"itemId": 1, "productName": "p", "priceMin": "10",
                 "commissionRate": "0.1", "offerLink": "https://s/1"}
        oferta = shopee.para_oferta(magro, "t")
        self.assertIsNone(oferta.taxa_vendedor)
        self.assertIsNone(oferta.comissao_api)


class TestFragilidade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"
        self.cfg = Config(caminho_banco=self.caminho)

    def tearDown(self):
        self._tmp.cleanup()

    def gravar(self, **trocas):
        no = {"itemId": "1", "productName": "p", "priceMin": "100",
              "commissionRate": "0.80", "sellerCommissionRate": "0.77",
              "shopeeCommissionRate": "0.03", "offerLink": "https://s/1",
              "shopId": 9, "shopName": "L", "sales": 500, "ratingStar": "4.8"}
        no.update(trocas)
        oferta = shopee.para_oferta(no, "t")
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(conexao, [(oferta, avaliar(oferta, ScoringConfig()))],
                              "2026-09-04")
        return api.top(self.cfg, {"dia": "2026-09-04"})["itens"][0]

    def test_comissao_alta_do_vendedor_e_fragil(self):
        item = self.gravar()
        self.assertTrue(item["fragilidade"]["fragil"])
        self.assertAlmostEqual(item["fragilidade"]["fatia_vendedor"], 0.963, places=2)

    def test_calcula_o_piso_se_a_campanha_acabar(self):
        """Sobra so a parte da Shopee: 3% de R$ 100."""
        self.assertAlmostEqual(self.gravar()["fragilidade"]["piso"], 3.0)

    def test_comissao_majoritariamente_da_shopee_nao_e_fragil(self):
        item = self.gravar(commissionRate="0.10", sellerCommissionRate="0.02",
                           shopeeCommissionRate="0.08")
        self.assertFalse(item["fragilidade"]["fragil"])

    def test_sem_os_campos_nao_opina(self):
        item = self.gravar(sellerCommissionRate=None, shopeeCommissionRate=None)
        self.assertIsNone(item["fragilidade"])


class TestQueryDeLoja(unittest.TestCase):
    def test_monta_com_pagina_e_limite(self):
        query = shopee.montar_query_shop_offer(2, 50, shopee.CAMPOS_LOJA)
        self.assertIn("shopOfferV2(", query)
        self.assertIn("page: 2", query)
        self.assertIn("limit: 50", query)

    def test_pede_a_verba_restante(self):
        """`remainingBudget` nao existe no nivel do produto -- e o motivo
        de consultar loja."""
        self.assertIn("remainingBudget",
                      shopee.montar_query_shop_offer(1, 10, shopee.CAMPOS_LOJA))

    def test_filtro_de_vendedor_selecionado(self):
        query = shopee.montar_query_shop_offer(1, 10, shopee.CAMPOS_LOJA,
                                               so_selecionadas=True)
        self.assertIn("isKeySeller: true", query)

    def test_sem_filtro_nao_manda_o_argumento(self):
        self.assertNotIn("isKeySeller",
                         shopee.montar_query_shop_offer(1, 10, shopee.CAMPOS_LOJA))


class TestLinkEmLote(unittest.TestCase):
    def test_monta_lista_de_links(self):
        query = shopee.montar_mutation_batch_short_link(
            ["https://shopee.com.br/a", "https://shopee.com.br/b"])
        self.assertIn("generateBatchShortLink(input: { links: [", query)
        self.assertIn('originUrl: "https://shopee.com.br/a"', query)
        self.assertIn('originUrl: "https://shopee.com.br/b"', query)

    def test_pede_o_resultado_completo(self):
        query = shopee.montar_mutation_batch_short_link(["https://x"])
        for campo in ("shortLinks", "total", "successCount"):
            self.assertIn(campo, query)

    def test_sub_ids_em_cada_link(self):
        query = shopee.montar_mutation_batch_short_link(
            ["https://a", "https://b"], ["tiktok"])
        self.assertEqual(query.count('subIds: ["tiktok"]'), 2)

    def test_recusa_sub_ids_demais(self):
        with self.assertRaises(ValueError):
            shopee.montar_mutation_batch_short_link(["https://a"], list("abcdef"))

    def test_lista_vazia_nao_chama_a_api(self):
        class Espiao(shopee.FonteShopee):
            def cliente(self, cfg):
                raise AssertionError("nao deveria abrir cliente")

        self.assertEqual(Espiao().gerar_links_em_lote(Config(), []), {})


class TestFiltrosDeConsulta(unittest.TestCase):
    def test_somente_ams(self):
        consulta = ConsultaConfig(nome="t", is_ams_offer=True)
        query = shopee.montar_query_product_offer(consulta, 1, 10, ["itemId"])
        self.assertIn("isAMSOffer: true", query)

    def test_somente_vendedor_selecionado(self):
        consulta = ConsultaConfig(nome="t", is_key_seller=True)
        query = shopee.montar_query_product_offer(consulta, 1, 10, ["itemId"])
        self.assertIn("isKeySeller: true", query)

    def test_excluir_ams(self):
        consulta = ConsultaConfig(nome="t", is_ams_offer=False)
        query = shopee.montar_query_product_offer(consulta, 1, 10, ["itemId"])
        self.assertIn("isAMSOffer: false", query)

    def test_nao_definido_fica_de_fora(self):
        query = shopee.montar_query_product_offer(
            ConsultaConfig(nome="t"), 1, 10, ["itemId"])
        self.assertNotIn("isAMSOffer", query)
        self.assertNotIn("isKeySeller", query)


class TestQuedaParaChamadaIndividual(unittest.TestCase):
    """Se o lote falhar, o caminho individual (ja testado em producao) assume."""

    def test_cai_para_individual_quando_o_lote_e_recusado(self):
        chamadas = []

        class ClienteFalso:
            def executar(self, query):
                raise shopee.ErroShopee(shopee.CODIGO_PARAMETROS, "sem suporte")

            def gerar_link_curto(self, url, sub_ids=()):
                chamadas.append(url)
                return f"curto:{url}"

        class Fonte(shopee.FonteShopee):
            def cliente(self, cfg):
                return ClienteFalso()

        resultado = Fonte().gerar_links_em_lote(Config(), ["https://a", "https://b"])
        self.assertEqual(resultado, {"https://a": "curto:https://a",
                                     "https://b": "curto:https://b"})
        self.assertEqual(chamadas, ["https://a", "https://b"])

    def test_usa_o_lote_quando_a_resposta_bate(self):
        class ClienteFalso:
            def executar(self, query):
                return {"generateBatchShortLink": {
                    "shortLinks": ["curto-a", "curto-b"], "total": 2,
                    "successCount": 2}}

            def gerar_link_curto(self, url, sub_ids=()):
                raise AssertionError("nao deveria cair para individual")

        class Fonte(shopee.FonteShopee):
            def cliente(self, cfg):
                return ClienteFalso()

        self.assertEqual(
            Fonte().gerar_links_em_lote(Config(), ["https://a", "https://b"]),
            {"https://a": "curto-a", "https://b": "curto-b"})

    def test_resposta_de_tamanho_errado_cai_para_individual(self):
        """Faltou um link: usar a lista desalinhada trocaria produto por produto."""
        class ClienteFalso:
            def executar(self, query):
                return {"generateBatchShortLink": {"shortLinks": ["so-um"]}}

            def gerar_link_curto(self, url, sub_ids=()):
                return f"individual:{url}"

        class Fonte(shopee.FonteShopee):
            def cliente(self, cfg):
                return ClienteFalso()

        resultado = Fonte().gerar_links_em_lote(Config(), ["https://a", "https://b"])
        self.assertTrue(all(v.startswith("individual:") for v in resultado.values()))


if __name__ == "__main__":
    unittest.main()
