from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.config import Config, ScoringConfig
from flow02.models import Oferta
from flow02.scoring import avaliar
from flow02.web import api


def oferta(item_id="1", **kwargs) -> Oferta:
    base = dict(
        plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
        preco=100.0, taxa_comissao=0.10, origem_consulta="t",
        vendas=500, rating=4.5, loja_id="loja-a", loja_nome="Loja A",
        imagem="https://cdn/x.jpg", link_oferta=f"https://s/{item_id}",
    )
    base.update(kwargs)
    return Oferta(**base)


class BaseVitrine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"
        self.cfg = Config(caminho_banco=self.caminho)
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(conexao, [
                (oferta(str(i)), avaliar(oferta(str(i)), ScoringConfig()))
                for i in range(1, 4)
            ], "2026-09-04")

    def tearDown(self):
        self._tmp.cleanup()

    def adicionar(self, item_id, colecao=db.COLECAO_PADRAO):
        with db.conectar(self.caminho) as conexao:
            db.adicionar_vitrine(conexao, "shopee", item_id, colecao)

    def listar(self, colecao=None):
        with db.conectar(self.caminho) as conexao:
            return db.listar_vitrine(conexao, colecao)


class TestCuradoria(BaseVitrine):
    def test_adiciona_e_lista(self):
        self.adicionar("1")
        linhas = self.listar()
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["item_id"], "1")
        self.assertEqual(linhas[0]["nome"], "produto 1")

    def test_preserva_a_ordem_de_insercao(self):
        """A curadoria e uma escolha ordenada, nao um conjunto."""
        for item in ("3", "1", "2"):
            self.adicionar(item)
        self.assertEqual([l["item_id"] for l in self.listar()], ["3", "1", "2"])

    def test_adicionar_duas_vezes_nao_duplica(self):
        self.adicionar("1")
        self.adicionar("1")
        self.assertEqual(len(self.listar()), 1)

    def test_colecoes_separadas(self):
        self.adicionar("1", "casa")
        self.adicionar("2", "moda")
        self.assertEqual(len(self.listar("casa")), 1)
        self.assertEqual(len(self.listar()), 2)

    def test_mesmo_item_em_duas_colecoes(self):
        self.adicionar("1", "casa")
        self.adicionar("1", "presentes")
        self.assertEqual(len(self.listar()), 2)

    def test_remover_de_uma_colecao(self):
        self.adicionar("1", "casa")
        self.adicionar("1", "presentes")
        with db.conectar(self.caminho) as conexao:
            db.remover_vitrine(conexao, "shopee", "1", "casa")
        self.assertEqual([l["colecao"] for l in self.listar()], ["presentes"])

    def test_remover_de_todas(self):
        self.adicionar("1", "casa")
        self.adicionar("1", "presentes")
        with db.conectar(self.caminho) as conexao:
            db.remover_vitrine(conexao, "shopee", "1")
        self.assertEqual(self.listar(), [])

    def test_remover_inexistente(self):
        with db.conectar(self.caminho) as conexao:
            self.assertFalse(db.remover_vitrine(conexao, "shopee", "999"))

    def test_traz_o_snapshot_mais_recente(self):
        self.adicionar("1")
        with db.conectar(self.caminho) as conexao:
            nova = oferta("1", preco=55.0)
            db.salvar_ofertas(conexao, [(nova, avaliar(nova, ScoringConfig()))],
                              "2026-09-05")
        self.assertAlmostEqual(self.listar()[0]["preco"], 55.0)

    def test_item_sem_coleta_aparece_sem_dados(self):
        """Curar um item que sumiu do catalogo nao pode quebrar a listagem."""
        self.adicionar("inexistente")
        linha = self.listar()[0]
        self.assertEqual(linha["item_id"], "inexistente")
        self.assertIsNone(linha["nome"])

    def test_contagem_por_colecao(self):
        self.adicionar("1", "casa")
        self.adicionar("2", "casa")
        self.adicionar("3", "moda")
        with db.conectar(self.caminho) as conexao:
            grupos = {g["colecao"]: g["itens"] for g in db.colecoes(conexao)}
        self.assertEqual(grupos, {"casa": 2, "moda": 1})

    def test_conjunto_de_curados(self):
        self.adicionar("1")
        self.adicionar("2")
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.esta_na_vitrine(conexao),
                             {("shopee", "1"), ("shopee", "2")})


class TestApiVitrine(BaseVitrine):
    def test_adiciona_pelo_painel(self):
        resultado = api.alternar_vitrine(self.cfg, {"item_id": "1"})
        self.assertTrue(resultado["na_vitrine"])
        self.assertEqual(resultado["total"], 1)

    def test_remove_pelo_painel(self):
        api.alternar_vitrine(self.cfg, {"item_id": "1"})
        resultado = api.alternar_vitrine(self.cfg, {"item_id": "1", "remover": True})
        self.assertFalse(resultado["na_vitrine"])
        self.assertEqual(resultado["total"], 0)

    def test_item_id_obrigatorio(self):
        with self.assertRaises(ValueError):
            api.alternar_vitrine(self.cfg, {})

    def test_plataforma_invalida(self):
        with self.assertRaises(ValueError):
            api.alternar_vitrine(self.cfg, {"item_id": "1", "plataforma": "amazon"})

    def test_colecao_longa_e_truncada(self):
        api.alternar_vitrine(self.cfg, {"item_id": "1", "colecao": "x" * 200})
        self.assertLessEqual(len(self.listar()[0]["colecao"]), 40)

    def test_listagem_pela_api(self):
        api.alternar_vitrine(self.cfg, {"item_id": "1", "colecao": "casa"})
        dados = api.vitrine(self.cfg, {})
        self.assertEqual(len(dados["itens"]), 1)
        self.assertEqual(dados["colecoes"][0]["colecao"], "casa")

    def test_marca_item_que_nunca_apareceu_como_indisponivel(self):
        """Publicar link que leva a 'produto indisponivel' queima confianca."""
        api.alternar_vitrine(self.cfg, {"item_id": "fantasma"})
        dados = api.vitrine(self.cfg, {})
        self.assertTrue(dados["itens"][0]["indisponivel"])
        self.assertEqual(dados["indisponiveis"], 1)

    def test_item_da_coleta_de_hoje_esta_disponivel(self):
        api.alternar_vitrine(self.cfg, {"item_id": "1"})
        dados = api.vitrine(self.cfg, {})
        self.assertFalse(dados["itens"][0]["indisponivel"])
        self.assertEqual(dados["indisponiveis"], 0)

    def test_indisponivel_nao_vai_para_o_firebase(self):
        from flow02.saidas import firebase

        enviados = {}

        class ClienteFake:
            def escrever(self, caminho, dados):
                enviados[caminho] = dados

        api.alternar_vitrine(self.cfg, {"item_id": "1"})
        api.alternar_vitrine(self.cfg, {"item_id": "fantasma"})
        with db.conectar(self.caminho) as conexao:
            linhas = db.listar_vitrine(conexao)

        total = firebase.sincronizar_vitrine(
            ClienteFake(), "flow02", linhas, {}, {("shopee", "fantasma")}
        )
        self.assertEqual(total, 1)
        self.assertNotIn("fantasma", enviados["flow02/vitrine"]["principal"])

    def test_ranking_marca_o_que_esta_curado(self):
        api.alternar_vitrine(self.cfg, {"item_id": "2"})
        itens = {i["item_id"]: i for i in api.top(self.cfg, {"dia": "2026-09-04"})["itens"]}
        self.assertTrue(itens["2"]["na_vitrine"])
        self.assertFalse(itens["1"]["na_vitrine"])


if __name__ == "__main__":
    unittest.main()
