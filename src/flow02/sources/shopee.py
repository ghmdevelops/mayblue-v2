"""Adapter da Shopee Affiliate Open API (GraphQL).

Autenticacao por assinatura, sem login:
    Authorization: SHA256 Credential={AppId}, Timestamp={ts},
                   Signature=SHA256(AppId + Timestamp + Payload + Secret)

O payload assinado precisa ser byte-a-byte identico ao corpo enviado, por isso
o JSON e serializado uma unica vez e reaproveitado.

Operacoes cobertas:
    productOfferV2     descoberta de produtos (retorno potencial)
    shopeeOfferV2      campanhas e colecoes
    conversionReport   cliques, pedidos e comissao estimada (retorno realizado)
    validatedReport    comissao definitiva, paginada por scrollId (30s de vida)
    generateShortLink  link curto rastreado, com ate 5 subIds

AVISO sobre nomes de campo: a documentacao publica da Shopee mostra exemplos
genericos e diverge do playground em alguns pontos. Por isso toda selecao de
campos vem do config.toml e o parsing usa aliases tolerantes. Se a API
responder 10010 (parsing error), rode `flow02 doctor` -- ele imprime a query
exata enviada e o campo recusado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable, Sequence

from ..config import Config, ConsultaConfig
from ..models import Conversao, Oferta
from ..rede import ErroHTTP, requisitar
from ..tempo import iso_utc
from .base import FonteIndisponivel, deduplicar

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
PLATAFORMA = "shopee"

CODIGO_SISTEMA = 10000
CODIGO_PARSING = 10010
CODIGO_AUTENTICACAO = 10020
CODIGO_RATE_LIMIT = 10030
CODIGO_SEM_ACESSO = 10035
CODIGO_NEGOCIO = 11000
CODIGO_PARAMETROS = 11001
CODIGO_VINCULO_CONTA = 11002

FATAIS = frozenset({
    CODIGO_PARSING, CODIGO_AUTENTICACAO, CODIGO_SEM_ACESSO,
    CODIGO_PARAMETROS, CODIGO_VINCULO_CONTA,
})
CAMPOS_PAGE_INFO = ("page", "limit", "hasNextPage")
MAX_SUB_IDS = 5
LIMITE_VALIDADO = 500

ALIAS_CONVERSAO_ID = ("conversionId", "id", "orderId", "checkoutId")
ALIAS_CLIQUES = ("clicks", "clickCount", "totalClicks")
ALIAS_PEDIDOS = ("orders", "orderCount", "purchases", "totalOrders")
ALIAS_COMISSAO = ("estimatedCommission", "totalCommission", "commission",
                  "finalCommission", "netCommission")
ALIAS_ITEM = ("itemId", "productId")
ALIAS_LOJA = ("shopId", "sellerId")
ALIAS_MOMENTO = ("purchaseTime", "clickTime", "orderTime", "date", "conversionTime")
ALIAS_STATUS = ("status", "orderStatus", "purchaseStatus")

_log = logging.getLogger(__name__)


class ErroShopee(RuntimeError):
    def __init__(self, codigo: int | None, mensagem: str, query: str = "") -> None:
        super().__init__(f"shopee [{codigo}]: {mensagem}")
        self.codigo = codigo
        self.mensagem = mensagem
        self.query = query

    @property
    def fatal(self) -> bool:
        return self.codigo in FATAIS


def assinar(app_id: str, secret: str, payload: str, timestamp: int) -> str:
    base = f"{app_id}{timestamp}{payload}{secret}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def montar_header(app_id: str, secret: str, payload: str, timestamp: int) -> str:
    assinatura = assinar(app_id, secret, payload, timestamp)
    return (
        f"SHA256 Credential={app_id}, Timestamp={timestamp}, "
        f"Signature={assinatura}"
    )


def _fmt_arg(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, (list, tuple)):
        return "[" + ", ".join(_fmt_arg(v) for v in valor) + "]"
    return json.dumps(str(valor), ensure_ascii=False)


def _argumentos(pares: dict) -> str:
    usados = {k: v for k, v in pares.items() if v is not None}
    return ", ".join(f"{k}: {_fmt_arg(v)}" for k, v in usados.items())


def montar_query_product_offer(
    consulta: ConsultaConfig, pagina: int, limite: int, campos: Iterable[str]
) -> str:
    args = _argumentos({
        "page": pagina,
        "limit": limite,
        "sortType": consulta.sort_type,
        "listType": consulta.list_type,
        "matchId": consulta.match_id,
        "keyword": consulta.keyword or None,
        "productCatId": consulta.product_cat_id,
        "isAMSOffer": consulta.is_ams_offer,
        "isKeySeller": consulta.is_key_seller,
    })
    return (
        f"query {{ productOfferV2({args}) "
        f"{{ nodes {{ {' '.join(campos)} }} "
        f"pageInfo {{ {' '.join(CAMPOS_PAGE_INFO)} }} }} }}"
    )


def montar_query_shopee_offer(
    pagina: int, limite: int, campos: Iterable[str],
    sort_type: int = 2, keyword: str | None = None,
) -> str:
    args = _argumentos({
        "page": pagina, "limit": limite,
        "sortType": sort_type, "keyword": keyword or None,
    })
    return (
        f"query {{ shopeeOfferV2({args}) "
        f"{{ nodes {{ {' '.join(campos)} }} "
        f"pageInfo {{ {' '.join(CAMPOS_PAGE_INFO)} }} }} }}"
    )


def montar_query_conversion_report(
    limite: int, campos: Iterable[str],
    inicio: int | None = None, fim: int | None = None,
    scroll_id: str | None = None,
) -> str:
    """Relatorio de conversao.

    Verificado por introspecao: NAO existe argumento `page`. A paginacao e
    por cursor (`scrollId`), igual ao validatedReport.
    """
    args = _argumentos({
        "limit": limite,
        "purchaseTimeStart": inicio, "purchaseTimeEnd": fim,
        "scrollId": scroll_id,
    })
    return (
        f"query {{ conversionReport({args}) "
        f"{{ nodes {{ {' '.join(campos)} }} "
        f"pageInfo {{ {' '.join(CAMPOS_PAGE_INFO)} }} }} }}"
    )


def montar_query_validated_report(
    campos: Iterable[str], validation_id: int,
    limite: int = LIMITE_VALIDADO, scroll_id: str | None = None,
) -> str:
    """Relatorio validado de UM lote de validacao.

    `validationId` e obrigatorio (Int64!) -- nao existe listagem geral. O id
    de cada lote aparece no extrato do portal do afiliado, em Pagamentos.
    """
    args = _argumentos({
        "validationId": validation_id, "limit": limite, "scrollId": scroll_id,
    })
    return (
        f"query {{ validatedReport({args}) "
        f"{{ nodes {{ {' '.join(campos)} }} "
        f"pageInfo {{ {' '.join(CAMPOS_PAGE_INFO)} }} }} }}"
    )


CAMPOS_LOJA = (
    "shopId", "shopName", "commissionRate", "offerLink", "originalLink",
    "imageUrl", "ratingStar", "shopType", "remainingBudget",
    "sellerCommCoveRatio", "periodEndTime",
)


def montar_query_shop_offer(
    pagina: int, limite: int, campos: Iterable[str],
    sort_type: int = 2, so_selecionadas: bool | None = None,
) -> str:
    """Ofertas por LOJA. Complementa a busca por produto: loja que paga bem
    de forma consistente rende mais que cacar item isolado todo dia."""
    args = _argumentos({
        "sortType": sort_type, "page": pagina, "limit": limite,
        "isKeySeller": so_selecionadas,
    })
    return (
        f"query {{ shopOfferV2({args}) "
        f"{{ nodes {{ {' '.join(campos)} }} "
        f"pageInfo {{ {' '.join(CAMPOS_PAGE_INFO)} }} }} }}"
    )


def montar_mutation_batch_short_link(
    urls: Sequence[str], sub_ids: Sequence[str] = ()
) -> str:
    """Encurta varios links numa chamada so.

    Confirmado por introspecao: `input: BatchShortLinkInput` com o campo
    `links`, que e uma lista de ShortLinkInput. Uma chamada em vez de N
    reduz o tempo da geracao em lote e o consumo de cota.
    """
    if len(sub_ids) > MAX_SUB_IDS:
        raise ValueError(f"a Shopee aceita no maximo {MAX_SUB_IDS} subIds")
    lista = ", ".join(
        "{{ originUrl: {}{} }}".format(
            _fmt_arg(url),
            f", subIds: {_fmt_arg(list(sub_ids))}" if sub_ids else "",
        )
        for url in urls
    )
    return (
        f"mutation {{ generateBatchShortLink(input: {{ links: [{lista}] }}) "
        f"{{ shortLinks total successCount }} }}"
    )


def montar_mutation_short_link(url_origem: str, sub_ids: Sequence[str] = ()) -> str:
    """A mutation recebe um objeto `input: ShortLinkInput`, nao args soltos."""
    if len(sub_ids) > MAX_SUB_IDS:
        raise ValueError(f"a Shopee aceita no maximo {MAX_SUB_IDS} subIds")
    campos = {"originUrl": url_origem}
    if sub_ids:
        campos["subIds"] = list(sub_ids)
    entrada = ", ".join(f"{k}: {_fmt_arg(v)}" for k, v in campos.items())
    return (
        f"mutation {{ generateShortLink(input: {{ {entrada} }}) "
        f"{{ shortLink }} }}"
    )


class ClienteShopee:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        timeout_s: float = 30.0,
        tentativas_max: int = 4,
        pausa_s: float = 1.0,
        endpoint: str = ENDPOINT,
    ) -> None:
        self._app_id = app_id
        self._secret = app_secret
        self._timeout = timeout_s
        self._tentativas = max(1, tentativas_max)
        self._pausa = pausa_s
        self._endpoint = endpoint

    def _post(self, payload: str) -> dict:
        timestamp = int(time.time())
        resposta = requisitar(
            self._endpoint,
            dados=payload.encode("utf-8"),
            metodo="POST",
            timeout_s=self._timeout,
            cabecalhos={
                "Content-Type": "application/json",
                "Authorization": montar_header(
                    self._app_id, self._secret, payload, timestamp
                ),
            },
        )
        return resposta.json()

    def executar(self, query: str) -> dict:
        payload = json.dumps({"query": query}, ensure_ascii=False, separators=(",", ":"))
        espera = self._pausa
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self._tentativas + 1):
            try:
                corpo = self._post(payload)
            except (ErroHTTP, TimeoutError, OSError, json.JSONDecodeError) as exc:
                ultimo_erro = exc
                _log.warning("falha de rede (tentativa %s/%s): %s",
                             tentativa, self._tentativas, exc)
            else:
                erros = corpo.get("errors") or []
                if not erros:
                    return corpo.get("data") or {}
                codigo, mensagem = _primeiro_erro(erros)
                erro = ErroShopee(codigo, mensagem, query)
                if erro.fatal:
                    raise erro
                ultimo_erro = erro
                _log.warning("erro recuperavel (tentativa %s/%s): %s",
                             tentativa, self._tentativas, erro)

            if tentativa < self._tentativas:
                time.sleep(espera)
                espera *= 2

        raise ErroShopee(
            None, f"esgotadas {self._tentativas} tentativas", query
        ) from ultimo_erro

    def paginar(
        self, construir_query, limite: int, paginas: int, pausa_s: float,
        raiz: str, cache=None, nome_cache: str | None = None,
    ) -> list[dict]:
        """Percorre paginas ate acabar, respeitando hasNextPage e o limite.

        Com `cache`, cada pagina obtida e gravada antes da proxima e uma
        pagina ja guardada nao e pedida de novo. Numa rede que derruba o
        processo no meio, isso e a diferenca entre retomar e recomecar.
        """
        acumulado: list[dict] = []
        chave = nome_cache or raiz
        for pagina in range(1, paginas + 1):
            if cache is not None:
                guardada = cache.obter(chave, pagina)
                if guardada is not None:
                    acumulado.extend(guardada)
                    # Pagina cheia sugere que ha mais; incompleta encerra.
                    if len(guardada) < limite:
                        break
                    continue

            dados = self.executar(construir_query(pagina))
            bloco = dados.get(raiz)
            nodes = extrair_nodes(bloco)
            if cache is not None:
                cache.guardar(chave, pagina, nodes)
            acumulado.extend(nodes)
            _log.info("%s pagina=%s nodes=%s", raiz, pagina, len(nodes))
            info = (bloco or {}).get("pageInfo") or {}
            if info.get("hasNextPage") is False or len(nodes) < limite:
                break
            if pagina < paginas:
                time.sleep(pausa_s)
        return acumulado

    def gerar_link_curto(self, url_origem: str, sub_ids: Sequence[str] = ()) -> str:
        dados = self.executar(montar_mutation_short_link(url_origem, sub_ids))
        resultado = dados.get("generateShortLink") or {}
        link = resultado.get("shortLink")
        if not link:
            raise ErroShopee(None, f"generateShortLink nao retornou link: {dados}")
        return link

    def rolar(
        self, construir_query, raiz: str, paginas_max: int = 10
    ) -> list[dict]:
        """Paginacao por cursor (`scrollId`), usada nos relatorios.

        O scrollId vale poucos segundos, entao nao ha pausa entre paginas.
        """
        acumulado: list[dict] = []
        scroll_id: str | None = None
        for _ in range(max(1, paginas_max)):
            dados = self.executar(construir_query(scroll_id))
            bloco = dados.get(raiz)
            nodes = extrair_nodes(bloco)
            acumulado.extend(nodes)
            _log.info("%s scroll nodes=%s", raiz, len(nodes))
            info = (bloco or {}).get("pageInfo") or {}
            scroll_id = info.get("scrollId")
            if not info.get("hasNextPage") or not scroll_id:
                break
        return acumulado

    def relatorio_validado(
        self, campos: Sequence[str], validation_id: int, paginas_max: int = 10
    ) -> list[dict]:
        return self.rolar(
            lambda cursor: montar_query_validated_report(
                campos, validation_id, LIMITE_VALIDADO, cursor
            ),
            "validatedReport", paginas_max,
        )


def _primeiro_erro(erros: list[dict]) -> tuple[int | None, str]:
    primeiro = erros[0] or {}
    extensoes = primeiro.get("extensions") or {}
    codigo = extensoes.get("code")
    mensagem = (extensoes.get("message") or extensoes.get("details")
                or primeiro.get("message") or "erro desconhecido")
    valido = isinstance(codigo, (int, str)) and str(codigo).isdigit()
    return (int(codigo) if valido else None), str(mensagem)


def extrair_nodes(bloco: dict | None) -> list[dict]:
    """Aceita tanto `nodes` quanto o formato Relay `edges { node }`.

    A documentacao publica da Shopee mostra os dois formatos em lugares
    diferentes, entao os dois sao tolerados em vez de assumir um.
    """
    if not bloco:
        return []
    if isinstance(bloco.get("nodes"), list):
        return [n for n in bloco["nodes"] if isinstance(n, dict)]
    arestas = bloco.get("edges")
    if isinstance(arestas, list):
        return [a["node"] for a in arestas
                if isinstance(a, dict) and isinstance(a.get("node"), dict)]
    return []


def _primeiro_de(no: dict, chaves: Sequence[str]):
    for chave in chaves:
        if no.get(chave) not in (None, ""):
            return no[chave]
    return None


def _flutuante(valor, padrao: float | None = None) -> float | None:
    if valor is None or valor == "":
        return padrao
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def _inteiro(valor, padrao: int | None = None) -> int | None:
    convertido = _flutuante(valor)
    return int(convertido) if convertido is not None else padrao


def para_oferta(no: dict, origem_consulta: str) -> Oferta | None:
    item_id = no.get("itemId") or no.get("id")
    preco = _flutuante(no.get("priceMin") or no.get("price"))
    if item_id is None or preco is None:
        return None
    categorias = no.get("productCatIds") or []
    if not isinstance(categorias, list):
        categorias = [categorias]
    return Oferta(
        plataforma=PLATAFORMA,
        item_id=str(item_id),
        nome=str(no.get("productName") or no.get("name") or f"item {item_id}"),
        preco=preco,
        taxa_comissao=_flutuante(no.get("commissionRate"), 0.0) or 0.0,
        origem_consulta=origem_consulta,
        loja_id=str(no["shopId"]) if no.get("shopId") is not None else None,
        loja_nome=no.get("shopName"),
        vendas=_inteiro(no.get("sales"), 0),
        rating=_flutuante(no.get("ratingStar")),
        desconto_pct=_flutuante(no.get("priceDiscountRate"), 0.0),
        link_oferta=no.get("offerLink"),
        link_produto=no.get("productLink"),
        imagem=no.get("imageUrl"),
        expira_em=_para_data(no.get("periodEndTime")),
        taxa_vendedor=_flutuante(no.get("sellerCommissionRate")),
        taxa_shopee=_flutuante(no.get("shopeeCommissionRate")),
        comissao_api=_flutuante(no.get("commission")),
        categoria_ids=tuple(str(c) for c in categorias),
    )


def _para_data(valor) -> str | None:
    """Timestamp unix da Shopee -> YYYY-MM-DD."""
    if valor in (None, "", 0):
        return None
    if isinstance(valor, (int, float)) or str(valor).isdigit():
        return time.strftime("%Y-%m-%d", time.gmtime(int(valor)))
    return str(valor)


def para_conversoes(no: dict, validada: bool = False) -> list[Conversao]:
    """Achata uma conversao em uma linha por PRODUTO vendido.

    A estrutura real e aninhada: conversao -> orders[] -> items[]. A comissao
    que interessa para ranquear produto e `itemTotalCommission`, dentro do
    item; a do nivel de cima e o total do pedido.

    `cliques` fica em 0 de proposito: a API de afiliados da Shopee NAO expoe
    numero de cliques em lugar nenhum (confirmado por introspecao -- so
    existem relatorios de oferta, conversao e pedido). Por isso nao da para
    medir taxa de conversao; o que da para medir e comissao realizada.
    """
    identificador = _primeiro_de(no, ALIAS_CONVERSAO_ID)
    if identificador is None:
        return []

    momento = _para_data(_primeiro_de(no, ALIAS_MOMENTO))
    clicado = _para_data(no.get("clickTime"))
    status = str(_primeiro_de(no, ALIAS_STATUS) or "") or None
    # utmContent carrega os subIds no formato "canal----" (5 posicoes).
    canal = str(no.get("utmContent") or "").strip("-").split("-")[0] or None
    comissao_total = _flutuante(_primeiro_de(no, ALIAS_COMISSAO), 0.0) or 0.0

    pedidos = no.get("orders")
    if isinstance(pedidos, dict):
        pedidos = [pedidos]
    if not isinstance(pedidos, list):
        pedidos = []

    linhas: list[Conversao] = []
    for pedido in pedidos:
        if not isinstance(pedido, dict):
            continue
        itens = pedido.get("items")
        if isinstance(itens, dict):
            itens = [itens]
        for item in itens or []:
            if not isinstance(item, dict):
                continue
            linhas.append(Conversao(
                plataforma=PLATAFORMA,
                conversao_id=f"{identificador}:{item.get('itemId')}",
                cliques=0,
                pedidos=_inteiro(item.get("qty"), 1) or 1,
                comissao=_flutuante(item.get("itemTotalCommission"), 0.0) or 0.0,
                item_id=str(item["itemId"]) if item.get("itemId") is not None else None,
                loja_id=str(item["shopId"]) if item.get("shopId") is not None else None,
                ocorrido_em=momento,
                status=str(pedido.get("orderStatus") or status or "") or None,
                validada=validada,
                pedido_id=str(pedido.get("orderId") or "") or None,
                clicado_em=clicado,
                canal=canal,
            ))

    if linhas:
        return linhas

    # Conversao sem detalhamento de item: guarda o total, sem atribuicao.
    return [Conversao(
        plataforma=PLATAFORMA,
        conversao_id=str(identificador),
        cliques=0,
        pedidos=1,
        comissao=comissao_total,
        ocorrido_em=momento,
        status=status,
        validada=validada,
        clicado_em=clicado,
        canal=canal,
    )]


def para_conversao(no: dict, validada: bool = False) -> Conversao | None:
    """Compatibilidade: devolve so a primeira linha achatada."""
    linhas = para_conversoes(no, validada)
    return linhas[0] if linhas else None


def para_oferta_campanha(no: dict) -> Oferta | None:
    """Converte um shopeeOfferV2 (colecao/categoria) no modelo comum.

    Campanha nao tem preco de produto; o preco fica em 0 e ela nunca compete
    no ranking de EPC -- serve para listagem separada de campanhas ativas.
    """
    link = no.get("offerLink") or no.get("originalLink")
    nome = no.get("offerName")
    if not link or not nome:
        return None
    identificador = no.get("collectionId") or no.get("categoryId") or nome
    return Oferta(
        plataforma=PLATAFORMA,
        item_id=f"campanha:{identificador}",
        nome=str(nome),
        preco=0.0,
        taxa_comissao=_flutuante(no.get("commissionRate"), 0.0) or 0.0,
        origem_consulta="campanha",
        link_oferta=link,
        link_produto=no.get("originalLink"),
        imagem=no.get("imageUrl"),
    )


class FonteShopee:
    nome = PLATAFORMA

    def disponivel(self, cfg: Config) -> tuple[bool, str]:
        if not cfg.credenciais.shopee_ok:
            return False, (
                "SHOPEE_APP_ID / SHOPEE_APP_SECRET ausentes -- gere em "
                "affiliate.shopee.com.br > Open API e coloque no .env"
            )
        return True, "ok"

    def cliente(self, cfg: Config) -> ClienteShopee:
        pode, motivo = self.disponivel(cfg)
        if not pode:
            raise FonteIndisponivel(motivo)
        return ClienteShopee(
            app_id=cfg.credenciais.shopee_app_id,
            app_secret=cfg.credenciais.shopee_app_secret,
            timeout_s=cfg.coleta.timeout_s,
            tentativas_max=cfg.coleta.tentativas_max,
            pausa_s=cfg.coleta.pausa_entre_requisicoes_s,
        )

    def diagnosticar(self, cfg: Config) -> dict:
        """Consulta minima (limit=1) para validar credencial e nomes de campo."""
        cliente = self.cliente(cfg)
        consulta = ConsultaConfig(nome="diagnostico", sort_type=5, list_type=0)
        query = montar_query_product_offer(consulta, 1, 1, cfg.coleta.campos_produto)
        dados = cliente.executar(query)
        nodes = extrair_nodes(dados.get("productOfferV2"))
        return {"query": query, "nodes_recebidos": len(nodes),
                "exemplo": nodes[0] if nodes else None}

    def coletar(self, cfg: Config, cache=None) -> list[Oferta]:
        cliente = self.cliente(cfg)
        limite = cfg.coleta.limite_por_pagina
        pausa = cfg.coleta.pausa_entre_requisicoes_s
        coletadas: list[Oferta] = []

        for consulta in cfg.coleta.consultas:
            nodes = cliente.paginar(
                lambda pagina, c=consulta: montar_query_product_offer(
                    c, pagina, limite, cfg.coleta.campos_produto
                ),
                limite, consulta.paginas, pausa, "productOfferV2",
                cache, f"produto:{consulta.nome}",
            )
            coletadas.extend(
                o for o in (para_oferta(n, consulta.nome) for n in nodes)
                if o is not None
            )

        coletadas.extend(self._varrer_categorias(cliente, cfg, cache))

        if cfg.coleta.coletar_campanhas:
            nodes = cliente.paginar(
                lambda pagina: montar_query_shopee_offer(
                    pagina, limite, cfg.coleta.campos_campanha
                ),
                limite, cfg.coleta.campanhas_paginas, pausa, "shopeeOfferV2",
                cache, "campanhas",
            )
            coletadas.extend(
                o for o in map(para_oferta_campanha, nodes) if o is not None
            )

        return deduplicar(coletadas)

    def _varrer_categorias(self, cliente: ClienteShopee, cfg: Config,
                           cache=None) -> list[Oferta]:
        """Percorre o topo de cada categoria, nao so o ranking global.

        O ranking global e estreito: repete as mesmas categorias grandes. A
        varredura por productCatId acha oferta boa em nicho que o global nunca
        mostra. `requisicoes_max` existe para nao estourar o rate limit 10030.
        """
        varredura = cfg.coleta.varredura
        if not (varredura.habilitada and varredura.categorias):
            return []

        limite = cfg.coleta.limite_por_pagina
        orcamento = varredura.requisicoes_max
        paginas = max(1, varredura.paginas_por_categoria)
        coletadas: list[Oferta] = []

        for categoria in varredura.categorias:
            if orcamento <= 0:
                _log.warning("varredura interrompida: orcamento de requisicoes esgotado")
                break
            consulta = ConsultaConfig(
                nome=f"categoria_{categoria}",
                sort_type=varredura.sort_type,
                product_cat_id=int(categoria),
                paginas=min(paginas, orcamento),
            )
            nodes = cliente.paginar(
                lambda pagina, c=consulta: montar_query_product_offer(
                    c, pagina, limite, cfg.coleta.campos_produto
                ),
                limite, consulta.paginas,
                cfg.coleta.pausa_entre_requisicoes_s, "productOfferV2",
                cache, f"categoria:{categoria}",
            )
            orcamento -= max(1, (len(nodes) + limite - 1) // limite)
            coletadas.extend(
                o for o in (para_oferta(n, consulta.nome) for n in nodes)
                if o is not None
            )
        _log.info("varredura por categoria: %s ofertas em %s categorias",
                  len(coletadas), len(varredura.categorias))
        return coletadas

    def coletar_lojas(self, cfg: Config, paginas: int = 2) -> list[dict]:
        """Ofertas por loja, ja normalizadas.

        `remainingBudget` e o dado que nao existe em produto: quando a verba
        da campanha acaba, a comissao cai. Loja com orcamento no fim e aposta
        ruim para investir tempo.
        """
        cliente = self.cliente(cfg)
        nodes = cliente.paginar(
            lambda pagina: montar_query_shop_offer(
                pagina, cfg.coleta.limite_por_pagina, CAMPOS_LOJA
            ),
            cfg.coleta.limite_por_pagina, paginas,
            cfg.coleta.pausa_entre_requisicoes_s, "shopOfferV2",
        )
        lojas = []
        for no in nodes:
            if not no.get("shopName"):
                continue
            lojas.append({
                "loja_id": str(no.get("shopId") or ""),
                "nome": str(no["shopName"]),
                "taxa": _flutuante(no.get("commissionRate"), 0.0) or 0.0,
                "nota": _flutuante(no.get("ratingStar")),
                "link": no.get("offerLink"),
                "imagem": no.get("imageUrl"),
                "orcamento": _inteiro(no.get("remainingBudget")),
                "cobertura": _flutuante(no.get("sellerCommCoveRatio")),
                "expira_em": _para_data(no.get("periodEndTime")),
            })
        return lojas

    def gerar_links_em_lote(
        self, cfg: Config, urls: Sequence[str], sub_ids: Sequence[str] = ()
    ) -> dict[str, str]:
        """Encurta varios de uma vez, com queda para chamada individual.

        A forma exata do BatchShortLinkInput veio de introspecao, mas se a
        API recusar por qualquer motivo o lote inteiro falharia -- entao cai
        para o caminho individual, que ja e testado em producao.
        """
        if not urls:
            return {}
        cliente = self.cliente(cfg)
        try:
            dados = cliente.executar(
                montar_mutation_batch_short_link(urls, sub_ids)
            )
            curtos = (dados.get("generateBatchShortLink") or {}).get("shortLinks")
            if isinstance(curtos, list) and len(curtos) == len(urls):
                return {url: str(link) for url, link in zip(urls, curtos) if link}
            _log.info("lote respondeu formato inesperado, indo de um em um")
        except ErroShopee as exc:
            _log.info("lote recusado (%s), indo de um em um", exc.codigo)

        return {url: cliente.gerar_link_curto(url, sub_ids) for url in urls}

    def coletar_conversoes(
        self, cfg: Config, inicio: int | None = None, fim: int | None = None
    ) -> list[Conversao]:
        """Retorno REALIZADO, uma linha por produto vendido."""
        cliente = self.cliente(cfg)
        nodes = cliente.rolar(
            lambda cursor: montar_query_conversion_report(
                cfg.coleta.limite_por_pagina, cfg.coleta.campos_conversao,
                inicio, fim, cursor,
            ),
            "conversionReport", cfg.coleta.conversoes_paginas,
        )
        return [c for no in nodes for c in para_conversoes(no)]

    def coletar_validadas(self, cfg: Config) -> list[Conversao]:
        """Comissao definitiva de um lote de validacao especifico.

        Sem `validation_id` nao ha o que consultar: a API exige o id do lote
        e nao oferece listagem geral. Pegue o id no portal do afiliado, em
        Pagamentos, e coloque em [coleta].validation_ids no config.toml.
        """
        ids = cfg.coleta.validation_ids
        if not ids:
            _log.info("validatedReport pulado: nenhum validation_id configurado")
            return []
        cliente = self.cliente(cfg)
        conversoes: list[Conversao] = []
        for validation_id in ids:
            nodes = cliente.relatorio_validado(
                cfg.coleta.campos_validado, int(validation_id)
            )
            conversoes.extend(c for no in nodes for c in para_conversoes(no, True))
        return conversoes

    def gerar_link(
        self, cfg: Config, url: str, sub_ids: Sequence[str] = ()
    ) -> dict[str, str]:
        link = self.cliente(cfg).gerar_link_curto(url, sub_ids)
        return {"url_origem": url, "link_curto": link,
                "sub_ids": ",".join(sub_ids), "gerado_em": iso_utc()}
