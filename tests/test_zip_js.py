"""Valida o escritor de ZIP em JS usando o zipfile do Python.

Formato binario escrito a mao sem validacao vira arquivo corrompido que so
aparece quando o usuario tenta abrir. Aqui o Node gera o ZIP e o Python -- uma
implementacao independente -- tenta ler. Se as duas concordam, o formato esta
certo.

Pulado automaticamente se o Node nao estiver instalado.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests import SRC  # noqa: F401

ESTATICO = Path(__file__).resolve().parents[1] / "src" / "flow02" / "web" / "estatico"
NODE = shutil.which("node")

GERADOR = """
const fs = require('fs');
{zip_js}

// Blob/TextEncoder existem no Node 18+; File API de escrita nao, entao le o
// buffer do Blob e grava com fs.
const arquivos = {arquivos};
const blob = ZIP.criar(arquivos.map(a => ({{
  nome: a.nome,
  dados: new TextEncoder().encode(a.texto),
}})));
// argv[0] = node, argv[1] = este script, argv[2] = destino
blob.arrayBuffer().then(b => fs.writeFileSync(process.argv[2], Buffer.from(b)));
"""


@unittest.skipUnless(NODE, "node nao instalado")
class TestEscritorZip(unittest.TestCase):
    def gerar(self, arquivos: list[dict]) -> Path:
        zip_js = (ESTATICO / "zip.js").read_text(encoding="utf-8")
        script = GERADOR.format(zip_js=zip_js, arquivos=repr(arquivos).replace("'", '"'))
        pasta = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        (pasta / "gerar.js").write_text(script, encoding="utf-8")
        destino = pasta / "saida.zip"
        resultado = subprocess.run(
            [NODE, str(pasta / "gerar.js"), str(destino)],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        return destino

    def test_zip_valido_para_o_python(self):
        caminho = self.gerar([{"nome": "a.txt", "texto": "conteudo A"}])
        self.assertTrue(zipfile.is_zipfile(caminho))

    def test_conteudo_intacto(self):
        caminho = self.gerar([{"nome": "a.txt", "texto": "conteudo A"}])
        with zipfile.ZipFile(caminho) as arquivo:
            self.assertEqual(arquivo.read("a.txt").decode(), "conteudo A")

    def test_varios_arquivos(self):
        caminho = self.gerar([
            {"nome": "artes/01-fone.txt", "texto": "primeiro"},
            {"nome": "artes/02-cadeira.txt", "texto": "segundo"},
            {"nome": "legendas.txt", "texto": "terceiro"},
        ])
        with zipfile.ZipFile(caminho) as arquivo:
            self.assertEqual(len(arquivo.namelist()), 3)
            self.assertEqual(arquivo.read("artes/02-cadeira.txt").decode(), "segundo")

    def test_crc_confere(self):
        """testzip() devolve o primeiro arquivo corrompido, ou None."""
        caminho = self.gerar([
            {"nome": "a.txt", "texto": "x" * 5000},
            {"nome": "b.txt", "texto": "y"},
        ])
        with zipfile.ZipFile(caminho) as arquivo:
            self.assertIsNone(arquivo.testzip())

    def test_conteudo_vazio(self):
        caminho = self.gerar([{"nome": "vazio.txt", "texto": ""}])
        with zipfile.ZipFile(caminho) as arquivo:
            self.assertEqual(arquivo.read("vazio.txt"), b"")

    def test_acentos_no_conteudo(self):
        caminho = self.gerar([{"nome": "a.txt", "texto": "comissao de R$ 12,14"}])
        with zipfile.ZipFile(caminho) as arquivo:
            self.assertIn("12,14", arquivo.read("a.txt").decode("utf-8"))

    def test_metodo_store(self):
        caminho = self.gerar([{"nome": "a.txt", "texto": "abc"}])
        with zipfile.ZipFile(caminho) as arquivo:
            info = arquivo.getinfo("a.txt")
        self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(info.file_size, info.compress_size)


if __name__ == "__main__":
    unittest.main()
