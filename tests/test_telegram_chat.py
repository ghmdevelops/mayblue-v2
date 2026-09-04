from __future__ import annotations

import json
import unittest

from tests import SRC  # noqa: F401

from flow02.rede import ErroHTTP, Resposta
from flow02.saidas import telegram
from flow02.saidas.telegram import ErroTelegram, descobrir_chats


class BaseTelegram(unittest.TestCase):
    def responder(self, corpo):
        def responder(*args, **kwargs):
            return Resposta(200, json.dumps(corpo))
        telegram.requisitar, self._original = responder, telegram.requisitar

    def falhar(self, status):
        def falhar(*args, **kwargs):
            raise ErroHTTP(status, "", "u")
        telegram.requisitar, self._original = falhar, telegram.requisitar

    def tearDown(self):
        if hasattr(self, "_original"):
            telegram.requisitar = self._original


class TestDescobrirChats(BaseTelegram):
    def test_extrai_chat_de_mensagem_privada(self):
        self.responder({"result": [
            {"message": {"chat": {"id": 123456, "type": "private",
                                  "first_name": "Joao", "last_name": "Silva"}}}
        ]})
        chats = descobrir_chats("TOK")
        self.assertEqual(chats[0]["chat_id"], "123456")
        self.assertEqual(chats[0]["tipo"], "private")
        self.assertEqual(chats[0]["nome"], "Joao Silva")

    def test_extrai_chat_de_grupo(self):
        self.responder({"result": [
            {"message": {"chat": {"id": -100987, "type": "supergroup",
                                  "title": "Meus Achadinhos"}}}
        ]})
        chats = descobrir_chats("TOK")
        self.assertEqual(chats[0]["chat_id"], "-100987")
        self.assertEqual(chats[0]["nome"], "Meus Achadinhos")

    def test_extrai_de_canal(self):
        self.responder({"result": [
            {"channel_post": {"chat": {"id": -100555, "type": "channel",
                                       "title": "Canal Ofertas"}}}
        ]})
        self.assertEqual(descobrir_chats("TOK")[0]["chat_id"], "-100555")

    def test_extrai_de_my_chat_member(self):
        """Bot adicionado a grupo gera esse evento, sem mensagem."""
        self.responder({"result": [
            {"my_chat_member": {"chat": {"id": -777, "type": "group",
                                         "title": "Grupo VIP"}}}
        ]})
        self.assertEqual(descobrir_chats("TOK")[0]["chat_id"], "-777")

    def test_deduplica_o_mesmo_chat(self):
        mensagem = {"message": {"chat": {"id": 1, "type": "private",
                                         "first_name": "A"}}}
        self.responder({"result": [mensagem, mensagem, mensagem]})
        self.assertEqual(len(descobrir_chats("TOK")), 1)

    def test_varios_chats_distintos(self):
        self.responder({"result": [
            {"message": {"chat": {"id": 1, "type": "private", "first_name": "A"}}},
            {"message": {"chat": {"id": -2, "type": "group", "title": "B"}}},
        ]})
        self.assertEqual(len(descobrir_chats("TOK")), 2)

    def test_ignora_atualizacao_sem_chat(self):
        self.responder({"result": [{"poll": {"id": "x"}}]})
        self.assertEqual(descobrir_chats("TOK"), [])

    def test_sem_atualizacoes(self):
        self.responder({"result": []})
        self.assertEqual(descobrir_chats("TOK"), [])

    def test_usa_username_quando_nao_ha_nome(self):
        self.responder({"result": [
            {"message": {"chat": {"id": 9, "type": "private", "username": "fulano"}}}
        ]})
        self.assertEqual(descobrir_chats("TOK")[0]["nome"], "fulano")

    def test_token_ausente(self):
        with self.assertRaises(ErroTelegram):
            descobrir_chats("")

    def test_token_invalido(self):
        self.falhar(401)
        with self.assertRaises(ErroTelegram) as ctx:
            descobrir_chats("TOK")
        self.assertIn("token do bot invalido", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
