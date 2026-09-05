"""Endpoints JSON do painel. So leitura -- escrita passa pelo executor."""

from __future__ import annotations

from datetime import date, timedelta

import json

from .. import analise, calibracao, config, db, oportunidade, variantes
# Apelido: este modulo ja tem uma funcao `categorias` exposta na API, e o
# nome importado seria sombreado por ela.
from .. import categorias as rotulos
from ..scoring import score_historico
from ..sources.shopee import FonteShopee
from ..tempo import dia_brasil

PLATAFORMAS = ("shopee", "mercadolivre")


def _dia(valor: str | None) -> str:
    if not valor or valor == "hoje":
        return dia_brasil()
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        return dia_brasil()


def _plataforma(valor: str | None) -> str | None:
    return valor if valor in PLATAFORMAS else None


def _inteiro(valor: str | None, padrao: int) -> int:
    try:
        return int(valor) if valor is not None else padrao
    except (TypeError, ValueError):
        return padrao


def estado(cfg: config.Config) -> dict:
    with db.conectar(cfg.caminho_banco) as conexao:
        dias = db.dias_disponiveis(conexao)
        calibrador = calibracao.carregar(conexao, cfg.calibracao)
        total = conexao.execute(
            "SELECT COUNT(*) c FROM oferta_dia"
        ).fetchone()["c"]
        conversoes = conexao.execute(
            "SELECT COUNT(*) c FROM conversao"
        ).fetchone()["c"]
        ultimas = [
            dict(linha) for linha in conexao.execute(
                "SELECT plataforma, iniciado_em, finalizado_em, ofertas_salvas, erro "
                "FROM execucao ORDER BY id DESC LIMIT 5"
            )
        ]
    with db.conectar(cfg.caminho_banco) as conexao:
        coleta = _diagnostico_coleta(conexao, dia_brasil())

    return {
        "versao": config.RAIZ.name,
        "hoje": dia_brasil(),
        "coleta": coleta,
        "dias": dias,
        "total_ofertas": total,
        "total_conversoes": conversoes,
        "escopos_calibrados": len(calibrador.tabela),
        "banco": str(cfg.caminho_banco),
        "integracoes": {
            "shopee": cfg.credenciais.shopee_ok,
            "telegram": cfg.telegram.configurado,
            "firebase": cfg.firebase.configurado,
        },
        "ranking": {"top": cfg.ranking.top, "max_por_loja": cfg.ranking.max_por_loja},
        "execucoes": ultimas,
    }


def top(cfg: config.Config, parametros: dict) -> dict:
    dia = _dia(parametros.get("dia"))
    plataforma = _plataforma(parametros.get("plataforma"))
    limite = max(1, min(_inteiro(parametros.get("limite"), cfg.ranking.top), 500))
    busca = (parametros.get("busca") or "").strip().lower()
    diversificar = parametros.get("diversificar") != "0"

    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ranking_dia(
            conexao, dia, 100_000,
            cfg.ranking.max_por_loja if diversificar else 0, plataforma,
            parametros.get("ordenar") or "epc",
        )
        links = db.buscar_links(conexao, "shopee", "")
        desde_preco = (date.fromisoformat(dia)
                       - timedelta(days=cfg.preco.janela_dias)).isoformat()
        series = db.serie_de_precos(conexao, plataforma, desde_preco)
        datadas = db.serie_de_precos_datada(conexao, plataforma, desde_preco)
        curados = db.esta_na_vitrine(conexao)
        velocidades = db.velocidade_vendas(conexao, dia, plataforma)

    if busca:
        linhas = [l for l in linhas if busca in (l["nome"] or "").lower()
                  or busca in (l["loja_nome"] or "").lower()]

    linhas = _filtrar(linhas, parametros, curados)

    categoria = (parametros.get("categoria") or "").strip()
    if categoria:
        linhas = [l for l in linhas
                  if categoria in (l["categoria_ids"] or "").split(",")]

    # "Mais vendidos" com recorte de periodo so existe comparando snapshots:
    # o campo da API e acumulado. Sem a segunda ponta, nao ha o que subtrair.
    ordenacao = parametros.get("ordenar") or "epc"
    janela = _inteiro(parametros.get("janela"), 0)
    if ordenacao == "tendencia" and janela <= 0:
        janela = JANELA_TENDENCIA

    periodo = {}
    if janela > 0:
        with db.conectar(cfg.caminho_banco) as conexao:
            periodo = db.vendas_no_periodo(conexao, dia, janela, plataforma)
        if periodo:
            linhas = [l for l in linhas
                      if (l["plataforma"], l["item_id"]) in periodo]
            chave_ordem = (_forca_da_tendencia if ordenacao == "tendencia"
                           else lambda p: p["delta"])
            linhas.sort(key=lambda l: -chave_ordem(
                periodo[(l["plataforma"], l["item_id"])]))

    # Monta um pouco alem do limite: o filtro de oportunidade descarta itens,
    # e cortar antes deixaria a lista mais curta do que o pedido. O teto
    # evita percorrer o catalogo inteiro quando nao ha filtro.
    teto = min(len(linhas), max(limite * 8, 200))

    itens = []
    for posicao, linha in enumerate(linhas[:teto], start=1):
        chave = (linha["plataforma"], linha["item_id"])
        desconto = analise.avaliar_desconto(
            linha["preco"], linha["desconto_pct"], series.get(chave, []), cfg.preco,
        )
        posicao_preco = analise.avaliar_posicao_preco(
            linha["preco"], datadas.get(chave, []), cfg.preco.dias_minimos,
        )
        itens.append({
            "posicao": posicao,
            "plataforma": linha["plataforma"],
            "item_id": linha["item_id"],
            "nome": linha["nome"],
            "preco": linha["preco"],
            "taxa_comissao": linha["taxa_comissao"],
            "comissao_valor": linha["comissao_valor"],
            "epc": linha["score"],
            "cvr": linha["cvr_estimado"],
            "calibrado": bool(linha["calibrado"]),
            "vendas": linha["vendas"],
            "rating": linha["rating"],
            "loja": linha["loja_nome"],
            "imagem": linha["imagem"],
            "desconto_declarado": linha["desconto_pct"],
            "desconto_real": desconto.desconto_real,
            "desconto_inflado": analise.desconto_inflado(desconto, cfg.preco),
            "desconto_confiavel": desconto.confiavel,
            "na_vitrine": chave in curados,
            "expira_em": linha["expira_em"],
            "dias_ate_expirar": _dias_ate(linha["expira_em"], dia),
            "velocidade": velocidades.get(chave),
            "taxa_vendedor": linha["taxa_vendedor"],
            "taxa_shopee": linha["taxa_shopee"],
            "comissao_api": linha["comissao_api"],
            "fragilidade": _fragilidade(linha),
            "ritmo": _ritmo_da_oferta(linha, dia),
            "no_periodo": periodo.get(chave),
            "tendencia": (round(_forca_da_tendencia(periodo[chave]) * 100, 1)
                          if chave in periodo else None),
            "menor_preco": posicao_preco.e_minimo,
            "preco_minimo": posicao_preco.minimo,
            "dia_minimo": posicao_preco.dia_minimo,
            "dias_preco": posicao_preco.dias_observados,
            "link": links.get(linha["item_id"]) or linha["link_oferta"]
            or linha["link_produto"],
            "link_produto": linha["link_produto"] or linha["link_oferta"],
        })
    # O corte de EPC depende da distribuicao do dia, entao so da para avaliar
    # depois de montar a lista inteira.
    corte_epc = oportunidade.corte_de_epc(itens)
    for item in itens:
        aval = oportunidade.avaliar_oportunidade(item, corte_epc)
        item["oportunidade"] = {
            "forte": aval.forte,
            "quase": aval.quase,
            "atendidos": aval.atendidos,
            "total": aval.total,
            "motivos": list(aval.motivos),
            "impedimentos": list(aval.impedimentos),
        }

    # Agrupar depois da avaliacao: o melhor do grupo pode nao ser o primeiro.
    escondidas = 0
    if parametros.get("agrupar_variantes") in ("1", "true", "sim"):
        itens, escondidas = variantes.agrupar(itens)

    fortes = sum(1 for i in itens if i["oportunidade"]["forte"])
    quases = sum(1 for i in itens if i["oportunidade"]["quase"])
    escolha = parametros.get("so_oportunidades")
    if escolha in ("1", "true", "sim"):
        itens = [i for i in itens if i["oportunidade"]["forte"]]
    elif escolha == "quase":
        itens = [i for i in itens
                 if i["oportunidade"]["forte"] or i["oportunidade"]["quase"]]

    return {"dia": dia, "total": len(linhas), "oportunidades": fortes,
            "quase_oportunidades": quases, "variantes_escondidas": escondidas,
            "janela": janela, "com_periodo": len(periodo),
            "itens": itens[:limite]}


def lista_categorias(cfg: config.Config, parametros: dict) -> dict:
    """Categorias vistas na coleta, com rotulo derivado dos nomes."""
    dia = _dia(parametros.get("dia"))
    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = conexao.execute(
            "SELECT categoria_ids, nome, comissao_valor FROM oferta_dia "
            "WHERE dia = ?", (dia,),
        ).fetchall()
        dias = db.dias_de_historico(conexao)
    return {
        "dia": dia,
        "dias_de_historico": dias,
        "itens": rotulos.resumir(linhas),
    }


def alertas(cfg: config.Config, parametros: dict) -> dict:
    dia = _dia(parametros.get("dia"))
    plataforma = _plataforma(parametros.get("plataforma"))
    with db.conectar(cfg.caminho_banco) as conexao:
        anterior = db.dia_anterior_a(conexao, dia)
        if anterior is None:
            return {"dia": dia, "anterior": None, "itens": []}
        atuais = db.ofertas_do_dia(conexao, dia, plataforma)
        antigos = db.ofertas_do_dia(conexao, anterior, plataforma)

    detectados = analise.detectar_alertas(atuais, antigos, cfg.alertas)
    return {
        "dia": dia,
        "anterior": anterior,
        "itens": [
            {
                "tipo": a.tipo, "rotulo": a.rotulo, "nome": a.nome,
                "plataforma": a.plataforma, "item_id": a.item_id,
                "antes": a.antes, "depois": a.depois,
                "variacao": a.variacao, "link": a.link,
            }
            for a in detectados
        ],
    }


def ganhos(cfg: config.Config, parametros: dict) -> dict:
    dias = _inteiro(parametros.get("dias"), 30)
    desde = (date.today() - timedelta(days=dias)).isoformat() if dias > 0 else None
    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ganhos(conexao, desde, _plataforma(parametros.get("plataforma")))

    itens = [
        {
            "dia": l["dia"], "plataforma": l["plataforma"],
            "cliques": l["cliques"] or 0, "pedidos": l["pedidos"] or 0,
            "comissao": l["comissao"] or 0.0, "validada": bool(l["validada"]),
        }
        for l in linhas
    ]
    return {
        "itens": itens,
        "totais": {
            "cliques": sum(i["cliques"] for i in itens),
            "pedidos": sum(i["pedidos"] for i in itens),
            "comissao": sum(i["comissao"] for i in itens),
        },
    }


def compartilhados(cfg: config.Config, parametros: dict) -> dict:
    """O que voce divulgou, com o que aconteceu depois."""
    dias = _inteiro(parametros.get("dias"), 30)
    desde = ((date.today() - timedelta(days=dias)).isoformat()
             if dias > 0 else None)

    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.compartilhados(conexao, desde)
        vendas_canal = db.vendas_por_canal(conexao, desde)
        esperas = db.tempo_ate_comprar(conexao)
        tem_cliques = bool(conexao.execute(
            "SELECT 1 FROM clique LIMIT 1").fetchone())

    itens = [
        {
            "item_id": l["item_id"],
            "nome": l["nome"] or l["item_id"],
            "imagem": l["imagem"],
            "canal": l["canal"] or "link simples",
            "link": l["link_curto"],
            "gerado_em": l["gerado_em"],
            "cliques": l["cliques"],
            "ultimo_clique": l["ultimo_clique"],
            "pedidos": l["pedidos"],
            "comissao": round(l["comissao"] or 0, 2),
            "ultima_venda": l["ultima_venda"],
            "comissao_prevista": l["comissao_prevista"],
            # Conversao so faz sentido quando ha cliques medidos.
            "conversao": (round(l["pedidos"] / l["cliques"] * 100, 1)
                          if l["cliques"] else None),
        }
        for l in linhas
    ]
    por_canal: dict[str, dict] = {}
    for item in itens:
        alvo = por_canal.setdefault(
            item["canal"], {"canal": item["canal"], "links": 0,
                            "cliques": 0, "pedidos": 0, "comissao": 0.0})
        alvo["links"] += 1
        alvo["cliques"] += item["cliques"]
        alvo["pedidos"] += item["pedidos"]
        alvo["comissao"] += item["comissao"]

    return {
        "itens": itens,
        "tem_cliques": tem_cliques,
        "vendas_por_canal": [
            {
                "canal": v["canal"], "vendas": v["vendas"],
                "unidades": v["unidades"] or 0,
                "comissao": round(v["comissao"] or 0, 2),
                "ticket": round(v["ticket"] or 0, 2),
                "produtos": v["produtos"],
                "primeira": v["primeira"], "ultima": v["ultima"],
            }
            for v in vendas_canal
        ],
        "espera": {
            "total": len(esperas),
            "mediana": esperas[len(esperas) // 2] if esperas else None,
            "no_dia": sum(1 for d in esperas if d == 0),
            "tardias": sum(1 for d in esperas if d >= 4),
        },
        "canais": sorted(por_canal.values(),
                         key=lambda c: -c["comissao"]),
        "totais": {
            "links": len(itens),
            "cliques": sum(i["cliques"] for i in itens),
            "pedidos": sum(i["pedidos"] for i in itens),
            "comissao": round(sum(i["comissao"] for i in itens), 2),
        },
    }


def historico(cfg: config.Config, parametros: dict) -> dict:
    limite = max(1, min(_inteiro(parametros.get("limite"), cfg.ranking.top), 500))
    hoje = date.fromisoformat(dia_brasil())
    with db.conectar(cfg.caminho_banco) as conexao:
        dias = db.dias_disponiveis(conexao)
        registros = db.historico(
            conexao, cfg.ranking.dias_minimos_historico,
            _plataforma(parametros.get("plataforma")),
        )
    total_dias = len(dias) or 1
    ordenados = sorted(
        (
            (score_historico(r.score_medio, r.dias_visto, total_dias,
                             (hoje - date.fromisoformat(r.ultimo_dia)).days), r)
            for r in registros
        ),
        key=lambda par: par[0], reverse=True,
    )[:limite]
    return {
        "total_dias": total_dias,
        "itens": [
            {
                "nome": r.nome, "score": score, "epc_medio": r.score_medio,
                "dias_visto": r.dias_visto, "comissao_media": r.comissao_media,
                "preco_medio": r.preco_medio, "vendas_delta": r.vendas_delta,
                "primeiro_dia": r.primeiro_dia, "ultimo_dia": r.ultimo_dia,
                "link": r.link_oferta,
            }
            for score, r in ordenados
        ],
    }


DOMINIOS_LINK = ("shopee.com.br", "shopee.com")
MAX_SUB_IDS = 5
DIAS_PARA_SUMIDO = 2


def _numero(valor: str | None) -> float | None:
    """Campo vazio significa 'sem filtro', nao zero."""
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        return None


def _filtrar(linhas, parametros: dict, curados: set) -> list:
    """Aplica os filtros numericos antes do corte por limite.

    Filtrar depois do corte devolveria menos itens do que o pedido, o que
    parece bug para quem usa: o usuario pede 25 e recebe 4.
    """
    minimos = {
        "preco": _numero(parametros.get("preco_min")),
        "comissao_valor": _numero(parametros.get("comissao_min")),
        "vendas": _numero(parametros.get("vendas_min")),
        "rating": _numero(parametros.get("nota_min")),
    }
    taxa_min = _numero(parametros.get("taxa_min"))
    preco_max = _numero(parametros.get("preco_max"))
    loja = (parametros.get("loja") or "").strip().lower()
    so_vitrine = parametros.get("so_vitrine") in ("1", "true", "sim")

    def passa(linha) -> bool:
        for campo, minimo in minimos.items():
            if minimo is not None and (linha[campo] or 0) < minimo:
                return False
        if preco_max is not None and (linha["preco"] or 0) > preco_max:
            return False
        # A taxa vem como fracao (0.25); o usuario digita em porcentagem.
        if taxa_min is not None and (linha["taxa_comissao"] or 0) * 100 < taxa_min:
            return False
        if loja and loja not in (linha["loja_nome"] or "").lower():
            return False
        if so_vitrine and (linha["plataforma"], linha["item_id"]) not in curados:
            return False
        return True

    return [linha for linha in linhas if passa(linha)]


FATIA_FRAGIL = 0.6  # acima disso a comissao depende demais do vendedor


def _fragilidade(linha) -> dict | None:
    """Quanto da comissao depende do vendedor.

    A parte do vendedor e campanha dele: pode ser cortada a qualquer momento
    e a comissao despenca. A parte da Shopee e estavel. Comissao de 80% quase
    sempre e 77% vendedor + 3% Shopee -- otima hoje, incerta amanha.
    """
    vendedor = linha["taxa_vendedor"]
    shopee = linha["taxa_shopee"]
    if vendedor is None or shopee is None:
        return None
    total = vendedor + shopee
    if total <= 0:
        return None
    fatia = vendedor / total
    return {
        "fatia_vendedor": round(fatia, 3),
        "taxa_vendedor": vendedor,
        "taxa_shopee": shopee,
        # Piso: o que sobra se o vendedor encerrar a campanha dele.
        "piso": round((linha["preco"] or 0) * shopee, 2),
        "fragil": fatia >= FATIA_FRAGIL,
    }


JANELA_TENDENCIA = 7
VOLUME_MINIMO_TENDENCIA = 10  # abaixo disso, crescimento e ruido


def _forca_da_tendencia(registro: dict) -> float:
    """Crescimento relativo, nao volume absoluto.

    Um produto que saltou de 20 para 60 vendas cresceu 200%; um que foi de
    9.000 para 9.100 cresceu 1%, apesar de ter vendido mais unidades. Para
    "em alta" o que interessa e a aceleracao -- o gigante ja estava vendendo
    antes e nao e novidade.

    Sem piso de volume, qualquer produto que saiu de 1 para 3 vendas
    apareceria com 200% e dominaria a lista com ruido.
    """
    delta = registro["delta"] or 0
    if delta < VOLUME_MINIMO_TENDENCIA:
        return 0.0
    base = max((registro["acumulado"] or 0) - delta, 1)
    return delta / base


def _ritmo_da_oferta(linha, dia: str) -> dict | None:
    """Vendas por dia desde que a oferta entrou no ar.

    Diferente da velocidade, que compara dois dias de coleta e so funciona a
    partir do segundo dia: este sai de uma coleta so, dividindo o acumulado
    pelo tempo no ar.

    E uma media do periodo inteiro, nao o ritmo de hoje -- produto que
    bombou no lancamento e parou aparece bem aqui. Serve para comparar
    produtos entre si, nao para dizer o que esta acelerando agora.
    """
    if not linha["comecou_em"] or not linha["vendas"]:
        return None
    try:
        dias = (date.fromisoformat(dia)
                - date.fromisoformat(linha["comecou_em"])).days
    except ValueError:
        return None
    if dias < 1 or dias > HORIZONTE_PLAUSIVEL:
        return None
    return {
        "dias_no_ar": dias,
        "por_dia": round(linha["vendas"] / dias, 1),
        "desde": linha["comecou_em"],
    }


HORIZONTE_PLAUSIVEL = 730  # 2 anos


def _dias_ate(quando: str | None, referencia: str) -> int | None:
    """Quantos dias faltam para a oferta expirar. Negativo = ja expirou.

    `None` quando nao da para saber. A Shopee usa `2999-12-31` como sentinela
    de "sem validade" -- verificado no dado real: de 842 produtos, 799 vinham
    nulos e 43 com essa data, nenhum com prazo de verdade. Tratar a sentinela
    como prazo faria o criterio de validade passar sempre, dando peso a um
    sinal que nao carrega informacao nenhuma.
    """
    if not quando:
        return None
    try:
        dias = (date.fromisoformat(quando) - date.fromisoformat(referencia)).days
    except ValueError:
        return None
    return None if dias > HORIZONTE_PLAUSIVEL else dias


def _diagnostico_coleta(conexao, hoje: str) -> dict:
    """Distingue 'coletei' de 'coletei metade' e de 'nao coleto ha dias'.

    O indicador antigo so contava ofertas do dia, entao uma rodada que morreu
    depois de salvar parte parecia sucesso.
    """
    ultima = db.ultima_rodada(conexao)
    boa = db.ultima_rodada_completa(conexao)

    atraso = None
    if boa:
        atraso = (date.fromisoformat(hoje) - date.fromisoformat(boa["dia"])).days

    problemas = []
    if ultima is None:
        problemas.append("nenhuma coleta registrada ainda")
    elif ultima["estado"] == db.ESTADO_PARCIAL:
        etapas = json.loads(ultima["etapas"] or "{}")
        faltando = ", ".join(n for n, v in etapas.items() if v is None) or "etapas"
        problemas.append(f"a última coleta ficou incompleta ({faltando})")
    elif ultima["estado"] == db.ESTADO_ERRO:
        problemas.append(f"a última coleta falhou: {(ultima['erro'] or '')[:120]}")
    if atraso is not None and atraso >= 2:
        problemas.append(f"última coleta completa foi há {atraso} dia(s)")
    elif boa is None and ultima is not None:
        problemas.append("ainda não houve nenhuma coleta completa")

    return {
        "estado": ultima["estado"] if ultima else None,
        "quando": ultima["iniciado_em"] if ultima else None,
        "dia_ok": boa["dia"] if boa else None,
        "dias_atraso": atraso,
        "etapas": json.loads(ultima["etapas"] or "{}") if ultima else {},
        "erro": ultima["erro"] if ultima else None,
        "problemas": problemas,
    }


def saude(cfg: config.Config, parametros: dict) -> dict:
    """Checagens que evitam perder venda: link morto, combinacoes, cadencia."""
    hoje = dia_brasil()
    minimo = max(1, int(parametros.get("minimo") or 2))

    with db.conectar(cfg.caminho_banco) as conexao:
        atrasos = db.dias_sem_aparecer(conexao, hoje)
        pares = db.pares_comprados_juntos(conexao, minimo)
        esperas = db.tempo_ate_comprar(conexao)
        dias = db.dias_disponiveis(conexao)
        alvos = db.alvos(conexao)
        nomes = {
            (l["plataforma"], l["item_id"]): l["nome"]
            for l in db.listar_vitrine(conexao)
        }

    itens = [
        {
            "plataforma": plataforma, "item_id": item_id,
            "nome": nomes.get((plataforma, item_id)) or item_id,
            "dias_sumido": atraso,
            "morto": atraso is None or atraso >= 2,
        }
        for (plataforma, item_id), atraso in sorted(
            atrasos.items(), key=lambda par: -(par[1] if par[1] is not None else 999)
        )
    ]

    distribuicao: dict[str, int] = {}
    for dia in esperas:
        faixa = "mesmo dia" if dia == 0 else f"{dia}d"
        distribuicao[faixa] = distribuicao.get(faixa, 0) + 1

    return {
        "dias_coletados": len(dias),
        "itens": itens,
        "minimo": minimo,
        "alvos": [
            {
                "item_id": a["item_id"],
                "nome": a["nome"] or a["item_id"],
                "alvo": a["alvo"],
                "preco_atual": a["preco_atual"],
                "atingido": a["preco_atual"] is not None
                and a["preco_atual"] <= a["alvo"],
                "falta_pct": None if not a["preco_atual"]
                or a["preco_atual"] <= a["alvo"]
                else round((a["preco_atual"] - a["alvo"]) / a["preco_atual"] * 100),
                "link": a["link"],
            }
            for a in alvos
        ],
        "pares": [
            {
                "item_a": p["item_a"], "item_b": p["item_b"],
                "nome_a": p["nome_a"] or p["item_a"],
                "nome_b": p["nome_b"] or p["item_b"],
                "juntos": p["juntos"], "comissao": round(p["comissao"] or 0, 2),
            }
            for p in pares[:20]
        ],
        "esperas": {
            "total": len(esperas),
            "mediana": esperas[len(esperas) // 2] if esperas else None,
            "no_dia": sum(1 for d in esperas if d == 0),
            "tardias": sum(1 for d in esperas if d >= 4),
            "distribuicao": distribuicao,
        },
    }


def definir_alvo(cfg: config.Config, corpo: dict) -> dict:
    """Cria ou remove um alerta de preco-alvo."""
    item_id = str(corpo.get("item_id") or "").strip()
    if not item_id:
        raise ValueError("item_id obrigatorio")
    plataforma = corpo.get("plataforma") or "shopee"
    if plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma invalida: {plataforma}")

    with db.conectar(cfg.caminho_banco) as conexao:
        if corpo.get("remover"):
            db.remover_alvo(conexao, plataforma, item_id)
            return {"item_id": item_id, "tem_alvo": False}
        try:
            alvo = float(corpo.get("preco"))
        except (TypeError, ValueError):
            raise ValueError("preco invalido")
        if not 0 < alvo < 1_000_000:
            raise ValueError("preco fora da faixa")
        db.definir_alvo(conexao, plataforma, item_id, alvo,
                        corpo.get("nome") or None)
    return {"item_id": item_id, "tem_alvo": True, "alvo": alvo}


def alternar_vitrine(cfg: config.Config, corpo: dict) -> dict:
    """Liga/desliga um item da vitrine curada."""
    item_id = str(corpo.get("item_id") or "").strip()
    if not item_id:
        raise ValueError("item_id obrigatorio")
    plataforma = corpo.get("plataforma") or "shopee"
    if plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma invalida: {plataforma}")
    colecao = str(corpo.get("colecao") or db.COLECAO_PADRAO).strip()[:40]

    with db.conectar(cfg.caminho_banco) as conexao:
        if corpo.get("remover"):
            db.remover_vitrine(conexao, plataforma, item_id)
            dentro = False
        else:
            db.adicionar_vitrine(conexao, plataforma, item_id, colecao)
            dentro = True
        total = len(db.listar_vitrine(conexao))
    return {"item_id": item_id, "na_vitrine": dentro, "total": total}


def vitrine(cfg: config.Config, parametros: dict) -> dict:
    hoje = dia_brasil()
    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.listar_vitrine(conexao, parametros.get("colecao") or None)
        grupos = [dict(g) for g in db.colecoes(conexao)]
        atrasos = db.dias_sem_aparecer(conexao, hoje)
        dias_coletados = len(db.dias_disponiveis(conexao))
        velocidades = db.velocidade_vendas(conexao, hoje)

    itens = []
    for linha in linhas:
        chave = (linha["plataforma"], linha["item_id"])
        atraso = atrasos.get(chave)
        itens.append({
            "plataforma": linha["plataforma"], "item_id": linha["item_id"],
            "colecao": linha["colecao"], "posicao": linha["posicao"],
            "nome": linha["nome"] or linha["item_id"], "preco": linha["preco"],
            "taxa_comissao": linha["taxa_comissao"],
            "comissao_valor": linha["comissao_valor"], "epc": linha["epc"],
            "vendas": linha["vendas"], "rating": linha["rating"],
            "desconto_declarado": linha["desconto_pct"], "loja": linha["loja_nome"],
            "imagem": linha["imagem"],
            "link": linha["link_oferta"] or linha["link_produto"],
            "link_produto": linha["link_produto"] or linha["link_oferta"],
            "dias_sumido": atraso,
            "indisponivel": atraso is None or atraso >= DIAS_PARA_SUMIDO,
            "expira_em": linha["expira_em"],
            "dias_ate_expirar": _dias_ate(linha["expira_em"], hoje),
            "velocidade": velocidades.get(chave),
        })
    return {
        "colecoes": grupos,
        "dias_coletados": dias_coletados,
        "indisponiveis": sum(1 for i in itens if i["indisponivel"]),
        "itens": itens,
    }


def gerar_link(cfg: config.Config, url: str, sub_ids: list) -> dict:
    """Encurta um link com sub-IDs de campanha, com cache no banco.

    Valida o dominio para o painel nao virar um encurtador aberto: so aceita
    URL da Shopee, que e a unica que a mutation sabe rastrear mesmo.
    """
    from urllib.parse import urlparse

    url = (url or "").strip()
    dominio = urlparse(url).netloc.lower().removeprefix("www.")
    if not any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_LINK):
        raise ValueError(f"url fora da Shopee: {dominio or url[:40]}")

    tags = [str(s).strip()[:50] for s in sub_ids if str(s).strip()][:MAX_SUB_IDS]
    chave = ",".join(tags)
    item_id = db.item_id_da_url(url)

    with db.conectar(cfg.caminho_banco) as conexao:
        cache = db.buscar_links(conexao, "shopee", chave)
        if url in cache:
            return {"link": cache[url], "sub_ids": tags, "cache": True}

        registro = FonteShopee().gerar_link(cfg, url, tags)
        db.salvar_link(conexao, "shopee",
                       {**registro, "sub_ids": chave, "item_id": item_id})
    return {"link": registro["link_curto"], "sub_ids": tags, "cache": False}


def categorias(cfg: config.Config, parametros: dict) -> dict:
    plataforma = _plataforma(parametros.get("plataforma")) or "shopee"
    with db.conectar(cfg.caminho_banco) as conexao:
        return {"plataforma": plataforma,
                "itens": db.categorias_vistas(conexao, plataforma)}
