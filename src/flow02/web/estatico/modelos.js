"use strict";

/**
 * Modelos de post prontos, preenchidos com o produto.
 *
 * Uma ressalva honesta: isto NÃO busca posts de criadores reais. Não existe
 * API que entregue o conteúdo de terceiros, e raspar perfil alheio seria
 * violar os termos das redes.
 *
 * O que existe aqui são estruturas de copy conhecidas — as mesmas que
 * publicitário usa há décadas — aplicadas aos dados reais do seu produto.
 * Cada uma tem uma nota dizendo em que situação costuma funcionar melhor,
 * para você escolher em vez de sortear.
 *
 * Nenhum modelo inventa característica do produto: só usam preço, desconto,
 * nota, vendas e nome, que vêm da API.
 */
const MODELOS = (() => {
  const moeda = (v) => (v ?? 0).toLocaleString("pt-BR",
    { style: "currency", currency: "BRL" });
  const num = (v) => (v ?? 0).toLocaleString("pt-BR");

  /** Primeiras palavras do nome: nome inteiro de marketplace é ilegível. */
  function curto(nome, palavras = 6) {
    return (nome || "").split(/\s+/).slice(0, palavras).join(" ");
  }

  function dados(item, link) {
    const desconto = Math.round(item.desconto_real ?? item.desconto_declarado ?? 0);
    const antes = desconto > 0 ? item.preco / (1 - desconto / 100) : null;
    return {
      nome: item.nome || "",
      curto: curto(item.nome),
      preco: moeda(item.preco),
      antes: antes ? moeda(antes) : null,
      economia: antes ? moeda(antes - item.preco) : null,
      desconto,
      vendas: item.vendas || 0,
      nota: item.rating || 0,
      link: link || item.link || "(gere o link primeiro)",
    };
  }

  const MODELO = [
    {
      id: "achadinho",
      nome: "Achadinho",
      quando: "O padrão para feed e grupos. Direto, sem enrolação.",
      montar: (d) => [
        `${d.curto}`,
        "",
        d.antes ? `De ${d.antes} por ${d.preco}` : `Por ${d.preco}`,
        d.economia ? `Você economiza ${d.economia}` : "",
        "",
        d.vendas > 500 ? `${num(d.vendas)} pessoas já compraram` : "",
        d.nota >= 4.5 ? `Nota ${d.nota.toFixed(1)} de quem recebeu` : "",
        "",
        "Link para comprar:",
        d.link,
        "",
        "#achadinhos #promocao #ofertas #shopee",
      ],
    },
    {
      id: "urgencia",
      nome: "Preço que muda",
      quando: "Quando o produto está no menor preço. Não invente prazo que"
        + " você não conhece — a Shopee não informa quando a oferta acaba.",
      montar: (d) => [
        `Preço de hoje: ${d.preco}`,
        d.antes ? `Estava ${d.antes}.` : "",
        "",
        `${d.curto}`,
        "",
        "Preço de marketplace muda sozinho, sem aviso.",
        "Se estiver no seu radar, vale conferir agora.",
        "",
        d.link,
        "",
        "#promocao #ofertas #achadinhos",
      ],
    },
    {
      id: "prova",
      nome: "Prova social",
      quando: "Para produto com muita venda. É o argumento mais forte que"
        + " existe: outras pessoas já arriscaram por você.",
      montar: (d) => [
        `${num(d.vendas)} pessoas compraram isso.`,
        d.nota ? `Nota ${d.nota.toFixed(1)}.` : "",
        "",
        `${d.curto}`,
        `${d.preco}${d.desconto ? ` (${d.desconto}% OFF)` : ""}`,
        "",
        "Não é lançamento nem novidade duvidosa —",
        "é produto que já rodou e continua bem avaliado.",
        "",
        d.link,
        "",
        "#achadosdashopee #ofertas",
      ],
    },
    {
      id: "pergunta",
      nome: "Pergunta e resposta",
      quando: "Bom para stories e comentário. Pergunta abre conversa, e"
        + " conversa é o que o algoritmo premia.",
      montar: (d) => [
        `Vale ${d.preco}?`,
        "",
        `${d.curto}`,
        "",
        d.nota >= 4.5
          ? `Quem comprou deu nota ${d.nota.toFixed(1)}.`
          : "Confira as avaliações antes de decidir.",
        d.vendas > 500 ? `Já são ${num(d.vendas)} vendidos.` : "",
        d.economia ? `Hoje sai ${d.economia} mais barato.` : "",
        "",
        "Na minha opinião, vale. Mas veja você:",
        d.link,
        "",
        "#achadinhos",
      ],
    },
    {
      id: "lista",
      nome: "Item de lista",
      quando: "Para post com vários produtos. Compacto o bastante para"
        + " caber cinco na mesma legenda.",
      montar: (d) => [
        `• ${d.curto}`,
        `  ${d.preco}${d.desconto ? ` — ${d.desconto}% OFF` : ""}`,
        `  ${d.link}`,
      ],
    },
    {
      id: "whatsapp",
      nome: "WhatsApp e Telegram",
      quando: "Curto e com formatação de negrito das duas plataformas."
        + " Mensagem longa em grupo não é lida.",
      montar: (d) => [
        `*${d.curto}*`,
        "",
        d.antes ? `~${d.antes}~  *${d.preco}*` : `*${d.preco}*`,
        d.vendas > 500 ? `${num(d.vendas)} vendidos` : "",
        "",
        d.link,
      ],
    },
  ];

  /**
   * `""` é linha em branco de propósito; condição não atendida vira `""`
   * também, então não dá para filtrar por igualdade. A solução é montar e
   * depois colapsar as sequências de branco, preservando o parágrafo.
   */
  function montar(id, item, link) {
    const modelo = MODELO.find((m) => m.id === id) || MODELO[0];
    return modelo.montar(dados(item, link))
      .join("\n")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^\n+|\n+$/g, "");
  }

  function lista() {
    return MODELO.map(({ id, nome, quando }) => ({ id, nome, quando }));
  }

  return { montar, lista };
})();
