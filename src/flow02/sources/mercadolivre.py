"""Adapter do Mercado Livre.

Estado verificado ao vivo (probe em 2026-09-03):
    GET /sites/MLB/search   -> 403
    GET /items/{id}         -> 403
    GET /sites/MLB          -> 403
    GET /categories/{id}    -> 200

O Mercado Livre nao possui API de afiliados (nem geracao de link, nem
relatorio de ganhos) e restringiu os endpoints publicos de busca e de item.
Nao existe, portanto, descoberta automatica de produtos aqui.

O que este modulo entrega dentro dessa limitacao:

- `validar_url_afiliado`: aplica as regras oficiais do programa, que proibem
  link para home, ofertas do dia, busca, categoria, mais vendidos, carrinho,
  checkout, pagina de vendedor, Mercado Shops, video/musica, minhas compras,
  minha conta, favoritos, emprestimos e imoveis/servicos. Gerar link para
  essas paginas nao converte, entao filtrar antes evita trabalho perdido.
- `extrair_item_id`: normaliza os varios formatos de URL do ML.
- importacao de curadoria via CSV completo ou via lista simples de URLs.
- tabela de comissao por categoria, ja que a plataforma nao expoe a taxa.

Scraping do site e automacao de login no portal foram descartados de
proposito: violam os Termos de Uso e colocam a conta de afiliado em risco.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config, resolver
from ..models import Oferta
from ..rede import status_de
from .base import FonteIndisponivel

PLATAFORMA = "mercadolivre"
BASE_API = "https://api.mercadolibre.com"
ENDPOINTS_PROBE = (
    "/sites/MLB/search?q=fone",
    "/items/MLB3567891234",
    "/sites/MLB",
    "/categories/MLB1051",
)
COLUNAS_OBRIGATORIAS = {"item_id", "nome", "preco"}

PADRAO_ITEM_ID = re.compile(r"\bML[A-Z]-?(\d{6,})\b", re.IGNORECASE)
PADRAO_LINK_CURTO = re.compile(r"/sec/[A-Za-z0-9]+")

CAMINHOS_PROIBIDOS: tuple[tuple[str, str], ...] = (
    (r"^/?$", "pagina principal"),
    (r"^/ofertas", "ofertas do dia"),
    (r"^/mais-vendidos", "ranking de mais vendidos"),
    (r"^/c/", "pagina de categoria"),
    (r"^/l/", "pagina de listagem/categoria"),
    (r"^/listado", "resultado de busca"),
    (r"^/perfil/", "pagina de vendedor"),
    (r"^/carrinho", "carrinho de compras"),
    (r"^/checkout", "pagina de pagamento"),
    (r"^/gz/checkout", "pagina de pagamento"),
    (r"^/compras", "minhas compras"),
    (r"^/minha-conta", "minha conta"),
    (r"^/favoritos", "favoritos"),
    (r"^/emprestimos", "emprestimos"),
    (r"^/creditos", "emprestimos"),
    (r"^/play", "video e musica"),
    (r"^/imoveis", "imoveis"),
    (r"^/servicos", "servicos"),
    (r"^/veiculos", "veiculos"),
)
DOMINIOS_PROIBIDOS = ("mercadoshops.com.br", "mercadopago.com.br")
DOMINIOS_VALIDOS = ("mercadolivre.com.br", "mercadolibre.com", "mercadolivre.com")

_log = logging.getLogger(__name__)


def probe(timeout_s: float = 15.0, token: str | None = None) -> dict[str, int | str]:
    """Mede o estado real dos endpoints do ML. Usado pelo comando `doctor`."""
    cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
    return {
        caminho: status_de(BASE_API + caminho, cabecalhos, timeout_s)
        for caminho in ENDPOINTS_PROBE
    }


def extrair_item_id(url_ou_id: str) -> str | None:
    """Extrai o MLB id de URL de produto, URL de catalogo ou do proprio id."""
    texto = (url_ou_id or "").strip()
    if not texto:
        return None
    encontrado = PADRAO_ITEM_ID.search(texto)
    if encontrado:
        prefixo = texto[encontrado.start():encontrado.start() + 3].upper()
        return f"{prefixo}{encontrado.group(1)}"
    if PADRAO_LINK_CURTO.search(texto):
        return urlparse(texto).path.rsplit("/", 1)[-1]
    return None


def validar_url_afiliado(url: str) -> tuple[bool, str]:
    """Aplica as regras oficiais de elegibilidade de link do programa."""
    texto = (url or "").strip()
    if not texto:
        return False, "url vazia"

    partes = urlparse(texto if "//" in texto else f"https://{texto}")
    dominio = (partes.netloc or "").lower().removeprefix("www.")
    if not dominio:
        return False, "url sem dominio"
    if any(proibido in dominio for proibido in DOMINIOS_PROIBIDOS):
        return False, f"dominio nao elegivel: {dominio}"
    if not any(dominio.endswith(valido) for valido in DOMINIOS_VALIDOS):
        return False, f"dominio fora do Mercado Livre: {dominio}"

    caminho = partes.path or "/"
    if PADRAO_LINK_CURTO.search(caminho):
        return True, "link curto de afiliado"

    consulta = parse_qs(partes.query)
    if "q" in consulta or "search" in consulta:
        return False, "resultado de busca nao e elegivel"

    for padrao, motivo in CAMINHOS_PROIBIDOS:
        if re.search(padrao, caminho, re.IGNORECASE):
            return False, f"{motivo} nao e elegivel para link de afiliado"

    if extrair_item_id(texto) is None:
        return False, "nao parece uma pagina de produto (sem id MLB)"
    return True, "pagina de produto elegivel"


def comissao_para(categoria: str | None, cfg_ml) -> float:
    """Taxa por categoria; o ML nao expoe comissao, entao vem da config."""
    if categoria:
        chave = categoria.strip().lower()
        if chave in cfg_ml.comissao_por_categoria:
            return float(cfg_ml.comissao_por_categoria[chave])
    return float(cfg_ml.taxa_comissao_padrao)


def _flutuante(valor, padrao: float | None = None) -> float | None:
    if valor is None or str(valor).strip() == "":
        return padrao
    try:
        return float(str(valor).strip().replace("R$", "").replace(",", "."))
    except ValueError:
        return padrao


def _detectar_dialeto(amostra: str) -> str:
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t").delimiter
    except csv.Error:
        return ";" if amostra.count(";") > amostra.count(",") else ","


def ler_csv_curado(caminho: Path, cfg_ml) -> list[Oferta]:
    """Le a curadoria manual e a normaliza no mesmo modelo da Shopee.

    Colunas: item_id, nome, preco (obrigatorias); taxa_comissao, categoria,
    vendas, rating, desconto_pct, link_oferta, link_produto, loja_nome.
    Aceita `,` ou `;` como separador e virgula decimal.
    """
    if not caminho.exists():
        raise FonteIndisponivel(f"arquivo de curadoria nao encontrado: {caminho}")

    bruto = caminho.read_text(encoding="utf-8-sig")
    if not bruto.strip():
        return []
    delimitador = _detectar_dialeto(bruto.splitlines()[0])
    leitor = csv.DictReader(bruto.splitlines(), delimiter=delimitador)

    colunas = {c.strip() for c in (leitor.fieldnames or [])}
    faltando = COLUNAS_OBRIGATORIAS - colunas
    if faltando:
        raise FonteIndisponivel(
            f"{caminho.name} sem as colunas obrigatorias: {sorted(faltando)}"
        )

    ofertas = []
    for numero, linha in enumerate(leitor, start=2):
        linha = {(k or "").strip(): v for k, v in linha.items()}
        preco = _flutuante(linha.get("preco"))
        item_id = (linha.get("item_id") or "").strip()
        if not item_id or preco is None:
            _log.warning("%s linha %s ignorada: item_id ou preco invalido",
                         caminho.name, numero)
            continue
        taxa = _flutuante(linha.get("taxa_comissao"))
        if taxa is None:
            taxa = comissao_para(linha.get("categoria"), cfg_ml)
        link = (linha.get("link_oferta") or "").strip() or None
        ofertas.append(Oferta(
            plataforma=PLATAFORMA,
            item_id=item_id,
            nome=(linha.get("nome") or item_id).strip(),
            preco=preco,
            taxa_comissao=taxa,
            origem_consulta="curadoria_csv",
            loja_nome=(linha.get("loja_nome") or "").strip() or None,
            vendas=int(_flutuante(linha.get("vendas"), 0) or 0),
            rating=_flutuante(linha.get("rating")),
            desconto_pct=_flutuante(linha.get("desconto_pct"), 0.0),
            link_oferta=link,
            link_produto=(linha.get("link_produto") or "").strip() or None,
            categoria_ids=((linha.get("categoria") or "").strip(),)
            if linha.get("categoria") else (),
        ))
    return ofertas


def ler_lista_urls(caminho: Path, cfg_ml) -> tuple[list[Oferta], list[tuple[str, str]]]:
    """Le um .txt com uma URL por linha (formato `url[;preco[;categoria]]`).

    Retorna (ofertas, rejeitadas) -- rejeitadas traz o motivo da inelegibilidade
    para o usuario nao perder tempo gerando link que o ML nao aceita.
    """
    if not caminho.exists():
        raise FonteIndisponivel(f"lista de urls nao encontrada: {caminho}")

    ofertas: list[Oferta] = []
    rejeitadas: list[tuple[str, str]] = []
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        partes = [p.strip() for p in linha.split(";")]
        url = partes[0]
        elegivel, motivo = validar_url_afiliado(url)
        if not elegivel:
            rejeitadas.append((url, motivo))
            continue
        item_id = extrair_item_id(url) or url.rsplit("/", 1)[-1]
        preco = _flutuante(partes[1]) if len(partes) > 1 else None
        categoria = partes[2] if len(partes) > 2 else None
        if preco is None:
            rejeitadas.append((url, "sem preco (o ML nao expoe preco via API)"))
            continue
        ofertas.append(Oferta(
            plataforma=PLATAFORMA,
            item_id=item_id,
            nome=item_id,
            preco=preco,
            taxa_comissao=comissao_para(categoria, cfg_ml),
            origem_consulta="curadoria_urls",
            link_produto=url,
            link_oferta=url,
            categoria_ids=(categoria,) if categoria else (),
        ))
    return ofertas, rejeitadas


class FonteMercadoLivre:
    nome = PLATAFORMA

    def _fontes_de_entrada(self, cfg: Config) -> list[Path]:
        candidatos = [
            resolver(cfg.mercadolivre.caminho_curadoria),
            resolver(cfg.mercadolivre.caminho_urls),
        ]
        return [c for c in candidatos if c.exists()]

    def disponivel(self, cfg: Config) -> tuple[bool, str]:
        entradas = self._fontes_de_entrada(cfg)
        if entradas:
            return True, "curadoria manual: " + ", ".join(e.name for e in entradas)
        return False, (
            "sem API de afiliados e endpoints publicos em 403. Para incluir o ML, "
            f"crie {resolver(cfg.mercadolivre.caminho_curadoria)} "
            f"ou {resolver(cfg.mercadolivre.caminho_urls)}"
        )

    def diagnosticar(self, cfg: Config) -> dict:
        return {"probe": probe(cfg.coleta.timeout_s, cfg.mercadolivre.access_token)}

    def coletar(self, cfg: Config, cache=None) -> list[Oferta]:
        pode, motivo = self.disponivel(cfg)
        if not pode:
            raise FonteIndisponivel(motivo)

        ofertas: list[Oferta] = []
        csv_path = resolver(cfg.mercadolivre.caminho_curadoria)
        if csv_path.exists():
            ofertas.extend(ler_csv_curado(csv_path, cfg.mercadolivre))

        urls_path = resolver(cfg.mercadolivre.caminho_urls)
        if urls_path.exists():
            novas, rejeitadas = ler_lista_urls(urls_path, cfg.mercadolivre)
            ofertas.extend(novas)
            for url, razao in rejeitadas:
                _log.warning("url rejeitada (%s): %s", razao, url)
        return ofertas
