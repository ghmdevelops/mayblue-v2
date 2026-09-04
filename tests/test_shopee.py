from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401  (garante src/ no sys.path)

from flow02.config import ConsultaConfig
from flow02.sources import shopee

FIXTURES = Path(__file__).parent / "fixtures"


def carregar_fixture(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


class TestAssinatura(unittest.TestCase):
    def test_ordem_da_concatenacao(self):
        """A ordem AppId+Timestamp+Payload+Secret e o que quebra na pratica."""
        payload = '{"query":"{ __typename }"}'
        esperado = hashlib.sha256(
            b'1234561577836800{"query":"{ __typename }"}segredo'
        ).hexdigest()
        self.assertEqual(
            shopee.assinar("123456", "segredo", payload, 1577836800), esperado
        )

    def test_header_completo(self):
        header = shopee.montar_header("123456", "segredo", "{}", 1577836800)
        self.assertTrue(header.startswith("SHA256 Credential=123456, "))
        self.assertIn("Timestamp=1577836800", header)
        self.assertIn("Signature=", header)

    def test_secret_diferente_muda_assinatura(self):
        a = shopee.assinar("1", "s1", "{}", 1)
        b = shopee.assinar("1", "s2", "{}", 1)
        self.assertNotEqual(a, b)


class TestMontagemQuery(unittest.TestCase):
    def test_argumentos_opcionais_omitidos(self):
        consulta = ConsultaConfig(nome="x", sort_type=5, list_type=0)
        query = shopee.montar_query_product_offer(consulta, 2, 50, ["itemId"])
        self.assertIn("page: 2", query)
        self.assertIn("limit: 50", query)
        self.assertIn("sortType: 5", query)
        self.assertIn("listType: 0", query)
        self.assertNotIn("keyword", query)
        self.assertNotIn("isAMSOffer", query)

    def test_booleano_em_minusculo(self):
        consulta = ConsultaConfig(nome="x", is_ams_offer=True)
        query = shopee.montar_query_product_offer(consulta, 1, 10, ["itemId"])
        self.assertIn("isAMSOffer: true", query)
        self.assertNotIn("isAMSOffer: True", query)

    def test_keyword_vai_com_aspas(self):
        consulta = ConsultaConfig(nome="x", keyword='fone "pro"')
        query = shopee.montar_query_product_offer(consulta, 1, 10, ["itemId"])
        self.assertIn(r'keyword: "fone \"pro\""', query)

    def test_selecao_de_campos_da_config(self):
        consulta = ConsultaConfig(nome="x")
        query = shopee.montar_query_product_offer(
            consulta, 1, 1, ["itemId", "productName", "commissionRate"]
        )
        self.assertIn("nodes { itemId productName commissionRate }", query)


class TestExtracaoNodes(unittest.TestCase):
    def test_formato_nodes(self):
        bloco = {"nodes": [{"itemId": 1}, {"itemId": 2}]}
        self.assertEqual(len(shopee.extrair_nodes(bloco)), 2)

    def test_formato_relay_edges(self):
        bloco = {"edges": [{"node": {"itemId": 1}}, {"node": {"itemId": 2}}]}
        self.assertEqual(len(shopee.extrair_nodes(bloco)), 2)

    def test_bloco_vazio_ou_desconhecido(self):
        self.assertEqual(shopee.extrair_nodes(None), [])
        self.assertEqual(shopee.extrair_nodes({}), [])
        self.assertEqual(shopee.extrair_nodes({"outra": 1}), [])

    def test_ignora_arestas_malformadas(self):
        bloco = {"edges": [{"node": {"itemId": 1}}, {"sem_node": True}, None]}
        self.assertEqual(len(shopee.extrair_nodes(bloco)), 1)


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.nodes = shopee.extrair_nodes(
            carregar_fixture("product_offer_v2.json")["data"]["productOfferV2"]
        )

    def test_converte_strings_numericas(self):
        oferta = shopee.para_oferta(self.nodes[0], "maior_comissao")
        self.assertEqual(oferta.item_id, "23798776965")
        self.assertAlmostEqual(oferta.preco, 89.90)
        self.assertAlmostEqual(oferta.taxa_comissao, 0.1350)
        self.assertAlmostEqual(oferta.rating, 4.8)
        self.assertEqual(oferta.vendas, 12480)
        self.assertEqual(oferta.desconto_pct, 45.0)
        self.assertEqual(oferta.loja_id, "750190")
        self.assertEqual(oferta.categoria_ids, ("100017", "100630"))

    def test_comissao_em_reais(self):
        oferta = shopee.para_oferta(self.nodes[0], "x")
        self.assertAlmostEqual(oferta.comissao_valor, 89.90 * 0.1350, places=4)

    def test_node_sem_preco_e_descartado(self):
        self.assertIsNone(shopee.para_oferta({"itemId": 1}, "x"))

    def test_node_sem_item_id_e_descartado(self):
        self.assertIsNone(shopee.para_oferta({"priceMin": "10"}, "x"))

    def test_rating_zero_vira_none_no_scoring(self):
        oferta = shopee.para_oferta(self.nodes[2], "x")
        self.assertEqual(oferta.rating, 0.0)
        self.assertIsNone(oferta.link_oferta)


class TestErros(unittest.TestCase):
    def test_codigo_extraido_das_extensions(self):
        codigo, mensagem = shopee._primeiro_erro(
            [{"message": "x", "extensions": {"code": 10020, "message": "auth ruim"}}]
        )
        self.assertEqual(codigo, shopee.CODIGO_AUTENTICACAO)
        self.assertEqual(mensagem, "auth ruim")

    def test_auth_e_parsing_sao_fatais(self):
        self.assertTrue(shopee.ErroShopee(shopee.CODIGO_AUTENTICACAO, "").fatal)
        self.assertTrue(shopee.ErroShopee(shopee.CODIGO_PARSING, "").fatal)

    def test_rate_limit_nao_e_fatal(self):
        """10030 deve ser reprocessado com backoff, nao abortar a coleta."""
        self.assertFalse(shopee.ErroShopee(shopee.CODIGO_RATE_LIMIT, "").fatal)


class ClienteFake(shopee.ClienteShopee):
    def __init__(self, respostas):
        super().__init__("id", "secret", pausa_s=0, tentativas_max=3)
        self.respostas = list(respostas)
        self.payloads = []

    def _post(self, payload):
        self.payloads.append(payload)
        return self.respostas.pop(0)


class TestRetry(unittest.TestCase):
    def test_retenta_no_rate_limit_e_depois_sucede(self):
        erro = {"errors": [{"extensions": {"code": 10030, "message": "limite"}}]}
        ok = {"data": {"productOfferV2": {"nodes": []}}}
        cliente = ClienteFake([erro, ok])
        dados = cliente.executar("query { x }")
        self.assertEqual(dados, {"productOfferV2": {"nodes": []}})
        self.assertEqual(len(cliente.payloads), 2)

    def test_nao_retenta_erro_de_autenticacao(self):
        erro = {"errors": [{"extensions": {"code": 10020, "message": "auth"}}]}
        cliente = ClienteFake([erro])
        with self.assertRaises(shopee.ErroShopee) as ctx:
            cliente.executar("query { x }")
        self.assertEqual(ctx.exception.codigo, shopee.CODIGO_AUTENTICACAO)
        self.assertEqual(len(cliente.payloads), 1)

    def test_payload_assinado_e_o_mesmo_enviado(self):
        ok = {"data": {}}
        cliente = ClienteFake([ok])
        cliente.executar("query { x }")
        self.assertEqual(cliente.payloads[0], '{"query":"query { x }"}')


if __name__ == "__main__":
    unittest.main()
