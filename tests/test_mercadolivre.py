from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import SRC  # noqa: F401

from flow02.config import MercadoLivreConfig
from flow02.sources.base import FonteIndisponivel
from flow02.sources.mercadolivre import (
    PLATAFORMA,
    comissao_para,
    extrair_item_id,
    ler_csv_curado,
    ler_lista_urls,
    validar_url_afiliado,
)

CABECALHO = ("item_id,nome,preco,taxa_comissao,categoria,vendas,rating,"
             "desconto_pct,link_oferta,loja_nome")
CFG = MercadoLivreConfig(taxa_comissao_padrao=0.0)
PRODUTO = "https://www.mercadolivre.com.br/cadeira-gamer-xp/p/MLB19876543"


class TestExtracaoItemId(unittest.TestCase):
    def test_url_de_catalogo(self):
        self.assertEqual(extrair_item_id(PRODUTO), "MLB19876543")

    def test_url_com_hifen(self):
        self.assertEqual(
            extrair_item_id("https://produto.mercadolivre.com.br/MLB-1234567890-fone"),
            "MLB1234567890",
        )

    def test_id_puro(self):
        self.assertEqual(extrair_item_id("MLB1234567890"), "MLB1234567890")

    def test_outros_sites_do_grupo(self):
        self.assertEqual(extrair_item_id("https://x/p/MLA9876543"), "MLA9876543")

    def test_link_curto_de_afiliado(self):
        self.assertEqual(
            extrair_item_id("https://mercadolivre.com/sec/2abcXYZ"), "2abcXYZ"
        )

    def test_sem_id(self):
        self.assertIsNone(extrair_item_id("https://www.mercadolivre.com.br/ofertas"))
        self.assertIsNone(extrair_item_id(""))


class TestValidacaoDeLink(unittest.TestCase):
    def test_pagina_de_produto_e_elegivel(self):
        elegivel, _ = validar_url_afiliado(PRODUTO)
        self.assertTrue(elegivel)

    def test_link_curto_e_elegivel(self):
        elegivel, _ = validar_url_afiliado("https://mercadolivre.com/sec/2abcXYZ")
        self.assertTrue(elegivel)

    def test_home_nao_e_elegivel(self):
        elegivel, motivo = validar_url_afiliado("https://www.mercadolivre.com.br/")
        self.assertFalse(elegivel)
        self.assertIn("principal", motivo)

    def test_ofertas_do_dia_nao_e_elegivel(self):
        elegivel, motivo = validar_url_afiliado("https://www.mercadolivre.com.br/ofertas")
        self.assertFalse(elegivel)
        self.assertIn("ofertas", motivo)

    def test_mais_vendidos_nao_e_elegivel(self):
        elegivel, _ = validar_url_afiliado(
            "https://www.mercadolivre.com.br/mais-vendidos/MLB1051"
        )
        self.assertFalse(elegivel)

    def test_busca_nao_e_elegivel(self):
        elegivel, motivo = validar_url_afiliado(
            "https://lista.mercadolivre.com.br/fone?q=fone+bluetooth"
        )
        self.assertFalse(elegivel)
        self.assertIn("busca", motivo)

    def test_categoria_nao_e_elegivel(self):
        elegivel, _ = validar_url_afiliado("https://www.mercadolivre.com.br/c/eletronicos")
        self.assertFalse(elegivel)

    def test_pagina_de_vendedor_nao_e_elegivel(self):
        elegivel, _ = validar_url_afiliado("https://www.mercadolivre.com.br/perfil/LOJAXP")
        self.assertFalse(elegivel)

    def test_carrinho_e_checkout_nao_sao_elegiveis(self):
        for caminho in ("/carrinho", "/checkout/v1"):
            elegivel, _ = validar_url_afiliado(f"https://www.mercadolivre.com.br{caminho}")
            self.assertFalse(elegivel, caminho)

    def test_mercado_shops_nao_e_elegivel(self):
        elegivel, motivo = validar_url_afiliado("https://lojax.mercadoshops.com.br/produto")
        self.assertFalse(elegivel)
        self.assertIn("nao elegivel", motivo)

    def test_imoveis_e_servicos_nao_sao_elegiveis(self):
        for caminho in ("/imoveis/casa", "/servicos/pintura", "/veiculos/carro"):
            elegivel, _ = validar_url_afiliado(f"https://www.mercadolivre.com.br{caminho}")
            self.assertFalse(elegivel, caminho)

    def test_dominio_de_fora_nao_e_elegivel(self):
        elegivel, motivo = validar_url_afiliado("https://www.amazon.com.br/dp/B01")
        self.assertFalse(elegivel)
        self.assertIn("fora do Mercado Livre", motivo)

    def test_url_vazia(self):
        self.assertEqual(validar_url_afiliado("")[0], False)


class TestComissaoPorCategoria(unittest.TestCase):
    def setUp(self):
        self.cfg = MercadoLivreConfig(
            taxa_comissao_padrao=0.03,
            comissao_por_categoria={"moda": 0.06, "casa": 0.05},
        )

    def test_usa_tabela_quando_categoria_conhecida(self):
        self.assertAlmostEqual(comissao_para("moda", self.cfg), 0.06)

    def test_ignora_maiusculas_e_espacos(self):
        self.assertAlmostEqual(comissao_para("  MODA ", self.cfg), 0.06)

    def test_cai_para_o_padrao(self):
        self.assertAlmostEqual(comissao_para("desconhecida", self.cfg), 0.03)
        self.assertAlmostEqual(comissao_para(None, self.cfg), 0.03)


class BaseArquivo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def escrever(self, nome: str, conteudo: str) -> Path:
        caminho = self.dir / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    def csv(self, *linhas, cabecalho=CABECALHO) -> Path:
        return self.escrever("ml.csv", "\n".join([cabecalho, *linhas]) + "\n")


class TestLeituraCsv(BaseArquivo):
    def test_normaliza_para_o_modelo_comum(self):
        caminho = self.csv(
            "MLB123,Cadeira Gamer,899.90,0.06,casa,42,4.7,15,https://mercadolivre.com/sec/x,Loja XP"
        )
        item = ler_csv_curado(caminho, CFG)[0]
        self.assertEqual(item.plataforma, PLATAFORMA)
        self.assertEqual(item.item_id, "MLB123")
        self.assertAlmostEqual(item.preco, 899.90)
        self.assertAlmostEqual(item.taxa_comissao, 0.06)
        self.assertAlmostEqual(item.comissao_valor, 899.90 * 0.06, places=3)
        self.assertEqual(item.origem_consulta, "curadoria_csv")
        self.assertEqual(item.categoria_ids, ("casa",))

    def test_aceita_ponto_e_virgula_como_separador(self):
        caminho = self.escrever(
            "ml.csv",
            CABECALHO.replace(",", ";") + "\nMLB1;Item;100.00;0.05;casa;1;5;0;;\n",
        )
        self.assertEqual(len(ler_csv_curado(caminho, CFG)), 1)

    def test_aceita_virgula_decimal_e_prefixo_reais(self):
        caminho = self.escrever(
            "ml.csv",
            CABECALHO.replace(",", ";") + '\nMLB1;Item;"R$ 1499,90";"0,08";casa;1;5;0;;\n',
        )
        item = ler_csv_curado(caminho, CFG)[0]
        self.assertAlmostEqual(item.preco, 1499.90)
        self.assertAlmostEqual(item.taxa_comissao, 0.08)

    def test_taxa_vem_da_tabela_de_categoria(self):
        cfg = MercadoLivreConfig(
            taxa_comissao_padrao=0.01, comissao_por_categoria={"moda": 0.06}
        )
        caminho = self.csv("MLB1,Camisa,100.00,,moda,1,5,0,,")
        self.assertAlmostEqual(ler_csv_curado(caminho, cfg)[0].taxa_comissao, 0.06)

    def test_taxa_cai_para_o_padrao_sem_categoria(self):
        cfg = MercadoLivreConfig(taxa_comissao_padrao=0.045)
        caminho = self.csv("MLB1,Item,100.00,,,1,5,0,,")
        self.assertAlmostEqual(ler_csv_curado(caminho, cfg)[0].taxa_comissao, 0.045)

    def test_linha_sem_preco_ou_id_e_ignorada(self):
        caminho = self.csv(
            "MLB1,Bom,100.00,0.05,casa,1,5,0,,",
            "MLB2,Sem preco,,0.05,casa,1,5,0,,",
            ",Sem id,100.00,0.05,casa,1,5,0,,",
        )
        self.assertEqual([o.item_id for o in ler_csv_curado(caminho, CFG)], ["MLB1"])

    def test_arquivo_inexistente(self):
        with self.assertRaises(FonteIndisponivel):
            ler_csv_curado(self.dir / "nao_existe.csv", CFG)

    def test_colunas_obrigatorias_ausentes(self):
        caminho = self.csv("100.00", cabecalho="preco")
        with self.assertRaises(FonteIndisponivel) as ctx:
            ler_csv_curado(caminho, CFG)
        self.assertIn("item_id", str(ctx.exception))

    def test_arquivo_vazio(self):
        self.assertEqual(ler_csv_curado(self.escrever("ml.csv", ""), CFG), [])


class TestListaDeUrls(BaseArquivo):
    def test_aceita_produto_com_preco(self):
        caminho = self.escrever("urls.txt", f"{PRODUTO};899.90;casa\n")
        cfg = MercadoLivreConfig(comissao_por_categoria={"casa": 0.05})
        ofertas, rejeitadas = ler_lista_urls(caminho, cfg)
        self.assertEqual(rejeitadas, [])
        self.assertEqual(ofertas[0].item_id, "MLB19876543")
        self.assertAlmostEqual(ofertas[0].taxa_comissao, 0.05)
        self.assertEqual(ofertas[0].origem_consulta, "curadoria_urls")

    def test_rejeita_url_inelegivel_com_motivo(self):
        caminho = self.escrever(
            "urls.txt", "https://www.mercadolivre.com.br/ofertas;100\n"
        )
        ofertas, rejeitadas = ler_lista_urls(caminho, CFG)
        self.assertEqual(ofertas, [])
        self.assertIn("ofertas do dia", rejeitadas[0][1])

    def test_rejeita_sem_preco(self):
        """O ML nao expoe preco via API, entao ele precisa vir na linha."""
        caminho = self.escrever("urls.txt", f"{PRODUTO}\n")
        ofertas, rejeitadas = ler_lista_urls(caminho, CFG)
        self.assertEqual(ofertas, [])
        self.assertIn("sem preco", rejeitadas[0][1])

    def test_ignora_comentarios_e_linhas_vazias(self):
        caminho = self.escrever(
            "urls.txt", f"# comentario\n\n{PRODUTO};50\n"
        )
        ofertas, _ = ler_lista_urls(caminho, CFG)
        self.assertEqual(len(ofertas), 1)

    def test_arquivo_inexistente(self):
        with self.assertRaises(FonteIndisponivel):
            ler_lista_urls(self.dir / "nao_existe.txt", CFG)


if __name__ == "__main__":
    unittest.main()
