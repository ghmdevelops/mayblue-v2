"""Executa as funcoes de render do painel, nao so a sintaxe.

`node --check` valida sintaxe e passa em erro de execucao. Foi o que
aconteceu: `linhaOferta` usava a constante `variantes` numa linha acima da
declaracao dela -- zona morta temporal. Sintaxe perfeita, tela em branco.

Aqui o app.js e carregado com um DOM minimo e as funcoes de render sao
chamadas de verdade, com um produto de exemplo que tem todos os selos
ligados ao mesmo tempo.

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

# DOM minimo: so o que app.js toca ao carregar e ao montar uma linha.
AMBIENTE = """
// `escapar()` do app.js escreve em textContent e le innerHTML -- e assim que
// o navegador neutraliza HTML no nome do produto. O esboco precisa fazer a
// mesma conversao, senao todo nome sairia vazio e o teste mediria nada.
const elemento = () => {
  let texto = "";
  return {
    get textContent(){ return texto; },
    set textContent(v){ texto = v ?? ""; },
    get innerHTML(){
      return texto.replace(/&/g, "&amp;").replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
    },
    set innerHTML(v){ texto = v ?? ""; },
    value: "", checked: false, hidden: false, className: "",
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    dataset: {}, style: {}, addEventListener(){}, appendChild(){}, click(){},
    querySelectorAll: () => [], closest: () => null, focus(){},
    getBoundingClientRect: () => ({ top:0, left:0, width:0, height:0, bottom:0 }),
  };
};
globalThis.location = { search: "?t=teste", hostname: "local" };
globalThis.localStorage = { getItem: () => null, setItem(){}, };
globalThis.document = {
  getElementById: elemento, createElement: elemento,
  querySelector: elemento, querySelectorAll: () => [],
  addEventListener(){},
  body: { appendChild(){} },
};
globalThis.window = { addEventListener(){}, innerWidth: 1200, innerHeight: 800 };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.navigator = { clipboard: { writeText: async () => {} } };
globalThis.URLSearchParams = URLSearchParams;
// Modulos irmaos que o app.js consulta. O alvo aqui e o render das linhas,
// nao a arte nem as dicas -- entao viram esbocos.
globalThis.ARTE = {
  abrir(){}, fechar(){}, redesenhar(){}, baixar(){}, gerarLote: async () => {},
  definirMarca(){}, marca: () => ({ assinatura: "", chamada: "LINK" }),
  montarLegenda: () => "", montarRoteiro: () => "",
};
globalThis.DICAS = { ligar(){}, mostrar(){}, esconder(){} };
globalThis.ZIP = { criar: () => new Uint8Array(), baixar(){}, crc32: () => 0 };
"""

SONDA = """
{ambiente}
{modelos_js}
{app_js}

const item = {item};
const saidas = {{
  linha: linhaOferta(item),
  linhaMinima: linhaOferta({{ item_id: "2", nome: "Simples", posicao: 2,
                             preco: 10, comissao_valor: 1, epc: 0.1, cvr: 0.02 }}),
  linhaPerigosa: linhaOferta({{ ...item, nome: "<script>alert(1)</script>" }}),
  // Todos os modelos de post, montados com o produto de exemplo.
  ...Object.fromEntries(MODELOS.lista().map(
    (m) => ["modelo_" + m.id, MODELOS.montar(m.id, item, "https://s/1")])),
  desconto: textoDesconto(item),
  preco: seloPreco(item),
  velocidade: seloVelocidade(item),
  fragilidade: seloFragilidade(item),
  disponibilidade: seloDisponibilidade(item),
  validade: seloValidade(item),
}};
console.log(JSON.stringify(saidas));
// O app.js agenda um setInterval para o relogio de "atualizado ha X". Um
// temporizador pendente segura o processo do Node vivo indefinidamente, e a
// sonda ficaria travada ate estourar o tempo limite.
process.exit(0);
"""

ITEM = {
    "item_id": "1", "posicao": 1, "nome": "Produto de Teste 15ml",
    "preco": 147.0, "comissao_valor": 36.75, "taxa_comissao": 0.25,
    "epc": 1.0172, "cvr": 0.0277, "calibrado": False,
    "vendas": 18765, "rating": 4.9, "loja": "Loja Teste",
    "imagem": "https://cdn/x.jpg", "link": "https://s/1",
    "link_produto": "https://shopee.com.br/product/1/1",
    "desconto_declarado": 33.0, "desconto_real": 30.0,
    "desconto_confiavel": True, "desconto_inflado": False,
    "menor_preco": True, "preco_minimo": 147.0, "dia_minimo": "2026-09-01",
    "dias_preco": 30, "na_vitrine": False, "indisponivel": False,
    "dias_sumido": 0, "dias_ate_expirar": 10, "variantes": 4,
    "velocidade": {"delta": 340, "dias": 1, "por_dia": 340.0,
                   "desde": "2026-09-03"},
    "fragilidade": {"fatia_vendedor": 0.96, "taxa_vendedor": 0.77,
                    "taxa_shopee": 0.03, "piso": 4.41, "fragil": True},
    "oportunidade": {"forte": True, "quase": False, "atendidos": 4,
                     "total": 4, "motivos": ["paga bem", "vendendo"],
                     "impedimentos": []},
}


@unittest.skipUnless(NODE, "node nao instalado")
class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = SONDA.format(
            ambiente=AMBIENTE,
            modelos_js=(ESTATICO / "modelos.js").read_text(encoding="utf-8"),
            app_js=(ESTATICO / "app.js").read_text(encoding="utf-8"),
            item=json.dumps(ITEM, ensure_ascii=False),
        )
        pasta = Path(tempfile.mkdtemp())
        try:
            (pasta / "sonda.js").write_text(script, encoding="utf-8")
            resultado = subprocess.run(
                [NODE, str(pasta / "sonda.js")],
                capture_output=True, text=True, timeout=60, encoding="utf-8",
            )
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
        if resultado.returncode != 0:
            raise AssertionError(
                "o render falhou em execucao:\n" + resultado.stderr[-1200:])
        cls.saidas = json.loads(resultado.stdout)

    def contem(self, html: str, trecho: str, contexto: str = "") -> None:
        """assertIn despejaria a linha inteira; aqui a falha e legivel."""
        self.assertTrue(trecho in html, f"faltou {trecho!r} {contexto}".strip())

    def test_linha_completa_renderiza(self):
        """Era exatamente isto que quebrava com a zona morta temporal."""
        self.contem(self.saidas["linha"], "Produto de Teste", "na linha")

    def test_produto_sem_campos_opcionais_renderiza(self):
        self.contem(self.saidas["linhaMinima"], "Simples", "na linha minima")

    def test_todos_os_selos_aparecem_juntos(self):
        for marca in ("OPORTUNIDADE", "4 versões", "menor preço",
                      "comissão frágil", "vendidos desde ontem"):
            with self.subTest(selo=marca):
                self.contem(self.saidas["linha"], marca)

    def test_botoes_de_acao_presentes(self):
        for rotulo in ("Abrir", "Copiar", "Arte", "Vitrine", "Alvo"):
            with self.subTest(botao=rotulo):
                self.contem(self.saidas["linha"], rotulo)

    def test_dicas_chegam_no_html(self):
        self.assertGreaterEqual(self.saidas["linha"].count("data-dica"), 6)

    def test_selo_de_fragilidade_mostra_o_percentual(self):
        self.assertIn("96%", self.saidas["fragilidade"])

    def test_velocidade_traz_o_numero(self):
        self.assertIn("340", self.saidas["velocidade"])

    def test_nao_sobra_marcador_de_template(self):
        """`${x}` literal no HTML significa aspas simples onde era crase."""
        for nome, html in self.saidas.items():
            with self.subTest(saida=nome):
                self.assertFalse("${" in html, f"{nome}: sobrou ${{...}} cru")

    def modelos(self) -> dict:
        return {k[7:]: v for k, v in self.saidas.items() if k.startswith("modelo_")}

    def test_todo_modelo_de_post_monta(self):
        self.assertGreaterEqual(len(self.modelos()), 5)

    def test_todo_modelo_leva_o_link_de_afiliado(self):
        """Post sem link nao rende comissao nenhuma."""
        for nome, texto in self.modelos().items():
            with self.subTest(modelo=nome):
                self.assertIn("https://s/1", texto)

    def test_todo_modelo_leva_o_preco(self):
        for nome, texto in self.modelos().items():
            with self.subTest(modelo=nome):
                self.assertIn("147,00", texto)

    def test_nenhum_modelo_fica_vazio_ou_gigante(self):
        for nome, texto in self.modelos().items():
            with self.subTest(modelo=nome):
                self.assertGreater(len(texto), 30, f"{nome} curto demais")
                self.assertLess(len(texto), 900, f"{nome} longo demais")

    def test_modelos_nao_deixam_linha_tripla(self):
        """Filtrar condicao nao atendida nao pode furar o paragrafo."""
        for nome, texto in self.modelos().items():
            with self.subTest(modelo=nome):
                self.assertNotIn("\\n\\n\\n", texto)

    def test_modelo_de_whatsapp_usa_negrito_da_plataforma(self):
        self.assertIn("*", self.modelos()["whatsapp"])

    def test_nome_com_html_e_neutralizado(self):
        """Nome de produto vem da API; nao pode virar tag na pagina."""
        self.assertNotIn("<script", self.saidas["linhaPerigosa"])
        self.contem(self.saidas["linhaPerigosa"], "&lt;script")


if __name__ == "__main__":
    unittest.main()
