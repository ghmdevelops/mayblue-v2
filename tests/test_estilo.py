"""Checagens estruturais do CSS e do HTML do painel.

Erro de CSS nao quebra nada: a regra e ignorada em silencio e a tela fica
levemente errada, o que passa despercebido em revisao. Estas verificacoes
pegam justamente o que nao aparece como erro.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

ESTATICO = Path(__file__).resolve().parents[1] / "src" / "flow02" / "web" / "estatico"
CSS = (ESTATICO / "estilo.css").read_text(encoding="utf-8")
HTML = (ESTATICO / "index.html").read_text(encoding="utf-8")
JS = (ESTATICO / "app.js").read_text(encoding="utf-8")

PAINEIS = ("ranking", "vitrine", "saude", "alertas", "ganhos", "historico", "acoes")


class TestCss(unittest.TestCase):
    def test_chaves_balanceadas(self):
        self.assertEqual(CSS.count("{"), CSS.count("}"))

    def test_toda_variavel_usada_existe(self):
        """var(--cor-que-nao-existe) nao gera erro: só nao pinta nada."""
        usadas = set(re.findall(r"var\(--([\w-]+)\)", CSS))
        # Varias podem ser declaradas na mesma linha, entao nao ancora em ^.
        definidas = set(re.findall(r"--([\w-]+)\s*:", CSS))
        self.assertEqual(sorted(usadas - definidas), [])

    def test_toda_classe_usada_tem_estilo(self):
        no_css = set(re.findall(r"\.([a-z][\w-]*)", CSS))
        usadas = set()
        for atributo in re.findall(r'class="([^"{}]*)"', JS + HTML):
            usadas.update(atributo.split())
        self.assertEqual(sorted(c for c in usadas if c and c not in no_css), [])

    def test_continua_escuro(self):
        """O usuario pediu para manter o tema escuro."""
        fundo = re.search(r"--fundo:\s*#([0-9a-fA-F]{6})", CSS).group(1)
        luminancia = sum(int(fundo[i:i + 2], 16) for i in (0, 2, 4)) / 3
        self.assertLess(luminancia, 40, f"fundo claro demais: #{fundo}")

    def test_texto_principal_tem_contraste(self):
        claro = re.search(r"--texto:\s*#([0-9a-fA-F]{6})", CSS).group(1)
        luminancia = sum(int(claro[i:i + 2], 16) for i in (0, 2, 4)) / 3
        self.assertGreater(luminancia, 200, f"texto escuro demais: #{claro}")

    def test_rotulos_pequenos_tem_contraste(self):
        """Texto pequeno em CAIXA ALTA precisa de mais contraste que corpo.

        Foi o defeito reportado: DIA, PLATAFORMA e ITENS ficaram apagados.
        """
        cor = re.search(r"--rotulo:\s*#([0-9a-fA-F]{6})", CSS).group(1)
        canais = [int(cor[i:i + 2], 16) for i in (0, 2, 4)]
        self.assertGreater(sum(canais) / 3, 150, f"rotulo apagado: #{cor}")

    def test_texto_secundario_nao_e_apagado(self):
        for nome in ("texto-2", "texto-3"):
            cor = re.search(rf"--{nome}:\s*#([0-9a-fA-F]{{6}})", CSS).group(1)
            media = sum(int(cor[i:i + 2], 16) for i in (0, 2, 4)) / 3
            self.assertGreater(media, 125, f"--{nome} apagado: #{cor}")

    def test_nao_deixa_important_espalhado(self):
        """`!important` costuma ser remendo, mas tem dois usos legitimos:

        - anular o separador `·` dos selos, que vem de um `::after` generico;
        - o bloco de movimento reduzido, que precisa vencer toda transicao --
          e o padrao recomendado para acessibilidade.

        O teste conta so os que estao fora desses casos.
        """
        bloco_movimento = CSS[CSS.find("prefers-reduced-motion"):]
        bloco_movimento = bloco_movimento[:bloco_movimento.find("}\n}") + 3]
        fora = CSS.replace(bloco_movimento, "")

        separadores = len(re.findall(r"content:\s*none\s*!important", fora))
        total = fora.count("!important")
        self.assertEqual(total - separadores, 0,
                         f"{total - separadores} !important sem justificativa")


class TestResponsivo(unittest.TestCase):
    def test_declara_viewport(self):
        """Sem isto o celular renderiza como desktop e reduz tudo."""
        self.assertIn('name="viewport"', HTML)
        self.assertIn("width=device-width", HTML)

    def test_tem_quebras_para_celular(self):
        larguras = {int(v) for v in re.findall(r"max-width:\s*(\d+)px", CSS)}
        self.assertTrue(any(l <= 600 for l in larguras),
                        f"nenhuma quebra para celular: {sorted(larguras)}")
        self.assertGreaterEqual(len(larguras), 3)

    def test_lista_de_ofertas_reorganiza_no_estreito(self):
        self.assertIn("grid-template-areas", CSS)

    def test_campos_nao_forcam_largura_minima_no_celular(self):
        """min-width fixo em input causa rolagem horizontal no telefone."""
        estreito = CSS[CSS.find("@media (max-width: 860px)"):]
        estreito = estreito[:estreito.find("@media (max-width: 560px)")]
        self.assertIn("min-width: 0", estreito)

    def test_respeita_quem_pede_menos_movimento(self):
        self.assertIn("prefers-reduced-motion", CSS)


class TestTitulos(unittest.TestCase):
    def test_todo_painel_tem_titulo_visivel(self):
        for painel in PAINEIS:
            trecho = HTML[HTML.find(f'id="painel-{painel}"'):]
            trecho = trecho[:trecho.find("</section>")]
            self.assertIn("titulo-secao", trecho, f"painel {painel} sem título")
            self.assertIn("<h2>", trecho, f"painel {painel} sem h2")

    def test_titulo_de_secao_e_colorido(self):
        bloco = CSS[CSS.find(".titulo-secao h2"):]
        bloco = bloco[:bloco.find("}")]
        self.assertIn("var(--acento)", bloco)

    def test_todo_titulo_tem_legenda(self):
        for painel in PAINEIS:
            trecho = HTML[HTML.find(f'id="painel-{painel}"'):]
            trecho = trecho[:trecho.find("</section>")]
            self.assertIn("legenda", trecho, f"painel {painel} sem legenda")


class TestEstruturaPreservada(unittest.TestCase):
    """O redesenho nao pode ter mexido em nada funcional."""

    def test_todos_os_paineis_continuam_existindo(self):
        for painel in PAINEIS:
            self.assertIn(f'id="painel-{painel}"', HTML)
            self.assertIn(f'data-painel="{painel}"', HTML)

    def test_ids_que_o_js_busca_existem(self):
        ids_html = set(re.findall(r'id="([\w-]+)"', HTML))
        # Alguns elementos o proprio JS cria antes de buscar.
        ids_criados = set(re.findall(r'id=\\?"([\w-]+)\\?"', JS))
        ids_js = set(re.findall(r'\$\("([\w-]+)"\)', JS))
        faltando = sorted(ids_js - ids_html - ids_criados)
        self.assertEqual(faltando, [], f"ids inexistentes: {faltando}")

    def test_botoes_de_comando_intactos(self):
        from flow02.web.tarefas import COMANDOS

        no_html = set(re.findall(r'data-comando="(\w+)"', HTML))
        self.assertTrue(no_html.issubset(set(COMANDOS)))
        self.assertGreaterEqual(len(no_html), 8)


if __name__ == "__main__":
    unittest.main()
