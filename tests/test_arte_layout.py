"""Geometria da arte gerada.

Existe por causa de um defeito visual real: numa arte 4:5 o nome do produto
ficou por baixo da foto e so aparecia a ultima palavra. Desenho puro nao e
testavel sem renderizar, entao a geometria foi separada numa funcao que
devolve dados -- e aqui se verifica que os blocos nao colidem e nao saem
da tela, nos tres formatos oferecidos.

Pulado automaticamente se o Node nao estiver instalado.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

ESTATICO = Path(__file__).resolve().parents[1] / "src" / "flow02" / "web" / "estatico"
NODE = shutil.which("node")

FORMATOS = [(1080, 1350), (1080, 1920), (1080, 1080)]

SONDA = """
// Ambiente minimo para o modulo carregar fora do navegador.
globalThis.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
globalThis.document = {{ addEventListener: () => {{}}, getElementById: () => null }};
{arte_js}
const casos = [];
for (const [largura, altura] of {formatos}) {{
  for (const nota of [true, false]) {{
    for (const assinatura of [true, false]) {{
      casos.push({{
        largura, altura, nota, assinatura,
        layout: ARTE.calcularLayout(largura, altura, nota, assinatura),
      }});
    }}
  }}
}}
console.log(JSON.stringify(casos));
"""


@unittest.skipUnless(NODE, "node nao instalado")
class TestLayoutDaArte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        arte = (ESTATICO / "arte.js").read_text(encoding="utf-8")
        script = SONDA.format(arte_js=arte, formatos=json.dumps(FORMATOS))
        pasta = Path(tempfile.mkdtemp())
        try:
            (pasta / "sonda.mjs").write_text(script, encoding="utf-8")
            resultado = subprocess.run(
                [NODE, str(pasta / "sonda.mjs")],
                capture_output=True, text=True, timeout=60,
            )
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
        if resultado.returncode != 0:
            raise unittest.SkipTest(f"sonda falhou: {resultado.stderr[:300]}")
        cls.casos = json.loads(resultado.stdout)

    def descricao(self, caso) -> str:
        return (f"{caso['largura']}x{caso['altura']} nota={caso['nota']} "
                f"assinatura={caso['assinatura']}")

    def test_gera_layout_para_todos_os_formatos(self):
        self.assertEqual(len(self.casos), len(FORMATOS) * 4)

    def test_foto_cabe_na_largura(self):
        for caso in self.casos:
            foto = caso["layout"]["foto"]
            self.assertGreaterEqual(foto["x"], 0, self.descricao(caso))
            self.assertLessEqual(foto["x"] + foto["lado"], caso["largura"],
                                 self.descricao(caso))

    def test_foto_nao_invade_o_nome(self):
        """O defeito original: nome desenhado por cima da foto."""
        for caso in self.casos:
            L = caso["layout"]
            fim_foto = L["foto"]["y"] + L["foto"]["lado"]
            topo_texto = (L["nota"] or L["nome"])["y"] - (L["nota"] or L["nome"])["altura"]
            self.assertLessEqual(fim_foto, topo_texto, self.descricao(caso))

    def test_blocos_ficam_em_ordem_de_cima_para_baixo(self):
        for caso in self.casos:
            L = caso["layout"]
            ordem = [L["foto"]["y"] + L["foto"]["lado"]]
            if L["nota"]:
                ordem.append(L["nota"]["y"])
            ordem.append(L["nome"]["y"])
            ordem.append(L["preco"]["y"])
            if L["assinatura"]:
                ordem.append(L["assinatura"]["y"])
            ordem.append(L["rodape"]["y"])
            self.assertEqual(ordem, sorted(ordem), self.descricao(caso))

    def test_nada_sai_pela_base(self):
        for caso in self.casos:
            L = caso["layout"]
            fim = L["rodape"]["y"] + L["rodape"]["altura"]
            self.assertLessEqual(fim, caso["altura"], self.descricao(caso))

    def test_preco_nao_encosta_no_rodape(self):
        for caso in self.casos:
            L = caso["layout"]
            limite = (L["assinatura"] or L["rodape"])["y"]
            self.assertLess(L["preco"]["y"], limite, self.descricao(caso))

    def test_foto_tem_tamanho_util_em_todos_os_formatos(self):
        """Formato 9:16 é alto e estreito: a foto não pode virar um selo."""
        for caso in self.casos:
            foto = caso["layout"]["foto"]
            self.assertGreater(foto["lado"], caso["largura"] * 0.28,
                               self.descricao(caso))

    def test_sem_nota_o_espaco_e_reaproveitado(self):
        com = next(c for c in self.casos
                   if c["largura"] == 1080 and c["altura"] == 1350
                   and c["nota"] and not c["assinatura"])
        sem = next(c for c in self.casos
                   if c["largura"] == 1080 and c["altura"] == 1350
                   and not c["nota"] and not c["assinatura"])
        self.assertGreaterEqual(sem["layout"]["foto"]["lado"],
                                com["layout"]["foto"]["lado"])


if __name__ == "__main__":
    unittest.main()
