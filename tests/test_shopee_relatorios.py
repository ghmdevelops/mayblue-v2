"""Testes das operações de relatório e link da Shopee.

Todas as formas aqui foram confirmadas por introspecção do schema real em
2026-09-04. A documentação pública divergia em três pontos importantes:

- `conversionReport` não tem argumento `page`; pagina por `scrollId`
- `validatedReport` exige `validationId` obrigatório, sem listagem geral
- `generateShortLink` recebe `input: ShortLinkInput`, não argumentos soltos
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02.sources import shopee

FIXTURES = Path(__file__).parent / "fixtures"
CAMPOS_CONVERSAO = [
    "conversionId", "purchaseTime", "totalCommission",
    "orders { orderId orderStatus items { itemId shopId qty itemTotalCommission } }",
]


def fixture(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


class ClienteFake(shopee.ClienteShopee):
    def __init__(self, respostas):
        super().__init__("id", "secret", pausa_s=0, tentativas_max=2)
        self.respostas = list(respostas)
        self.payloads = []

    def _post(self, payload):
        self.payloads.append(payload)
        return self.respostas.pop(0)


class TestQueryConversionReport(unittest.TestCase):
    def test_nao_usa_argumento_page(self):
        """A API rejeita `page` com erro 10010."""
        query = shopee.montar_query_conversion_report(20, ["conversionId"])
        self.assertNotIn("page:", query)

    def test_usa_limit(self):
        query = shopee.montar_query_conversion_report(20, ["conversionId"])
        self.assertIn("limit: 20", query)

    def test_janela_de_compra(self):
        query = shopee.montar_query_conversion_report(
            20, ["conversionId"], inicio=1700000000, fim=1700086400
        )
        self.assertIn("purchaseTimeStart: 1700000000", query)
        self.assertIn("purchaseTimeEnd: 1700086400", query)

    def test_sem_janela_omite_argumentos(self):
        query = shopee.montar_query_conversion_report(20, ["conversionId"])
        self.assertNotIn("purchaseTimeStart", query)

    def test_scroll_id_quando_informado(self):
        query = shopee.montar_query_conversion_report(
            20, ["conversionId"], scroll_id="abc123"
        )
        self.assertIn('scrollId: "abc123"', query)

    def test_selecao_aninhada_passa_intacta(self):
        query = shopee.montar_query_conversion_report(20, CAMPOS_CONVERSAO)
        self.assertIn("orders { orderId orderStatus items {", query)


class TestQueryValidatedReport(unittest.TestCase):
    def test_validation_id_e_obrigatorio_na_query(self):
        query = shopee.montar_query_validated_report(["conversionId"], 987654)
        self.assertIn("validationId: 987654", query)

    def test_nao_usa_first(self):
        query = shopee.montar_query_validated_report(["conversionId"], 1)
        self.assertNotIn("first:", query)

    def test_scroll_id_opcional(self):
        query = shopee.montar_query_validated_report(["conversionId"], 1, 500, "s1")
        self.assertIn('scrollId: "s1"', query)


class TestMutationShortLink(unittest.TestCase):
    def test_usa_objeto_input(self):
        """A mutation recebe `input: ShortLinkInput`, nao args soltos."""
        query = shopee.montar_mutation_short_link("https://shopee.com.br/x")
        self.assertIn("generateShortLink(input: {", query)
        self.assertIn('originUrl: "https://shopee.com.br/x"', query)

    def test_sub_ids_dentro_do_input(self):
        query = shopee.montar_mutation_short_link("https://x", ["tiktok", "maio"])
        self.assertIn('subIds: ["tiktok", "maio"]', query)

    def test_sem_sub_ids_omite_o_campo(self):
        self.assertNotIn("subIds", shopee.montar_mutation_short_link("https://x"))

    def test_recusa_mais_de_cinco_sub_ids(self):
        with self.assertRaises(ValueError):
            shopee.montar_mutation_short_link("https://x", list("abcdef"))

    def test_extrai_o_link_da_resposta(self):
        cliente = ClienteFake([
            {"data": {"generateShortLink": {"shortLink": "https://s.shopee.com.br/abc"}}}
        ])
        self.assertEqual(
            cliente.gerar_link_curto("https://shopee.com.br/x"),
            "https://s.shopee.com.br/abc",
        )

    def test_resposta_sem_link_vira_erro(self):
        cliente = ClienteFake([{"data": {"generateShortLink": {}}}])
        with self.assertRaises(shopee.ErroShopee):
            cliente.gerar_link_curto("https://shopee.com.br/x")


class TestAchatamentoDeConversao(unittest.TestCase):
    """conversao -> orders[] -> items[] vira uma linha por produto."""

    def setUp(self):
        self.nodes = shopee.extrair_nodes(
            fixture("conversion_report.json")["data"]["conversionReport"]
        )

    def test_um_item_gera_uma_linha(self):
        linhas = shopee.para_conversoes(self.nodes[0])
        self.assertEqual(len(linhas), 1)

    def test_dois_itens_geram_duas_linhas(self):
        linhas = shopee.para_conversoes(self.nodes[1])
        self.assertEqual(len(linhas), 2)
        self.assertEqual([l.item_id for l in linhas], ["555001", "555002"])

    def test_usa_comissao_do_item_nao_do_pedido(self):
        """itemTotalCommission, nao totalCommission -- senao duplica."""
        linhas = shopee.para_conversoes(self.nodes[1])
        self.assertAlmostEqual(linhas[0].comissao, 9.40)
        self.assertAlmostEqual(linhas[1].comissao, 3.00)
        self.assertAlmostEqual(sum(l.comissao for l in linhas), 12.40)

    def test_quantidade_vem_do_item(self):
        self.assertEqual(shopee.para_conversoes(self.nodes[1])[0].pedidos, 2)

    def test_id_composto_evita_colisao_entre_itens(self):
        linhas = shopee.para_conversoes(self.nodes[1])
        self.assertNotEqual(linhas[0].conversao_id, linhas[1].conversao_id)

    def test_timestamp_unix_vira_data(self):
        self.assertEqual(shopee.para_conversoes(self.nodes[0])[0].ocorrido_em,
                         "2026-08-31")

    def test_loja_e_preservada(self):
        self.assertEqual(shopee.para_conversoes(self.nodes[0])[0].loja_id,
                         "1108440804")

    def test_status_vem_do_pedido(self):
        self.assertEqual(shopee.para_conversoes(self.nodes[1])[0].status, "COMPLETED")

    def test_cliques_e_sempre_zero(self):
        """A API nao expoe cliques em lugar nenhum -- ver docstring do modulo."""
        for linha in shopee.para_conversoes(self.nodes[1]):
            self.assertEqual(linha.cliques, 0)

    def test_conversao_sem_itens_ainda_registra_o_total(self):
        linhas = shopee.para_conversoes(
            {"conversionId": 1, "totalCommission": "5.50", "purchaseTime": 1788210965}
        )
        self.assertEqual(len(linhas), 1)
        self.assertAlmostEqual(linhas[0].comissao, 5.50)
        self.assertIsNone(linhas[0].item_id)

    def test_sem_identificador_e_descartada(self):
        self.assertEqual(shopee.para_conversoes({"totalCommission": "1"}), [])

    def test_validada_marca_a_flag(self):
        linhas = shopee.para_conversoes(self.nodes[0], validada=True)
        self.assertTrue(linhas[0].validada)

    def test_orders_como_objeto_unico_e_tolerado(self):
        no = {"conversionId": 9, "orders": {"orderId": "x", "items": [
            {"itemId": 1, "itemTotalCommission": "2.00", "qty": 1}]}}
        self.assertEqual(len(shopee.para_conversoes(no)), 1)


class TestParseCampanha(unittest.TestCase):
    def test_converte_colecao(self):
        oferta = shopee.para_oferta_campanha({
            "offerName": "KOL_KOC_LT - BAU - Health",
            "commissionRate": "0.03",
            "offerLink": "https://s.shopee.com.br/80COgD3uB0",
            "categoryId": 11059981,
            "offerType": 2,
        })
        self.assertEqual(oferta.item_id, "campanha:11059981")
        self.assertAlmostEqual(oferta.taxa_comissao, 0.03)

    def test_campanha_nao_compete_no_ranking_de_epc(self):
        oferta = shopee.para_oferta_campanha({
            "offerName": "X", "commissionRate": "0.20", "offerLink": "https://s/x",
        })
        self.assertEqual(oferta.preco, 0.0)
        self.assertEqual(oferta.comissao_valor, 0.0)

    def test_sem_link_ou_nome_e_descartada(self):
        self.assertIsNone(shopee.para_oferta_campanha({"offerName": "X"}))
        self.assertIsNone(shopee.para_oferta_campanha({"offerLink": "https://s/x"}))


class TestPaginacaoPorPagina(unittest.TestCase):
    """productOfferV2 e shopeeOfferV2 usam page/limit."""

    def _resposta(self, raiz, quantidade, tem_proxima):
        return {"data": {raiz: {
            "nodes": [{"itemId": i} for i in range(quantidade)],
            "pageInfo": {"hasNextPage": tem_proxima},
        }}}

    def test_para_quando_nao_ha_proxima_pagina(self):
        cliente = ClienteFake([
            self._resposta("productOfferV2", 50, True),
            self._resposta("productOfferV2", 50, False),
        ])
        nodes = cliente.paginar(lambda p: "q", 50, 5, 0, "productOfferV2")
        self.assertEqual(len(nodes), 100)

    def test_para_quando_a_pagina_vem_incompleta(self):
        cliente = ClienteFake([self._resposta("productOfferV2", 7, True)])
        self.assertEqual(len(cliente.paginar(lambda p: "q", 50, 5, 0, "productOfferV2")), 7)


class TestPaginacaoPorCursor(unittest.TestCase):
    """Relatórios paginam por scrollId, não por número de página."""

    def _resposta(self, quantidade, scroll, tem_proxima):
        return {"data": {"conversionReport": {
            "nodes": [{"conversionId": i} for i in range(quantidade)],
            "pageInfo": {"scrollId": scroll, "hasNextPage": tem_proxima},
        }}}

    def test_segue_o_cursor(self):
        cliente = ClienteFake([
            self._resposta(2, "s1", True),
            self._resposta(1, None, False),
        ])
        nodes = cliente.rolar(lambda c: f"q{c}", "conversionReport")
        self.assertEqual(len(nodes), 3)
        self.assertIn("s1", cliente.payloads[1])

    def test_para_sem_cursor_mesmo_com_has_next(self):
        cliente = ClienteFake([self._resposta(2, None, True)])
        self.assertEqual(len(cliente.rolar(lambda c: "q", "conversionReport")), 2)

    def test_respeita_o_maximo_de_paginas(self):
        cliente = ClienteFake([self._resposta(2, "s", True)] * 3)
        cliente.rolar(lambda c: "q", "conversionReport", paginas_max=3)
        self.assertEqual(len(cliente.payloads), 3)


class TestErros(unittest.TestCase):
    def test_sem_acesso_e_fatal(self):
        self.assertTrue(shopee.ErroShopee(shopee.CODIGO_SEM_ACESSO, "").fatal)

    def test_parametros_invalidos_e_fatal(self):
        self.assertTrue(shopee.ErroShopee(shopee.CODIGO_PARAMETROS, "").fatal)

    def test_erro_de_sistema_e_recuperavel(self):
        self.assertFalse(shopee.ErroShopee(shopee.CODIGO_SISTEMA, "").fatal)

    def test_query_fica_anexada_ao_erro(self):
        cliente = ClienteFake([
            {"errors": [{"extensions": {"code": 10010, "message": "campo x"}}]}
        ])
        with self.assertRaises(shopee.ErroShopee) as ctx:
            cliente.executar("query { campo_errado }")
        self.assertEqual(ctx.exception.query, "query { campo_errado }")

    def test_details_e_usado_quando_nao_ha_message(self):
        codigo, mensagem = shopee._primeiro_erro(
            [{"extensions": {"code": "10020", "details": "assinatura vencida"}}]
        )
        self.assertEqual(codigo, 10020)
        self.assertEqual(mensagem, "assinatura vencida")


if __name__ == "__main__":
    unittest.main()
