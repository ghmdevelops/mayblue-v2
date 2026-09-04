"""Modelo normalizado de oferta, comum a todas as plataformas."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True, slots=True)
class Oferta:
    plataforma: str
    item_id: str
    nome: str
    preco: float
    taxa_comissao: float
    origem_consulta: str
    loja_id: str | None = None
    loja_nome: str | None = None
    vendas: int | None = None
    rating: float | None = None
    desconto_pct: float | None = None
    link_oferta: str | None = None
    link_produto: str | None = None
    imagem: str | None = None
    expira_em: str | None = None
    # A taxa total se divide entre o que o vendedor banca e o que a Shopee
    # banca. A parte do vendedor e campanha dele e pode acabar a qualquer
    # momento; a da Shopee e estavel. Guardar as duas permite dizer quais
    # comissoes altas sao frageis.
    taxa_vendedor: float | None = None
    taxa_shopee: float | None = None
    comissao_api: float | None = None
    categoria_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def chave(self) -> str:
        return f"{self.plataforma}:{self.item_id}"

    @property
    def comissao_valor(self) -> float:
        """Comissao em reais por venda concretizada."""
        return round(self.preco * self.taxa_comissao, 4)


@dataclass(frozen=True, slots=True)
class Conversao:
    """Retorno REALIZADO: o que de fato foi clicado, pedido e comissionado."""

    plataforma: str
    conversao_id: str
    cliques: int
    pedidos: int
    comissao: float
    item_id: str | None = None
    loja_id: str | None = None
    ocorrido_em: str | None = None
    status: str | None = None
    validada: bool = False
    pedido_id: str | None = None
    clicado_em: str | None = None
    canal: str | None = None

    @property
    def cvr(self) -> float | None:
        return self.pedidos / self.cliques if self.cliques else None


@dataclass(frozen=True, slots=True)
class Avaliacao:
    """Resultado do scoring, com os componentes expostos para auditoria."""

    cvr_estimado: float
    score: float
    componentes: dict[str, float]
    calibrado: bool = False


@dataclass(frozen=True, slots=True)
class OfertaAvaliada:
    oferta: Oferta
    avaliacao: Avaliacao

    def com_score(self, avaliacao: Avaliacao) -> "OfertaAvaliada":
        return replace(self, avaliacao=avaliacao)
