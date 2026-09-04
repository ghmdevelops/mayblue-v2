"""O README acompanha os comandos que existem.

Documentacao que envelhece silenciosamente e pior que documentacao ausente:
quem le confia. Hoje o programa tem 20 comandos, e varios foram adicionados
sem nunca aparecer no manual.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02.cli import COMANDOS

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(
    encoding="utf-8")


class TestReadme(unittest.TestCase):
    def test_todo_comando_esta_na_tabela_resumo(self):
        na_tabela = set(re.findall(r"^\| `(\w+)` \|", README, re.M))
        faltando = sorted(set(COMANDOS) - na_tabela)
        self.assertEqual(faltando, [], f"fora da tabela: {faltando}")

    def test_tabela_nao_lista_comando_inexistente(self):
        na_tabela = set(re.findall(r"^\| `(\w+)` \|", README, re.M))
        # A tabela tambem cita flags e outras coisas; so checa o que parece
        # comando, isto e, o que existe no codigo ou some dele.
        fantasmas = sorted(
            nome for nome in na_tabela
            if nome not in COMANDOS and nome in {
                "coletar", "top", "ganhos", "alertas", "doctor", "web",
                "calibrar", "sincronizar", "publicar", "link", "historico",
                "categorias", "notificar", "simular", "exportar",
            }
        )
        self.assertEqual(fantasmas, [], f"comando removido ainda citado: {fantasmas}")

    def test_comandos_principais_tem_secao_propria(self):
        """Os que mudam decisao merecem explicacao, nao so uma linha."""
        documentados = set(re.findall(r"^### `(\w+)`", README, re.M))
        essenciais = {"doctor", "coletar", "top", "ganhos", "backtest",
                      "saude", "lojas", "backup", "agendar", "cliques"}
        faltando = sorted(essenciais - documentados)
        self.assertEqual(faltando, [], f"sem secao propria: {faltando}")

    def test_contagem_de_testes_nao_esta_absurdamente_velha(self):
        """Numero no cabecalho vira mentira rapido; tolera defasagem pequena."""
        casamento = re.search(r"\*\*(\d+) testes", README)
        self.assertIsNotNone(casamento, "o README nao declara a contagem")
        declarado = int(casamento.group(1))
        arquivos = list((Path(__file__).parent).glob("test_*.py"))
        # Estimativa grosseira: cada arquivo tem varios testes.
        self.assertGreater(declarado, len(arquivos) * 5,
                           "contagem declarada parece de outra epoca")

    def test_registra_as_limitacoes_da_api(self):
        """Elas explicam metade das decisoes do projeto.

        `assertIn` despejaria o README inteiro na falha, entao o teste
        verifica a presenca e reporta so o termo que faltou.
        """
        faltando = [
            termo for termo in
            ("Sem cliques", "Sem estoque", "validatedReport", "Mercado Livre")
            if termo not in README
        ]
        self.assertEqual(faltando, [], f"README nao menciona: {faltando}")

    def test_avisa_o_que_depende_do_usuario(self):
        faltando = [
            termo for termo in
            ("Rotacionar o segredo", "regras do Firebase", "Netlify")
            if termo not in README
        ]
        self.assertEqual(faltando, [], f"README nao avisa sobre: {faltando}")


if __name__ == "__main__":
    unittest.main()
