"""Registro de rodada, deteccao de coleta parcial e backup.

O motivo destes testes: em 2026-09-04 uma coleta morreu no meio, gravou so as
conversoes e passou por sucesso. So deu para perceber comparando horarios de
gravacao no banco na mao. Coleta pela metade tem que ser tratada como falha,
nao como sucesso silencioso.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02 import backup, db
from flow02.config import Config, ScoringConfig
from flow02.models import Conversao, Oferta
from flow02.scoring import avaliar
from flow02.web import api


def oferta(item_id="1") -> Oferta:
    return Oferta(
        plataforma="shopee", item_id=item_id, nome=f"produto {item_id}",
        preco=100.0, taxa_comissao=0.10, origem_consulta="t",
        vendas=10, rating=4.5, loja_id="loja-a", loja_nome="Loja A",
    )


class BaseRodada(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self._tmp.name)
        self.caminho = self.pasta / "t.sqlite3"
        self.cfg = Config(caminho_banco=self.caminho)

    def tearDown(self):
        self._tmp.cleanup()

    def rodada(self, etapas, erro=None, dia="2026-09-04"):
        with db.conectar(self.caminho) as conexao:
            identificador = db.abrir_rodada(conexao, "coletar", dia)
            return db.fechar_rodada(conexao, identificador, etapas, erro)


class TestEstadoDaRodada(BaseRodada):
    def test_todas_as_etapas_ok(self):
        self.assertEqual(
            self.rodada({"ofertas:shopee": 402, "conversoes": 53}), db.ESTADO_OK)

    def test_etapa_que_nao_terminou_vira_parcial(self):
        """Foi exatamente o caso real: conversoes salvaram, ofertas nao."""
        self.assertEqual(
            self.rodada({"ofertas:shopee": None, "conversoes": 53}),
            db.ESTADO_PARCIAL)

    def test_erro_sem_nada_gravado_e_erro(self):
        self.assertEqual(
            self.rodada({"ofertas:shopee": None}, erro="rede caiu"),
            db.ESTADO_ERRO)

    def test_erro_com_algo_gravado_e_parcial(self):
        self.assertEqual(
            self.rodada({"ofertas:shopee": 402, "conversoes": None},
                        erro="conversoes falharam"),
            db.ESTADO_PARCIAL)

    def test_zero_gravado_nao_e_falha(self):
        """Coleta que rodou e nao achou nada e diferente de coleta que morreu."""
        self.assertEqual(self.rodada({"ofertas:shopee": 0}), db.ESTADO_OK)

    def test_guarda_as_etapas(self):
        self.rodada({"ofertas:shopee": 402, "conversoes": None})
        with db.conectar(self.caminho) as conexao:
            linha = db.ultima_rodada(conexao)
        self.assertEqual(json.loads(linha["etapas"])["ofertas:shopee"], 402)

    def test_ultima_completa_ignora_as_parciais(self):
        self.rodada({"ofertas:shopee": 402}, dia="2026-09-01")
        self.rodada({"ofertas:shopee": None}, dia="2026-09-04")
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(db.ultima_rodada_completa(conexao)["dia"], "2026-09-01")
            self.assertEqual(db.ultima_rodada(conexao)["dia"], "2026-09-04")


class TestDiagnosticoNoPainel(BaseRodada):
    def diagnostico(self, hoje="2026-09-04"):
        with db.conectar(self.caminho) as conexao:
            return api._diagnostico_coleta(conexao, hoje)

    def test_sem_coleta_nenhuma(self):
        self.assertIn("nenhuma coleta registrada ainda",
                      self.diagnostico()["problemas"])

    def test_coleta_completa_de_hoje_nao_alerta(self):
        self.rodada({"ofertas:shopee": 402, "conversoes": 53})
        self.assertEqual(self.diagnostico()["problemas"], [])

    def test_coleta_parcial_alerta_dizendo_o_que_faltou(self):
        self.rodada({"ofertas:shopee": None, "conversoes": 53})
        problemas = " ".join(self.diagnostico()["problemas"])
        self.assertIn("incompleta", problemas)
        self.assertIn("ofertas:shopee", problemas)

    def test_coleta_atrasada_alerta(self):
        self.rodada({"ofertas:shopee": 402}, dia="2026-09-01")
        diagnostico = self.diagnostico("2026-09-05")
        self.assertEqual(diagnostico["dias_atraso"], 4)
        self.assertIn("4 dia(s)", " ".join(diagnostico["problemas"]))

    def test_um_dia_de_atraso_nao_alerta(self):
        """Coletar de manha e olhar a noite nao pode virar alarme falso."""
        self.rodada({"ofertas:shopee": 402}, dia="2026-09-03")
        self.assertEqual(self.diagnostico("2026-09-04")["problemas"], [])

    def test_erro_aparece_no_diagnostico(self):
        self.rodada({"ofertas:shopee": None}, erro="token invalido")
        self.assertIn("falhou", " ".join(self.diagnostico()["problemas"]))

    def test_estado_entra_na_resposta_da_api(self):
        self.rodada({"ofertas:shopee": 402, "conversoes": 53})
        self.assertIn("coleta", api.estado(self.cfg))


class TestBackupLocal(BaseRodada):
    def semear(self):
        with db.conectar(self.caminho) as conexao:
            db.salvar_ofertas(
                conexao, [(oferta(), avaliar(oferta(), ScoringConfig()))],
                "2026-09-04")

    def test_gera_arquivo_legivel(self):
        self.semear()
        resultado = backup.copiar_local(self.caminho, self.pasta / "b")
        self.assertTrue(resultado.caminho.is_file())
        copia = sqlite3.connect(resultado.caminho)
        try:
            total = copia.execute("SELECT COUNT(*) FROM oferta_dia").fetchone()[0]
        finally:
            copia.close()
        self.assertEqual(total, 1)

    def test_mantem_apenas_as_n_mais_recentes(self):
        self.semear()
        pasta = self.pasta / "b"
        for indice in range(5):
            resultado = backup.copiar_local(self.caminho, pasta, manter=3)
            # O carimbo tem resolucao de segundo; renomeia para simular dias.
            resultado.caminho.rename(pasta / f"t-2026090{indice}{backup.SUFIXO}")
        backup.copiar_local(self.caminho, pasta, manter=3)
        self.assertLessEqual(len(list(pasta.glob(f"*{backup.SUFIXO}"))), 3)

    def test_copia_de_banco_em_wal_fica_consistente(self):
        """Copiar o arquivo na mao em modo WAL pode perder transacoes."""
        self.semear()
        resultado = backup.copiar_local(self.caminho, self.pasta / "b")
        copia = sqlite3.connect(resultado.caminho)
        try:
            self.assertIsNone(copia.execute("PRAGMA integrity_check").fetchone()[0]
                              if False else None)
            self.assertEqual(
                copia.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            copia.close()


class TestBackupNaNuvem(BaseRodada):
    def semear(self):
        with db.conectar(self.caminho) as conexao:
            db.salvar_conversoes(conexao, [Conversao(
                plataforma="shopee", conversao_id="c1", cliques=0, pedidos=1,
                comissao=5.5, item_id="123", ocorrido_em="2026-08-01",
            )])
            db.adicionar_vitrine(conexao, "shopee", "123", "casa")
            db.definir_alvo(conexao, "shopee", "123", 40.0)

    def exportar(self):
        with db.conectar(self.caminho) as conexao:
            return backup.exportar_para_nuvem(conexao)

    def test_leva_o_que_nao_da_para_refazer(self):
        self.semear()
        pacote = self.exportar()
        self.assertEqual(pacote["contagem"]["conversoes"], 1)
        self.assertEqual(pacote["contagem"]["vitrine"], 1)
        self.assertEqual(pacote["contagem"]["alvos"], 1)

    def test_nao_leva_ofertas(self):
        """Oferta e recolhivel a qualquer momento -- ocuparia espaco a toa."""
        self.semear()
        self.assertNotIn("ofertas", self.exportar())

    def test_saneia_chaves_proibidas_no_firebase(self):
        """A Realtime Database recusa chave com . $ # [ ] /"""
        with db.conectar(self.caminho) as conexao:
            db.salvar_conversoes(conexao, [Conversao(
                plataforma="shopee", conversao_id="a.b/c#d", cliques=0,
                pedidos=1, comissao=1.0, item_id="9",
            )])
        for chave in self.exportar()["conversoes"]:
            for proibido in ".$#[]/":
                self.assertNotIn(proibido, chave)

    def test_ciclo_completo_de_ida_e_volta(self):
        self.semear()
        pacote = self.exportar()

        outro = self.pasta / "vazio.sqlite3"
        with db.conectar(outro) as conexao:
            contagem = backup.restaurar_da_nuvem(conexao, pacote)
        self.assertEqual(contagem["conversoes"], 1)

        with db.conectar(outro) as conexao:
            linha = conexao.execute(
                "SELECT item_id, comissao FROM conversao").fetchone()
            self.assertEqual(len(db.listar_vitrine(conexao)), 1)
            self.assertEqual(len(db.alvos(conexao)), 1)
        self.assertEqual(linha["item_id"], "123")
        self.assertAlmostEqual(linha["comissao"], 5.5)

    def test_restaurar_pacote_vazio_nao_quebra(self):
        with db.conectar(self.caminho) as conexao:
            self.assertEqual(backup.restaurar_da_nuvem(conexao, {}),
                             {"conversoes": 0, "vitrine": 0, "alvos": 0,
                              "cliques": 0})


if __name__ == "__main__":
    unittest.main()
