from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import analise, db, rastreio
from flow02.config import PrecoConfig


class TestCodigoCurto(unittest.TestCase):
    def test_deterministico(self):
        """O codigo nao pode mudar: links ja publicados parariam de funcionar."""
        a = rastreio.codigo_curto("shopee", "123", "tiktok")
        b = rastreio.codigo_curto("shopee", "123", "tiktok")
        self.assertEqual(a, b)

    def test_canal_muda_o_codigo(self):
        self.assertNotEqual(
            rastreio.codigo_curto("shopee", "123", "tiktok"),
            rastreio.codigo_curto("shopee", "123", "instagram"),
        )

    def test_item_muda_o_codigo(self):
        self.assertNotEqual(
            rastreio.codigo_curto("shopee", "123"),
            rastreio.codigo_curto("shopee", "124"),
        )

    def test_tamanho_fixo(self):
        self.assertEqual(len(rastreio.codigo_curto("shopee", "1")), rastreio.TAMANHO)

    def test_sem_caracteres_ambiguos(self):
        """0/O e 1/l confundem quando alguem digita o link a mao."""
        for i in range(200):
            codigo = rastreio.codigo_curto("shopee", str(i))
            self.assertNotIn("0", codigo)
            self.assertNotIn("1", codigo)
            self.assertNotIn("l", codigo)
            self.assertNotIn("o", codigo)

    def test_bate_com_o_regex_da_function(self):
        import re
        padrao = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
        self.assertRegex(rastreio.codigo_curto("shopee", "abc"), padrao)

    def test_url_rastreada(self):
        self.assertEqual(
            rastreio.url_rastreada("https://meusite.netlify.app/", "abc123"),
            "https://meusite.netlify.app/r/abc123",
        )


class TestPosicaoPreco(unittest.TestCase):
    CFG = PrecoConfig(dias_minimos=5)

    def _hist(self, precos):
        return [(f"2026-09-{i + 1:02d}", p) for i, p in enumerate(precos)]

    def test_historico_curto_nao_avalia(self):
        pos = analise.avaliar_posicao_preco(90.0, self._hist([90, 95]), 5)
        self.assertFalse(pos.confiavel)
        self.assertFalse(pos.e_minimo)

    def test_detecta_menor_preco(self):
        pos = analise.avaliar_posicao_preco(74.0, self._hist([100, 95, 90, 80, 74]), 5)
        self.assertTrue(pos.e_minimo)
        self.assertAlmostEqual(pos.minimo, 74.0)

    def test_nao_e_minimo_quando_ja_esteve_menor(self):
        pos = analise.avaliar_posicao_preco(90.0, self._hist([100, 74, 95, 90, 90]), 5)
        self.assertFalse(pos.e_minimo)
        self.assertAlmostEqual(pos.minimo, 74.0)
        self.assertEqual(pos.dia_minimo, "2026-09-02")

    def test_tolera_meio_por_cento_de_diferenca(self):
        """Centavos de arredondamento nao devem tirar o selo."""
        pos = analise.avaliar_posicao_preco(74.2, self._hist([100, 74, 95, 90, 74.2]), 5)
        self.assertTrue(pos.e_minimo)

    def test_calcula_quanto_esta_acima(self):
        pos = analise.avaliar_posicao_preco(100.0, self._hist([50, 60, 70, 80, 100]), 5)
        self.assertAlmostEqual(pos.acima_do_minimo_pct, 100.0)

    def test_ignora_precos_invalidos(self):
        pos = analise.avaliar_posicao_preco(90.0, self._hist([0, 90, 95, 92, 91, 90]), 5)
        self.assertEqual(pos.dias_observados, 5)


class BasePrecoAlvo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()


class TestPrecoAlvo(BasePrecoAlvo):
    def test_define_e_lista(self):
        with db.conectar(self.caminho) as conexao:
            db.definir_alvo(conexao, "shopee", "123", 70.0, "Fone")
            linhas = db.alvos(conexao)
        self.assertEqual(len(linhas), 1)
        self.assertAlmostEqual(linhas[0]["alvo"], 70.0)
        self.assertEqual(linhas[0]["nome"], "Fone")

    def test_redefinir_atualiza_e_limpa_disparo(self):
        with db.conectar(self.caminho) as conexao:
            db.definir_alvo(conexao, "shopee", "123", 70.0)
            db.marcar_alvo_disparado(conexao, "shopee", "123")
            db.definir_alvo(conexao, "shopee", "123", 60.0)
            linha = db.alvos(conexao)[0]
        self.assertAlmostEqual(linha["alvo"], 60.0)
        self.assertIsNone(linha["disparado_em"])

    def test_remover(self):
        with db.conectar(self.caminho) as conexao:
            db.definir_alvo(conexao, "shopee", "123", 70.0)
            self.assertTrue(db.remover_alvo(conexao, "shopee", "123"))
            self.assertEqual(db.alvos(conexao), [])

    def test_remover_inexistente(self):
        with db.conectar(self.caminho) as conexao:
            self.assertFalse(db.remover_alvo(conexao, "shopee", "999"))


class TestCliques(BasePrecoAlvo):
    def _gravar(self, registros):
        with db.conectar(self.caminho) as conexao:
            return db.salvar_cliques(conexao, registros)

    def test_grava_e_soma_por_item(self):
        self._gravar([
            {"plataforma": "shopee", "codigo": "a", "dia": "2026-09-01",
             "item_id": "1", "canal": "tiktok", "total": 10},
            {"plataforma": "shopee", "codigo": "b", "dia": "2026-09-02",
             "item_id": "1", "canal": "instagram", "total": 5},
        ])
        with db.conectar(self.caminho) as conexao:
            por_item = db.cliques_por_item(conexao)
        self.assertEqual(por_item[("shopee", "1")], 15)

    def test_reimportar_substitui_em_vez_de_somar(self):
        """O contador do Firebase e cumulativo; somar duplicaria."""
        registro = {"plataforma": "shopee", "codigo": "a", "dia": "2026-09-01",
                    "item_id": "1", "canal": "tiktok", "total": 10}
        self._gravar([registro])
        self._gravar([{**registro, "total": 14}])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.cliques_por_item(conexao)[("shopee", "1")], 14)

    def test_agrupa_por_canal(self):
        self._gravar([
            {"plataforma": "shopee", "codigo": "a", "dia": "2026-09-01",
             "item_id": "1", "canal": "tiktok", "total": 10},
            {"plataforma": "shopee", "codigo": "b", "dia": "2026-09-01",
             "item_id": "2", "canal": "tiktok", "total": 7},
            {"plataforma": "shopee", "codigo": "c", "dia": "2026-09-01",
             "item_id": "3", "canal": "instagram", "total": 4},
        ])
        with db.conectar(self.caminho) as conexao:
            canais = {l["canal"]: l["cliques"] for l in db.cliques_por_canal(conexao)}
        self.assertEqual(canais["tiktok"], 17)
        self.assertEqual(canais["instagram"], 4)

    def test_lista_vazia(self):
        self.assertEqual(self._gravar([]), 0)


if __name__ == "__main__":
    unittest.main()
