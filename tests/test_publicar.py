from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02.config import Config, FirebaseConfig
from flow02.web import publicar
from flow02.web.publicar import ErroPublicacao

URL = "https://valida-10345-default-rtdb.firebaseio.com"


def config(**kwargs) -> Config:
    base = dict(habilitado=True, database_url=URL, raiz="flow02",
                api_key="AIzaSyFAKE")
    base.update(kwargs)
    return Config(firebase=FirebaseConfig(**base))


class TestGeracao(unittest.TestCase):
    def test_sem_database_url_falha_com_instrucao(self):
        with self.assertRaises(ErroPublicacao) as ctx:
            publicar.gerar(config(database_url=""))
        self.assertIn("Realtime Database", str(ctx.exception))

    def test_substitui_todos_os_marcadores(self):
        html = publicar.gerar(config())
        self.assertNotIn("__DATABASE_URL__", html)
        self.assertNotIn("__API_KEY__", html)
        self.assertNotIn("__RAIZ__", html)

    def test_embute_a_url_do_banco(self):
        self.assertIn(URL, publicar.gerar(config()))

    def test_remove_barra_final_da_url(self):
        html = publicar.gerar(config(database_url=URL + "/"))
        self.assertIn(f'databaseUrl: "{URL}"', html)

    def test_embute_a_raiz_customizada(self):
        self.assertIn('raiz: "meuapp"', publicar.gerar(config(raiz="meuapp")))

    def test_api_key_ausente_vira_string_vazia(self):
        html = publicar.gerar(config(api_key=None))
        self.assertIn('apiKey: ""', html)

    def test_pagina_e_autocontida(self):
        """Sem <script src> externo: precisa abrir do disco e sem npm."""
        html = publicar.gerar(config())
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)

    def test_nao_referencia_o_sdk_do_firebase(self):
        html = publicar.gerar(config())
        self.assertNotIn("gstatic.com/firebasejs", html)
        self.assertNotIn("firebase/app", html)

    def test_usa_a_api_rest_da_rtdb(self):
        html = publicar.gerar(config())
        self.assertIn(".json", html)
        self.assertIn("identitytoolkit.googleapis.com", html)


class TestEscrita(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_grava_no_destino_pedido(self):
        destino = self.dir / "sub" / "painel.html"
        resultado = publicar.escrever(config(), destino)
        self.assertEqual(resultado, destino)
        self.assertIn(URL, destino.read_text(encoding="utf-8"))

    def test_cria_o_diretorio(self):
        destino = self.dir / "a" / "b" / "c.html"
        publicar.escrever(config(), destino)
        self.assertTrue(destino.exists())

    def test_destino_padrao_usa_caminho_saida(self):
        cfg = Config(
            firebase=FirebaseConfig(habilitado=True, database_url=URL),
            caminho_saida=self.dir / "saida",
        )
        self.assertEqual(publicar.escrever(cfg), self.dir / "saida" / "painel.html")


class TestInstrucoes(unittest.TestCase):
    def test_extrai_o_id_do_projeto_da_url(self):
        texto = publicar.instrucoes(config(), Path("x.html"))
        self.assertIn("--project valida-10345", texto)

    def test_mostra_as_regras_com_a_raiz(self):
        texto = publicar.instrucoes(config(raiz="meuapp"), Path("x.html"))
        self.assertIn('"meuapp"', texto)

    def test_menciona_o_caminho_gerado(self):
        texto = publicar.instrucoes(config(), Path("saida/painel.html"))
        self.assertIn("painel.html", texto)


if __name__ == "__main__":
    unittest.main()
