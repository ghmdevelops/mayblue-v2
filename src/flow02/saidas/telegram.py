"""Envio para o Telegram via Bot API.

Rodar comando manual todo dia nao vira habito; receber a lista as 7h vira.
Isso nao melhora a qualidade do dado -- melhora a chance de o dado ser usado.

A Bot API e REST pura, entao funciona com a stdlib.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..rede import ErroHTTP, requisitar

BASE = "https://api.telegram.org/bot"
LIMITE_MENSAGEM = 4096


class ErroTelegram(RuntimeError):
    pass


def dividir(texto: str, limite: int = LIMITE_MENSAGEM) -> list[str]:
    """Quebra em pedacos respeitando o limite, sem cortar linha no meio."""
    if len(texto) <= limite:
        return [texto] if texto else []

    partes: list[str] = []
    atual: list[str] = []
    tamanho = 0
    for bloco in texto.split("\n\n"):
        pedaco = bloco if len(bloco) <= limite else bloco[:limite]
        if tamanho + len(pedaco) + 2 > limite and atual:
            partes.append("\n\n".join(atual))
            atual, tamanho = [], 0
        atual.append(pedaco)
        tamanho += len(pedaco) + 2
    if atual:
        partes.append("\n\n".join(atual))
    return partes


def enviar(
    token: str,
    chat_id: str,
    texto: str,
    timeout_s: float = 30.0,
    desabilitar_previa: bool = True,
) -> int:
    """Envia o texto, quebrando em varias mensagens se preciso."""
    if not token:
        raise ErroTelegram("TELEGRAM_BOT_TOKEN ausente")
    if not chat_id:
        raise ErroTelegram("chat_id ausente")

    partes = dividir(texto)
    for parte in partes:
        corpo = json.dumps({
            "chat_id": chat_id,
            "text": parte,
            "disable_web_page_preview": desabilitar_previa,
        }).encode("utf-8")
        try:
            requisitar(
                f"{BASE}{token}/sendMessage", dados=corpo, metodo="POST",
                cabecalhos={"Content-Type": "application/json"}, timeout_s=timeout_s,
            )
        except ErroHTTP as exc:
            if exc.status == 401:
                raise ErroTelegram("401: token do bot invalido") from exc
            if exc.status == 400 and "chat not found" in exc.corpo:
                raise ErroTelegram(
                    f"chat {chat_id} nao encontrado. Envie /start para o bot primeiro "
                    "e confira o chat_id em api.telegram.org/bot<TOKEN>/getUpdates"
                ) from exc
            raise ErroTelegram(f"envio falhou: {exc}") from exc
    return len(partes)


def descobrir_chats(token: str, timeout_s: float = 15.0) -> list[dict]:
    """Lista os chats que ja falaram com o bot.

    O chat_id nao aparece em lugar nenhum da interface do Telegram; a unica
    forma de descobrir e ler o getUpdates depois de mandar uma mensagem.
    """
    if not token:
        raise ErroTelegram("TELEGRAM_BOT_TOKEN ausente")
    try:
        resposta = requisitar(f"{BASE}{token}/getUpdates", timeout_s=timeout_s).json()
    except ErroHTTP as exc:
        if exc.status == 401:
            raise ErroTelegram("401: token do bot invalido") from exc
        raise ErroTelegram(f"getUpdates falhou ({exc.status})") from exc

    vistos: dict[str, dict] = {}
    for atualizacao in resposta.get("result") or []:
        mensagem = (atualizacao.get("message") or atualizacao.get("channel_post")
                    or atualizacao.get("my_chat_member") or {})
        chat = mensagem.get("chat") or {}
        if not chat.get("id"):
            continue
        vistos[str(chat["id"])] = {
            "chat_id": str(chat["id"]),
            "tipo": chat.get("type", "?"),
            "nome": chat.get("title") or " ".join(filter(None, (
                chat.get("first_name"), chat.get("last_name")))) or chat.get("username")
            or "(sem nome)",
        }
    return list(vistos.values())


def verificar(token: str, timeout_s: float = 15.0) -> dict:
    """Chama getMe para validar o token. Usado pelo `doctor`."""
    if not token:
        raise ErroTelegram("TELEGRAM_BOT_TOKEN ausente")
    try:
        resposta = requisitar(f"{BASE}{token}/getMe", timeout_s=timeout_s).json()
    except ErroHTTP as exc:
        raise ErroTelegram(f"getMe falhou ({exc.status})") from exc
    return resposta.get("result") or {}


def montar_alertas(alertas: Sequence, dia: str, limite: int = 20) -> str:
    if not alertas:
        return f"flow02 {dia}\nNenhuma mudanca relevante desde o dia anterior."

    linhas = [f"flow02 - mudancas em {dia}", ""]
    for alerta in alertas[:limite]:
        variacao = (f"  ({alerta.variacao * 100:+.0f}%)"
                    if alerta.variacao is not None else "")
        linhas.append(f"[{alerta.rotulo}] {alerta.nome}{variacao}")
        if alerta.antes is not None and alerta.depois is not None:
            linhas.append(f"   {alerta.antes:.4g} -> {alerta.depois:.4g}")
        if alerta.link:
            linhas.append(f"   {alerta.link}")
        linhas.append("")
    if len(alertas) > limite:
        linhas.append(f"... e mais {len(alertas) - limite} mudanca(s)")
    return "\n".join(linhas)
