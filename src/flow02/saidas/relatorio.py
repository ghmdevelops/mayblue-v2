"""Renderizacao de rankings em tabela de terminal, markdown e CSV."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

Linha = Sequence[str]


def _larguras(cabecalhos: Linha, linhas: Sequence[Linha]) -> list[int]:
    larguras = [len(c) for c in cabecalhos]
    for linha in linhas:
        for indice, celula in enumerate(linha):
            larguras[indice] = max(larguras[indice], len(celula))
    return larguras


def truncar(texto: str, limite: int) -> str:
    texto = " ".join(str(texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "\u2026"


def renderizar_tabela(cabecalhos: Linha, linhas: Sequence[Linha]) -> str:
    if not linhas:
        return "(nenhum resultado)"
    larguras = _larguras(cabecalhos, linhas)
    separador = "  "
    partes = [
        separador.join(c.ljust(l) for c, l in zip(cabecalhos, larguras)),
        separador.join("-" * l for l in larguras),
    ]
    partes.extend(
        separador.join(celula.ljust(l) for celula, l in zip(linha, larguras))
        for linha in linhas
    )
    return "\n".join(partes)


def renderizar_markdown(cabecalhos: Linha, linhas: Sequence[Linha]) -> str:
    if not linhas:
        return "_(nenhum resultado)_"
    cabecalho = "| " + " | ".join(cabecalhos) + " |"
    divisor = "| " + " | ".join("---" for _ in cabecalhos) + " |"
    corpo = [
        "| " + " | ".join(str(c).replace("|", "\\|") for c in linha) + " |"
        for linha in linhas
    ]
    return "\n".join([cabecalho, divisor, *corpo])


def escrever_csv(cabecalhos: Linha, linhas: Sequence[Linha], caminho: Path) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(cabecalhos)
        escritor.writerows(linhas)
    return caminho


def moeda(valor: float) -> str:
    inteiro, _, centavos = f"{valor:,.2f}".partition(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def renderizar_post(itens: Sequence[dict]) -> str:
    """Blocos prontos para colar em Telegram, WhatsApp ou descricao de video."""
    if not itens:
        return "(nenhum resultado)"
    blocos = []
    for posicao, item in enumerate(itens, start=1):
        cabecalho = f"{posicao}. {item['nome']}"
        preco = moeda(item["preco"])
        if item.get("desconto_pct"):
            preco += f"  ({item['desconto_pct']:.0f}% OFF)"
        detalhe = f"   {preco}"
        if item.get("vendas"):
            detalhe += f"  |  {item['vendas']} vendidos"
        if item.get("rating"):
            detalhe += f"  |  nota {item['rating']:.1f}"
        comissao = (f"   sua comissao: {moeda(item['comissao_valor'])} "
                    f"({item['taxa_comissao'] * 100:.1f}%)")
        blocos.append("\n".join([cabecalho, detalhe, comissao, f"   {item['link']}"]))
    return "\n\n".join(blocos)


def escrever_texto(conteudo: str, caminho: Path) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho
