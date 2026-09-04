from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.saidas.relatorio import moeda, renderizar_post


def item(**kwargs) -> dict:
    base = {
        "nome": "Fone Bluetooth ANC Pro",
        "preco": 249.00,
        "desconto_pct": 30.0,
        "vendas": 3100,
        "rating": 4.5,
        "comissao_valor": 9.96,
        "taxa_comissao": 0.04,
        "link": "https://s.shopee.com.br/abc123",
    }
    base.update(kwargs)
    return base


class TestMoeda(unittest.TestCase):
    def test_formato_brasileiro(self):
        self.assertEqual(moeda(1499.9), "R$ 1.499,90")

    def test_valor_pequeno(self):
        self.assertEqual(moeda(9.5), "R$ 9,50")

    def test_milhar_com_ponto(self):
        self.assertEqual(moeda(1234567.89), "R$ 1.234.567,89")

    def test_zero(self):
        self.assertEqual(moeda(0), "R$ 0,00")


class TestRenderizarPost(unittest.TestCase):
    def test_traz_nome_preco_e_link(self):
        texto = renderizar_post([item()])
        self.assertIn("1. Fone Bluetooth ANC Pro", texto)
        self.assertIn("R$ 249,00", texto)
        self.assertIn("https://s.shopee.com.br/abc123", texto)

    def test_mostra_comissao_em_reais_e_percentual(self):
        texto = renderizar_post([item()])
        self.assertIn("sua comissao: R$ 9,96", texto)
        self.assertIn("(4.0%)", texto)

    def test_desconto_aparece_quando_existe(self):
        self.assertIn("(30% OFF)", renderizar_post([item()]))

    def test_sem_desconto_nao_mostra_off(self):
        self.assertNotIn("OFF", renderizar_post([item(desconto_pct=0)]))

    def test_vendas_e_nota_opcionais(self):
        texto = renderizar_post([item(vendas=None, rating=None)])
        self.assertNotIn("vendidos", texto)
        self.assertNotIn("nota", texto)

    def test_numera_em_sequencia(self):
        texto = renderizar_post([item(nome="A"), item(nome="B")])
        self.assertIn("1. A", texto)
        self.assertIn("2. B", texto)

    def test_lista_vazia(self):
        self.assertEqual(renderizar_post([]), "(nenhum resultado)")


class TestCacheDeLinks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "teste.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def salvar(self, url, curto, sub_ids=""):
        with db.conectar(self.caminho) as conexao:
            db.salvar_link(conexao, "shopee", {
                "url_origem": url, "link_curto": curto,
                "sub_ids": sub_ids, "gerado_em": "2026-09-03T00:00:00+00:00",
            })

    def buscar(self, sub_ids=""):
        with db.conectar(self.caminho) as conexao:
            return db.buscar_links(conexao, "shopee", sub_ids)

    def test_guarda_e_recupera(self):
        self.salvar("https://shopee.com.br/p/1", "https://s.shopee.com.br/aaa")
        self.assertEqual(
            self.buscar()["https://shopee.com.br/p/1"], "https://s.shopee.com.br/aaa"
        )

    def test_sub_ids_diferentes_geram_entradas_separadas(self):
        """Cada campanha precisa do proprio link para o tracking funcionar."""
        self.salvar("https://shopee.com.br/p/1", "https://s/aaa", "tiktok")
        self.salvar("https://shopee.com.br/p/1", "https://s/bbb", "instagram")
        self.assertEqual(self.buscar("tiktok")["https://shopee.com.br/p/1"], "https://s/aaa")
        self.assertEqual(self.buscar("instagram")["https://shopee.com.br/p/1"], "https://s/bbb")

    def test_regravar_atualiza_o_link(self):
        self.salvar("https://shopee.com.br/p/1", "https://s/antigo")
        self.salvar("https://shopee.com.br/p/1", "https://s/novo")
        self.assertEqual(len(self.buscar()), 1)
        self.assertEqual(self.buscar()["https://shopee.com.br/p/1"], "https://s/novo")

    def test_cache_vazio(self):
        self.assertEqual(self.buscar(), {})


if __name__ == "__main__":
    unittest.main()
