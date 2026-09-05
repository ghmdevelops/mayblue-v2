"""Quanto precisa converter para o anuncio nao dar prejuizo.

Para trafego organico, o que manda e volume: voce nao paga pelo clique, entao
produto que vende muito ganha. Para trafego PAGO a conta inverte -- cada
clique custa, e comissao alta tolera conversao baixa.

    custo por clique  =  CPC
    receita por clique =  comissao x taxa_de_conversao

    empata quando:  comissao x conversao = CPC
    logo:           conversao_necessaria = CPC / comissao

Um produto de R$ 255 de comissao a R$ 1,00 o clique precisa converter 0,39%.
Um de R$ 36 precisa de 2,72% -- sete vezes mais. Pelo criterio de volume o
segundo parece melhor; pelo criterio de anuncio pago, o primeiro e o que
aguenta errar.

Nada aqui inventa numero: o CPC e o que VOCE observou no gerenciador de
anuncios, e a comissao vem da API. O que a funcao faz e a divisao que decide.
"""

from __future__ import annotations

from dataclasses import dataclass

# Faixas de referencia para trafego frio no Brasil. Nao sao promessa: servem
# para dizer se a conversao exigida esta dentro do plausivel ou fora.
CONVERSAO_TIPICA_MIN = 0.005   # 0,5%
CONVERSAO_TIPICA_MAX = 0.02    # 2,0%


@dataclass(frozen=True, slots=True)
class Viabilidade:
    comissao: float
    cpc: float
    conversao_necessaria: float
    cliques_por_venda: float

    @property
    def veredito(self) -> str:
        if self.conversao_necessaria <= CONVERSAO_TIPICA_MIN:
            return "folga grande: converte bem abaixo do tipico e ainda paga"
        if self.conversao_necessaria <= CONVERSAO_TIPICA_MAX:
            return "viavel: precisa de conversao dentro do tipico"
        if self.conversao_necessaria <= CONVERSAO_TIPICA_MAX * 2:
            return "apertado: exige conversao acima do tipico"
        return "provavel prejuizo: exigiria conversao fora do plausivel"

    @property
    def viavel(self) -> bool:
        return self.conversao_necessaria <= CONVERSAO_TIPICA_MAX

    def resultado(self, cliques: int, conversao: float) -> dict:
        """Simula um cenario concreto de gasto e retorno."""
        vendas = cliques * conversao
        receita = vendas * self.comissao
        custo = cliques * self.cpc
        return {
            "cliques": cliques,
            "vendas": vendas,
            "receita": round(receita, 2),
            "custo": round(custo, 2),
            "lucro": round(receita - custo, 2),
        }


def avaliar(comissao: float, cpc: float) -> Viabilidade | None:
    """`None` quando falta dado para a conta ter sentido."""
    if comissao <= 0 or cpc <= 0:
        return None
    necessaria = cpc / comissao
    return Viabilidade(
        comissao=comissao,
        cpc=cpc,
        conversao_necessaria=necessaria,
        cliques_por_venda=1 / necessaria if necessaria else 0.0,
    )


def orcamento_de_teste(comissao: float, cpc: float,
                       vendas_desejadas: int = 3) -> float:
    """Quanto gastar para o teste ter chance de mostrar alguma coisa.

    Testar com orcamento que nem daria para uma venda nao mede nada: zero
    vendas pode significar produto ruim ou amostra pequena, e nao ha como
    distinguir. O padrao de 3 vendas e o minimo para o resultado deixar de
    ser sorte.
    """
    aval = avaliar(comissao, cpc)
    if aval is None:
        return 0.0
    return round(aval.cliques_por_venda * vendas_desejadas * cpc, 2)
