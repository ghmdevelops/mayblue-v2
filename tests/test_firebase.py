from __future__ import annotations

import json
import unittest

from tests import SRC  # noqa: F401

from flow02.rede import ErroHTTP, Resposta
from flow02.saidas import firebase
from flow02.saidas.firebase import ClienteFirebase, ErroFirebase

URL = "https://valida-10345-default-rtdb.firebaseio.com"


class ClienteEspiao(ClienteFirebase):
    """Substitui a camada HTTP para inspecionar url, metodo e corpo."""

    def __init__(self, respostas=None, **kwargs):
        super().__init__(**kwargs)
        self.chamadas: list[tuple[str, str, object]] = []
        self.respostas = list(respostas or [])

    def _enviar(self, caminho, dados, metodo):
        self.chamadas.append((metodo, caminho, dados))

    def _autenticar(self):
        return super()._autenticar() if (self.segredo or self.email) else None


def linha(item_id="1", plataforma="shopee", **kwargs):
    base = {
        "item_id": item_id, "plataforma": plataforma, "nome": "Fone Pro",
        "preco": 89.9, "taxa_comissao": 0.135, "comissao_valor": 12.14,
        "score": 0.2671, "cvr_estimado": 0.022, "calibrado": 0,
        "vendas": 12480, "rating": 4.8, "desconto_pct": 45.0,
        "loja_nome": "AudioTech", "link_oferta": "https://s.shopee.com.br/aaa",
        "link_produto": "https://shopee.com.br/product/1/2",
    }
    base.update(kwargs)
    return base


class TestConstrucao(unittest.TestCase):
    def test_url_ausente_falha_com_mensagem_util(self):
        """O snippet do console so traz databaseURL se o RTDB ja existe."""
        with self.assertRaises(ErroFirebase) as ctx:
            ClienteFirebase(database_url="")
        self.assertIn("databaseURL", str(ctx.exception))

    def test_remove_barra_final(self):
        self.assertEqual(ClienteFirebase(database_url=URL + "/").database_url, URL)


class TestModoAuth(unittest.TestCase):
    def test_conta_de_servico(self):
        cliente = ClienteFirebase(URL, api_key="k", email="a@b.c", senha="x")
        self.assertEqual(cliente.modo_auth, "conta de servico")

    def test_segredo_legado(self):
        self.assertEqual(
            ClienteFirebase(URL, segredo="s").modo_auth, "database secret (legado)"
        )

    def test_sem_autenticacao(self):
        self.assertEqual(ClienteFirebase(URL).modo_auth, "sem autenticacao")

    def test_email_sem_api_key_nao_conta(self):
        cliente = ClienteFirebase(URL, email="a@b.c", senha="x")
        self.assertEqual(cliente.modo_auth, "sem autenticacao")


class TestMontagemDeUrl(unittest.TestCase):
    def test_caminho_vira_json(self):
        cliente = ClienteFirebase(URL)
        self.assertEqual(cliente._url("flow02/ranking"), f"{URL}/flow02/ranking.json")

    def test_barras_extras_sao_ignoradas(self):
        cliente = ClienteFirebase(URL)
        self.assertEqual(cliente._url("/flow02//ranking/"), f"{URL}/flow02/ranking.json")

    def test_segredo_vai_no_query_auth(self):
        cliente = ClienteFirebase(URL, segredo="abc123")
        self.assertTrue(cliente._url("x").endswith("x.json?auth=abc123"))

    def test_sem_auth_nao_tem_query(self):
        self.assertNotIn("?", ClienteFirebase(URL)._url("x"))

    def test_caracteres_proibidos_sao_escapados(self):
        """A RTDB rejeita . $ # [ ] / em nome de chave."""
        cliente = ClienteFirebase(URL)
        self.assertIn("a%23b", cliente._url("flow02/a#b"))


class TestConsole(unittest.TestCase):
    def test_deriva_url_do_console(self):
        cliente = ClienteFirebase(URL)
        self.assertIn("project/valida-10345/database", cliente.url_console())


class TestSincronizacaoRanking(unittest.TestCase):
    def setUp(self):
        self.cliente = ClienteEspiao(database_url=URL)

    def test_agrupa_por_plataforma(self):
        enviados = firebase.sincronizar_ranking(
            self.cliente, "flow02", "2026-09-03",
            [linha("1"), linha("2"), linha("3", plataforma="mercadolivre")],
        )
        self.assertEqual(enviados, {"shopee": 2, "mercadolivre": 1})

    def test_caminho_inclui_dia_e_plataforma(self):
        firebase.sincronizar_ranking(self.cliente, "flow02", "2026-09-03", [linha()])
        metodo, caminho, _ = self.cliente.chamadas[0]
        self.assertEqual(metodo, "PUT")
        self.assertEqual(caminho, "flow02/ranking/2026-09-03/shopee")

    def test_itens_ficam_indexados_por_item_id(self):
        firebase.sincronizar_ranking(self.cliente, "flow02", "2026-09-03", [linha("777")])
        _, _, dados = self.cliente.chamadas[0]
        self.assertIn("777", dados)
        self.assertEqual(dados["777"]["nome"], "Fone Pro")
        self.assertEqual(dados["777"]["posicao"], 1)

    def test_link_curto_tem_precedencia(self):
        firebase.sincronizar_ranking(
            self.cliente, "flow02", "2026-09-03", [linha("1")],
            links={"1": "https://s.shopee.com.br/curto"},
        )
        _, _, dados = self.cliente.chamadas[0]
        self.assertEqual(dados["1"]["link"], "https://s.shopee.com.br/curto")

    def test_sem_link_curto_usa_offer_link(self):
        firebase.sincronizar_ranking(self.cliente, "flow02", "2026-09-03", [linha("1")])
        _, _, dados = self.cliente.chamadas[0]
        self.assertEqual(dados["1"]["link"], "https://s.shopee.com.br/aaa")

    def test_resumo_e_mesclado_nao_substituido(self):
        firebase.sincronizar_ranking(self.cliente, "flow02", "2026-09-03", [linha()])
        metodo, caminho, dados = self.cliente.chamadas[-1]
        self.assertEqual(metodo, "PATCH")
        self.assertEqual(caminho, "flow02/resumo/2026-09-03")
        self.assertEqual(dados["total_itens"], 1)
        self.assertAlmostEqual(dados["melhor_epc"], 0.2671)

    def test_raiz_customizada(self):
        firebase.sincronizar_ranking(self.cliente, "meuapp", "2026-09-03", [linha()])
        self.assertTrue(self.cliente.chamadas[0][1].startswith("meuapp/"))

    def test_payload_e_serializavel(self):
        firebase.sincronizar_ranking(self.cliente, "flow02", "2026-09-03", [linha()])
        for _, _, dados in self.cliente.chamadas:
            json.dumps(dados)


class TestSincronizacaoGanhos(unittest.TestCase):
    def setUp(self):
        self.cliente = ClienteEspiao(database_url=URL)

    def _ganho(self, dia="2026-09-03", plataforma="shopee", **kwargs):
        base = {"dia": dia, "plataforma": plataforma, "cliques": 150,
                "pedidos": 6, "comissao": 74.5, "validada": 0}
        base.update(kwargs)
        return base

    def test_chaveia_por_dia_e_plataforma(self):
        total = firebase.sincronizar_ganhos(self.cliente, "flow02", [
            self._ganho(), self._ganho(plataforma="mercadolivre"),
        ])
        self.assertEqual(total, 2)
        _, caminho, dados = self.cliente.chamadas[0]
        self.assertEqual(caminho, "flow02/ganhos")
        self.assertIn("2026-09-03_shopee", dados)
        self.assertIn("2026-09-03_mercadolivre", dados)

    def test_lista_vazia_nao_envia(self):
        self.assertEqual(firebase.sincronizar_ganhos(self.cliente, "flow02", []), 0)
        self.assertEqual(self.cliente.chamadas, [])

    def test_usa_patch_para_nao_apagar_historico(self):
        firebase.sincronizar_ganhos(self.cliente, "flow02", [self._ganho()])
        self.assertEqual(self.cliente.chamadas[0][0], "PATCH")


class TestTratamentoDeErro(unittest.TestCase):
    def test_401_explica_as_regras(self):
        cliente = ClienteFirebase(URL)
        cliente._url = lambda caminho: f"{URL}/{caminho}.json"

        def falhar(*args, **kwargs):
            raise ErroHTTP(401, "Permission denied", URL)

        firebase.requisitar, original = falhar, firebase.requisitar
        try:
            with self.assertRaises(ErroFirebase) as ctx:
                cliente.escrever("x", {})
            self.assertIn("regras", str(ctx.exception))
        finally:
            firebase.requisitar = original

    def test_404_explica_url_errada(self):
        cliente = ClienteFirebase(URL)
        cliente._url = lambda caminho: f"{URL}/{caminho}.json"

        def falhar(*args, **kwargs):
            raise ErroHTTP(404, "not found", URL)

        firebase.requisitar, original = falhar, firebase.requisitar
        try:
            with self.assertRaises(ErroFirebase) as ctx:
                cliente.escrever("x", {})
            self.assertIn("nao foi criado", str(ctx.exception))
        finally:
            firebase.requisitar = original


class TestLoginComToken(unittest.TestCase):
    def _com_login(self, resposta):
        cliente = ClienteFirebase(URL, api_key="k", email="a@b.c", senha="x")

        def responder(*args, **kwargs):
            return Resposta(200, json.dumps(resposta))

        firebase.requisitar, original = responder, firebase.requisitar
        return cliente, original

    def test_usa_id_token_no_query(self):
        cliente, original = self._com_login({"idToken": "tok123", "expiresIn": "3600"})
        try:
            self.assertIn("auth=tok123", cliente._url("x"))
        finally:
            firebase.requisitar = original

    def test_token_e_reaproveitado(self):
        cliente = ClienteFirebase(URL, api_key="k", email="a@b.c", senha="x")
        chamadas = []

        def responder(*args, **kwargs):
            chamadas.append(1)
            return Resposta(200, json.dumps({"idToken": "t", "expiresIn": "3600"}))

        firebase.requisitar, original = responder, firebase.requisitar
        try:
            cliente._url("a")
            cliente._url("b")
            self.assertEqual(len(chamadas), 1)
        finally:
            firebase.requisitar = original

    def test_login_sem_token_falha(self):
        cliente, original = self._com_login({"error": {"message": "INVALID_PASSWORD"}})
        try:
            with self.assertRaises(ErroFirebase):
                cliente._url("x")
        finally:
            firebase.requisitar = original


if __name__ == "__main__":
    unittest.main()
