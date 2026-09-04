"""Garante que todo controle do painel tenha explicacao ao passar o mouse.

Ferramenta com conceito proprio (EPC, CVR, desconto real, sub-ID, vitrine)
sem explicacao vira caixa-preta: o usuario clica sem saber o que faz e para de
usar o que nao entende. Este teste falha quando alguem adiciona um botao ou
campo novo e esquece a dica -- que e exatamente quando isso acontece.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

ESTATICO = Path(__file__).resolve().parents[1] / "src" / "flow02" / "web" / "estatico"
HTML = (ESTATICO / "index.html").read_text(encoding="utf-8")
JS = (ESTATICO / "app.js").read_text(encoding="utf-8")

# Controles que nao precisam de dica: sao evidentes ou ja tem texto proprio.
DISPENSADOS = {
    "btn-cancelar",       # aparece so durante execucao, rotulo diz tudo
    "arte-canvas",        # e a propria previa
}

TAMANHO_MINIMO = 40  # dica curta demais nao explica nada


def controles(html: str) -> list[tuple[str, str]]:
    """(trecho, identificacao) de cada <button>, <select> e <input>."""
    achados = []
    for casamento in re.finditer(r"<(button|select|textarea)\b[^>]*>", html):
        trecho = casamento.group(0)
        ident = re.search(r'id="([\w-]+)"', trecho)
        achados.append((trecho, ident.group(1) if ident else trecho[:60]))
    return achados


def rotulo_de(html: str, ident: str) -> str | None:
    """O <label> que envolve o controle pode carregar a dica no lugar dele."""
    padrao = re.compile(
        r"<label[^>]*>(?:(?!</label>).)*?" + re.escape(ident) + r".*?</label>",
        re.S,
    )
    casamento = padrao.search(html)
    return casamento.group(0) if casamento else None


class TestDicasNoHtml(unittest.TestCase):
    def test_todo_botao_tem_dica(self):
        sem_dica = []
        for trecho, ident in controles(HTML):
            if ident in DISPENSADOS or "data-dica" in trecho:
                continue
            envolvente = rotulo_de(HTML, ident)
            if envolvente and "data-dica" in envolvente:
                continue
            sem_dica.append(ident)
        self.assertEqual(sem_dica, [], f"controles sem data-dica: {sem_dica}")

    def test_toda_aba_tem_dica(self):
        for casamento in re.finditer(r'<button class="aba[^"]*"[^>]*>', HTML):
            self.assertIn("data-dica", casamento.group(0))

    def test_todo_atalho_de_ordenacao_tem_dica(self):
        for casamento in re.finditer(r'<button class="atalho[^"]*"[^>]*>', HTML):
            self.assertIn("data-dica", casamento.group(0))

    def test_toda_acao_executavel_tem_dica(self):
        for casamento in re.finditer(r"<button[^>]*data-comando[^>]*>", HTML):
            self.assertIn("data-dica", casamento.group(0))

    def test_dicas_sao_explicativas(self):
        """Dica de duas palavras nao ajuda ninguem."""
        curtas = [
            texto[:45] for texto in re.findall(r'data-dica="([^"]*)"', HTML)
            if len(texto) < TAMANHO_MINIMO
        ]
        self.assertEqual(curtas, [], f"dicas curtas demais: {curtas}")

    def test_dicas_nao_tem_aspas_quebrando_o_atributo(self):
        for trecho in re.findall(r'data-dica="([^"]*)"', HTML):
            self.assertNotIn('"', trecho)


class TestDicasNoJs(unittest.TestCase):
    """As linhas de produto sao montadas em JS, entao a checagem e separada."""

    def test_botoes_da_linha_de_produto_tem_dica(self):
        for marcador in ("data-link=", "data-arte=", "data-vitrine=", "data-alvo="):
            posicao = JS.find(marcador)
            self.assertNotEqual(posicao, -1, f"{marcador} sumiu do template")
            # A dica deve estar no mesmo elemento, logo depois do marcador.
            self.assertIn("data-dica", JS[posicao:posicao + 700], marcador)

    def test_blocos_de_numero_tem_dica(self):
        for classe in ("valor-bloco", "ganho-bloco", "epc-bloco"):
            posicao = JS.find(f'class="{classe}"')
            self.assertNotEqual(posicao, -1, f"{classe} sumiu do template")
            self.assertIn("data-dica", JS[posicao:posicao + 500], classe)

    def test_engine_de_dicas_esta_carregada(self):
        self.assertIn('src="/estatico/dicas.js"', HTML)

    def test_dicas_js_nao_rouba_clique(self):
        """pointer-events: none evita a dica cobrir o proprio botao."""
        css = (ESTATICO / "estilo.css").read_text(encoding="utf-8")
        bloco = css[css.find(".dica-flutuante"):css.find(".dica-flutuante") + 500]
        self.assertIn("pointer-events: none", bloco)


if __name__ == "__main__":
    unittest.main()
