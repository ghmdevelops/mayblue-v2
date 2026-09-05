"""CLI do flow02.

    doctor      valida credenciais e mede o estado real das APIs
    coletar     coleta ofertas, pontua e grava o snapshot do dia
    top         ranking do dia por retorno esperado
    historico   ranking acumulado
    ganhos      retorno realizado (conversionReport / validatedReport)
    calibrar    recalcula o CVR medido e passa a usa-lo no score
    link        gera link curto rastreado (Shopee) ou valida URL (ML)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path


from . import (__version__, analise, backtest, backup, calibracao, campanha,
               config, db, rastreio)
from .models import Oferta
from .saidas import firebase, relatorio, telegram
from .scoring import avaliar, score_historico
from .sources import mercadolivre as ml
from .sources.base import FonteIndisponivel, aplicar_filtros
from .sources.mercadolivre import FonteMercadoLivre
from .sources import shopee
from .sources.shopee import FonteShopee
from .tempo import dia_brasil, iso_utc

FONTES = {"shopee": FonteShopee(), "mercadolivre": FonteMercadoLivre()}
LIMITE_NOME = 46
ROTULO_ORDEM = {
    "epc": "retorno esperado por clique",
    "comissao": "comissao em reais por venda",
    "vendas": "mais vendidos",
    "taxa": "maior percentual de comissao",
    "preco": "maior preco",
    "nota": "melhor nota",
}


def _log_config(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _fontes_pedidas(nomes: list[str] | None):
    if not nomes:
        return list(FONTES.items())
    invalidas = set(nomes) - set(FONTES)
    if invalidas:
        raise SystemExit(f"plataforma desconhecida: {sorted(invalidas)}")
    return [(nome, FONTES[nome]) for nome in nomes]


def _uma_plataforma(args) -> str | None:
    nomes = getattr(args, "plataforma", None)
    return nomes[0] if nomes and len(nomes) == 1 else None


def _resolver_dia(valor: str | None) -> str:
    if not valor or valor == "hoje":
        return dia_brasil()
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise SystemExit(f"--dia invalido: {valor} (use YYYY-MM-DD ou 'hoje')")


def _resolver_links(conexao, cfg: config.Config, linhas, sub_ids: list[str],
                    gerar: bool) -> dict[str, str]:
    """Link de afiliado por item.

    O `offerLink` do productOfferV2 ja e link de afiliado rastreado. O
    generateShortLink so e chamado com --com-link, porque encurta e carrega os
    subIds -- e cada chamada consome cota da API, entao o resultado fica em
    cache na tabela link_gerado.
    """
    chave_sub = ",".join(sub_ids)
    cache = db.buscar_links(conexao, "shopee", chave_sub)
    cliente = None
    links: dict[str, str] = {}

    for linha in linhas:
        padrao = linha["link_oferta"] or linha["link_produto"] or "-"
        origem = linha["link_produto"] or linha["link_oferta"]
        if not gerar or linha["plataforma"] != "shopee" or not origem:
            links[linha["item_id"]] = padrao
            continue
        if origem in cache:
            links[linha["item_id"]] = cache[origem]
            continue
        try:
            if cliente is None:
                cliente = FONTES["shopee"].cliente(cfg)
            curto = cliente.gerar_link_curto(origem, sub_ids)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "link curto falhou para %s: %s", linha["item_id"], exc
            )
            links[linha["item_id"]] = padrao
        else:
            db.salvar_link(conexao, "shopee", {
                "url_origem": origem, "link_curto": curto,
                "sub_ids": chave_sub, "gerado_em": iso_utc(),
            })
            links[linha["item_id"]] = curto
    return links


def _celula_desconto(avaliacao, cfg: config.Config) -> str:
    """Desconto real contra a mediana historica; `!` marca anuncio inflado."""
    if not avaliacao.confiavel:
        return f"{avaliacao.desconto_declarado:.0f}?"
    marca = "!" if analise.desconto_inflado(avaliacao, cfg.preco) else ""
    return f"{avaliacao.desconto_real:.0f}{marca}"


def _emitir(cabecalhos, linhas, args, titulo: str, base_saida: Path,
            nome_base: str, itens_post=None) -> None:
    formato = getattr(args, "formato", "tabela")
    caminho = getattr(args, "saida", None)
    if formato == "post":
        conteudo = relatorio.renderizar_post(itens_post or [])
        if caminho:
            relatorio.escrever_texto(conteudo, caminho)
            print(f"texto gravado em {caminho}")
        else:
            print(f"\n{titulo}\n")
            print(conteudo)
        return
    if formato == "csv":
        destino = caminho or base_saida / f"{nome_base}.csv"
        relatorio.escrever_csv(cabecalhos, linhas, destino)
        print(f"csv gravado em {destino}")
        return
    if formato == "md":
        destino = caminho or base_saida / f"{nome_base}.md"
        conteudo = f"# {titulo}\n\n" + relatorio.renderizar_markdown(cabecalhos, linhas)
        relatorio.escrever_texto(conteudo, destino)
        print(f"markdown gravado em {destino}")
        return
    print(f"\n{titulo}\n")
    print(relatorio.renderizar_tabela(cabecalhos, linhas))


def comando_doctor(args, cfg: config.Config) -> int:
    print(f"flow02 {__version__}")
    print(f"config : {config.CAMINHO_CONFIG} "
          f"({'ok' if config.CAMINHO_CONFIG.exists() else 'AUSENTE'})")
    print(f"banco  : {cfg.caminho_banco}")
    print(f"consultas: {[c.nome for c in cfg.coleta.consultas]}")
    problemas = 0

    for nome, fonte in _fontes_pedidas(args.plataforma):
        pode, motivo = fonte.disponivel(cfg)
        print(f"\n[{nome}] {'DISPONIVEL' if pode else 'INDISPONIVEL'}: {motivo}")
        if nome == "mercadolivre":
            print("   probe da API publica (nao ha API de afiliados):")
            for caminho, status in fonte.diagnosticar(cfg)["probe"].items():
                print(f"     GET {caminho:<26} -> {status}")
            continue
        if not pode:
            problemas += 1
            continue
        try:
            resultado = fonte.diagnosticar(cfg)
        except Exception as exc:
            problemas += 1
            print(f"   falha no diagnostico: {exc}")
            query = getattr(exc, "query", "")
            if query:
                print(f"   query enviada: {query}")
                print("   se o codigo for 10010, remova o campo recusado de "
                      "[coleta].campos_produto no config.toml")
        else:
            print(f"   consulta minima ok, nodes={resultado['nodes_recebidos']}")
            for chave, valor in (resultado["exemplo"] or {}).items():
                print(f"     {chave} = {valor!r}")

    with db.conectar(cfg.caminho_banco) as conexao:
        calibrador = calibracao.carregar(conexao, cfg.calibracao)
        dias = db.dias_disponiveis(conexao)
    print(f"\nhistorico: {len(dias)} dia(s) coletado(s)"
          f"{f' (de {dias[-1]} a {dias[0]})' if dias else ''}")
    print(f"cvr calibrado: {len(calibrador.tabela)} escopo(s) com amostra suficiente"
          if calibrador else "cvr calibrado: nenhum -- score usa heuristica")

    if not cfg.telegram.habilitado:
        print("telegram: desabilitado")
    elif not cfg.telegram.configurado:
        print("telegram: HABILITADO SEM token ou chat_id")
        problemas += 1
    else:
        try:
            bot = telegram.verificar(cfg.telegram.token, cfg.coleta.timeout_s)
        except telegram.ErroTelegram as exc:
            print(f"telegram: {exc}")
            problemas += 1
        else:
            print(f"telegram: @{bot.get('username', '?')} -> chat {cfg.telegram.chat_id}")

    problemas += _doctor_firebase(cfg)
    return 1 if problemas else 0


def _doctor_firebase(cfg: config.Config) -> int:
    fb = cfg.firebase
    if not fb.habilitado:
        print("firebase: desabilitado (os dados ficam so no SQLite)")
        return 0

    candidatas = fb.urls_candidatas()
    if not candidatas:
        print("firebase: HABILITADO sem database_url nem projeto. Preencha um "
              "dos dois no config.toml.")
        return 1

    print(f"firebase: {'URL inferida do projeto' if fb.url_inferida else 'URL informada'}"
          f" -- sondando {len(candidatas)} endereco(s)")
    encontradas = []
    for url in candidatas:
        estado, detalhe = firebase.sondar(url, cfg.coleta.timeout_s)
        marca = {"existe": "OK    ", "ausente": "-     ", "erro": "ERRO  "}[estado]
        print(f"   {marca} {url}")
        print(f"          {detalhe}")
        if estado == "existe":
            encontradas.append(url)

    if not encontradas:
        print("\n   nenhum Realtime Database encontrado. Ele e um produto separado "
              "do Firestore:")
        print("   console.firebase.google.com > Realtime Database > Criar banco")
        return 1

    escolhida = encontradas[0]
    cliente = firebase.ClienteFirebase(
        escolhida, fb.api_key, fb.email, fb.senha, fb.segredo, cfg.coleta.timeout_s,
    )
    print(f"\n   usando {escolhida} (auth: {cliente.modo_auth})")
    if fb.url_inferida:
        print("   fixe no config.toml para nao depender da inferencia:")
        print(f'   [firebase]\n   database_url = "{escolhida}"')
    if cliente.modo_auth == "sem autenticacao":
        print("   ATENCAO: sem auth so funciona com regras abertas, o que deixa "
              "seus dados publicos para leitura e escrita")
    return 0


def _ajustar_quantidade(cfg: config.Config, quantidade: int | None,
                        limite: int | None) -> config.Config:
    """Traduz "quero N produtos" em páginas por consulta.

    Sem isso, mudar a quantidade exigia editar o config.toml. A Shopee pagina
    de 50 em 50, então N produtos viram ceil(N / limite) páginas em cada
    consulta configurada.
    """
    if not quantidade and not limite:
        return cfg
    por_pagina = min(limite or cfg.coleta.limite_por_pagina, 100)
    trocas: dict = {"limite_por_pagina": por_pagina}
    if quantidade:
        paginas = max(1, -(-quantidade // por_pagina))
        trocas["consultas"] = tuple(
            replace(consulta, paginas=paginas) for consulta in cfg.coleta.consultas
        )
        trocas["campanhas_paginas"] = min(cfg.coleta.campanhas_paginas, paginas)
    return replace(cfg, coleta=replace(cfg.coleta, **trocas))


def comando_coletar(args, cfg: config.Config) -> int:
    """Coleta ofertas e, opcionalmente, conversoes.

    Cada etapa e registrada na tabela `execucao`. Sem isso, uma rodada que
    morre no meio grava parte do trabalho e passa por sucesso -- foi o que
    aconteceu em 2026-09-04, quando so as conversoes salvaram e ninguem
    percebeu ate comparar horarios de gravacao na mao.
    """
    cfg = _ajustar_quantidade(cfg, args.quantidade, args.limite)
    dia = _resolver_dia(args.dia)
    falhas = 0

    with db.conectar(cfg.caminho_banco) as conexao:
        rodada = db.abrir_rodada(conexao, "coletar", dia)
        # None = a etapa nao chegou a terminar. Zero = terminou sem nada.
        etapas: dict[str, int | None] = {}
        ultimo_erro: str | None = None
        calibrador = calibracao.carregar(conexao, cfg.calibracao)

        for nome, fonte in _fontes_pedidas(args.plataforma):
            etapas[f"ofertas:{nome}"] = None
            execucao_id = db.registrar_execucao(conexao, nome)
            cache = None if args.sem_retomar else db.CachePaginas(conexao, dia, nome)
            try:
                brutas: list[Oferta] = fonte.coletar(cfg, cache)
            except FonteIndisponivel as exc:
                db.finalizar_execucao(conexao, execucao_id, 0, 0, f"indisponivel: {exc}")
                etapas.pop(f"ofertas:{nome}")  # plataforma desligada nao e falha
                print(f"[{nome}] pulado -- {exc}")
                continue
            except Exception as exc:
                falhas += 1
                ultimo_erro = f"{nome}: {exc}"
                db.finalizar_execucao(conexao, execucao_id, 0, 0, str(exc))
                print(f"[{nome}] ERRO: {exc}", file=sys.stderr)
                continue

            filtradas = aplicar_filtros(brutas, cfg.filtros)
            avaliadas = [
                (o, avaliar(o, cfg.scoring, calibrador.cvr_para(o))) for o in filtradas
            ]
            salvas = db.salvar_ofertas(conexao, avaliadas, dia)
            db.finalizar_execucao(conexao, execucao_id, len(brutas), salvas)
            etapas[f"ofertas:{nome}"] = salvas
            calibradas = sum(1 for _, a in avaliadas if a.calibrado)
            reaproveitadas = getattr(cache, "reaproveitadas", 0)
            print(f"[{nome}] brutas={len(brutas)} pos-filtro={len(filtradas)} "
                  f"gravadas={salvas} com-cvr-real={calibradas} dia={dia}"
                  + (f" (retomou {reaproveitadas} pagina(s))"
                     if reaproveitadas else ""))
            # Etapa concluida: o checkpoint cumpriu o papel e vira lixo.
            db.limpar_paginas(conexao, dia, nome)

            if args.com_conversoes and nome == "shopee":
                etapas["conversoes"] = None
                gravadas, erro = _coletar_conversoes(conexao, fonte, cfg)
                if erro:
                    falhas += 1
                    ultimo_erro = erro
                else:
                    etapas["conversoes"] = gravadas

        estado = db.fechar_rodada(conexao, rodada, etapas, ultimo_erro)

    if estado == db.ESTADO_PARCIAL:
        incompletas = [n for n, v in etapas.items() if v is None]
        print(f"\nATENCAO: coleta PARCIAL -- nao concluiu: {', '.join(incompletas)}",
              file=sys.stderr)
        print("o painel vai marcar este dia como incompleto. rode de novo.",
              file=sys.stderr)
    return 1 if falhas else 0


def _coletar_conversoes(conexao, fonte, cfg: config.Config) -> tuple[int, str | None]:
    try:
        estimadas = fonte.coletar_conversoes(cfg)
        validadas = fonte.coletar_validadas(cfg)
    except Exception as exc:
        print(f"[shopee] conversoes falharam: {exc}", file=sys.stderr)
        return 0, f"conversoes: {exc}"
    total = db.salvar_conversoes(conexao, [*estimadas, *validadas])
    print(f"[shopee] conversoes: estimadas={len(estimadas)} "
          f"validadas={len(validadas)} gravadas={total}")
    return total, None


def comando_ganhos(args, cfg: config.Config) -> int:
    desde = None
    if args.dias > 0:
        desde = (date.today() - timedelta(days=args.dias)).isoformat()

    if args.por_item:
        return _ganhos_por_item(args, cfg, desde)

    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ganhos(conexao, desde, _uma_plataforma(args),
                           True if args.validadas else None)

    if not linhas:
        print("nenhuma conversao gravada. rode `coletar --com-conversoes` "
              "(exige credenciais da Shopee).")
        return 0

    cabecalhos = ["dia", "plataforma", "cliques", "pedidos", "cvr%", "comissao R$", "tipo"]
    tabela = [
        [
            linha["dia"] or "-",
            linha["plataforma"],
            str(linha["cliques"] or 0),
            str(linha["pedidos"] or 0),
            f"{(linha['pedidos'] / linha['cliques'] * 100):.2f}" if linha["cliques"] else "-",
            f"{linha['comissao']:.2f}",
            "validada" if linha["validada"] else "estimada",
        ]
        for linha in linhas
    ]
    total = sum(l["comissao"] or 0 for l in linhas)
    tabela.append(["TOTAL", "", str(sum(l["cliques"] or 0 for l in linhas)),
                   str(sum(l["pedidos"] or 0 for l in linhas)), "",
                   f"{total:.2f}", ""])
    _emitir(cabecalhos, tabela, args,
            f"Retorno realizado{f' (ultimos {args.dias} dias)' if desde else ''}",
            cfg.caminho_saida, "ganhos")
    return 0


def _carregar_alertas(cfg: config.Config, dia: str, plataforma: str | None):
    with db.conectar(cfg.caminho_banco) as conexao:
        anterior = db.dia_anterior_a(conexao, dia)
        if anterior is None:
            return None, None, []
        atual_linhas = db.ofertas_do_dia(conexao, dia, plataforma)
        anteriores = db.ofertas_do_dia(conexao, anterior, plataforma)
    return anterior, atual_linhas, analise.detectar_alertas(
        atual_linhas, anteriores, cfg.alertas
    )


def comando_alertas(args, cfg: config.Config) -> int:
    dia = _resolver_dia(args.dia)
    anterior, atuais, alertas = _carregar_alertas(cfg, dia, _uma_plataforma(args))

    if anterior is None:
        print(f"nao ha snapshot anterior a {dia} para comparar. "
              "rode `coletar` em pelo menos dois dias distintos.")
        return 0
    if not atuais:
        print(f"nenhuma oferta gravada em {dia}.")
        return 0
    if not alertas:
        print(f"nenhuma mudanca relevante entre {anterior} e {dia}.")
        return 0

    cabecalhos = ["tipo", "produto", "antes", "depois", "variacao", "link"]
    tabela = [
        [
            alerta.rotulo,
            relatorio.truncar(alerta.nome, LIMITE_NOME),
            f"{alerta.antes:.4g}" if alerta.antes is not None else "-",
            f"{alerta.depois:.4g}" if alerta.depois is not None else "-",
            f"{alerta.variacao * 100:+.0f}%" if alerta.variacao is not None else "-",
            alerta.link or "-",
        ]
        for alerta in alertas[: args.top or len(alertas)]
    ]
    _emitir(cabecalhos, tabela, args, f"Mudancas entre {anterior} e {dia}",
            cfg.caminho_saida, f"alertas_{dia}")
    if args.formato == "tabela":
        print("\ncomissao em taxa (0.15 = 15%), preco em reais. "
              "COMISSAO SUBIU costuma indicar campanha nova, com prazo.")
    return 0


def comando_categorias(args, cfg: config.Config) -> int:
    with db.conectar(cfg.caminho_banco) as conexao:
        categorias = db.categorias_vistas(conexao, args.plataforma[0]
                                          if args.plataforma else "shopee")
    if not categorias:
        print("nenhuma categoria registrada. rode `coletar` primeiro -- os ids "
              "vem do campo productCatIds das ofertas coletadas.")
        return 0

    cabecalhos = ["categoria_id", "itens", "comissao media %", "melhor EPC"]
    tabela = [
        [
            str(c["categoria_id"]), str(c["itens"]),
            f"{c['comissao_media'] * 100:.1f}", f"{c['melhor_epc']:.4f}",
        ]
        for c in categorias[: args.top or len(categorias)]
    ]
    _emitir(cabecalhos, tabela, args, "Categorias observadas na coleta",
            cfg.caminho_saida, "categorias")

    if args.formato != "tabela":
        return 0
    numericas = [c["categoria_id"] for c in categorias[:10]
                 if str(c["categoria_id"]).isdigit()]
    if numericas:
        print("\npara varrer o topo de cada uma, copie no config.toml:\n")
        print("[coleta.varredura]\nhabilitada = true")
        print(f"categorias = [{', '.join(numericas)}]")
    else:
        print("\na varredura por categoria so vale para a Shopee, onde o "
              "productCatId e numerico. Os valores acima sao rotulos do seu CSV.")
    return 0


def comando_notificar(args, cfg: config.Config) -> int:
    if args.descobrir_chat:
        return _descobrir_chat(cfg)
    if not cfg.telegram.configurado:
        print("telegram desabilitado. Ligue [telegram].habilitado, defina chat_id "
              "e TELEGRAM_BOT_TOKEN no .env.", file=sys.stderr)
        return 2

    dia = _resolver_dia(args.dia)
    plataforma = _uma_plataforma(args)
    if args.tipo == "alertas":
        anterior, _, alertas = _carregar_alertas(cfg, dia, plataforma)
        if anterior is None:
            print("sem snapshot anterior para comparar.")
            return 0
        texto = telegram.montar_alertas(alertas, dia, cfg.telegram.top)
    else:
        with db.conectar(cfg.caminho_banco) as conexao:
            linhas = db.ranking_dia(conexao, dia, cfg.telegram.top,
                                    cfg.ranking.max_por_loja, plataforma)
            links = db.buscar_links(conexao, "shopee", "")
        if not linhas:
            print(f"nada para enviar em {dia}.")
            return 0
        itens = [
            {
                "nome": l["nome"], "preco": l["preco"],
                "desconto_pct": l["desconto_pct"], "vendas": l["vendas"],
                "rating": l["rating"], "comissao_valor": l["comissao_valor"],
                "taxa_comissao": l["taxa_comissao"],
                "link": links.get(l["item_id"]) or l["link_oferta"] or l["link_produto"],
            }
            for l in linhas
        ]
        texto = (f"flow02 - top {len(itens)} de {dia}\n\n"
                 + relatorio.renderizar_post(itens))

    try:
        partes = telegram.enviar(cfg.telegram.token, cfg.telegram.chat_id, texto,
                                 cfg.coleta.timeout_s)
    except telegram.ErroTelegram as exc:
        print(f"telegram: {exc}", file=sys.stderr)
        return 1
    print(f"enviado para o chat {cfg.telegram.chat_id} em {partes} mensagem(ns)")
    return 0


def _descobrir_chat(cfg: config.Config) -> int:
    """Mostra os chat_id disponiveis -- o passo que trava todo mundo."""
    if not cfg.telegram.token:
        print("falta TELEGRAM_BOT_TOKEN no .env.\n"
              "  1. no Telegram, fale com @BotFather\n"
              "  2. /newbot e siga as instrucoes\n"
              "  3. copie o token para o .env", file=sys.stderr)
        return 2

    try:
        bot = telegram.verificar(cfg.telegram.token, cfg.coleta.timeout_s)
        chats = telegram.descobrir_chats(cfg.telegram.token, cfg.coleta.timeout_s)
    except telegram.ErroTelegram as exc:
        print(f"telegram: {exc}", file=sys.stderr)
        return 1

    print(f"bot: @{bot.get('username', '?')}")
    if not chats:
        print("\nnenhum chat encontrado. Abra o Telegram, procure por "
              f"@{bot.get('username', 'seu_bot')}, mande qualquer mensagem "
              "e rode este comando de novo.")
        print("para grupo: adicione o bot ao grupo e mande uma mensagem la.")
        return 0

    print(f"\n{len(chats)} chat(s) encontrado(s):\n")
    for chat in chats:
        print(f"  chat_id {chat['chat_id']:<16} {chat['tipo']:<10} {chat['nome']}")
    print("\ncoloque o escolhido no config.toml:\n")
    print(f'[telegram]\nhabilitado = true\nchat_id = "{chats[0]["chat_id"]}"')
    return 0


def comando_sincronizar(args, cfg: config.Config) -> int:
    if not cfg.firebase.configurado:
        print("firebase desabilitado. Ligue [firebase].habilitado no config.toml "
              "e defina database_url (ou FIREBASE_DATABASE_URL no .env).",
              file=sys.stderr)
        return 2

    dia = _resolver_dia(args.dia)
    try:
        cliente = firebase.ClienteFirebase(
            database_url=cfg.firebase.url,
            api_key=cfg.firebase.api_key,
            email=cfg.firebase.email,
            senha=cfg.firebase.senha,
            segredo=cfg.firebase.segredo,
            timeout_s=cfg.coleta.timeout_s,
        )
    except firebase.ErroFirebase as exc:
        print(f"firebase: {exc}", file=sys.stderr)
        return 2

    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ranking_dia(conexao, dia, 1_000_000, 0, _uma_plataforma(args))
        links = db.buscar_links(conexao, "shopee", "")
        ganhos = db.ganhos(conexao) if cfg.firebase.enviar_ganhos else []
        curados = db.listar_vitrine(conexao)
        atrasos = db.dias_sem_aparecer(conexao, dia)
        mortos = {
            chave for chave, atraso in atrasos.items()
            if atraso is None or atraso >= 2
        }

    if not linhas:
        print(f"nada para sincronizar em {dia}. rode `coletar` primeiro.")
        return 0

    entradas_links = {
        rastreio.codigo_curto(l["plataforma"], l["item_id"]): {
            "url": links.get(l["item_id"]) or l["link_oferta"] or l["link_produto"],
            "item_id": l["item_id"],
            "canal": None,
        }
        for l in linhas
        if links.get(l["item_id"]) or l["link_oferta"] or l["link_produto"]
    }

    try:
        enviados = firebase.sincronizar_ranking(
            cliente, cfg.firebase.raiz, dia, linhas, links
        )
        publicados = firebase.sincronizar_links(
            cliente, cfg.firebase.raiz, entradas_links
        )
        na_vitrine = firebase.sincronizar_vitrine(
            cliente, cfg.firebase.raiz, curados, links, mortos
        )
        total_ganhos = (firebase.sincronizar_ganhos(cliente, cfg.firebase.raiz, ganhos)
                        if ganhos else 0)
    except firebase.ErroFirebase as exc:
        print(f"firebase: {exc}", file=sys.stderr)
        return 1

    print(f"auth: {cliente.modo_auth}")
    for plataforma, quantidade in enviados.items():
        print(f"enviados {quantidade} itens -> {cfg.firebase.raiz}/ranking/{dia}/{plataforma}")
    if publicados:
        print(f"publicados {publicados} codigo(s) de rastreio -> "
              f"{cfg.firebase.raiz}/links")
    if na_vitrine:
        print(f"publicados {na_vitrine} item(ns) curado(s) -> "
              f"{cfg.firebase.raiz}/vitrine")
    if total_ganhos:
        print(f"enviados {total_ganhos} registros -> {cfg.firebase.raiz}/ganhos")
    print(f"\nver no console : {cliente.url_console()}")
    print(f"ver via REST   : {cfg.firebase.url}/{cfg.firebase.raiz}/ranking/{dia}.json")
    return 0


def _ganhos_por_item(args, cfg: config.Config, desde: str | None) -> int:
    """Quais produtos de fato pagaram -- o sinal medido que sobra sem cliques."""
    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ganhos_por_item(conexao, desde, args.top or 50)

    if not linhas:
        print("nenhuma conversao com produto identificado. rode "
              "`coletar --com-conversoes`.")
        return 0

    cabecalhos = ["#", "produto", "conversoes", "unidades", "comissao R$",
                  "1a venda", "ultima", "link"]
    tabela = [
        [
            str(posicao),
            relatorio.truncar(linha["nome"] or linha["item_id"], LIMITE_NOME),
            str(linha["conversoes"]),
            str(linha["unidades"] or 0),
            f"{linha['comissao']:.2f}",
            linha["primeira"] or "-",
            linha["ultima"] or "-",
            linha["link"] or "-",
        ]
        for posicao, linha in enumerate(linhas, start=1)
    ]
    total = sum(l["comissao"] or 0 for l in linhas)
    tabela.append(["", "TOTAL", "", "", f"{total:.2f}", "", "", ""])
    _emitir(cabecalhos, tabela, args, "Comissao realizada por produto",
            cfg.caminho_saida, "ganhos_por_item")
    if args.formato == "tabela":
        print("\nesta e a unica medida real de desempenho disponivel: a API nao "
              "expoe cliques, entao nao ha taxa de conversao.")
    return 0


def comando_vitrine(args, cfg: config.Config) -> int:
    """Selecao curada: os produtos que voce escolheu, na ordem que escolheu."""
    plataforma = (args.plataforma[0] if args.plataforma else "shopee")
    colecao = args.colecao or db.COLECAO_PADRAO

    with db.conectar(cfg.caminho_banco) as conexao:
        if args.item and args.remover:
            removido = db.remover_vitrine(conexao, plataforma, args.item,
                                          args.colecao)
            print("removido da vitrine" if removido else "esse item nao estava la")
            return 0
        if args.item:
            db.adicionar_vitrine(conexao, plataforma, args.item, colecao)
            print(f"adicionado a colecao '{colecao}': {args.item}")
            return 0
        linhas = db.listar_vitrine(conexao, args.colecao)
        grupos = db.colecoes(conexao)

    if args.para_shopee:
        return _lista_para_o_portal(linhas)

    if not linhas:
        print("vitrine vazia. adicione com:\n"
              "  flow02 vitrine --item 18699075500\n"
              "  flow02 vitrine --item 18699075500 --colecao casa\n"
              "ou pelo botao 'Vitrine' no painel web.")
        return 0

    cabecalhos = ["colecao", "#", "produto", "preco", "R$/venda", "EPC", "link"]
    tabela = [
        [
            linha["colecao"],
            str(linha["posicao"]),
            relatorio.truncar(linha["nome"] or linha["item_id"], LIMITE_NOME),
            f"{linha['preco']:.2f}" if linha["preco"] is not None else "-",
            f"{linha['comissao_valor']:.2f}" if linha["comissao_valor"] is not None else "-",
            f"{linha['epc']:.4f}" if linha["epc"] is not None else "-",
            linha["link_oferta"] or "-",
        ]
        for linha in linhas
    ]
    _emitir(cabecalhos, tabela, args,
            f"Vitrine curada -- {len(linhas)} item(ns)",
            cfg.caminho_saida, "vitrine")
    if args.formato == "tabela" and len(grupos) > 1:
        resumo = ", ".join(f"{g['colecao']} ({g['itens']})" for g in grupos)
        print(f"\ncolecoes: {resumo}")
    return 0


def _lista_para_o_portal(linhas) -> int:
    """Lista pronta para montar a mesma vitrine no portal da Shopee.

    A API de afiliados tem exatamente duas mutations, as duas de link
    (verificado por introspecao em 2026-09-04): nao ha como criar ou editar
    vitrine por programa. O portal e o unico caminho.

    O que da para fazer e tirar o trabalho de garimpar: aqui saem os links
    de produto na ordem que voce curou, um por linha, para colar na busca do
    portal sem precisar procurar cada item pelo nome.
    """
    if not linhas:
        print("vitrine vazia -- nada a levar para o portal")
        return 0

    print(f"{len(linhas)} produto(s) para adicionar em")
    print("  affiliate.shopee.com.br > Minha Vitrine > Adicionar produto")
    print("\ncole cada link na busca do portal, na ordem:\n")
    for posicao, linha in enumerate(linhas, start=1):
        nome = relatorio.truncar(linha["nome"] or linha["item_id"], 52)
        print(f"[ ] {posicao:>2}. {nome}")
        print(f"       {linha['link_produto'] or linha['link_oferta'] or '-'}")
    print("\nnao da para automatizar: a API de afiliados so tem mutation de")
    print("link (generateShortLink e generateBatchShortLink). Vitrine, so no portal.")
    return 0


NOME_TAREFA = "flow02-coleta-diaria"


def _schtasks(*argumentos: str) -> tuple[int, str, str]:
    """Chama o schtasks decodificando na pagina de codigo do console.

    Ferramentas de linha de comando do Windows respondem em cp850/cp1252, nao
    em UTF-8. Deixar o subprocess assumir UTF-8 estoura UnicodeDecodeError na
    primeira acentuacao -- e a saida do schtasks e traduzida.
    """
    import locale
    import subprocess

    resultado = subprocess.run(["schtasks", *argumentos], capture_output=True)
    codificacao = locale.getpreferredencoding(False) or "utf-8"

    def texto(bruto: bytes) -> str:
        return bruto.decode(codificacao, errors="replace").strip()

    return resultado.returncode, texto(resultado.stdout), texto(resultado.stderr)


def comando_agendar(args, cfg: config.Config) -> int:
    """Cria a tarefa diaria no Agendador do Windows.

    Sem coleta diaria, metade do que o programa faz nao funciona: velocidade
    de venda, menor preco historico, deteccao de link morto e a aba de
    mudancas dependem de comparar dias. Depender da memoria do usuario para
    isso e o mesmo que nao ter o recurso.
    """
    if os.name != "nt":
        print("no Linux/macOS use cron:")
        print(f"  0 {args.hora.split(':')[0]} * * *  cd {config.RAIZ} && "
              "./flow02.cmd coletar --com-conversoes")
        return 0

    if args.remover:
        codigo, _, erro = _schtasks("/delete", "/tn", NOME_TAREFA, "/f")
        print("tarefa removida" if codigo == 0 else f"nada a remover ({erro[:80]})")
        return 0

    if args.ver:
        codigo, saida, _ = _schtasks("/query", "/tn", NOME_TAREFA, "/v",
                                     "/fo", "list")
        if codigo != 0:
            print("nenhuma tarefa agendada. crie com: flow02 agendar")
            return 0
        interessa = ("Nome da tarefa", "TaskName", "Prox", "Next", "Ultim",
                     "Last", "Status", "Estado", "Result")
        for linha in saida.splitlines():
            if any(linha.strip().startswith(p) for p in interessa):
                print("  " + linha.strip())
        return 0

    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", args.hora):
        print(f"hora invalida: {args.hora} (use HH:MM)", file=sys.stderr)
        return 2

    comando = f'"{config.RAIZ / "flow02.cmd"}" coletar --com-conversoes'
    if args.com_backup:
        comando = f'cmd /c {comando} ^& "{config.RAIZ / "flow02.cmd"}" backup'

    codigo, _, erro = _schtasks(
        "/create", "/tn", NOME_TAREFA, "/tr", comando,
        "/sc", "daily", "/st", args.hora, "/f",
    )
    if codigo != 0:
        print(f"falhou: {erro[:200]}", file=sys.stderr)
        print("se disser acesso negado, abra o terminal como administrador.",
              file=sys.stderr)
        return 1

    print(f"agendado: coleta diaria as {args.hora}")
    print(f"  tarefa : {NOME_TAREFA}")
    print(f"  roda   : {comando}")
    print("\nconferir : flow02 agendar --ver")
    print("remover  : flow02 agendar --remover")
    print("\nobs: o computador precisa estar ligado no horario. Se ficar "
          "desligado, o Windows roda assim que voltar.")
    return 0


def comando_backup(args, cfg: config.Config) -> int:
    """Copia local sempre; nuvem so com --nuvem, porque depende de rede."""
    if args.restaurar:
        return _restaurar(cfg)

    resultado = backup.copiar_local(cfg.caminho_banco, manter=args.manter)
    tamanho = resultado.bytes_ / 1024 / 1024
    print(f"copia local: {resultado.caminho.name}  ({tamanho:.1f} MB)")
    if resultado.removidos:
        print(f"  {resultado.removidos} copia(s) antiga(s) removida(s), "
              f"mantendo {args.manter}")

    if not args.nuvem:
        print("\nuse --nuvem para enviar tambem ao Firebase (protege contra "
              "perder a maquina)")
        return 0
    if not cfg.firebase.configurado:
        print("firebase desabilitado -- so a copia local foi feita", file=sys.stderr)
        return 2

    with db.conectar(cfg.caminho_banco) as conexao:
        pacote = backup.exportar_para_nuvem(conexao)
    cliente = firebase.ClienteFirebase(
        cfg.firebase.url, cfg.firebase.api_key, cfg.firebase.email,
        cfg.firebase.senha, cfg.firebase.segredo, cfg.coleta.timeout_s,
    )
    try:
        cliente.escrever(f"{cfg.firebase.raiz}/backup", pacote)
    except firebase.ErroFirebase as exc:
        print(f"firebase: {exc}", file=sys.stderr)
        return 1
    contagem = pacote["contagem"]
    print(f"enviado ao Firebase: {contagem['conversoes']} conversoes, "
          f"{contagem['vitrine']} curados, {contagem['alvos']} alvos, "
          f"{contagem['cliques']} cliques")
    print("ofertas nao vao: sao recolhiveis a qualquer momento")
    return 0


def _restaurar(cfg: config.Config) -> int:
    if not cfg.firebase.configurado:
        print("firebase desabilitado", file=sys.stderr)
        return 2
    cliente = firebase.ClienteFirebase(
        cfg.firebase.url, cfg.firebase.api_key, cfg.firebase.email,
        cfg.firebase.senha, cfg.firebase.segredo, cfg.coleta.timeout_s,
    )
    try:
        pacote = cliente.ler(f"{cfg.firebase.raiz}/backup")
    except firebase.ErroFirebase as exc:
        print(f"firebase: {exc}", file=sys.stderr)
        return 1
    if not pacote:
        print("nao ha backup no Firebase")
        return 1

    print(f"backup de {pacote.get('em', '?')}")
    with db.conectar(cfg.caminho_banco) as conexao:
        contagem = backup.restaurar_da_nuvem(conexao, pacote)
    print("restaurado: " + ", ".join(f"{v} {k}" for k, v in contagem.items()))
    return 0


def comando_buscar(args, cfg: config.Config) -> int:
    """Busca por palavra-chave direto na API, fora das consultas fixas.

    As consultas do config.toml trazem sempre o mesmo ranking. Aqui voce
    procura o que quiser -- util quando ja sabe o nicho que vai divulgar em
    vez de esperar o produto aparecer no top.
    """
    consulta = config.ConsultaConfig(
        nome=f"busca:{args.termo[:30]}",
        sort_type=args.ordenar,
        keyword=args.termo,
        paginas=args.paginas,
    )
    fonte = FONTES["shopee"]
    cliente = fonte.cliente(cfg)
    limite = cfg.coleta.limite_por_pagina

    try:
        nodes = cliente.paginar(
            lambda pagina: shopee.montar_query_product_offer(
                consulta, pagina, limite, cfg.coleta.campos_produto),
            limite, consulta.paginas,
            cfg.coleta.pausa_entre_requisicoes_s, "productOfferV2",
        )
    except Exception as exc:
        print(f"[shopee] ERRO: {exc}", file=sys.stderr)
        return 1

    ofertas = [o for o in (shopee.para_oferta(n, consulta.nome) for n in nodes)
               if o is not None]
    if not ofertas:
        print(f"nada encontrado para '{args.termo}'")
        return 0

    if args.gravar:
        dia = _resolver_dia("hoje")
        calibrador = calibracao.Calibrador({}, cfg.calibracao)
        avaliadas = [(o, avaliar(o, cfg.scoring, calibrador))
                     for o in aplicar_filtros(ofertas, cfg.filtros)]
        with db.conectar(cfg.caminho_banco) as conexao:
            gravadas = db.salvar_ofertas(conexao, avaliadas, dia)
        print(f"gravadas {gravadas} ofertas no dia {dia}\n")

    ofertas.sort(key=lambda o: -(o.preco * o.taxa_comissao))
    cabecalhos = ["produto", "preco", "%", "R$/venda", "vendas", "nota", "link"]
    tabela = [
        [
            relatorio.truncar(o.nome, LIMITE_NOME),
            f"{o.preco:.2f}",
            f"{o.taxa_comissao * 100:.0f}",
            f"{o.preco * o.taxa_comissao:.2f}",
            f"{o.vendas:,}".replace(",", "."),
            f"{o.rating:.1f}" if o.rating else "-",
            o.link_oferta or "-",
        ]
        for o in ofertas[: args.top]
    ]
    _emitir(cabecalhos, tabela, args, f"Busca: {args.termo}",
            cfg.caminho_saida, "busca")
    if args.formato == "tabela" and not args.gravar:
        print("\nuse --gravar para incluir no ranking do dia")
    return 0


def comando_campanha(args, cfg: config.Config) -> int:
    """Quais produtos aguentam trafego pago no CPC que voce paga."""
    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ranking_dia(conexao, _resolver_dia(args.dia),
                                2000, 0, "shopee", "comissao")

    avaliados = []
    for linha in linhas:
        aval = campanha.avaliar(linha["comissao_valor"] or 0, args.cpc)
        if aval is None or not aval.viavel:
            continue
        avaliados.append((linha, aval))
        if len(avaliados) >= args.top:
            break

    print(f"=== o que aguenta CPC de R$ {args.cpc:.2f} ===")
    print(f"  empata quando comissao x conversao = CPC\n")
    if not avaliados:
        print("  nenhum produto viavel nesse CPC. tente um CPC menor ou")
        print("  produtos de comissao maior.")
        return 0

    cabecalhos = ["produto", "R$/venda", "conv. min", "cliques/venda",
                  "teste R$", "vendas"]
    tabela = [
        [
            relatorio.truncar(l["nome"], LIMITE_NOME),
            f"{l['comissao_valor']:.2f}",
            f"{a.conversao_necessaria * 100:.2f}%",
            f"{a.cliques_por_venda:.0f}",
            f"{campanha.orcamento_de_teste(l['comissao_valor'], args.cpc):.0f}",
            f"{l['vendas']:,}".replace(",", "."),
        ]
        for l, a in avaliados
    ]
    _emitir(cabecalhos, tabela, args,
            f"Viabilidade a CPC R$ {args.cpc:.2f}", cfg.caminho_saida, "campanha")

    if args.formato == "tabela":
        print("\nconv. min     = conversao minima para nao ter prejuizo")
        print("cliques/venda = quantos cliques ate empatar")
        print("teste R$      = orcamento para o teste render ~3 vendas;")
        print("                menos que isso, zero vendas nao prova nada")
        print(f"\nreferencia: trafego frio no Brasil costuma converter entre "
              f"{campanha.CONVERSAO_TIPICA_MIN * 100:.1f}% e "
              f"{campanha.CONVERSAO_TIPICA_MAX * 100:.1f}%")
    return 0


def comando_backtest(args, cfg: config.Config) -> int:
    """Confere se o EPC previsto tem relacao com o que de fato rendeu."""
    with db.conectar(cfg.caminho_banco) as conexao:
        resultado = backtest.avaliar(conexao, args.fatia_topo)

    print("=== o ranking preve o que rende? ===")
    print(f"  produtos com previsao e venda: {resultado.pares}")

    if not resultado.suficiente:
        print(f"\n  amostra insuficiente (minimo {backtest.AMOSTRA_MINIMA}).")
        print("  divulgue mais produtos do ranking e rode de novo -- so da para")
        print("  medir acerto sobre decisao que foi tomada.")
        return 0

    if resultado.correlacao is None:
        print("\n  sem variacao suficiente para comparar")
        return 0

    print(f"  correlacao de postos (Spearman): {resultado.correlacao:+.3f}")
    print(f"  veredito: {resultado.veredito}")

    print(f"\n  entre os {resultado.previstos_no_topo} melhores previstos, "
          f"{resultado.acertos_no_topo} renderam acima da mediana")
    print(f"  comissao media no topo previsto : R$ {resultado.comissao_topo:.2f}")
    print(f"  comissao media no resto         : R$ {resultado.comissao_resto:.2f}")
    if resultado.comissao_resto > 0:
        vezes = resultado.comissao_topo / resultado.comissao_resto
        print(f"  o topo rendeu {vezes:.1f}x o resto")

    if not resultado.confiavel:
        print(f"\n  ATENCAO: com {resultado.pares} produtos o numero ainda oscila "
              f"muito.")
        print(f"  a partir de {backtest.AMOSTRA_CONFIAVEL} da para levar a serio.")
    return 0


def comando_lojas(args, cfg: config.Config) -> int:
    """Ranqueia LOJAS, nao produtos.

    Uma loja que paga bem de forma consistente rende mais que cacar produto
    solto todo dia -- e o `remainingBudget` avisa quando a verba da campanha
    esta no fim, sinal que nao existe no nivel do produto.
    """
    fonte = FONTES["shopee"]
    try:
        lojas = fonte.coletar_lojas(cfg, args.paginas)
    except Exception as exc:
        print(f"[shopee] ERRO: {exc}", file=sys.stderr)
        return 1

    if args.taxa_min:
        lojas = [l for l in lojas if l["taxa"] * 100 >= args.taxa_min]
    lojas.sort(key=lambda l: -l["taxa"])
    if not lojas:
        print("nenhuma loja atendeu ao filtro")
        return 0

    cabecalhos = ["loja", "comissao", "nota", "orcamento", "expira", "link"]
    tabela = [
        [
            relatorio.truncar(l["nome"], LIMITE_NOME),
            f"{l['taxa'] * 100:.1f}%",
            f"{l['nota']:.1f}" if l["nota"] else "-",
            f"{l['orcamento']:,}".replace(",", ".") if l["orcamento"] else "-",
            l["expira_em"] or "-",
            l["link"] or "-",
        ]
        for l in lojas[: args.top]
    ]
    _emitir(cabecalhos, tabela, args, f"Lojas por comissao -- {len(lojas)} encontradas",
            cfg.caminho_saida, "lojas")
    if args.formato == "tabela":
        print("\norcamento = verba restante da campanha da loja. perto de zero, "
              "a comissao alta esta prestes a cair.")
    return 0


def comando_saude(args, cfg: config.Config) -> int:
    """Checagens que evitam perder venda: link morto, combinacoes, cadencia."""
    hoje = dia_brasil()
    with db.conectar(cfg.caminho_banco) as conexao:
        atrasos = db.dias_sem_aparecer(conexao, hoje)
        pares = db.pares_comprados_juntos(conexao, args.minimo)
        esperas = db.tempo_ate_comprar(conexao)
        dias_coletados = len(db.dias_disponiveis(conexao))

    print("=== disponibilidade dos itens que voce divulga ===")
    if not atrasos:
        print("  nada na vitrine nem em alerta de preco")
    else:
        # "nunca apareceu" ja e detectavel com um dia so de coleta; apenas o
        # "sumiu ha N dias" precisa de dois pontos para comparar.
        problemas = 0
        for (_, item_id), atraso in sorted(
            atrasos.items(), key=lambda p: -(p[1] if p[1] is not None else 999)
        ):
            if atraso is None:
                print(f"  {item_id:<16} NUNCA apareceu na coleta -- "
                      "possivel link morto")
                problemas += 1
            elif dias_coletados >= 2 and atraso >= args.dias_sumido:
                print(f"  {item_id:<16} sem aparecer ha {atraso} dia(s) -- "
                      "possivel link morto")
                problemas += 1
        if dias_coletados < 2:
            print(f"  ({dias_coletados} dia de coleta: para medir sumico e "
                  "preciso comparar dias -- colete amanha)")
        if not problemas:
            print(f"  todos os {len(atrasos)} itens apareceram na coleta recente")

    print("\n=== produtos comprados juntos ===")
    if not pares:
        print(f"  nenhum par apareceu {args.minimo}+ vezes. "
              "com pouca amostra, sugerir combinacao seria inventar padrao.")
    else:
        for par in pares[:10]:
            print(f"  {par['juntos']}x  {relatorio.truncar(par['nome_a'] or par['item_a'], 34)}")
            print(f"      +  {relatorio.truncar(par['nome_b'] or par['item_b'], 34)}")

    print("\n=== tempo entre o clique e a compra ===")
    if not esperas:
        print("  sem dado ainda (precisa de clickTime nas conversoes)")
    else:
        no_dia = sum(1 for d in esperas if d == 0)
        meio = esperas[len(esperas) // 2]
        print(f"  {len(esperas)} conversoes  |  mediana {meio} dia(s)  |  "
              f"{no_dia} compraram no mesmo dia ({no_dia / len(esperas) * 100:.0f}%)")
        print(f"  o cookie da Shopee dura 7 dias; "
              f"{sum(1 for d in esperas if d >= 4)} compraram do 4o dia em diante")
    return 0


def comando_alvo(args, cfg: config.Config) -> int:
    """Alerta de preco-alvo: avisa quando o produto cai abaixo do valor."""
    plataforma = (args.plataforma[0] if args.plataforma else "shopee")

    with db.conectar(cfg.caminho_banco) as conexao:
        if args.item and args.remover:
            removido = db.remover_alvo(conexao, plataforma, args.item)
            print("alvo removido" if removido else "nao havia alvo para esse item")
            return 0
        if args.item:
            if args.preco is None:
                print("informe --preco junto com --item", file=sys.stderr)
                return 2
            db.definir_alvo(conexao, plataforma, args.item, args.preco)
            print(f"alvo definido: {args.item} abaixo de {args.preco:.2f}")
            return 0
        linhas = db.alvos(conexao)

    if not linhas:
        print("nenhum alvo definido. exemplo:\n"
              "  flow02 alvo --item 18699075500 --preco 45.00")
        return 0

    cabecalhos = ["produto", "alvo", "preco atual", "falta cair", "situacao", "link"]
    tabela = []
    for linha in linhas:
        atual = linha["preco_atual"]
        if atual is None:
            situacao, falta = "sem preco coletado", "-"
        elif atual <= linha["alvo"]:
            situacao, falta = "ATINGIDO", "-"
        else:
            situacao = "aguardando"
            falta = f"{(atual - linha['alvo']) / atual * 100:.0f}%"
        tabela.append([
            relatorio.truncar(linha["nome"] or linha["item_id"], LIMITE_NOME),
            f"{linha['alvo']:.2f}",
            f"{atual:.2f}" if atual is not None else "-",
            falta,
            situacao,
            linha["link"] or "-",
        ])
    _emitir(cabecalhos, tabela, args, "Alertas de preco-alvo",
            cfg.caminho_saida, "alvos")
    return 0


def comando_cliques(args, cfg: config.Config) -> int:
    """Importa do Firebase os cliques contados pelo redirecionador."""
    if args.por_canal:
        desde = ((date.today() - timedelta(days=args.dias)).isoformat()
                 if args.dias > 0 else None)
        with db.conectar(cfg.caminho_banco) as conexao:
            linhas = db.cliques_por_canal(conexao, desde)
        if not linhas:
            print("nenhum clique registrado. publique o redirecionador no Netlify "
                  "e rode `flow02 cliques` depois.")
            return 0
        cabecalhos = ["canal", "cliques", "itens"]
        tabela = [[l["canal"], str(l["cliques"]), str(l["itens"])] for l in linhas]
        _emitir(cabecalhos, tabela, args, "Cliques por canal",
                cfg.caminho_saida, "cliques")
        return 0

    if not cfg.firebase.configurado:
        print("firebase desabilitado -- os cliques ficam la.", file=sys.stderr)
        return 2

    cliente = firebase.ClienteFirebase(
        cfg.firebase.url, cfg.firebase.api_key, cfg.firebase.email,
        cfg.firebase.senha, cfg.firebase.segredo, cfg.coleta.timeout_s,
    )
    try:
        registros = firebase.ler_cliques(cliente, cfg.firebase.raiz)
    except firebase.ErroFirebase as exc:
        print(f"firebase: {exc}", file=sys.stderr)
        return 1

    with db.conectar(cfg.caminho_banco) as conexao:
        salvos = db.salvar_cliques(conexao, registros)
    total = sum(r["total"] for r in registros)
    print(f"importados {salvos} registro(s), {total} clique(s) no total")
    if salvos:
        print("rode `flow02 calibrar` para o cvr passar a usar dado medido")
    return 0


def comando_calibrar(args, cfg: config.Config) -> int:
    with db.conectar(cfg.caminho_banco) as conexao:
        tem_conversao = conexao.execute(
            "SELECT COUNT(*) AS n FROM conversao"
        ).fetchone()["n"]
        cliques = calibracao.ha_cliques(conexao)
        resumo = calibracao.recalcular(conexao, cfg.calibracao)

    if not cliques:
        print("a calibracao de CVR precisa de cliques, e a API de afiliados da "
              "Shopee nao expoe esse dado.")
        print("confirmado por introspecao do schema: existem relatorios de "
              "oferta, conversao e pedido -- nenhum de trafego.")
        print(f"\no cvr no ranking segue heuristico (marcado com *). "
              f"o que da para medir e comissao realizada: {tem_conversao} "
              "conversao(oes) gravada(s).")
        print("veja por produto com: flow02 ganhos --por-item")
        return 0
    if not resumo["total"]:
        print(f"nenhum escopo atingiu {cfg.calibracao.cliques_minimos} cliques. "
              "colete mais conversoes ou reduza [calibracao].cliques_minimos.")
        return 0
    print(f"cvr calibrado em {resumo['total']} escopo(s): "
          f"item={resumo['item']} loja={resumo['loja']} global={resumo['global']}")
    print("as proximas coletas usarao o cvr medido no lugar da heuristica.")
    return 0


def comando_web(args, cfg: config.Config) -> int:
    from .web import iniciar

    return iniciar(cfg, args.porta, not args.sem_navegador, args.rede)


def comando_publicar(args, cfg: config.Config) -> int:
    from .web import publicar

    try:
        if args.netlify:
            pacote = publicar.preparar_netlify(
                cfg, com_vitrine=not args.so_redirecionador)
            print(_instrucoes_netlify(cfg, pacote))
            return 0
        destino = publicar.escrever(cfg, args.saida)
    except publicar.ErroPublicacao as exc:
        print(f"nao consegui gerar a pagina: {exc}", file=sys.stderr)
        return 2
    print(publicar.instrucoes(cfg, destino))
    return 0


def _instrucoes_netlify(cfg: config.Config, pacote: dict) -> str:
    com_vitrine = pacote.get("com_vitrine", True)
    linhas = [
        "pacote pronto para o Netlify:",
        f"  pagina   {pacote['indice']}"
        + ("" if com_vitrine else "   (em branco, so o redirecionador sobe)"),
        f"  funcao   {pacote['funcao']}",
        f"  config   {pacote['config']}",
        "",
        "COMO O GIT ESTA CONFIGURADO",
        "  o netlify.toml aponta publish = 'publicado', entao essa pasta",
        "  PRECISA estar versionada. Confira com:",
        "     git status --short publicado",
        "  se nao aparecer nada e o deploy subir vazio, ela esta no .gitignore.",
        "",
        "PUBLICANDO",
        "  com a integracao de deploy que voce ja fez, basta:",
        "     git add publicado && git commit -m 'pagina' && git push",
        "  o Netlify constroi sozinho a cada push.",
        "",
        "VARIAVEIS DE AMBIENTE (Site settings > Environment variables)",
        f"     FIREBASE_DATABASE_URL = {cfg.firebase.url or '(sua url do RTDB)'}",
        f"     FLOW02_RAIZ           = {cfg.firebase.raiz}",
        "     FIREBASE_DB_SECRET    = (segredo do banco, se as regras exigirem auth)",
        "  a Function le daqui, no servidor. Nada disso vai para o navegador.",
        "",
        "REGRAS DO FIREBASE",
        "  cole o conteudo de firebase-regras.json no console do Firebase,",
        "  em Realtime Database > Regras. Sem isso o banco fica aberto para",
        "  qualquer pessoa que descubra a URL.",
        "",
        "O QUE NAO SOBE",
        "  o painel de controle. Ele e um servidor Python que le o SQLite da",
        "  sua maquina e executa comandos -- o Netlify so hospeda arquivo",
        "  estatico e funcao em JavaScript. Para usar no celular, rode aqui",
        "  `flow02 web --rede` e acesse pelo IP local.",
        "",
        "depois disso, os links /r/<codigo> contam clique antes de redirecionar,",
        "e `flow02 cliques` traz os numeros de volta para o CVR real.",
    ]
    if not com_vitrine:
        linhas[4:4] = [
            "",
            "MODO SO REDIRECIONADOR",
            "  a pagina publicada fica em branco e marcada como noindex.",
            "  o contador de cliques funciona igual; o que voce abre mao e de",
            "  navegar a vitrine pelo celular -- que o `flow02 web --rede`",
            "  resolve sem expor nada.",
        ]
    return "\n".join(linhas)


def comando_link(args, cfg: config.Config) -> int:
    if len(args.sub_id) > 5:
        raise SystemExit("a Shopee aceita no maximo 5 sub-ids")
    plataforma = args.plataforma[0] if args.plataforma else _detectar_plataforma(args.url)

    if plataforma == "mercadolivre":
        elegivel, motivo = ml.validar_url_afiliado(args.url)
        item_id = ml.extrair_item_id(args.url)
        print(f"plataforma : mercadolivre")
        print(f"item id    : {item_id or '(nao identificado)'}")
        print(f"elegivel   : {'sim' if elegivel else 'NAO'} -- {motivo}")
        if elegivel:
            print("\nO ML nao tem API de geracao de link. Cole esta URL no "
                  "Gerador de Links do Portal do Afiliado ou use a Barra de Afiliados.")
        return 0 if elegivel else 1

    fonte = FONTES["shopee"]
    try:
        registro = fonte.gerar_link(cfg, args.url, args.sub_id)
    except FonteIndisponivel as exc:
        print(f"shopee indisponivel: {exc}", file=sys.stderr)
        return 1
    with db.conectar(cfg.caminho_banco) as conexao:
        db.salvar_link(conexao, "shopee", registro)
    print(registro["link_curto"])
    return 0


def _detectar_plataforma(url: str) -> str:
    return "mercadolivre" if "mercadoliv" in url or "mercadolib" in url else "shopee"


def comando_top(args, cfg: config.Config) -> int:
    dia = _resolver_dia(args.dia)
    pedido = cfg.ranking.top if args.top is None else args.top
    limite = 1_000_000 if pedido == 0 else pedido

    with db.conectar(cfg.caminho_banco) as conexao:
        linhas = db.ranking_dia(
            conexao, dia, limite,
            0 if args.sem_diversificar else cfg.ranking.max_por_loja,
            _uma_plataforma(args), args.ordenar,
        )
        if not linhas:
            disponiveis = db.dias_disponiveis(conexao)
            print(f"nenhuma oferta gravada em {dia}. dias disponiveis: "
                  f"{disponiveis[:7] or 'nenhum -- rode `coletar` primeiro'}")
            return 0
        links = _resolver_links(conexao, cfg, linhas, args.sub_id, args.com_link)
        series = db.serie_de_precos(
            conexao, _uma_plataforma(args),
            (date.fromisoformat(dia) - timedelta(days=cfg.preco.janela_dias)).isoformat(),
        )

    descontos = {
        linha["item_id"]: analise.avaliar_desconto(
            linha["preco"], linha["desconto_pct"],
            series.get((linha["plataforma"], linha["item_id"]), []), cfg.preco,
        )
        for linha in linhas
    }
    if args.so_desconto_real:
        linhas = [
            l for l in linhas
            if not analise.desconto_inflado(descontos[l["item_id"]], cfg.preco)
        ]
        if not linhas:
            print("todos os itens do dia tem desconto inflado pelo criterio atual.")
            return 0

    cabecalhos = ["#", "produto", "preco", "com%", "R$/venda", "EPC", "cvr%",
                  "desc%", "vendas", "nota", "loja", "link de afiliado"]
    tabela = [
        [
            str(posicao),
            relatorio.truncar(linha["nome"], LIMITE_NOME),
            f"{linha['preco']:.2f}",
            f"{linha['taxa_comissao'] * 100:.1f}",
            f"{linha['comissao_valor']:.2f}",
            f"{linha['score']:.4f}",
            f"{linha['cvr_estimado'] * 100:.2f}{'' if linha['calibrado'] else '*'}",
            _celula_desconto(descontos[linha["item_id"]], cfg),
            str(linha["vendas"] or 0),
            f"{linha['rating']:.1f}" if linha["rating"] else "-",
            relatorio.truncar(linha["loja_nome"] or "-", 18),
            links.get(linha["item_id"], "-"),
        ]
        for posicao, linha in enumerate(linhas, start=1)
    ]
    itens_post = [
        {
            "nome": linha["nome"],
            "preco": linha["preco"],
            "desconto_pct": linha["desconto_pct"],
            "vendas": linha["vendas"],
            "rating": linha["rating"],
            "comissao_valor": linha["comissao_valor"],
            "taxa_comissao": linha["taxa_comissao"],
            "link": links.get(linha["item_id"], "-"),
        }
        for linha in linhas
    ]
    _emitir(cabecalhos, tabela, args,
            f"Top {len(tabela)} por {ROTULO_ORDEM[args.ordenar]} -- {dia}",
            cfg.caminho_saida, f"top_{dia}", itens_post)
    if args.formato == "tabela":
        print("\nEPC = R$ por clique estimado (comissao x cvr). "
              "cvr com * = heuristica; sem * = medido via `calibrar`.")
        print(f"desc% = desconto real contra a mediana de {cfg.preco.janela_dias} "
              "dias. '?' = historico insuficiente, '!' = anuncio inflado.")
        if not args.com_link:
            print("os links acima ja sao de afiliado; use --com-link para "
                  "encurtar e adicionar sub-ids de campanha.")
    return 0


def comando_historico(args, cfg: config.Config) -> int:
    hoje = date.fromisoformat(dia_brasil())
    with db.conectar(cfg.caminho_banco) as conexao:
        dias = db.dias_disponiveis(conexao)
        registros = db.historico(
            conexao, cfg.ranking.dias_minimos_historico, _uma_plataforma(args)
        )

    if not registros:
        print(f"sem historico suficiente (minimo {cfg.ranking.dias_minimos_historico} "
              f"dias distintos). dias coletados: {len(dias)}")
        return 0

    total_dias = len(dias) or 1
    ordenados = sorted(
        (
            (
                score_historico(
                    r.score_medio, r.dias_visto, total_dias,
                    (hoje - date.fromisoformat(r.ultimo_dia)).days,
                ),
                r,
            )
            for r in registros
        ),
        key=lambda par: par[0],
        reverse=True,
    )
    pedido = cfg.ranking.top if args.top is None else args.top
    if pedido:
        ordenados = ordenados[:pedido]

    cabecalhos = ["#", "produto", "dias", "score", "EPC medio", "R$/venda",
                  "preco medio", "vendas+", "1o dia", "ultimo", "link"]
    tabela = [
        [
            str(posicao),
            relatorio.truncar(r.nome, LIMITE_NOME),
            f"{r.dias_visto}/{total_dias}",
            f"{score:.4f}",
            f"{r.score_medio:.4f}",
            f"{r.comissao_media:.2f}",
            f"{r.preco_medio:.2f}",
            str(r.vendas_delta if r.vendas_delta is not None else "-"),
            r.primeiro_dia,
            r.ultimo_dia,
            r.link_oferta or "-",
        ]
        for posicao, (score, r) in enumerate(ordenados, start=1)
    ]
    itens_post = [
        {
            "nome": r.nome,
            "preco": r.preco_medio,
            "comissao_valor": r.comissao_media,
            "taxa_comissao": r.comissao_media / r.preco_medio if r.preco_medio else 0.0,
            "vendas": r.vendas_delta,
            "link": r.link_oferta or "-",
        }
        for _, r in ordenados
    ]
    _emitir(cabecalhos, tabela, args,
            f"Ranking acumulado -- {total_dias} dia(s) coletado(s)",
            cfg.caminho_saida, "historico", itens_post)
    if args.formato == "tabela":
        print("\nscore = EPC medio ponderado por persistencia no topo e atualidade. "
              "'vendas+' = crescimento de vendas no periodo observado.")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flow02",
        description="Curadoria automatizada de ofertas de afiliado (Shopee + ML).",
    )
    parser.add_argument("--version", action="version", version=f"flow02 {__version__}")
    parser.add_argument("-v", "--verboso", action="store_true")
    subparsers = parser.add_subparsers(dest="comando", required=True)

    def plataforma(sub):
        sub.add_argument("--plataforma", action="append",
                         choices=sorted(FONTES), help="repita para varias")
        return sub

    def saida(sub):
        sub.add_argument("--top", type=int, help="0 = todos")
        sub.add_argument("--formato", choices=("tabela", "csv", "md", "post"),
                         default="tabela")
        sub.add_argument("--saida", type=Path)
        return sub

    plataforma(subparsers.add_parser("doctor", help="valida credenciais e APIs"))

    coletar = plataforma(subparsers.add_parser(
        "coletar", help="coleta ofertas e grava o snapshot do dia"))
    coletar.add_argument("--dia", default="hoje", help="YYYY-MM-DD ou 'hoje'")
    coletar.add_argument("--com-conversoes", action="store_true",
                         help="tambem baixa conversionReport e validatedReport")
    coletar.add_argument("--quantidade", type=int,
                         help="quantos produtos buscar por consulta")
    coletar.add_argument("--limite", type=int,
                         help="itens por pagina da API (max 100)")
    coletar.add_argument("--sem-retomar", action="store_true",
                         help="ignora as paginas ja baixadas e busca tudo de novo")

    top = saida(plataforma(subparsers.add_parser("top", help="ranking do dia")))
    top.add_argument("--dia", default="hoje", help="YYYY-MM-DD ou 'hoje'")
    top.add_argument("--sem-diversificar", action="store_true",
                     help="desliga o limite de itens por loja")
    top.add_argument("--com-link", action="store_true",
                     help="encurta o link de afiliado via generateShortLink")
    top.add_argument("--sub-id", action="append", default=[],
                     help="ate 5, viram utm_content no link curto")
    top.add_argument("--so-desconto-real", action="store_true",
                     help="esconde itens cujo desconto anunciado e inflado")
    top.add_argument("--ordenar", choices=tuple(db.ORDENACOES), default="epc",
                     help="epc=retorno por clique, comissao=R$ por venda, "
                          "vendas=mais vendidos")

    saida(plataforma(subparsers.add_parser("historico", help="ranking acumulado")))

    ganhos = saida(plataforma(subparsers.add_parser(
        "ganhos", help="retorno realizado por dia")))
    ganhos.add_argument("--dias", type=int, default=30, help="0 = tudo")
    ganhos.add_argument("--validadas", action="store_true",
                        help="so comissao definitiva")
    ganhos.add_argument("--por-item", action="store_true",
                        help="agrupa por produto em vez de por dia")

    subparsers.add_parser("calibrar", help="recalcula o cvr medido")

    agenda = subparsers.add_parser(
        "agendar", help="cria a coleta diaria no Agendador do Windows")
    agenda.add_argument("--hora", default="07:00", help="HH:MM")
    agenda.add_argument("--com-backup", action="store_true",
                        help="faz backup logo depois da coleta")
    agenda.add_argument("--remover", action="store_true")
    agenda.add_argument("--ver", action="store_true")

    copia = subparsers.add_parser(
        "backup", help="copia de seguranca do banco, local e no Firebase")
    copia.add_argument("--nuvem", action="store_true",
                       help="envia tambem para o Firebase")
    copia.add_argument("--manter", type=int, default=7,
                       help="quantas copias locais preservar")
    copia.add_argument("--restaurar", action="store_true",
                       help="traz o backup do Firebase de volta para o banco")

    busca = saida(subparsers.add_parser(
        "buscar", help="procura produtos por palavra-chave na API"))
    busca.add_argument("termo", help="o que procurar, ex: 'creatina'")
    busca.add_argument("--paginas", type=int, default=2)
    busca.add_argument("--ordenar", type=int, default=5,
                       help="5=maior comissao 2=mais vendidos 4=menor preco")
    busca.add_argument("--gravar", action="store_true",
                       help="inclui os achados no ranking do dia")

    camp = saida(subparsers.add_parser(
        "campanha", help="quais produtos aguentam trafego pago no seu CPC"))
    camp.add_argument("--cpc", type=float, default=1.0,
                      help="quanto voce paga por clique (veja no gerenciador)")
    camp.add_argument("--dia", default="hoje")

    prova = subparsers.add_parser(
        "backtest", help="mede se o EPC previsto acertou o que rendeu")
    prova.add_argument("--fatia-topo", type=float, default=0.3,
                       help="que fracao contar como 'topo previsto'")

    lojas = saida(subparsers.add_parser(
        "lojas", help="ranqueia lojas por comissao, com verba restante"))
    lojas.add_argument("--paginas", type=int, default=2)
    lojas.add_argument("--taxa-min", type=float, default=0.0,
                       help="comissao minima em porcentagem")

    saude = subparsers.add_parser(
        "saude", help="link morto, combinacoes de produto e cadencia de repost")
    saude.add_argument("--dias-sumido", type=int, default=2,
                       help="a partir de quantos dias sem aparecer virar alerta")
    saude.add_argument("--minimo", type=int, default=2,
                       help="quantas vezes um par precisa ter acontecido")

    vitrine = saida(plataforma(subparsers.add_parser(
        "vitrine", help="selecao curada de produtos para a pagina publica")))
    vitrine.add_argument("--item", help="item_id do produto")
    vitrine.add_argument("--colecao", help="agrupa em uma colecao (ex: casa)")
    vitrine.add_argument("--remover", action="store_true")
    vitrine.add_argument("--para-shopee", action="store_true",
                         help="lista os links para montar a mesma vitrine no "
                              "portal da Shopee (a API nao permite automatizar)")

    alvo = saida(plataforma(subparsers.add_parser(
        "alvo", help="alerta quando o preco cair abaixo de um valor")))
    alvo.add_argument("--item", help="item_id do produto")
    alvo.add_argument("--preco", type=float, help="valor alvo em reais")
    alvo.add_argument("--remover", action="store_true")

    cliques = saida(subparsers.add_parser(
        "cliques", help="importa os cliques contados pelo redirecionador"))
    cliques.add_argument("--por-canal", action="store_true")
    cliques.add_argument("--dias", type=int, default=30, help="0 = tudo")

    sincronizar = plataforma(subparsers.add_parser(
        "sincronizar", help="espelha o resultado no Firebase Realtime Database"))
    sincronizar.add_argument("--dia", default="hoje", help="YYYY-MM-DD ou 'hoje'")

    alertas = saida(plataforma(subparsers.add_parser(
        "alertas", help="o que mudou desde o snapshot anterior")))
    alertas.add_argument("--dia", default="hoje", help="YYYY-MM-DD ou 'hoje'")

    saida(plataforma(subparsers.add_parser(
        "categorias", help="ids de categoria vistos, para configurar a varredura")))

    notificar = plataforma(subparsers.add_parser(
        "notificar", help="envia o resultado para o Telegram"))
    notificar.add_argument("--tipo", choices=("top", "alertas"), default="top")
    notificar.add_argument("--dia", default="hoje", help="YYYY-MM-DD ou 'hoje'")
    notificar.add_argument("--descobrir-chat", action="store_true",
                           help="lista os chat_id disponiveis para o seu bot")

    web = subparsers.add_parser("web", help="abre o painel local no navegador")
    web.add_argument("--porta", type=int, default=8765)
    web.add_argument("--sem-navegador", action="store_true")
    web.add_argument("--rede", action="store_true",
                     help="tambem aceita conexoes da rede local (para abrir no "
                          "celular). O painel executa comandos: use so em rede "
                          "de casa")

    publicar = subparsers.add_parser(
        "publicar", help="gera a pagina que le o Firebase (abre no celular)")
    publicar.add_argument("--saida", type=Path)
    publicar.add_argument("--netlify", action="store_true",
                          help="monta a pasta publicado/ para deploy no Netlify")
    publicar.add_argument("--so-redirecionador", action="store_true",
                          help="publica so o contador de cliques, sem a vitrine: "
                               "nao expoe a URL do Firebase no navegador")

    link = plataforma(subparsers.add_parser(
        "link", help="gera link curto (Shopee) ou valida URL (ML)"))
    link.add_argument("url")
    link.add_argument("--sub-id", action="append", default=[],
                      help="ate 5, viram utm_content no link")
    return parser


COMANDOS = {
    "doctor": comando_doctor,
    "coletar": comando_coletar,
    "top": comando_top,
    "historico": comando_historico,
    "ganhos": comando_ganhos,
    "calibrar": comando_calibrar,
    "alvo": comando_alvo,
    "vitrine": comando_vitrine,
    "saude": comando_saude,
    "lojas": comando_lojas,
    "backtest": comando_backtest,
    "campanha": comando_campanha,
    "buscar": comando_buscar,
    "backup": comando_backup,
    "agendar": comando_agendar,
    "cliques": comando_cliques,
    "sincronizar": comando_sincronizar,
    "alertas": comando_alertas,
    "categorias": comando_categorias,
    "notificar": comando_notificar,
    "web": comando_web,
    "publicar": comando_publicar,
    "link": comando_link,
}


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    _log_config(args.verboso)
    try:
        cfg = config.carregar()
    except config.ConfigInvalida as exc:
        print(f"config.toml invalido: {exc}", file=sys.stderr)
        return 2
    return COMANDOS[args.comando](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
