"""Velocidade de venda: quanto cada produto vendeu desde a coleta anterior.

Existe porque a API de afiliados da Shopee nao expoe estoque -- confirmado por
introspecao do schema, os 25 campos de productOfferV2 nao tem nada de
inventario. O movimento entre dois retratos e a informacao mais proxima
disso que da para obter sem violar os termos de uso.
"""

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


def oferta(item_id="1", vendas=100) -> Oferta:
    return Oferta(
        plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
        preco=100.0, taxa_comissao=0.10, origem_consulta="t",
        vendas=vendas, rating=4.5, loja_id="loja-a", loja_nome="Loja A",
        link_oferta=f"https://s/{item_id}",
    )


class BaseVelocidade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"
        self.cfg = Config(caminho_banco=self.caminho)

    def tearDown(self):
        self._tmp.cleanup()

    def gravar(self, dia, *pares):
        """pares: (item_id, vendas)"""
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(conexao, [
                (oferta(i, v), avaliar(oferta(i, v), ScoringConfig()))
                for i, v in pares
            ], dia)

    def medir(self, dia):
        with db.conectar(self.caminho) as conexao:
            return db.velocidade_vendas(conexao, dia)


class TestVelocidade(BaseVelocidade):
    def test_um_dia_so_nao_tem_com_o_que_comparar(self):
        self.gravar("2026-09-04", ("1", 100))
        self.assertEqual(self.medir("2026-09-04"), {})

    def test_calcula_o_movimento_entre_dois_dias(self):
        self.gravar("2026-09-03", ("1", 100))
        self.gravar("2026-09-04", ("1", 340))
        medida = self.medir("2026-09-04")[("shopee", "1")]
        self.assertEqual(medida["delta"], 240)
        self.assertEqual(medida["dias"], 1)
        self.assertEqual(medida["por_dia"], 240.0)

    def test_divide_pelo_intervalo_quando_ha_buraco(self):
        """Coleta falhou por dias? A media por dia continua comparavel."""
        self.gravar("2026-09-01", ("1", 100))
        self.gravar("2026-09-05", ("1", 500))
        medida = self.medir("2026-09-05")[("shopee", "1")]
        self.assertEqual(medida["delta"], 400)
        self.assertEqual(medida["dias"], 4)
        self.assertEqual(medida["por_dia"], 100.0)

    def test_compara_com_a_coleta_mais_recente_anterior(self):
        self.gravar("2026-09-01", ("1", 100))
        self.gravar("2026-09-03", ("1", 200))
        self.gravar("2026-09-04", ("1", 260))
        medida = self.medir("2026-09-04")[("shopee", "1")]
        self.assertEqual(medida["delta"], 60)
        self.assertEqual(medida["desde"], "2026-09-03")

    def test_produto_parado_tem_delta_zero(self):
        self.gravar("2026-09-03", ("1", 100))
        self.gravar("2026-09-04", ("1", 100))
        self.assertEqual(self.medir("2026-09-04")[("shopee", "1")]["delta"], 0)

    def test_produto_novo_nao_aparece(self):
        """Sem retrato anterior nao existe velocidade -- e nao pode inventar."""
        self.gravar("2026-09-03", ("1", 100))
        self.gravar("2026-09-04", ("1", 150), ("2", 999))
        medida = self.medir("2026-09-04")
        self.assertIn(("shopee", "1"), medida)
        self.assertNotIn(("shopee", "2"), medida)

    def test_varios_produtos_de_uma_vez(self):
        self.gravar("2026-09-03", ("1", 100), ("2", 50))
        self.gravar("2026-09-04", ("1", 140), ("2", 51))
        medida = self.medir("2026-09-04")
        self.assertEqual(medida[("shopee", "1")]["delta"], 40)
        self.assertEqual(medida[("shopee", "2")]["delta"], 1)

    def test_contagem_que_regride_nao_quebra(self):
        """A Shopee as vezes corrige o total para baixo."""
        self.gravar("2026-09-03", ("1", 500))
        self.gravar("2026-09-04", ("1", 480))
        self.assertEqual(self.medir("2026-09-04")[("shopee", "1")]["delta"], -20)


class TestVelocidadeNaApi(BaseVelocidade):
    def test_ranking_entrega_a_velocidade(self):
        self.gravar("2026-09-03", ("1", 100))
        self.gravar("2026-09-04", ("1", 340))
        item = api.top(self.cfg, {"dia": "2026-09-04"})["itens"][0]
        self.assertEqual(item["velocidade"]["delta"], 240)

    def test_campo_vem_nulo_sem_historico(self):
        self.gravar("2026-09-04", ("1", 100))
        item = api.top(self.cfg, {"dia": "2026-09-04"})["itens"][0]
        self.assertIsNone(item["velocidade"])

    def test_vitrine_tambem_entrega(self):
        self.gravar("2026-09-03", ("1", 100))
        self.gravar("2026-09-04", ("1", 180))
        api.alternar_vitrine(self.cfg, {"item_id": "1"})
        # A vitrine usa o dia de hoje; sem coleta de hoje nao ha comparacao,
        # entao o campo existe mas pode vir nulo -- o contrato e o que importa.
        item = api.vitrine(self.cfg, {})["itens"][0]
        self.assertIn("velocidade", item)


if __name__ == "__main__":
    unittest.main()
