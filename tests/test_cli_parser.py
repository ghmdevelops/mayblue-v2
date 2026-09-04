"""O parser da CLI precisa simplesmente construir.

Existe por causa de um erro real: um subcomando novo declarou `--top`, que o
helper compartilhado ja adicionava. O argparse so reclama na hora de montar o
parser -- e como nenhum teste montava, os 555 testes passaram enquanto o
programa nao abria mais.

Erro de configuracao de parser nao aparece em teste de unidade de logica.
Precisa de um teste que exercite a montagem.
"""

from __future__ import annotations

import unittest

from tests import SRC  # noqa: F401

from flow02 import cli

# Subcomandos que nao levam argumento obrigatorio.
COMANDOS_SIMPLES = (
    "doctor", "coletar", "top", "historico", "ganhos", "calibrar", "alvo",
    "vitrine", "saude", "lojas", "backup", "agendar", "cliques",
    "sincronizar", "alertas", "categorias", "notificar", "web", "publicar",
)


class TestParser(unittest.TestCase):
    def test_constroi_sem_conflito(self):
        """Falha se dois subcomandos declararem a mesma flag duas vezes."""
        self.assertIsNotNone(cli.construir_parser())

    def test_todo_comando_registrado_tem_subparser(self):
        parser = cli.construir_parser()
        subparsers = next(
            acao.choices for acao in parser._actions
            if hasattr(acao, "choices") and isinstance(acao.choices, dict)
        )
        faltando = sorted(set(cli.COMANDOS) - set(subparsers))
        self.assertEqual(faltando, [], f"sem subparser: {faltando}")

    def test_todo_subparser_tem_funcao(self):
        parser = cli.construir_parser()
        subparsers = next(
            acao.choices for acao in parser._actions
            if hasattr(acao, "choices") and isinstance(acao.choices, dict)
        )
        orfaos = sorted(set(subparsers) - set(cli.COMANDOS))
        self.assertEqual(orfaos, [], f"sem funcao: {orfaos}")

    def test_cada_comando_aceita_ser_chamado_sem_flags(self):
        parser = cli.construir_parser()
        for comando in COMANDOS_SIMPLES:
            with self.subTest(comando=comando):
                args = parser.parse_args([comando])
                self.assertEqual(args.comando, comando)

    def test_flags_de_saida_existem_onde_esperado(self):
        parser = cli.construir_parser()
        for comando in ("top", "ganhos", "vitrine", "lojas", "alvo"):
            with self.subTest(comando=comando):
                args = parser.parse_args([comando, "--formato", "csv"])
                self.assertEqual(args.formato, "csv")

    def test_lojas_aceita_seus_proprios_filtros(self):
        args = cli.construir_parser().parse_args(
            ["lojas", "--paginas", "3", "--taxa-min", "20", "--top", "5"])
        self.assertEqual((args.paginas, args.taxa_min, args.top), (3, 20.0, 5))

    def test_ajuda_de_cada_subcomando_renderiza(self):
        """format_help() percorre todas as acoes: pega descricao quebrada."""
        parser = cli.construir_parser()
        subparsers = next(
            acao.choices for acao in parser._actions
            if hasattr(acao, "choices") and isinstance(acao.choices, dict)
        )
        for nome, sub in subparsers.items():
            with self.subTest(comando=nome):
                self.assertIn(nome, sub.format_help())


if __name__ == "__main__":
    unittest.main()
