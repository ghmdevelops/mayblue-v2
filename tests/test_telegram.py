from __future__ import annotations

import json
import unittest

from tests import SRC  # noqa: F401

from flow02.analise import Alerta
from flow02.rede import ErroHTTP, Resposta
from flow02.saidas import telegram
from flow02.saidas.telegram import ErroTelegram, dividir, montar_alertas


class TestDivisao(unittest.TestCase):
    def test_texto_curto_vira_uma_mensagem(self):
        self.assertEqual(dividir("oi"), ["oi"])

    def test_texto_vazio_nao_gera_mensagem(self):
        self.assertEqual(dividir(""), [])

    def test_quebra_respeitando_o_limite(self):
        texto = "\n\n".join("bloco " + "x" * 90 for _ in range(10))
        partes = dividir(texto, limite=200)
        self.assertGreater(len(partes), 1)
        for parte in partes:
            self.assertLessEqual(len(parte), 200)

    def test_nao_parte_bloco_no_meio(self):
        texto = "\n\n".join(["AAA", "BBB", "CCC"])
        partes = dividir(texto, limite=10)
        for parte in partes:
            for bloco in parte.split("\n\n"):
                self.assertIn(bloco, {"AAA", "BBB", "CCC"})

    def test_bloco_maior_que_o_limite_e_truncado(self):
        partes = dividir("x" * 500, limite=100)
        self.assertTrue(all(len(p) <= 100 for p in partes))


class TestEnvio(unittest.TestCase):
    def _capturar(self, respostas=None):
        chamadas = []

        def responder(url, dados=None, cabecalhos=None, metodo="GET", timeout_s=30.0):
            chamadas.append((url, json.loads(dados) if dados else None))
            return Resposta(200, json.dumps({"ok": True}))

        telegram.requisitar, original = responder, telegram.requisitar
        return chamadas, original

    def test_envia_para_o_chat(self):
        chamadas, original = self._capturar()
        try:
            partes = telegram.enviar("TOK", "123", "mensagem")
        finally:
            telegram.requisitar = original
        self.assertEqual(partes, 1)
        url, corpo = chamadas[0]
        self.assertIn("/botTOK/sendMessage", url)
        self.assertEqual(corpo["chat_id"], "123")
        self.assertEqual(corpo["text"], "mensagem")

    def test_texto_longo_vira_varias_chamadas(self):
        chamadas, original = self._capturar()
        try:
            texto = "\n\n".join("b" * 500 for _ in range(20))
            partes = telegram.enviar("TOK", "123", texto)
        finally:
            telegram.requisitar = original
        self.assertGreater(partes, 1)
        self.assertEqual(len(chamadas), partes)

    def test_desabilita_previa_de_link(self):
        chamadas, original = self._capturar()
        try:
            telegram.enviar("TOK", "123", "x")
        finally:
            telegram.requisitar = original
        self.assertTrue(chamadas[0][1]["disable_web_page_preview"])

    def test_token_ausente(self):
        with self.assertRaises(ErroTelegram):
            telegram.enviar("", "123", "x")

    def test_chat_ausente(self):
        with self.assertRaises(ErroTelegram):
            telegram.enviar("TOK", "", "x")

    def test_401_explica_token(self):
        def falhar(*args, **kwargs):
            raise ErroHTTP(401, "Unauthorized", "u")

        telegram.requisitar, original = falhar, telegram.requisitar
        try:
            with self.assertRaises(ErroTelegram) as ctx:
                telegram.enviar("TOK", "123", "x")
            self.assertIn("token do bot invalido", str(ctx.exception))
        finally:
            telegram.requisitar = original

    def test_chat_nao_encontrado_da_instrucao(self):
        def falhar(*args, **kwargs):
            raise ErroHTTP(400, '{"description":"Bad Request: chat not found"}', "u")

        telegram.requisitar, original = falhar, telegram.requisitar
        try:
            with self.assertRaises(ErroTelegram) as ctx:
                telegram.enviar("TOK", "999", "x")
            self.assertIn("getUpdates", str(ctx.exception))
        finally:
            telegram.requisitar = original


class TestMensagemDeAlertas(unittest.TestCase):
    def _alerta(self, **kwargs):
        base = dict(
            tipo="comissao_subiu", plataforma="shopee", item_id="1",
            nome="Fone TWS Pro", antes=0.04, depois=0.15, variacao=2.75,
            link="https://s.shopee.com.br/x",
        )
        base.update(kwargs)
        return Alerta(**base)

    def test_sem_alertas_avisa(self):
        texto = montar_alertas([], "2026-09-03")
        self.assertIn("Nenhuma mudanca", texto)

    def test_inclui_rotulo_nome_variacao_e_link(self):
        texto = montar_alertas([self._alerta()], "2026-09-03")
        self.assertIn("COMISSAO SUBIU", texto)
        self.assertIn("Fone TWS Pro", texto)
        self.assertIn("+275%", texto)
        self.assertIn("https://s.shopee.com.br/x", texto)

    def test_mostra_antes_e_depois(self):
        self.assertIn("0.04 -> 0.15", montar_alertas([self._alerta()], "2026-09-03"))

    def test_respeita_o_limite_e_indica_o_resto(self):
        alertas = [self._alerta(item_id=str(i)) for i in range(10)]
        texto = montar_alertas(alertas, "2026-09-03", limite=3)
        self.assertIn("e mais 7 mudanca(s)", texto)

    def test_alerta_sem_variacao_nao_quebra(self):
        alerta = self._alerta(tipo="novo_no_top", antes=None, variacao=None)
        texto = montar_alertas([alerta], "2026-09-03")
        self.assertIn("NOVO NO TOP", texto)


if __name__ == "__main__":
    unittest.main()
