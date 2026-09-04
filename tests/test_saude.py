from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import db
from flow02.config import ScoringConfig
from flow02.models import Conversao, Oferta
from flow02.scoring import avaliar


def oferta(item_id="1", **kwargs) -> Oferta:
    base = dict(
        plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
        preco=100.0, taxa_comissao=0.10, origem_consulta="t",
        vendas=500, rating=4.5, loja_id="loja-a", loja_nome="Loja A",
        link_oferta=f"https://s/{item_id}",
    )
    base.update(kwargs)
    return Oferta(**base)


def conversao(item_id, pedido_id, **kwargs) -> Conversao:
    base = dict(
        plataforma="shopee",
        conversao_id=f"{pedido_id}:{item_id}",
        cliques=0, pedidos=1, comissao=5.0,
        item_id=item_id, loja_id="loja-a",
        ocorrido_em="2026-09-04", pedido_id=pedido_id,
    )
    base.update(kwargs)
    return Conversao(**base)


class BaseSaude(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def gravar_ofertas(self, dia, *ids):
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(conexao, [
                (oferta(i), avaliar(oferta(i), ScoringConfig())) for i in ids
            ], dia)

    def curar(self, *ids):
        with db.conectar(self.caminho) as conexao:
            for item in ids:
                db.adicionar_vitrine(conexao, "shopee", item)

    def gravar_conversoes(self, conversoes):
        with db.conectar(self.caminho) as conexao:
            db.salvar_conversoes(conexao, conversoes)


class TestDisponibilidade(BaseSaude):
    def test_item_visto_hoje_tem_atraso_zero(self):
        self.gravar_ofertas("2026-09-04", "1")
        self.curar("1")
        with db.conectar(self.caminho) as conexao:
            atrasos = db.dias_sem_aparecer(conexao, "2026-09-04")
        self.assertEqual(atrasos[("shopee", "1")], 0)

    def test_item_sumido_conta_os_dias(self):
        """Item que some da API geralmente esgotou -- link morto no seu post."""
        self.gravar_ofertas("2026-09-01", "1")
        self.curar("1")
        with db.conectar(self.caminho) as conexao:
            atrasos = db.dias_sem_aparecer(conexao, "2026-09-04")
        self.assertEqual(atrasos[("shopee", "1")], 3)

    def test_item_nunca_coletado(self):
        self.curar("fantasma")
        with db.conectar(self.caminho) as conexao:
            atrasos = db.dias_sem_aparecer(conexao, "2026-09-04")
        self.assertIsNone(atrasos[("shopee", "fantasma")])

    def test_inclui_itens_com_alerta_de_preco(self):
        self.gravar_ofertas("2026-09-04", "9")
        with db.conectar(self.caminho) as conexao:
            db.definir_alvo(conexao, "shopee", "9", 50.0)
            atrasos = db.dias_sem_aparecer(conexao, "2026-09-04")
        self.assertIn(("shopee", "9"), atrasos)

    def test_sem_nada_curado(self):
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.dias_sem_aparecer(conexao, "2026-09-04"), {})


class TestParesComprados(BaseSaude):
    def test_par_unico_nao_vira_padrao(self):
        """Um par que aconteceu uma vez e coincidencia."""
        self.gravar_conversoes([conversao("1", "p1"), conversao("2", "p1")])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.pares_comprados_juntos(conexao, minimo=2), [])

    def test_par_repetido_aparece(self):
        self.gravar_conversoes([
            conversao("1", "p1"), conversao("2", "p1"),
            conversao("1", "p2"), conversao("2", "p2"),
        ])
        with db.conectar(self.caminho) as conexao:
            pares = db.pares_comprados_juntos(conexao, minimo=2)
        self.assertEqual(len(pares), 1)
        self.assertEqual(pares[0]["juntos"], 2)

    def test_nao_pareia_item_com_ele_mesmo(self):
        self.gravar_conversoes([conversao("1", "p1")])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.pares_comprados_juntos(conexao, minimo=1), [])

    def test_cada_par_aparece_uma_vez_so(self):
        """a<b evita listar (1,2) e (2,1) como coisas diferentes."""
        self.gravar_conversoes([conversao("1", "p1"), conversao("2", "p1")])
        with db.conectar(self.caminho) as conexao:
            pares = db.pares_comprados_juntos(conexao, minimo=1)
        self.assertEqual(len(pares), 1)

    def test_pedidos_diferentes_nao_pareiam(self):
        self.gravar_conversoes([conversao("1", "p1"), conversao("2", "p2")])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.pares_comprados_juntos(conexao, minimo=1), [])

    def test_resgata_pedido_do_conversao_id_antigo(self):
        """Conversoes gravadas antes de `pedido_id` existir ainda agrupam:
        o id foi montado como "{conversionId}:{itemId}"."""
        self.gravar_conversoes([
            Conversao(plataforma="shopee", conversao_id="990:1", cliques=0,
                      pedidos=1, comissao=2.0, item_id="1", pedido_id=None),
            Conversao(plataforma="shopee", conversao_id="990:2", cliques=0,
                      pedidos=1, comissao=3.0, item_id="2", pedido_id=None),
        ])
        with db.conectar(self.caminho) as conexao:
            pares = db.pares_comprados_juntos(conexao, minimo=1)
        self.assertEqual(len(pares), 1)
        self.assertEqual((pares[0]["item_a"], pares[0]["item_b"]), ("1", "2"))

    def test_conversao_id_sem_prefixo_nao_agrupa(self):
        """Sem ':' nao da para inferir o pedido -- nao pode inventar grupo."""
        self.gravar_conversoes([
            Conversao(plataforma="shopee", conversao_id="avulsa1", cliques=0,
                      pedidos=1, comissao=2.0, item_id="1"),
            Conversao(plataforma="shopee", conversao_id="avulsa2", cliques=0,
                      pedidos=1, comissao=3.0, item_id="2"),
        ])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.pares_comprados_juntos(conexao, minimo=1), [])

    def test_traz_o_nome_do_produto(self):
        self.gravar_ofertas("2026-09-04", "1", "2")
        self.gravar_conversoes([conversao("1", "p1"), conversao("2", "p1")])
        with db.conectar(self.caminho) as conexao:
            par = db.pares_comprados_juntos(conexao, minimo=1)[0]
        self.assertEqual(par["nome_a"], "produto 1")
        self.assertEqual(par["nome_b"], "produto 2")


class TestTempoAteComprar(BaseSaude):
    def test_mesmo_dia_e_zero(self):
        self.gravar_conversoes([
            conversao("1", "p1", clicado_em="2026-09-04", ocorrido_em="2026-09-04")
        ])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.tempo_ate_comprar(conexao), [0])

    def test_conta_os_dias(self):
        self.gravar_conversoes([
            conversao("1", "p1", clicado_em="2026-09-01", ocorrido_em="2026-09-04")
        ])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.tempo_ate_comprar(conexao), [3])

    def test_ignora_sem_clique(self):
        self.gravar_conversoes([conversao("1", "p1", clicado_em=None)])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.tempo_ate_comprar(conexao), [])

    def test_ignora_intervalo_absurdo(self):
        """Acima de 30 dias o dado esta errado: o cookie dura 7."""
        self.gravar_conversoes([
            conversao("1", "p1", clicado_em="2024-01-01", ocorrido_em="2026-09-04")
        ])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.tempo_ate_comprar(conexao), [])

    def test_vem_ordenado(self):
        self.gravar_conversoes([
            conversao("1", "p1", clicado_em="2026-09-01", ocorrido_em="2026-09-04"),
            conversao("2", "p2", clicado_em="2026-09-04", ocorrido_em="2026-09-04"),
            conversao("3", "p3", clicado_em="2026-09-03", ocorrido_em="2026-09-04"),
        ])
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.tempo_ate_comprar(conexao), [0, 1, 3])


if __name__ == "__main__":
    unittest.main()
