from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02.config import FirebaseConfig
from flow02.rede import ErroHTTP, Resposta
from flow02.saidas import firebase

URL_EXPLICITA = "https://outra-coisa.firebaseio.com"


class TestInferenciaDeUrl(unittest.TestCase):
    """O snippet do console omite databaseURL enquanto o RTDB nao existe."""

    def test_url_explicita_tem_precedencia(self):
        cfg = FirebaseConfig(database_url=URL_EXPLICITA, projeto="valida-10345")
        self.assertEqual(cfg.url, URL_EXPLICITA)
        self.assertFalse(cfg.url_inferida)

    def test_infere_do_projeto(self):
        cfg = FirebaseConfig(projeto="valida-10345")
        self.assertEqual(cfg.url, "https://valida-10345-default-rtdb.firebaseio.com")
        self.assertTrue(cfg.url_inferida)

    def test_regiao_muda_o_dominio(self):
        cfg = FirebaseConfig(projeto="valida-10345", regiao="southamerica-east1")
        self.assertEqual(
            cfg.url,
            "https://valida-10345-default-rtdb.southamerica-east1.firebasedatabase.app",
        )

    def test_us_central1_usa_o_dominio_antigo(self):
        cfg = FirebaseConfig(projeto="valida-10345", regiao="us-central1")
        self.assertTrue(cfg.url.endswith("firebaseio.com"))

    def test_remove_barra_final(self):
        cfg = FirebaseConfig(database_url=URL_EXPLICITA + "/")
        self.assertEqual(cfg.url, URL_EXPLICITA)

    def test_sem_nada_fica_vazio(self):
        cfg = FirebaseConfig()
        self.assertEqual(cfg.url, "")
        self.assertFalse(cfg.configurado)

    def test_configurado_com_so_o_projeto(self):
        cfg = FirebaseConfig(habilitado=True, projeto="valida-10345")
        self.assertTrue(cfg.configurado)


class TestCandidatas(unittest.TestCase):
    def test_url_explicita_gera_uma_candidata(self):
        cfg = FirebaseConfig(database_url=URL_EXPLICITA)
        self.assertEqual(cfg.urls_candidatas(), (URL_EXPLICITA,))

    def test_projeto_gera_varias_regioes(self):
        candidatas = FirebaseConfig(projeto="valida-10345").urls_candidatas()
        self.assertGreater(len(candidatas), 1)
        self.assertTrue(all("valida-10345" in u for u in candidatas))

    def test_inclui_dominio_antigo_e_novo(self):
        candidatas = FirebaseConfig(projeto="p").urls_candidatas()
        self.assertTrue(any(u.endswith("firebaseio.com") for u in candidatas))
        self.assertTrue(any("firebasedatabase.app" in u for u in candidatas))

    def test_sem_projeto_nao_ha_candidatas(self):
        self.assertEqual(FirebaseConfig().urls_candidatas(), ())


class TestSondagem(unittest.TestCase):
    def _com_resposta(self, acao):
        firebase.requisitar, original = acao, firebase.requisitar
        return original

    def test_404_significa_banco_inexistente(self):
        original = self._com_resposta(
            lambda *a, **k: (_ for _ in ()).throw(ErroHTTP(404, "", "u")))
        try:
            estado, detalhe = firebase.sondar(URL_EXPLICITA)
        finally:
            firebase.requisitar = original
        self.assertEqual(estado, "ausente")
        self.assertIn("nao ha Realtime Database", detalhe)

    def test_401_significa_banco_existe(self):
        """401 e boa noticia: a URL esta certa, so as regras negam."""
        original = self._com_resposta(
            lambda *a, **k: (_ for _ in ()).throw(ErroHTTP(401, "", "u")))
        try:
            estado, detalhe = firebase.sondar(URL_EXPLICITA)
        finally:
            firebase.requisitar = original
        self.assertEqual(estado, "existe")
        self.assertIn("regras", detalhe)

    def test_200_com_null_e_banco_vazio(self):
        original = self._com_resposta(lambda *a, **k: Resposta(200, "null"))
        try:
            estado, detalhe = firebase.sondar(URL_EXPLICITA)
        finally:
            firebase.requisitar = original
        self.assertEqual(estado, "existe")
        self.assertIn("vazio", detalhe)

    def test_200_com_dados(self):
        original = self._com_resposta(
            lambda *a, **k: Resposta(200, '{"flow02":true}'))
        try:
            estado, detalhe = firebase.sondar(URL_EXPLICITA)
        finally:
            firebase.requisitar = original
        self.assertEqual(estado, "existe")
        self.assertIn("flow02", detalhe)

    def test_falha_de_rede_nao_afirma_ausencia(self):
        original = self._com_resposta(
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")))
        try:
            estado, _ = firebase.sondar(URL_EXPLICITA)
        finally:
            firebase.requisitar = original
        self.assertEqual(estado, "erro")


if __name__ == "__main__":
    unittest.main()
