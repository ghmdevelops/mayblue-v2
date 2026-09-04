"""Identifica quando vale a pena divulgar um produto agora.

Cada sinal isolado engana:

- EPC alto sozinho pode ser produto que ninguem compra;
- menor preco sozinho pode ser produto de comissao irrisoria;
- vendendo rapido sozinho pode ser item de R$ 10 que paga centavos;
- desconto grande sozinho costuma ser preco inflado antes da promocao.

O que identifica oportunidade e a coincidencia deles. Por isso aqui nao ha
nota unica ponderada: ha criterios explicitos, cada um verdadeiro ou falso,
e o motivo aparece na tela. Nota unica esconde o porque; lista de criterios
deixa voce discordar de um deles.

Criterio que nao pode ser avaliado (falta historico, por exemplo) nao conta
nem a favor nem contra -- fica de fora da conta.
"""

from __future__ import annotations

from dataclasses import dataclass

# Vender rapido depende da faixa de preco: 50 unidades/dia num produto de
# R$ 300 e muito mais significativo que num de R$ 15.
VELOCIDADE_MINIMA = 5.0
COMISSAO_MINIMA = 5.0       # abaixo disso o esforco de divulgar nao paga

# O corte de EPC e um percentil, nao uma fracao do maximo.
#
# Medido no dado real: o melhor EPC do dia era 1,5659 e a mediana 0,1100 --
# o topo e 14x a mediana. Usar "metade do maximo" colocava o corte em 0,7830,
# que na pratica e o percentil 99, e so 3 produtos de 400 passavam. Um unico
# item excepcional definia o criterio para todos os outros.
#
# Percentil e robusto a outlier: o corte acompanha a distribuicao do dia.
PERCENTIL_EPC = 75


def corte_de_epc(itens) -> float:
    """EPC no percentil configurado, entre os itens com EPC positivo."""
    valores = sorted(v for v in ((i.get("epc") or 0) for i in itens) if v > 0)
    if not valores:
        return 0.0
    posicao = min(len(valores) - 1, int(len(valores) * PERCENTIL_EPC / 100))
    return valores[posicao]


@dataclass(frozen=True, slots=True)
class Criterio:
    nome: str
    atendido: bool
    detalhe: str


@dataclass(frozen=True, slots=True)
class Oportunidade:
    criterios: tuple[Criterio, ...]

    @property
    def avaliaveis(self) -> tuple[Criterio, ...]:
        return self.criterios

    @property
    def atendidos(self) -> int:
        return sum(1 for c in self.criterios if c.atendido)

    @property
    def total(self) -> int:
        return len(self.criterios)

    @property
    def forte(self) -> bool:
        """Precisa de pelo menos 3 criterios avaliados e todos atendidos.

        Exigir todos evita o caso em que um sinal muito bom carrega dois
        ruins. Exigir 3 avaliados evita marcar como oportunidade um produto
        do qual mal se sabe alguma coisa.
        """
        return self.total >= 3 and self.atendidos == self.total

    @property
    def quase(self) -> bool:
        """Falta exatamente um criterio.

        Vale mostrar porque muitas vezes o que falta e algo que voce sabe
        avaliar melhor que o programa -- por exemplo, um produto sazonal que
        esta parado hoje mas vai vender no fim de semana.
        """
        return self.total >= 3 and self.atendidos == self.total - 1

    @property
    def motivos(self) -> tuple[str, ...]:
        return tuple(c.detalhe for c in self.criterios if c.atendido)

    @property
    def impedimentos(self) -> tuple[str, ...]:
        return tuple(c.detalhe for c in self.criterios if not c.atendido)


def avaliar_oportunidade(item: dict, corte_epc: float) -> Oportunidade:
    """`item` e o dicionario que a API do painel devolve para cada produto.

    `corte_epc` vem de `corte_de_epc()`, calculado sobre a lista do dia.
    """
    criterios: list[Criterio] = []

    def juntar(nome: str, atendido: bool, detalhe: str) -> None:
        criterios.append(Criterio(nome, atendido, detalhe))

    comissao = item.get("comissao_valor") or 0
    juntar(
        "comissao",
        comissao >= COMISSAO_MINIMA,
        f"paga R$ {comissao:.2f} por venda",
    )

    epc = item.get("epc") or 0
    if corte_epc > 0:
        acima = epc >= corte_epc
        juntar(
            "epc",
            acima,
            f"EPC {epc:.4f}, {'acima' if acima else 'abaixo'} do corte "
            f"{corte_epc:.4f} (top {100 - PERCENTIL_EPC}% do dia)",
        )

    # Preco: so avalia quando ha historico suficiente para saber.
    if item.get("preco_minimo") is not None:
        juntar(
            "preco",
            bool(item.get("menor_preco")),
            "esta no menor preco do historico" if item.get("menor_preco")
            else f"ja esteve por R$ {item['preco_minimo']:.2f}",
        )

    velocidade = item.get("velocidade")
    if velocidade:
        por_dia = velocidade.get("por_dia") or 0
        juntar(
            "tracao",
            por_dia >= VELOCIDADE_MINIMA,
            f"vendendo {por_dia:.0f} por dia",
        )

    if item.get("indisponivel") is not None:
        juntar(
            "disponivel",
            not item.get("indisponivel"),
            "aparece na coleta atual" if not item.get("indisponivel")
            else "sumiu da coleta -- link pode estar morto",
        )

    dias = item.get("dias_ate_expirar")
    if dias is not None:
        juntar(
            "validade",
            dias > 3,
            f"oferta valida por mais {dias} dia(s)" if dias > 3
            else f"a oferta {'expirou' if dias < 0 else f'expira em {dias} dia(s)'}",
        )

    if item.get("desconto_confiavel"):
        inflado = bool(item.get("desconto_inflado"))
        juntar(
            "honestidade",
            not inflado,
            "desconto conferido" if not inflado else "desconto inflado pela loja",
        )

    return Oportunidade(tuple(criterios))
