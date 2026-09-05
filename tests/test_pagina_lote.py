"""A pagina que o lote gera dentro do ZIP.

O lote entregava as imagens numa pasta e as legendas num .txt corrido: para
postar era preciso abrir os dois e casar arquivo com texto na mao. A pagina
resolve isso, mas introduz um risco novo -- ela e HTML montado com o nome do
produto, que vem da API. Nome com `<script>` viraria script executado ao
abrir o arquivo.

Pulado automaticamente se o Node nao estiver instalado.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

ESTATICO = Path(__file__).resolve().parents[1] / "src" / "flow02" / "web" / "estatico"
NODE = shutil.which("node")

SONDA = """
globalThis.localStorage = {{ getItem: () => null, setItem(){{}} }};
globalThis.document = {{ addEventListener(){{}}, getElementById: () => null,
  createElement: () => ({{ getContext: () => null }}) }};
{arte_js}
console.log(JSON.stringify({{
  normal: ARTE.paginaDoLote({entradas}, "1080x1350"),
  perigosa: ARTE.paginaDoLote({perigosa}, "1080x1350"),
  vazia: ARTE.paginaDoLote([], "1080x1350"),
}}));
process.exit(0);
"""

# 1x1 JPEG minimo, so para a pagina ter o que embutir no teste.
PREVIA = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAg"
          "GBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcK"
          "DcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAA"
          "AAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp"
          "//2Q==")

ENTRADAS = [
    {"arquivo": "1-celimax.png", "previa": PREVIA, "nome": "Celimax Retinal 15ml",
     "meta": "R$ 147,00 · você ganha R$ 36,75",
     "legenda": "Celimax Retinal\n\nDe R$ 219 por R$ 147\nhttps://s/1",
     "roteiro": "0-3s GANCHO\n  Fala o preço"},
    {"arquivo": "2-panela.png", "previa": PREVIA, "nome": "Panela de Pressão 5L",
     "meta": "R$ 249,00 · você ganha R$ 74,96",
     "legenda": "Panela de Pressão\n\nR$ 249,00\nhttps://s/2",
     "roteiro": "0-3s GANCHO"},
]

PERIGOSA = [{
    "arquivo": "1.png",
    "previa": PREVIA,
    "nome": '<script>alert("xss")</script>',
    "meta": 'aspas " e <tags>',
    "legenda": '</textarea><script>alert(1)</script>',
    "roteiro": "<img onerror=alert(1)>",
}]


class BasePagina(unittest.TestCase):
    """Gera as paginas uma vez so, para as duas suites."""

    @classmethod
    def setUpClass(cls):
        script = SONDA.format(
            arte_js=(ESTATICO / "arte.js").read_text(encoding="utf-8"),
            entradas=json.dumps(ENTRADAS, ensure_ascii=False),
            perigosa=json.dumps(PERIGOSA, ensure_ascii=False),
        )
        pasta = Path(tempfile.mkdtemp())
        try:
            (pasta / "sonda.js").write_text(script, encoding="utf-8")
            resultado = subprocess.run(
                [NODE, str(pasta / "sonda.js")], capture_output=True,
                text=True, timeout=60, encoding="utf-8")
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
        if resultado.returncode != 0:
            raise AssertionError("falhou:\n" + resultado.stderr[-900:])
        cls.paginas = json.loads(resultado.stdout)

    def contem(self, html, trecho, ctx=""):
        """assertIn despejaria a pagina inteira na falha."""
        self.assertTrue(trecho in html, f"faltou {trecho!r} {ctx}".strip())

    def ausente(self, html, trecho, ctx=""):
        self.assertFalse(trecho in html, f"nao deveria ter {trecho!r} {ctx}".strip())


@unittest.skipUnless(NODE, "node nao instalado")
class TestPaginaDoLote(BasePagina):
    def test_e_html_completo(self):
        pagina = self.paginas["normal"]
        for marca in ("<!doctype html>", "<html", "</html>", 'charset="utf-8"'):
            self.contem(pagina, marca)

    def test_traz_um_cartao_por_produto(self):
        self.assertEqual(self.paginas["normal"].count('class="cartao"'), 2)

    def test_imagem_vem_embutida_na_pagina(self):
        """Assim a pagina funciona sozinha, mesmo movida de lugar."""
        pagina = self.paginas["normal"]
        self.assertEqual(pagina.count('src="data:image/jpeg;base64,'), 2)
        self.ausente(pagina, 'src="artes/')

    def test_ainda_da_para_baixar_o_png_em_tamanho_cheio(self):
        """A previa embutida e reduzida; para postar precisa do original."""
        pagina = self.paginas["normal"]
        self.contem(pagina, 'download href="artes/1-celimax.png"')
        self.contem(pagina, 'download href="artes/2-panela.png"')

    def test_explica_que_a_previa_e_reduzida(self):
        self.contem(self.paginas["normal"], "prévias reduzidas")

    def test_legenda_vai_para_um_campo_editavel(self):
        """Editavel porque texto na sua voz converte mais que generico.

        Conta `data-legenda>`, com o fecha-tag: `data-legenda` sozinho
        apareceria tambem no seletor dentro do script da pagina.
        """
        pagina = self.paginas["normal"]
        self.assertEqual(pagina.count("data-legenda>"), 2)
        self.contem(pagina, "De R$ 219 por R$ 147")

    def test_tem_botao_de_copiar_em_cada_item(self):
        self.assertEqual(self.paginas["normal"].count('data-copiar="legenda"'), 2)

    def test_tem_copiar_tudo(self):
        self.contem(self.paginas["normal"], 'id="tudo"')

    def test_roteiro_fica_recolhido(self):
        """Roteiro e longo: aberto empurraria o proximo produto para fora."""
        self.assertEqual(self.paginas["normal"].count("<details>"), 2)

    def test_permite_baixar_a_imagem(self):
        self.contem(self.paginas["normal"], 'download href="artes/1-celimax.png"')

    def test_usa_execcommand_e_nao_a_api_moderna(self):
        """file:// nao e contexto seguro: navigator.clipboard nao funciona la.

        Procura a CHAMADA, nao a mencao: o proprio comentario da pagina cita
        `navigator.clipboard` para explicar por que nao o usa.
        """
        pagina = self.paginas["normal"]
        self.contem(pagina, "execCommand")
        self.ausente(pagina, "navigator.clipboard.writeText")

    def test_e_responsiva(self):
        self.contem(self.paginas["normal"], "@media")
        self.contem(self.paginas["normal"], "viewport")

    def test_lista_vazia_nao_quebra(self):
        self.contem(self.paginas["vazia"], "0 artes prontas")


@unittest.skipUnless(NODE, "node nao instalado")
class TestInjecaoPeloNomeDoProduto(BasePagina):
    """O nome vem da API; a pagina e aberta com file:// e permissoes locais."""

    def corpo(self) -> str:
        pagina = self.paginas["perigosa"]
        return pagina[pagina.index('class="cartao"'):]

    def test_script_no_nome_e_neutralizado(self):
        self.ausente(self.corpo(), "<script>alert")
        self.contem(self.corpo(), "&lt;script&gt;")

    def test_nao_da_para_escapar_do_textarea(self):
        """`</textarea>` na legenda fecharia o campo e liberaria HTML."""
        self.ausente(self.corpo(), "</textarea><script>")

    def test_aspas_nao_quebram_atributo(self):
        self.contem(self.corpo(), "&quot;")

    def test_atributo_de_evento_nao_passa(self):
        self.ausente(self.corpo(), "<img onerror")

    def test_o_script_da_pagina_continua_intacto(self):
        """Escapar demais quebraria o proprio JS da pagina."""
        self.contem(self.paginas["perigosa"], "function copiar(")
        self.assertEqual(
            len(re.findall(r"<script>", self.paginas["perigosa"])), 1)


if __name__ == "__main__":
    unittest.main()
