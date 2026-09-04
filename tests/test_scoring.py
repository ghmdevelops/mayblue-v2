from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02.config import FiltrosConfig, ScoringConfig
from flow02.models import Oferta
from flow02.scoring import avaliar, estimar_cvr, score_historico
from flow02.sources.base import aplicar_filtros, deduplicar

CFG = ScoringConfig()


def oferta(**kwargs) -> Oferta:
    base = dict(
        plataforma="shopee",
        item_id="1",
        nome="produto",
        preco=100.0,
        taxa_comissao=0.10,
        origem_consulta="teste",
        vendas=1000,
        rating=4.5,
        desconto_pct=20.0,
        link_oferta="https://s.shopee.com.br/x",
    )
    base.update(kwargs)
    return Oferta(**base)


class TestComissao(unittest.TestCase):
    def test_comissao_valor(self):
        self.assertAlmostEqual(oferta(preco=250.0, taxa_comissao=0.08).comissao_valor, 20.0)

    def test_chave_inclui_plataforma(self):
        self.assertEqual(oferta(item_id="99").chave, "shopee:99")


class TestCvr(unittest.TestCase):
    def test_mais_vendas_aumenta_cvr(self):
        baixo, _ = estimar_cvr(oferta(vendas=50), CFG)
        alto, _ = estimar_cvr(oferta(vendas=50000), CFG)
        self.assertGreater(alto, baixo)

    def test_rating_melhor_aumenta_cvr(self):
        ruim, _ = estimar_cvr(oferta(rating=3.0), CFG)
        bom, _ = estimar_cvr(oferta(rating=5.0), CFG)
        self.assertGreater(bom, ruim)

    def test_desconto_maior_aumenta_cvr(self):
        sem, _ = estimar_cvr(oferta(desconto_pct=0), CFG)
        com, _ = estimar_cvr(oferta(desconto_pct=70), CFG)
        self.assertGreater(com, sem)

    def test_preco_alto_reduz_cvr(self):
        barato, _ = estimar_cvr(oferta(preco=50.0), CFG)
        caro, _ = estimar_cvr(oferta(preco=2000.0), CFG)
        self.assertGreater(barato, caro)

    def test_abaixo_do_preco_referencia_nao_penaliza(self):
        _, comp_a = estimar_cvr(oferta(preco=10.0), CFG)
        _, comp_b = estimar_cvr(oferta(preco=CFG.preco_referencia), CFG)
        self.assertEqual(comp_a["atrito_preco"], 1.0)
        self.assertEqual(comp_b["atrito_preco"], 1.0)

    def test_sem_vendas_usa_piso(self):
        _, comp = estimar_cvr(oferta(vendas=0), CFG)
        self.assertEqual(comp["popularidade"], CFG.popularidade_min)

    def test_sem_rating_usa_neutro(self):
        _, comp = estimar_cvr(oferta(rating=None), CFG)
        self.assertEqual(comp["confianca"], CFG.confianca_sem_rating)

    def test_cvr_respeita_teto(self):
        agressiva = ScoringConfig(cvr_base=5.0, cvr_maximo=0.3)
        cvr, _ = estimar_cvr(oferta(vendas=999999, desconto_pct=90), agressiva)
        self.assertLessEqual(cvr, 0.3)


class TestAvaliacao(unittest.TestCase):
    def test_score_e_comissao_vezes_cvr(self):
        item = oferta()
        avaliacao = avaliar(item, CFG)
        self.assertAlmostEqual(
            avaliacao.score, item.comissao_valor * avaliacao.cvr_estimado, places=5
        )

    def test_cvr_calibrado_tem_precedencia(self):
        avaliacao = avaliar(oferta(), CFG, cvr_calibrado=0.05)
        self.assertTrue(avaliacao.calibrado)
        self.assertAlmostEqual(avaliacao.cvr_estimado, 0.05)

    def test_sem_calibracao_marca_como_estimado(self):
        self.assertFalse(avaliar(oferta(), CFG).calibrado)

    def test_comissao_zero_zera_score(self):
        self.assertEqual(avaliar(oferta(taxa_comissao=0.0), CFG).score, 0.0)

    def test_componentes_sao_expostos_para_auditoria(self):
        componentes = avaliar(oferta(), CFG).componentes
        for chave in ("popularidade", "confianca", "urgencia", "atrito_preco",
                      "comissao_valor"):
            self.assertIn(chave, componentes)

    def test_comissao_alta_supera_comissao_baixa_no_mesmo_preco(self):
        a = avaliar(oferta(taxa_comissao=0.02), CFG).score
        b = avaliar(oferta(taxa_comissao=0.15), CFG).score
        self.assertGreater(b, a)


class TestScoreHistorico(unittest.TestCase):
    def test_persistencia_aumenta_score(self):
        raro = score_historico(1.0, 1, 10, 0)
        constante = score_historico(1.0, 10, 10, 0)
        self.assertGreater(constante, raro)

    def test_oferta_antiga_decai(self):
        recente = score_historico(1.0, 5, 10, 0)
        antiga = score_historico(1.0, 5, 10, 30)
        self.assertLess(antiga, recente)

    def test_meia_vida_reduz_pela_metade(self):
        hoje = score_historico(1.0, 10, 10, 0, meia_vida_dias=7)
        em_sete_dias = score_historico(1.0, 10, 10, 7, meia_vida_dias=7)
        self.assertAlmostEqual(em_sete_dias, hoje / 2, places=4)

    def test_dias_totais_zero_nao_divide_por_zero(self):
        self.assertGreaterEqual(score_historico(1.0, 0, 0, 0), 0.0)


class TestFiltros(unittest.TestCase):
    def test_descarta_comissao_baixa(self):
        filtros = FiltrosConfig(taxa_comissao_min=0.05)
        self.assertEqual(aplicar_filtros([oferta(taxa_comissao=0.01)], filtros), [])

    def test_descarta_fora_da_faixa_de_preco(self):
        filtros = FiltrosConfig(preco_min=50.0, preco_max=500.0)
        aprovadas = aplicar_filtros(
            [oferta(preco=10.0), oferta(preco=100.0), oferta(preco=900.0)], filtros
        )
        self.assertEqual([o.preco for o in aprovadas], [100.0])

    def test_descarta_poucas_vendas(self):
        filtros = FiltrosConfig(vendas_min=100)
        self.assertEqual(aplicar_filtros([oferta(vendas=10)], filtros), [])

    def test_rating_zero_nao_e_tratado_como_nota_ruim(self):
        """rating 0 = sem avaliacao; nao deve ser confundido com nota pessima."""
        filtros = FiltrosConfig(rating_min=4.0)
        self.assertEqual(len(aplicar_filtros([oferta(rating=0.0)], filtros)), 1)
        self.assertEqual(aplicar_filtros([oferta(rating=3.0)], filtros), [])

    def test_exige_link_de_oferta(self):
        filtros = FiltrosConfig(exigir_link_oferta=True)
        self.assertEqual(aplicar_filtros([oferta(link_oferta=None)], filtros), [])

    def test_filtro_de_link_desligado(self):
        filtros = FiltrosConfig(exigir_link_oferta=False)
        self.assertEqual(len(aplicar_filtros([oferta(link_oferta=None)], filtros)), 1)


class TestDeduplicacao(unittest.TestCase):
    def test_mantem_a_de_maior_comissao(self):
        resultado = deduplicar([
            oferta(item_id="1", taxa_comissao=0.05, origem_consulta="a"),
            oferta(item_id="1", taxa_comissao=0.20, origem_consulta="b"),
            oferta(item_id="2", taxa_comissao=0.10),
        ])
        por_id = {o.item_id: o for o in resultado}
        self.assertEqual(len(resultado), 2)
        self.assertAlmostEqual(por_id["1"].taxa_comissao, 0.20)
        self.assertEqual(por_id["1"].origem_consulta, "b")

    def test_plataformas_diferentes_nao_colidem(self):
        resultado = deduplicar([
            oferta(item_id="1", plataforma="shopee"),
            oferta(item_id="1", plataforma="mercadolivre"),
        ])
        self.assertEqual(len(resultado), 2)


if __name__ == "__main__":
    unittest.main()
