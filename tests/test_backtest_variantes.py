"""Backtest do score e agrupamento de variantes.

O backtest responde a pergunta que o projeto vinha evitando: o EPC previsto
tem relacao com o que de fato rendeu? Sem isso, o ranking e hipotese, nao
fato demonstrado.

O agrupamento veio de um caso concreto: na lista de oportunidades de
2026-09-04, quatro vagas eram o mesmo produto Celimax em versoes diferentes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import backtest, db, variantes
from flow02.config import ScoringConfig
from flow02.models import Conversao, Oferta
from flow02.scoring import avaliar as pontuar


class TestSpearman(unittest.TestCase):
    def test_ordem_identica_da_um(self):
        self.assertAlmostEqual(backtest.spearman([1, 2, 3], [10, 20, 30]), 1.0)

    def test_ordem_invertida_da_menos_um(self):
        self.assertAlmostEqual(backtest.spearman([1, 2, 3], [30, 20, 10]), -1.0)

    def test_so_olha_ordem_nao_escala(self):
        """Spearman e sobre postos: dobrar os valores nao muda nada."""
        self.assertAlmostEqual(
            backtest.spearman([1, 2, 3], [10, 20, 30]),
            backtest.spearman([1, 2, 3], [100, 2000, 30000]),
        )

    def test_lado_sem_variacao_nao_tem_correlacao(self):
        self.assertIsNone(backtest.spearman([1, 1, 1], [1, 2, 3]))

    def test_amostra_de_um_nao_da_para_correlacionar(self):
        self.assertIsNone(backtest.spearman([1], [1]))

    def test_empates_usam_posto_medio(self):
        self.assertIsNotNone(backtest.spearman([1, 1, 2, 3], [1, 2, 3, 4]))

    def test_aguenta_valor_extremo(self):
        """Um item que rende 100x nao pode dominar o resultado."""
        xs = list(range(10))
        ys = [1] * 9 + [10_000]
        self.assertLess(abs(backtest.spearman(xs, ys)), 1.0)


class BaseBacktest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.caminho = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def semear(self, casos):
        """casos: (item_id, epc_desejado, comissao_realizada)"""
        with db.conectar(self.caminho) as conexao:
            for item_id, epc, comissao in casos:
                # O score sai do scoring; para controlar o EPC, grava direto.
                oferta = Oferta(
                    plataforma="shopee", item_id=item_id, nome=f"p{item_id}",
                    preco=100.0, taxa_comissao=0.1, origem_consulta="t",
                    vendas=100, rating=4.5, loja_id="l", loja_nome="L",
                )
                db.salvar_ofertas(
                    conexao, [(oferta, pontuar(oferta, ScoringConfig()))],
                    "2026-09-04")
                conexao.execute(
                    "UPDATE oferta_dia SET score = ? WHERE item_id = ?",
                    (epc, item_id))
                if comissao > 0:
                    db.salvar_conversoes(conexao, [Conversao(
                        plataforma="shopee", conversao_id=f"c{item_id}",
                        cliques=0, pedidos=1, comissao=comissao,
                        item_id=item_id, ocorrido_em="2026-08-01",
                    )])

    def avaliar(self):
        with db.conectar(self.caminho) as conexao:
            return backtest.avaliar(conexao)


class TestBacktest(BaseBacktest):
    def test_sem_dados(self):
        resultado = self.avaliar()
        self.assertEqual(resultado.pares, 0)
        self.assertFalse(resultado.suficiente)
        self.assertEqual(resultado.veredito, "amostra insuficiente")

    def test_amostra_pequena_avisa(self):
        self.semear([(str(i), i / 10, i) for i in range(1, 4)])
        self.assertFalse(self.avaliar().suficiente)

    def test_previsao_perfeita(self):
        self.semear([(str(i), i / 10, i * 10.0) for i in range(1, 13)])
        resultado = self.avaliar()
        self.assertAlmostEqual(resultado.correlacao, 1.0)
        self.assertEqual(resultado.veredito, "o ranking preve bem")

    def test_previsao_invertida_e_denunciada(self):
        """Se o EPC alto rende menos, ha erro de sinal em algum peso."""
        self.semear([(str(i), i / 10, (13 - i) * 10.0) for i in range(1, 13)])
        resultado = self.avaliar()
        self.assertLess(resultado.correlacao, -0.5)
        self.assertIn("CONTRARIO", resultado.veredito)

    def test_previsao_sem_relacao_e_denunciada(self):
        """Zigue-zague: cada EPC alto alterna com ganho alto e baixo, o que
        deixa a correlacao de postos perto de zero."""
        ganhos = [1.0, 10.0, 2.0, 9.0, 3.0, 8.0, 4.0, 7.0, 5.0, 6.0]
        self.semear([(str(i), i / 10, ganhos[i - 1]) for i in range(1, 11)])
        resultado = self.avaliar()
        self.assertLess(abs(resultado.correlacao), 0.2)
        self.assertIn("NAO preve", resultado.veredito)

    def test_compara_topo_com_o_resto(self):
        self.semear([(str(i), i / 10, i * 10.0) for i in range(1, 13)])
        resultado = self.avaliar()
        self.assertGreater(resultado.comissao_topo, resultado.comissao_resto)

    def test_so_conta_produto_que_vendeu(self):
        """Produto sem venda nao diz nada sobre acerto da previsao."""
        self.semear([("1", 0.9, 50.0), ("2", 0.8, 0.0)])
        self.assertEqual(self.avaliar().pares, 1)

    def test_amostra_media_nao_e_confiavel(self):
        self.semear([(str(i), i / 10, i * 1.0) for i in range(1, 13)])
        resultado = self.avaliar()
        self.assertTrue(resultado.suficiente)
        self.assertFalse(resultado.confiavel)


class TestAssinaturaDeProduto(unittest.TestCase):
    def test_ignora_caixa_e_acento(self):
        self.assertEqual(
            variantes.assinatura("CELIMAX Retinal Sérum Facial"),
            variantes.assinatura("celimax retinal serum facial"),
        )

    def test_ignora_volume_e_quantidade(self):
        """15ml e 30ml sao o mesmo produto em tamanhos diferentes."""
        self.assertEqual(
            variantes.assinatura("Celimax Vita Retinal Booster 15ml"),
            variantes.assinatura("Celimax Vita Retinal Booster 30 ml"),
        )

    def test_ignora_palavra_de_marketing(self):
        self.assertEqual(
            variantes.assinatura("Kit Original Celimax Vita Retinal Booster"),
            variantes.assinatura("Celimax Vita Retinal Booster Premium"),
        )

    def test_produtos_diferentes_nao_colidem(self):
        self.assertNotEqual(
            variantes.assinatura("Celimax Vita Retinal Booster"),
            variantes.assinatura("Numbuzin Volumetox Eye Cream"),
        )

    def test_nome_vazio(self):
        self.assertEqual(variantes.assinatura(""), "")


class TestAgrupamento(unittest.TestCase):
    def itens(self):
        return [
            {"nome": "Celimax The Vita A Retinal Shot Booster 15ml", "epc": 1.0},
            {"nome": "CELIMAX the vita A Retinal Shot Booster, 30 ml", "epc": 1.5},
            {"nome": "Numbuzin No.9 Volumetox Eye Cream", "epc": 0.7},
        ]

    def test_junta_as_variantes(self):
        resultado, escondidas = variantes.agrupar(self.itens())
        self.assertEqual(len(resultado), 2)
        self.assertEqual(escondidas, 1)

    def test_mantem_o_de_melhor_epc(self):
        resultado, _ = variantes.agrupar(self.itens())
        self.assertAlmostEqual(resultado[0]["epc"], 1.5)

    def test_registra_quantos_representa(self):
        resultado, _ = variantes.agrupar(self.itens())
        self.assertEqual(resultado[0]["variantes"], 2)
        self.assertEqual(resultado[1]["variantes"], 1)

    def test_preserva_a_ordem_de_entrada(self):
        """O grupo mantem a posicao do primeiro que apareceu, mesmo que o
        vencedor seja outro anuncio do grupo."""
        resultado, _ = variantes.agrupar(self.itens())
        self.assertIn("celimax", resultado[0]["nome"].lower())
        self.assertIn("numbuzin", resultado[1]["nome"].lower())

    def test_nome_curto_nao_e_agrupado_com_outro(self):
        """Agrupar demais esconderia produto legitimo -- pior que repetir."""
        resultado, _ = variantes.agrupar(
            [{"nome": "AB", "epc": 1.0}, {"nome": "XY", "epc": 2.0}])
        self.assertEqual(len(resultado), 2)

    def test_lista_vazia(self):
        self.assertEqual(variantes.agrupar([]), ([], 0))


if __name__ == "__main__":
    unittest.main()
