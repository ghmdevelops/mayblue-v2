"use strict";

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);

let ultimoRanking = [];
let linhasRecebidas = 0;
let enquete = null;
let ordenacao = "epc";

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    ...opcoes,
    headers: { "X-Token": TOKEN, "Content-Type": "application/json", ...(opcoes.headers || {}) },
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(dados.erro || `HTTP ${resposta.status}`);
  return dados;
}

function avisar(texto, erro = false, duracao = 3200) {
  const caixa = $("aviso");
  caixa.textContent = texto;
  caixa.className = erro ? "aviso erro" : "aviso";
  caixa.hidden = false;
  clearTimeout(avisar._t);
  avisar._t = setTimeout(() => { caixa.hidden = true; }, duracao);
}

/** Ativa uma aba pelo nome. Usado também por atalhos fora da barra de abas. */
function trocarAba(nome) {
  const aba = document.querySelector(`.aba[data-painel="${nome}"]`);
  if (!aba) return;
  document.querySelectorAll(".aba").forEach((b) => b.classList.remove("ativa"));
  document.querySelectorAll(".painel").forEach((p) => p.classList.remove("ativo"));
  aba.classList.add("ativa");
  $(`painel-${nome}`).classList.add("ativo");
  recarregarAbaAtiva();
}

const moeda = (v) => (v ?? 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const numero = (v, casas = 0) => (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
const pct = (v, casas = 1) => `${((v ?? 0) * 100).toFixed(casas)}%`;

function esqueleto(alvo, linhas = 6) {
  alvo.className = "";
  alvo.innerHTML = `<div class="esqueleto">${"<div></div>".repeat(linhas)}</div>`;
}

function tabela(alvo, colunas, linhas, vazio = "Nenhum resultado.") {
  if (!linhas.length) {
    alvo.innerHTML = `<tbody><tr><td><div class="vazio">${vazio}</div></td></tr></tbody>`;
    return;
  }
  const cabecalho = colunas.map((c) => `<th class="${c.num ? "num" : ""}">${c.titulo}</th>`).join("");
  const corpo = linhas.map((linha) =>
    "<tr>" + colunas.map((c) => {
      const conteudo = c.render(linha);
      const classe = [c.num ? "num" : "", c.classe || ""].filter(Boolean).join(" ");
      return `<td class="${classe}">${conteudo}</td>`;
    }).join("") + "</tr>"
  ).join("");
  alvo.innerHTML = `<thead><tr>${cabecalho}</tr></thead><tbody>${corpo}</tbody>`;
}

/** Liga/desliga o item da vitrine curada, sem recarregar a lista. */
async function alternarVitrine(botao) {
  const itemId = botao.dataset.vitrine;
  const item = ultimoRanking.find((i) => String(i.item_id) === String(itemId));
  const remover = botao.classList.contains("curado");
  try {
    const dados = await api("/api/vitrine", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId, remover }),
    });
    botao.classList.toggle("curado", dados.na_vitrine);
    botao.textContent = dados.na_vitrine ? "Na vitrine" : "+ Vitrine";
    if (item) item.na_vitrine = dados.na_vitrine;
    avisar(dados.na_vitrine
      ? `adicionado à vitrine · ${dados.total} item(ns)`
      : `removido da vitrine · ${dados.total} item(ns)`);
    // Na aba Vitrine, remover significa sair da lista: recarrega.
    if (abaAtiva() === "vitrine") carregarVitrine();
  } catch (erro) {
    avisar(erro.message, true);
  }
}

const LIMITE_LOTE = 40;

/** Gera um ZIP com as artes e uma página que mostra tudo montado. */
async function gerarLote(lista, botao) {
  if (!lista.length) return avisar("nada para gerar", true);
  const itens = lista.slice(0, LIMITE_LOTE);
  const canal = canalEscolhido();
  const rotulo = botao.textContent;
  botao.disabled = true;

  try {
    const links = {};
    if (canal) {
      botao.textContent = "gerando links…";
      for (const item of itens) {
        const origem = item.link_produto || item.link;
        if (!origem) continue;
        const dados = await api("/api/link", {
          method: "POST",
          body: JSON.stringify({ url: origem, sub_ids: [canal] }),
        });
        links[item.item_id] = dados.link;
      }
    }

    const formato = $("lote-formato")?.value || "1080x1350";
    const zip = await ARTE.gerarLote(itens, formato, links, (feito, total) => {
      botao.textContent = `arte ${feito}/${total}…`;
    });

    const dia = new Date().toISOString().slice(0, 10);
    ZIP.baixar(zip, `flow02-artes-${dia}${canal ? "-" + canal : ""}.zip`);
    avisar(`${itens.length} artes no zip — descompacte e abra o abrir.html`,
           false, 7000);
    if (lista.length > LIMITE_LOTE) {
      avisar(`limitado a ${LIMITE_LOTE} por lote`, true);
    }
  } catch (erro) {
    avisar(`falhou: ${erro.message}`, true);
  } finally {
    botao.disabled = false;
    botao.textContent = rotulo;
  }
}

/** Alerta de preço-alvo. Sugere 10% abaixo do preço atual como ponto de partida. */
async function definirAlvo(itemId, precoAtual) {
  const sugestao = precoAtual ? (precoAtual * 0.9).toFixed(2) : "";
  const resposta = prompt(
    `Avisar quando o preço cair abaixo de quanto?\n(hoje: ${moeda(precoAtual)})`,
    sugestao,
  );
  if (resposta === null) return;
  const preco = Number(String(resposta).replace(",", "."));
  if (!preco || preco <= 0) return avisar("preço inválido", true);
  try {
    await api("/api/alvo", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId, preco }),
    });
    avisar(`alvo definido: abaixo de ${moeda(preco)}`);
    if (abaAtiva() === "saude") carregarSaude();
  } catch (erro) {
    avisar(erro.message, true);
  }
}

async function removerAlvo(itemId) {
  try {
    await api("/api/alvo", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId, remover: true }),
    });
    avisar("alvo removido");
    carregarSaude();
  } catch (erro) {
    avisar(erro.message, true);
  }
}

function copiarTexto(texto, mensagem) {
  navigator.clipboard.writeText(texto)
    .then(() => avisar(mensagem))
    .catch(() => avisar("não foi possível copiar", true));
}

/** Abre o gerador de arte, já com o link do canal escolhido. */
async function abrirArte(itemId) {
  const item = ultimoRanking.find((i) => String(i.item_id) === String(itemId));
  if (!item) return avisar("item não encontrado", true);

  let link = item.link || "";
  const canal = canalEscolhido();
  const origem = item.link_produto || item.link;
  if (canal && origem) {
    try {
      const dados = await api("/api/link", {
        method: "POST",
        body: JSON.stringify({ url: origem, sub_ids: [canal] }),
      });
      link = dados.link;
    } catch (erro) {
      avisar(`link do canal falhou: ${erro.message}`, true);
    }
  }
  await ARTE.abrir(item, link);
}

/** Canal ativo: o do select, ou o texto livre quando "outro" está escolhido. */
function canalEscolhido() {
  const escolha = $("filtro-canal")?.value || "";
  if (escolha === "__outro") {
    return ($("filtro-subid")?.value || "").trim().toLowerCase();
  }
  return escolha;
}

/**
 * Copia o post pronto — não só o link.
 *
 * Link solto num grupo é ignorado: ninguém clica em URL sem saber o que é.
 * O texto completo traz nome, preço, economia, prova social e o link, tudo
 * no formato do modelo escolhido em "Copiar como".
 *
 * O link é sempre o de afiliado; com canal escolhido, gera um novo marcado
 * com o sub-ID para você saber depois de onde veio a venda.
 */
async function copiarLink(botao) {
  const rotulo = botao.textContent;
  let link = decodeURIComponent(botao.dataset.link || "");
  const campanha = canalEscolhido();
  const origem = decodeURIComponent(botao.dataset.origem || "");

  if (campanha && origem) {
    botao.textContent = "gerando…";
    try {
      const dados = await api("/api/link", {
        method: "POST",
        body: JSON.stringify({ url: origem, sub_ids: [campanha] }),
      });
      link = dados.link;
    } catch (erro) {
      botao.textContent = rotulo;
      return avisar(`campanha falhou: ${erro.message}`, true);
    }
  }

  if (!link) {
    botao.textContent = rotulo;
    return avisar("sem link", true);
  }

  const modelo = $("filtro-modelo")?.value || "achadinho";
  const item = ultimoRanking.find((i) => i.item_id === botao.dataset.item);
  const texto = (modelo === "so-link" || !item)
    ? link
    : MODELOS.montar(modelo, item, link);

  try {
    await navigator.clipboard.writeText(texto);
  } catch {
    botao.textContent = rotulo;
    return avisar("não foi possível copiar", true);
  }
  botao.textContent = campanha ? `copiado · ${campanha}` : "copiado";
  botao.classList.add("feito");
  setTimeout(() => {
    botao.textContent = rotulo;
    botao.classList.remove("feito");
  }, 1600);
}

function botaoLink(url, origem = "") {
  if (!url || url === "-") return '<span class="incerto">-</span>';
  return `<button class="link-copiar" data-link="${encodeURIComponent(url)}"`
    + ` data-origem="${encodeURIComponent(origem || url)}"`
    + ' data-dica="Copia o link de afiliado deste produto.&#10;&#10;Com um'
    + ' canal escolhido em &quot;Divulgar em&quot;, gera um link marcado para'
    + ' você saber depois de onde veio a venda.">copiar link</button>';
}

function celulaDesconto(item) {
  if (!item.desconto_confiavel) {
    return `<span class="incerto">${numero(item.desconto_declarado)}?</span>`;
  }
  const classe = item.desconto_inflado ? "inflado" : "";
  const marca = item.desconto_inflado ? "!" : "";
  return `<span class="${classe}">${numero(item.desconto_real)}${marca}</span>`;
}

// -------------------------------------------------------- filtros de valor

/** id do campo -> nome do parâmetro que a API espera. */
const FILTROS_VALOR = {
  "f-comissao-min": "comissao_min",
  "f-taxa-min": "taxa_min",
  "f-preco-min": "preco_min",
  "f-preco-max": "preco_max",
  "f-vendas-min": "vendas_min",
  "f-nota-min": "nota_min",
  "f-loja": "loja",
};

function filtrosDeValor() {
  const parametros = {};
  for (const [id, nome] of Object.entries(FILTROS_VALOR)) {
    const valor = $(id).value.trim();
    if (valor) parametros[nome] = valor;
  }
  if ($("f-so-vitrine").checked) parametros.so_vitrine = "1";
  return parametros;
}

function contarFiltrosAtivos() {
  const ativos = Object.keys(filtrosDeValor()).length;
  $("contador-filtros").textContent = ativos ? String(ativos) : "";
  // Abre sozinho quando há filtro ativo: filtro escondido que corta a lista
  // parece bug — o usuário vê poucos itens e não sabe por quê.
  if (ativos) $("filtros-avancados").open = true;
}

function limparFiltrosDeValor() {
  for (const id of Object.keys(FILTROS_VALOR)) $(id).value = "";
  $("f-so-vitrine").checked = false;
  contarFiltrosAtivos();
  carregarRanking();
}

// ---------------------------------------------------------------- ranking

let epcMaximo = 1;

const COLUNAS_RANKING = [
  { titulo: "#", num: true, render: (i) => i.posicao },
  { titulo: "Produto", classe: "produto", render: (i) => escapar(i.nome) },
  { titulo: "Preço", num: true, render: (i) => moeda(i.preco) },
  { titulo: "Com.", num: true, render: (i) => pct(i.taxa_comissao) },
  { titulo: "R$/venda", num: true, render: (i) => moeda(i.comissao_valor) },
  { titulo: "EPC", num: true, render: (i) => `<span class="marca-epc">${numero(i.epc, 4)}</span>` },
  { titulo: "CVR", num: true, render: (i) => i.calibrado ? pct(i.cvr, 2) : `<span class="estimado">${pct(i.cvr, 2)}*</span>` },
  { titulo: "Desc.", num: true, render: celulaDesconto },
  { titulo: "Vendas", num: true, render: (i) => numero(i.vendas) },
  { titulo: "Nota", num: true, render: (i) => i.rating ? numero(i.rating, 1) : "-" },
  { titulo: "Loja", render: (i) => escapar(i.loja || "-") },
  { titulo: "Link", render: (i) => botaoLink(i.link) },
];

function escapar(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}

/** Linha compacta: nome e loja à esquerda, dinheiro e EPC à direita. */
function linhaOferta(item) {
  const periodo = item.no_periodo && item.no_periodo.delta > 0
    ? `<span class="selo-quente" data-dica="Unidades vendidas entre ${item.no_periodo.base} e hoje, calculado subtraindo o acumulado das duas coletas.&#10;&#10;Total de sempre: ${numero(item.no_periodo.acumulado)}.">+${numero(item.no_periodo.delta)} no período${
        item.tendencia ? ` · +${numero(item.tendencia, 0)}%` : ""}</span>`
    : "";

  const variantes = item.variantes > 1
    ? `<span class="selo-variantes" data-dica="Este produto aparece em ${item.variantes} anúncios diferentes — versões, tamanhos ou lojas. Mostrando o de melhor EPC.">${item.variantes} versões</span>`
    : "";

  const meta = [
    item.loja ? escapar(item.loja) : "",
    item.vendas ? `${numero(item.vendas)} vendidos` : "",
    item.rating ? `${numero(item.rating, 1)} de nota` : "",
    textoDesconto(item),
    seloPreco(item),
    periodo,
    variantes,
    seloVelocidade(item),
    seloFragilidade(item),
    seloDisponibilidade(item),
    seloValidade(item),
  ].filter(Boolean).map((t) => `<span>${t}</span>`).join("");

  const proporcao = Math.max((item.epc / epcMaximo) * 100, 2);
  const cvr = item.calibrado
    ? `${pct(item.cvr, 2)} medido`
    : `${pct(item.cvr, 2)} estimado`;

  const foto = item.imagem
    ? `<img class="foto" src="${escapar(item.imagem)}" alt="" loading="lazy">`
    : '<div class="foto"></div>';

  const op = item.oportunidade;
  const selo = op && op.forte
    ? `<span class="selo-oportunidade" data-dica="Todos os ${op.total} critérios avaliados foram atendidos:&#10;&#10;${
        op.motivos.map((m) => "• " + m).join("&#10;")}&#10;&#10;Sinal isolado engana; o que identifica oportunidade é a coincidência deles.">OPORTUNIDADE</span>`
    : "";

  return `<article class="oferta ${item.posicao <= 3 ? "topo" : ""} ${
      item.indisponivel ? "morto" : ""} ${op && op.forte ? "oportuno" : ""}">
    <div class="pos">${item.posicao}</div>
    ${foto}
    <div class="info">
      <div class="titulo">${selo}${escapar(item.nome)}</div>
      <div class="meta">${meta}</div>
    </div>
    <div class="valores">
      <div class="valor-bloco"
           data-dica="Preço que o comprador paga hoje na Shopee. Pode mudar a qualquer momento — o flow02 grava um retrato por dia.">
        <div class="n">${moeda(item.preco)}</div>
        <div class="l">preço do produto</div>
      </div>
      <div class="ganho-bloco"
           data-dica="Quanto entra no seu bolso a cada venda: preço × ${pct(item.taxa_comissao)} de comissão.&#10;&#10;É valor estimado. Devolução e cancelamento reduzem o que é efetivamente pago — o valor definitivo aparece na aba Ganhos.">
        <div class="n">${moeda(item.comissao_valor)}</div>
        <div class="l">você ganha · ${pct(item.taxa_comissao)}</div>
      </div>
      <div class="epc-bloco"
           data-dica="EPC = quanto você ganha, em média, por clique.&#10;&#10;Comissão × chance estimada de compra (${cvr}). Um produto com EPC 1,00 rende cerca de R$ 1 a cada pessoa que clica.&#10;&#10;É a métrica mais honesta para comparar: junta o quanto paga com o quanto vende. O asterisco indica estimativa por heurística; sem ele, é conversão medida.">
        <div class="n">${numero(item.epc, 4)}</div>
        <div class="barra-epc"><i style="width:${proporcao}%"></i></div>
        <div class="l">EPC · ${cvr}</div>
      </div>
    </div>
    <div class="acoes">
      ${item.link ? `<a class="acao-icone" href="${escapar(item.link)}"
        target="_blank" rel="noopener noreferrer"
        data-dica="Abre a página do produto na Shopee, numa aba nova.&#10;&#10;O link já é o seu de afiliado: se alguém comprar por ele, a comissão é sua. Serve para conferir o produto antes de divulgar.">Abrir</a>` : ""}
      <button class="acao-icone" data-link="${encodeURIComponent(item.link || "")}"
              data-origem="${encodeURIComponent(item.link_produto || item.link || "")}"
              data-item="${escapar(item.item_id)}"
              data-dica="Copia o POST COMPLETO: nome, preço, economia, prova social, link de afiliado e hashtags — pronto para colar.&#10;&#10;O formato segue o que estiver em 'Copiar como'. Para receber só a URL, escolha 'só o link'.&#10;&#10;Com um canal em 'Divulgar em', gera link marcado para você saber de onde veio a venda.">Copiar</button>
      <button class="acao-icone" data-arte="${item.item_id}"
              data-dica="Monta uma imagem pronta para post com a foto, o preço, o selo de desconto e a prova social.&#10;&#10;Abre com prévia ao vivo, escolha de formato (feed, stories, quadrado), download em PNG, legenda pronta e roteiro cronometrado para gravar vídeo.">Arte</button>
      <button class="acao-icone ${item.na_vitrine ? "curado" : ""}"
              data-vitrine="${item.item_id}"
              data-dica="${item.na_vitrine
                ? "Tira este produto da sua seleção. Ele continua no ranking, só sai da página pública."
                : "Adiciona à sua seleção curada.&#10;&#10;A vitrine é o que aparece na página pública do celular — em vez de despejar os 400 produtos coletados, você escolhe os que quer divulgar."}"
              >${item.na_vitrine ? "Na vitrine" : "+ Vitrine"}</button>
      <button class="acao-icone" data-alvo="${item.item_id}"
              data-preco="${item.preco}"
              data-dica="Cria um alerta de preço: o flow02 avisa quando este produto cair abaixo do valor que você definir.&#10;&#10;Sugere 10% abaixo do preço de hoje. Acompanhe na aba Saúde.">Alvo</button>
    </div>
  </article>`;
}

function textoDesconto(item) {
  if (!item.desconto_declarado) return "";
  if (!item.desconto_confiavel) {
    return `<span class="incerto" data-dica="Desconto anunciado pela loja, ainda não conferido.&#10;&#10;Para verificar, o flow02 precisa de alguns dias de histórico de preço deste produto.">${numero(item.desconto_declarado)}% OFF (não verificado)</span>`;
  }
  if (item.desconto_inflado) {
    return `<span class="ruim" data-dica="Desconto inflado: a loja subiu o preço antes de anunciar a promoção.&#10;&#10;Comparando com a mediana dos últimos dias, o desconto real é bem menor que o anunciado. Divulgar isso queima sua credibilidade.">anuncia ${numero(item.desconto_declarado)}%, real ${numero(item.desconto_real)}%</span>`;
  }
  return `<span class="aviso" data-dica="Desconto conferido contra a mediana de preço dos últimos dias — não é só o número que a loja anuncia.">${numero(item.desconto_real)}% OFF real</span>`;
}

/** Aviso de link possivelmente morto, no próprio produto. */
function seloDisponibilidade(item) {
  if (!item.indisponivel) return "";
  const motivo = item.dias_sumido === null
    ? "nunca apareceu na coleta"
    : `fora da coleta há ${item.dias_sumido} dia(s)`;
  return '<span class="selo-morto" data-dica="Produto que some da API '
    + "geralmente esgotou ou saiu do ar.&#10;&#10;Confira antes de divulgar: "
    + "link que leva a &quot;produto indisponível&quot; faz quem clicou não "
    + 'clicar de novo. Itens assim não são publicados na página pública.">'
    + `indisponível? ${motivo}</span>`;
}

/**
 * Validade da oferta. Diferente do sumiço, este dado é declarado pela própria
 * Shopee (periodEndTime) — dá para avisar antes de a comissão cair.
 */
function seloValidade(item) {
  const dias = item.dias_ate_expirar;
  if (dias === null || dias === undefined) return "";
  const dica = "Prazo de validade declarado pela própria Shopee "
    + "(periodEndTime).&#10;&#10;Comissão de campanha tem data para acabar. "
    + "Depois disso o link continua funcionando, mas paga a taxa normal — "
    + "geralmente bem menor.";
  if (dias < 0) {
    return `<span class="selo-morto" data-dica="${dica}">oferta expirada</span>`;
  }
  if (dias === 0) {
    return `<span class="selo-morto" data-dica="${dica}">expira hoje</span>`;
  }
  if (dias <= 3) {
    return `<span class="selo-expira" data-dica="${dica}">expira em ${dias} dia(s)</span>`;
  }
  return "";
}

/**
 * Movimento de vendas desde a coleta anterior.
 *
 * A API não expõe estoque. O mais próximo disso é a aceleração: produto que
 * dispara em vendas é o que tem risco real de esgotar, e produto parado
 * dificilmente vale o esforço de divulgar.
 */
function seloVelocidade(item) {
  const v = item.velocidade;
  if (!v || v.delta <= 0) return "";

  const porDia = v.por_dia;
  const janela = v.dias === 1 ? "desde ontem" : `em ${v.dias} dias`;
  const rotulo = `+${numero(v.delta)} vendidos ${janela}`;
  const dica = `Vendeu ${numero(v.delta)} unidades entre ${v.desde} e hoje`
    + ` — cerca de ${numero(porDia, 1)} por dia.&#10;&#10;`
    + "A API não informa estoque; este é o sinal mais próximo disso. "
    + "Produto acelerando é o que tem mais chance de esgotar.";

  if (porDia >= 100) {
    return `<span class="selo-quente" data-dica="${dica}">${rotulo}</span>`;
  }
  if (porDia >= 10) {
    return `<span class="selo-movendo" data-dica="${dica}">${rotulo}</span>`;
  }
  return `<span data-dica="${dica}">${rotulo}</span>`;
}

/**
 * Quanto da comissão depende do vendedor.
 *
 * A parte do vendedor é campanha dele e some sem aviso; a da Shopee é estável.
 * Comissão de 80% quase sempre é 77% do vendedor — ótima hoje, incerta amanhã.
 */
function seloFragilidade(item) {
  const f = item.fragilidade;
  if (!f || !f.fragil) return "";
  const pct = Math.round(f.fatia_vendedor * 100);
  return `<span class="selo-fragil" data-dica="${pct}% da comissão vem de`
    + " campanha do vendedor, que pode acabar sem aviso. A parte estável é a"
    + ` da Shopee: se a campanha encerrar, sobra cerca de ${moeda(f.piso)}`
    + " por venda.&#10;&#10;Não é motivo para descartar — é motivo para não"
    + ` depender só disso.">comissão frágil · ${pct}% do vendedor</span>`;
}

/** Selo de menor preço: o oposto do "desconto inflado", e o que faz comprar. */
function seloPreco(item) {
  if (item.menor_preco) {
    return `<span class="selo-min" data-dica="Está no menor preço desde que o flow02 começou a acompanhar este produto.&#10;&#10;É o melhor argumento de venda que existe: prova que o momento é bom, em vez de repetir o desconto que a loja anuncia.">menor preço em ${item.dias_preco} dias</span>`;
  }
  if (item.preco_minimo && item.preco_minimo < item.preco) {
    return `<span class="incerto" data-dica="Já esteve mais barato no histórico. Pode valer esperar ou criar um alerta de preço no botão Alvo.">já esteve por ${moeda(item.preco_minimo)}`
      + `${item.dia_minimo ? ` em ${item.dia_minimo.slice(8)}/${item.dia_minimo.slice(5, 7)}` : ""}</span>`;
  }
  return "";
}

// -------------------------------------------------------------- categorias

/** Preenche o seletor com as categorias que apareceram na coleta. */
async function carregarCategorias() {
  try {
    const dados = await api("/api/categorias");
    const seletor = $("filtro-categoria");
    const anterior = seletor.value;
    seletor.innerHTML = '<option value="">todas as categorias</option>'
      + dados.itens.map((c) =>
        `<option value="${escapar(c.id)}">${escapar(c.rotulo || c.id)}`
        + ` (${c.itens})</option>`).join("");
    if (anterior) seletor.value = anterior;
  } catch (erro) {
    // Categoria é conveniência: se falhar, o resto do painel continua.
    console.warn("categorias:", erro.message);
  }
}

/**
 * Aviso quando o período pedido não tem coleta para comparar.
 *
 * "Vendidos na última semana" precisa da coleta de sete dias atrás para
 * subtrair. Sem ela o certo é dizer isso, não devolver o acumulado fingindo
 * ser do período.
 */
function avisarJanela(dados) {
  const janela = Number(dados.janela || 0);
  if (!janela) return "";
  if (!dados.com_periodo) {
    const oque = ordenacao === "tendencia" ? "crescimento" : "vendas no período";
    return ` · ${oque} precisa de coleta de ${janela} dia(s) atrás para comparar —`
      + " mostrando tudo. O histórico começa a existir a partir da segunda coleta.";
  }
  return ` · ${numero(dados.com_periodo)} produto(s) com base de comparação`;
}

// ------------------------------------------------------------- recarregar

let momentoDaCarga = null;

/**
 * "atualizado agora" -> "há 3 min" -> "há 1 h".
 *
 * Sem isso não dá para saber se a tela mostra o dado de agora ou o de três
 * horas atrás — e num painel que fica aberto o dia todo isso importa.
 */
function marcarAtualizado() {
  momentoDaCarga = Date.now();
  desenharRelogio();
}

function desenharRelogio() {
  const alvo = $("atualizado-em");
  if (!alvo || !momentoDaCarga) return;
  const seg = Math.round((Date.now() - momentoDaCarga) / 1000);
  alvo.textContent = seg < 45 ? "atualizado agora"
    : seg < 3600 ? `atualizado há ${Math.round(seg / 60)} min`
    : `atualizado há ${Math.round(seg / 3600)} h`;
}

async function recarregar() {
  const botao = $("btn-recarregar");
  botao.classList.add("ocupado");
  try {
    await carregarEstado();
    await carregarRanking();
  } finally {
    // Segura o estado ocupado por um instante: em banco local a resposta é
    // instantânea, e o botão piscaria sem o usuário perceber que agiu.
    setTimeout(() => botao.classList.remove("ocupado"), 350);
  }
}

/** Coleta de verdade: vai à Shopee, demora, e é o que traz produto novo. */
function coletarAgora() {
  const botao = $("btn-coletar-agora");
  botao.classList.add("ocupado");
  botao.textContent = "Buscando...";
  trocarAba("acoes");
  executar("coletar");
  avisar("coletando na Shopee — acompanhe o andamento abaixo", false, 6000);
}

async function carregarRanking() {
  const parametros = new URLSearchParams({
    dia: $("filtro-dia").value || "hoje",
    plataforma: $("filtro-plataforma").value,
    limite: $("filtro-limite").value,
    busca: $("filtro-busca").value,
    ordenar: ordenacao,
    diversificar: $("filtro-diversificar").checked ? "1" : "0",
    so_oportunidades: $("filtro-oportunidades").value,
    agrupar_variantes: $("filtro-variantes").checked ? "1" : "0",
    janela: $("filtro-janela").value,
    categoria: $("filtro-categoria").value,
    ...filtrosDeValor(),
  });
  try {
    const dados = await api(`/api/top?${parametros}`);
    let itens = dados.itens;
    if ($("filtro-desconto").checked) itens = itens.filter((i) => !i.desconto_inflado);
    ultimoRanking = itens;
    epcMaximo = Math.max(...itens.map((i) => i.epc || 0), 0.0001);

    const comissaoTotal = itens.reduce((s, i) => s + i.comissao_valor, 0);
    const inflados = itens.filter((i) => i.desconto_inflado).length;
    cartoes($("cartoes-ranking"), [
      { rotulo: "Itens", valor: `${itens.length}`, sufixo: `de ${dados.total}` },
      { rotulo: "Melhor EPC", valor: numero(epcMaximo, 4), classe: "destaque" },
      { rotulo: "Comissão somada", valor: moeda(comissaoTotal), classe: "positivo" },
      { rotulo: "Oportunidades", valor: `${dados.oportunidades ?? 0}`,
        sufixo: `+${dados.quase_oportunidades ?? 0} quase`,
        classe: dados.oportunidades ? "positivo" : "" },
      { rotulo: "Desconto inflado", valor: `${inflados}` },
    ]);

    // Lista curta sem explicação parece bug: diz quanto o filtro cortou.
    const ativos = Object.keys(filtrosDeValor()).length
      + ($("filtro-oportunidades").value !== "0" ? 1 : 0);
    const cortou = dados.total - itens.length;
    $("resumo-ranking").textContent =
      `${dados.dia} · EPC é o retorno esperado por clique, em reais`
      + (ativos && cortou > 0
        ? ` · ${ativos} filtro(s) ativo(s) escondendo ${numero(cortou)} produto(s)`
        : "")
      + avisarJanela(dados);
    marcarAtualizado();
    desenharRanking(itens);
  } catch (erro) {
    avisar(erro.message, true);
  }
}

function desenharRanking(itens) {
  const area = $("area-ranking");
  if (!itens.length) {
    area.className = "";
    area.innerHTML = '<div class="vazio"><b>Nada coletado nesse dia</b>'
      + "Vá na aba Ações e rode <code>Coletar</code>.</div>";
    return;
  }
  if ($("filtro-tabela").checked) {
    area.className = "tabela-area";
    area.innerHTML = '<table id="tabela-ranking"></table>';
    tabela($("tabela-ranking"), COLUNAS_RANKING, itens);
    return;
  }
  area.className = "lista";
  area.innerHTML = itens.map(linhaOferta).join("");
}

function cartoes(alvo, lista) {
  alvo.innerHTML = lista.map((c) =>
    `<div class="cartao"><div class="rotulo">${c.rotulo}</div>`
    + `<div class="valor ${c.classe || ""}">${c.valor}</div></div>`
  ).join("");
}

async function copiarPost(lista = ultimoRanking) {
  if (!lista.length) return avisar("nada para copiar", true);
  const canal = canalEscolhido();
  const botao = abaAtiva() === "vitrine" ? $("btn-vitrine-post") : $("btn-copiar-post");
  const rotulo = botao.textContent;

  let links = lista.map((i) => i.link || "");
  if (canal) {
    botao.disabled = true;
    botao.textContent = `gerando ${lista.length} links…`;
    try {
      links = await Promise.all(lista.map(async (item) => {
        const origem = item.link_produto || item.link;
        if (!origem) return "";
        const dados = await api("/api/link", {
          method: "POST",
          body: JSON.stringify({ url: origem, sub_ids: [canal] }),
        });
        return dados.link;
      }));
    } catch (erro) {
      avisar(`falha ao gerar links: ${erro.message}`, true);
    } finally {
      botao.disabled = false;
      botao.textContent = rotulo;
    }
  }

  const texto = lista.map((item, indice) => {
    const desconto = item.desconto_real ?? item.desconto_declarado;
    const preco = moeda(item.preco)
      + (desconto ? `  (${numero(desconto)}% OFF)` : "");
    return `${indice + 1}. ${item.nome}\n   ${preco}\n`
      + `   sua comissão: ${moeda(item.comissao_valor)}\n   ${links[indice] || ""}`;
  }).join("\n\n");

  navigator.clipboard.writeText(texto)
    .then(() => avisar(`${lista.length} itens copiados${canal ? ` · ${canal}` : ""}`))
    .catch(() => avisar("não foi possível copiar", true));
}

// ---------------------------------------------------------------- vitrine

let ultimaVitrine = [];

async function carregarVitrine() {
  const area = $("area-vitrine");
  try {
    const colecao = $("vitrine-colecao").value;
    const dados = await api(`/api/vitrine?colecao=${encodeURIComponent(colecao)}`);
    ultimaVitrine = dados.itens;

    const anterior = $("vitrine-colecao").value;
    $("vitrine-colecao").innerHTML = '<option value="">todas</option>'
      + dados.colecoes.map((c) =>
        `<option value="${escapar(c.colecao)}">${escapar(c.colecao)} (${c.itens})</option>`
      ).join("");
    $("vitrine-colecao").value = anterior;

    if (!dados.itens.length) {
      $("resumo-vitrine").textContent = "";
      area.className = "";
      area.innerHTML = '<div class="vazio"><b>Vitrine vazia</b>'
        + "Na aba Ranking, clique em <code>+ Vitrine</code> nos produtos "
        + "que você quer divulgar.</div>";
      return;
    }

    const total = dados.itens.reduce((s, i) => s + (i.comissao_valor || 0), 0);
    const alerta = dados.indisponiveis
      ? ` · <b class="ruim">${dados.indisponiveis} possivelmente indisponível(is)`
        + " — não vão para a página pública</b>"
      : "";
    $("resumo-vitrine").innerHTML =
      `${dados.itens.length} produto(s) curado(s) · `
      + `comissão somada por venda ${moeda(total)}${alerta}`;

    epcMaximo = Math.max(...dados.itens.map((i) => i.epc || 0), 0.0001);
    area.className = "lista";
    area.innerHTML = dados.itens.map((item, indice) =>
      linhaOferta({ ...item, posicao: indice + 1, na_vitrine: true })
    ).join("");
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// ------------------------------------------------------------------ saúde

function blocoSaude(titulo, explicacao, conteudo) {
  return `<section class="bloco-saude">
    <h3>${titulo}</h3>
    <p class="explicacao">${explicacao}</p>
    ${conteudo}
  </section>`;
}

function secaoDisponibilidade(dados) {
  if (!dados.itens.length) {
    return "<p class=\"vazio-inline\">Nada na vitrine nem em alerta de preço.</p>";
  }
  if (dados.dias_coletados < 2) {
    const nunca = dados.itens.filter((i) => i.dias_sumido === null);
    const aviso = `<p class="vazio-inline">Só ${dados.dias_coletados} dia de coleta —`
      + " para medir sumiço é preciso comparar dias. Colete amanhã.</p>";
    if (!nunca.length) return aviso;
    return aviso + `<ul class="lista-saude">` + nunca.map((i) =>
      `<li class="ruim"><b>${escapar(i.nome)}</b>
        <span>nunca apareceu na coleta — link provavelmente morto</span></li>`
    ).join("") + "</ul>";
  }

  const mortos = dados.itens.filter((i) => i.morto);
  if (!mortos.length) {
    return `<p class="vazio-inline ok">Todos os ${dados.itens.length} itens`
      + " apareceram na coleta recente.</p>";
  }
  return `<ul class="lista-saude">` + mortos.map((i) =>
    `<li class="ruim"><b>${escapar(i.nome)}</b><span>${
      i.dias_sumido === null
        ? "nunca apareceu na coleta"
        : `sem aparecer há ${i.dias_sumido} dia(s)`
    } — possível link morto</span></li>`
  ).join("") + "</ul>";
}

function secaoPares(dados) {
  if (!dados.pares.length) {
    return `<p class="vazio-inline">Nenhum par apareceu ${dados.minimo}+ vezes.`
      + " Com pouca amostra, sugerir combinação seria inventar padrão."
      + " Baixe o mínimo para 1 se quiser ver os pares avulsos.</p>";
  }
  return `<ul class="lista-saude">` + dados.pares.map((p) =>
    `<li><b>${p.juntos}x</b>
      <span>${escapar(p.nome_a)}<br>+ ${escapar(p.nome_b)}</span>
      <em>${moeda(p.comissao)}</em></li>`
  ).join("") + "</ul>";
}

function secaoEsperas(dados) {
  const e = dados.esperas;
  if (!e.total) {
    return "<p class=\"vazio-inline\">Sem dado ainda. O clickTime só é gravado"
      + " em coletas novas — conversões antigas não têm como recuperar.</p>";
  }
  const pct = Math.round((e.no_dia / e.total) * 100);
  return `<div class="numeros-saude">
      <div><b>${e.total}</b><span>conversões</span></div>
      <div><b>${e.mediana}d</b><span>mediana</span></div>
      <div><b>${pct}%</b><span>compram no mesmo dia</span></div>
      <div><b>${e.tardias}</b><span>do 4º dia em diante</span></div>
    </div>
    <p class="explicacao">O cookie da Shopee dura 7 dias. Se muita gente compra
    tarde, repostar antes do prazo recupera venda que já era sua.</p>`;
}

function secaoAlvos(dados) {
  if (!dados.alvos.length) {
    return "<p class=\"vazio-inline\">Nenhum alvo definido.</p>";
  }
  return `<ul class="lista-saude">` + dados.alvos.map((a) => {
    const situacao = a.preco_atual === null
      ? "<span>sem preço coletado</span>"
      : a.atingido
        ? `<span class="atingido">chegou a ${moeda(a.preco_atual)} — comprar agora</span>`
        : `<span>hoje ${moeda(a.preco_atual)} · falta cair ${a.falta_pct}%</span>`;
    return `<li class="${a.atingido ? "bom" : ""}">
      <b>${moeda(a.alvo)}</b>
      <span>${escapar(a.nome)}<br>${situacao}</span>
      <em><button class="acao-icone" data-remover-alvo="${a.item_id}">remover</button></em>
    </li>`;
  }).join("") + "</ul>";
}

async function carregarSaude() {
  const area = $("area-saude");
  try {
    const minimo = $("saude-minimo").value;
    const dados = await api(`/api/saude?minimo=${encodeURIComponent(minimo)}`);
    area.innerHTML = [
      blocoSaude("Links que podem estar mortos",
        "Item que some da API geralmente esgotou ou saiu do ar. "
        + "Nada frustra mais quem clica do que \"produto indisponível\".",
        secaoDisponibilidade(dados)),
      blocoSaude("Comprados juntos",
        "Itens que apareceram no mesmo pedido. Serve para montar combo.",
        secaoPares(dados)),
      blocoSaude("Tempo entre o clique e a compra",
        "Diz quando vale repostar antes de perder a atribuição.",
        secaoEsperas(dados)),
      blocoSaude("Alertas de preço",
        "Avisa quando o produto cair abaixo do valor que você definiu. "
        + "Para criar, use o botão <code>Alvo</code> na aba Ranking.",
        secaoAlvos(dados)),
    ].join("");
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// --------------------------------------------------------- compartilhados

/** Data ISO -> "04/09 14:32". A hora importa: revela quando você posta. */
function quando(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Vendas agrupadas pela marcação do link — inclui campanhas de tráfego pago. */
function desenharCanais(dados) {
  const area = $("area-canais");
  const canais = dados.vendas_por_canal.filter((c) => c.vendas > 0);
  if (!canais.length) {
    area.innerHTML = '<p class="vazio-inline">Nenhuma venda no período.</p>';
    return;
  }

  const e = dados.espera;
  const cadencia = e.total
    ? `<p class="explicacao">Entre o clique e a compra: mediana de ${e.mediana}`
      + ` dia(s). ${Math.round((e.no_dia / e.total) * 100)}% compram no mesmo dia,`
      + ` mas ${e.tardias} compraram do 4º dia em diante — o cookie da Shopee`
      + " dura 7 dias, então repostar dentro desse prazo recupera venda.</p>"
    : "";

  const linhas = canais.map((c) => `<tr>
    <td class="produto">${escapar(c.canal)}</td>
    <td class="num">${numero(c.vendas)}</td>
    <td class="num">${numero(c.produtos)}</td>
    <td class="num dinheiro">${moeda(c.comissao)}</td>
    <td class="num">${moeda(c.ticket)}</td>
    <td>${c.primeira || "-"} a ${c.ultima || "-"}</td>
  </tr>`).join("");

  area.innerHTML = '<div class="tabela-area"><table><thead><tr>'
    + "<th>Campanha</th><th>Vendas</th><th>Produtos</th><th>Comissão</th>"
    + "<th>Média por venda</th><th>Período</th>"
    + "</tr></thead><tbody>" + linhas + "</tbody></table></div>" + cadencia;
}

async function carregarCompartilhados() {
  const area = $("area-compartilhados");
  try {
    const dias = $("ganhos-dias").value;
    const dados = await api(`/api/compartilhados?dias=${encodeURIComponent(dias)}`);
    desenharCanais(dados);

    if (!dados.itens.length) {
      area.innerHTML = '<p class="vazio-inline">Nenhum link gerado nesse período.'
        + " Escolha um canal em <b>Divulgar em</b> e clique em <b>Copiar</b>"
        + " num produto — o link fica registrado aqui.</p>";
      return;
    }

    const t = dados.totais;
    const semCliques = !dados.tem_cliques
      ? '<p class="vazio-inline">Os cliques ficam em zero até você publicar o'
        + " redirecionador no Netlify. Sem ele, a Shopee não informa cliques"
        + " e não há como medir conversão.</p>"
      : "";

    const porCanal = dados.canais.length > 1
      ? `<div class="numeros-saude">${dados.canais.map((c) =>
          `<div><b>${moeda(c.comissao)}</b><span>${escapar(c.canal)} · `
          + `${c.links} link(s) · ${numero(c.cliques)} clique(s)</span></div>`
        ).join("")}</div>`
      : "";

    const linhas = dados.itens.map((i) => `<tr>
      <td class="produto">${escapar(i.nome)}</td>
      <td>${escapar(i.canal)}</td>
      <td class="num">${quando(i.gerado_em)}</td>
      <td class="num">${i.cliques ? numero(i.cliques) : "-"}</td>
      <td class="num">${i.pedidos || "-"}</td>
      <td class="num">${i.conversao !== null ? numero(i.conversao, 1) + "%" : "-"}</td>
      <td class="num dinheiro">${i.comissao ? moeda(i.comissao) : "-"}</td>
      <td>${i.ultima_venda || "-"}</td>
      <td>${botaoLink(i.link)}</td>
    </tr>`).join("");

    area.innerHTML = porCanal + semCliques
      + '<div class="tabela-area"><table><thead><tr>'
      + "<th>Produto</th><th>Canal</th><th>Gerado</th><th>Cliques</th>"
      + "<th>Vendas</th><th>Conversão</th><th>Comissão</th><th>Última venda</th>"
      + "<th>Link</th></tr></thead><tbody>" + linhas + "</tbody>"
      + `<tfoot><tr><td><b>${t.links} link(s)</b></td><td></td><td></td>`
      + `<td class="num"><b>${numero(t.cliques)}</b></td>`
      + `<td class="num"><b>${t.pedidos}</b></td><td></td>`
      + `<td class="num dinheiro"><b>${moeda(t.comissao)}</b></td>`
      + "<td></td><td></td></tr></tfoot></table></div>";
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// ---------------------------------------------------------------- alertas

const CLASSE_ETIQUETA = {
  comissao_subiu: "subiu", preco_caiu: "subiu", novo_no_top: "novo",
  comissao_caiu: "caiu", preco_subiu: "caiu", sumiu: "neutro",
};
const CLASSE_BORDA = {
  subiu: "destaque-alta", caiu: "destaque-baixa", novo: "destaque-novo", neutro: "",
};

async function carregarAlertas() {
  try {
    const dados = await api(`/api/alertas?dia=${$("alerta-dia").value || "hoje"}`);
    const lista = $("lista-alertas");
    if (!dados.anterior) {
      $("resumo-alertas").textContent = "";
      lista.innerHTML = '<div class="vazio"><b>Sem snapshot anterior</b>'
        + "A comparação precisa de pelo menos dois dias coletados.</div>";
      return;
    }
    $("resumo-alertas").textContent = `Comparando ${dados.anterior} com ${dados.dia} · ${dados.itens.length} mudança(s)`;
    if (!dados.itens.length) {
      lista.innerHTML = '<div class="vazio"><b>Nenhuma mudança relevante</b>'
        + "Nada passou dos limiares configurados em <code>[alertas]</code>.</div>";
      return;
    }
    lista.innerHTML = dados.itens.map((a) => {
      const classe = CLASSE_ETIQUETA[a.tipo] || "neutro";
      const variacao = a.variacao != null ? `${a.variacao > 0 ? "+" : ""}${(a.variacao * 100).toFixed(0)}%` : "";
      const valores = (a.antes != null && a.depois != null)
        ? `${numero(a.antes, 4)} → ${numero(a.depois, 4)}  ${variacao}` : variacao;
      return `<div class="alerta-linha ${CLASSE_BORDA[classe]}">
        <span class="etiqueta ${classe}">${a.rotulo}</span>
        <div><div class="alerta-nome">${escapar(a.nome)}</div>
        <div class="alerta-valores">${valores}</div></div>
        <div>${botaoLink(a.link)}</div>
      </div>`;
    }).join("");
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// ----------------------------------------------------------------- ganhos

async function carregarGanhos() {
  try {
    const dados = await api(`/api/ganhos?dias=${$("ganhos-dias").value}`);
    const t = dados.totais;
    const cvr = t.cliques ? t.pedidos / t.cliques : 0;
    cartoes($("cartoes-ganhos"), [
      { rotulo: "Comissão", valor: moeda(t.comissao), classe: "positivo" },
      { rotulo: "Cliques", valor: numero(t.cliques) },
      { rotulo: "Pedidos", valor: numero(t.pedidos) },
      { rotulo: "Conversão", valor: pct(cvr, 2), classe: "destaque" },
    ]);

    const serie = [...dados.itens].reverse();
    const maximo = Math.max(...serie.map((i) => i.comissao), 1);
    $("grafico-ganhos").innerHTML = serie.map((i) =>
      `<div class="barra ${i.validada ? "validada" : ""}" style="height:${Math.max((i.comissao / maximo) * 100, 2)}%" title="${i.dia}: ${moeda(i.comissao)}"></div>`
    ).join("") || '<div class="vazio"><b>Sem conversões</b>Rode <code>coletar --com-conversoes</code>.</div>';

    tabela($("tabela-ganhos"), [
      { titulo: "Dia", render: (i) => i.dia },
      { titulo: "Plataforma", render: (i) => i.plataforma },
      { titulo: "Cliques", num: true, render: (i) => numero(i.cliques) },
      { titulo: "Pedidos", num: true, render: (i) => numero(i.pedidos) },
      { titulo: "CVR", num: true, render: (i) => i.cliques ? pct(i.pedidos / i.cliques, 2) : "-" },
      { titulo: "Comissão", num: true, render: (i) => moeda(i.comissao) },
      { titulo: "Tipo", render: (i) => i.validada ? "validada" : "estimada" },
    ], dados.itens, "Nenhuma conversão registrada.");
  } catch (erro) {
    avisar(erro.message, true);
  }
  await carregarCompartilhados();
}

// -------------------------------------------------------------- acumulado

async function carregarHistorico() {
  try {
    const dados = await api("/api/historico?limite=50");
    $("resumo-historico").textContent =
      `${dados.itens.length} produto(s) com histórico · ${dados.total_dias} dia(s) coletado(s) · `
      + "score pondera persistência no topo e atualidade";
    tabela($("tabela-historico"), [
      { titulo: "Produto", classe: "produto", render: (i) => escapar(i.nome) },
      { titulo: "Dias", num: true, render: (i) => `${i.dias_visto}/${dados.total_dias}` },
      { titulo: "Score", num: true, render: (i) => `<span class="marca-epc">${numero(i.score, 4)}</span>` },
      { titulo: "EPC médio", num: true, render: (i) => numero(i.epc_medio, 4) },
      { titulo: "R$/venda", num: true, render: (i) => moeda(i.comissao_media) },
      { titulo: "Preço médio", num: true, render: (i) => moeda(i.preco_medio) },
      { titulo: "Vendas+", num: true, render: (i) => i.vendas_delta ?? "-" },
      { titulo: "Último", render: (i) => i.ultimo_dia },
      { titulo: "Link", render: (i) => botaoLink(i.link) },
    ], dados.itens, "Histórico insuficiente. Colete em mais dias.");
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// --------------------------------------------------------------- execução

function opcoesDoComando(comando) {
  if (comando === "coletar") {
    return {
      com_conversoes: $("acao-conversoes").checked,
      plataforma: $("acao-plataforma").value,
      quantidade: $("acao-quantidade").value,
    };
  }
  if (comando === "notificar") return { tipo: $("acao-tipo").value };
  if (comando === "cliques") return { por_canal: $("acao-por-canal").checked };
  if (comando === "publicar") return { netlify: $("acao-netlify").checked };
  return {};
}

async function executar(comando) {
  try {
    await api("/api/tarefa", {
      method: "POST",
      body: JSON.stringify({ comando, opcoes: opcoesDoComando(comando) }),
    });
    linhasRecebidas = 0;
    $("console").textContent = "";
    acompanhar();
  } catch (erro) {
    avisar(erro.message, true);
  }
}

async function acompanhar() {
  clearTimeout(enquete);
  try {
    const dados = await api(`/api/tarefa?desde=${linhasRecebidas}`);
    const tarefa = dados.tarefa;
    if (!tarefa) return;

    if (tarefa.total_linhas < linhasRecebidas) {
      linhasRecebidas = 0;
      $("console").textContent = "";
    }
    if (tarefa.linhas.length) {
      $("console").textContent += tarefa.linhas.join("\n") + "\n";
      $("console").scrollTop = $("console").scrollHeight;
      linhasRecebidas = tarefa.total_linhas;
    }

    const rodando = tarefa.estado === "rodando";
    $("console-titulo").innerHTML =
      `${tarefa.rotulo} — <span class="estado-${tarefa.estado === "ok" ? "ok" : rodando ? "rodando" : "erro"}">${tarefa.estado}</span>`;
    $("btn-cancelar").hidden = !rodando;
    document.querySelectorAll("[data-comando]").forEach((b) => { b.disabled = rodando; });

    if (rodando) {
      enquete = setTimeout(acompanhar, 800);
    } else {
      // Devolve o botão de coleta ao normal, com sucesso ou com falha:
      // deixá-lo girando para sempre depois de um erro seria pior que o erro.
      const coletar = $("btn-coletar-agora");
      coletar.classList.remove("ocupado");
      coletar.textContent = "Buscar novos na Shopee";

      if (tarefa.estado === "ok") {
        avisar(`${tarefa.rotulo}: concluído`);
        recarregarAbaAtiva();
        carregarEstado();
      }
    }
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// ------------------------------------------------------------------ geral

const CARREGADORES = {
  ranking: carregarRanking, vitrine: carregarVitrine, saude: carregarSaude,
  alertas: carregarAlertas,
  ganhos: carregarGanhos, historico: carregarHistorico, acoes: acompanhar,
};

function abaAtiva() {
  return document.querySelector(".aba.ativa").dataset.painel;
}

function recarregarAbaAtiva() {
  const carregar = CARREGADORES[abaAtiva()];
  if (carregar) carregar();
}

/**
 * Faixa de aviso quando a coleta falhou, ficou pela metade ou está atrasada.
 *
 * O indicador antigo só contava ofertas no banco, então uma rodada que morreu
 * depois de salvar parte parecia sucesso. Como velocidade de venda, menor
 * preço e a aba Mudanças dependem de coletar todo dia, isso precisa aparecer
 * sem o usuário ir procurar.
 */
function mostrarAlertaColeta(coleta) {
  const faixa = $("alerta-coleta");
  if (!coleta || !coleta.problemas.length) {
    faixa.hidden = true;
    return;
  }
  const grave = coleta.estado === "erro" || (coleta.dias_atraso ?? 0) >= 3;
  faixa.className = `faixa-alerta${grave ? " grave" : ""}`;
  faixa.innerHTML = `<b>Atenção:</b> ${coleta.problemas.join(" · ")}.`
    + " O histórico de preço, a velocidade de venda e a aba Mudanças"
    + " dependem de coletar todo dia."
    + ' <button id="alerta-coletar">Coletar agora</button>'
    + ' <button id="alerta-agendar">Como agendar</button>';
  faixa.hidden = false;

  $("alerta-coletar").addEventListener("click", () => {
    trocarAba("acoes");
    executar("coletar");
  });
  $("alerta-agendar").addEventListener("click", () => {
    avisar("no terminal: flow02 agendar --hora 07:00", false, 9000);
  });
}

async function carregarEstado() {
  try {
    const estado = await api("/api/estado");
    const integracoes = estado.integracoes;
    $("indicadores").innerHTML = [
      ["Shopee", integracoes.shopee], ["Telegram", integracoes.telegram],
      ["Firebase", integracoes.firebase],
    ].map(([nome, ativo]) =>
      `<span class="pilula ${ativo ? "on" : "off"}">${nome}: ${ativo ? "ok" : "off"}</span>`
    ).join("")
      + `<span class="pilula">${numero(estado.total_ofertas)} ofertas</span>`
      + `<span class="pilula">${estado.escopos_calibrados} escopos CVR</span>`;

    mostrarAlertaColeta(estado.coleta);
    carregarCategorias();

    const opcoes = ['<option value="hoje">hoje</option>']
      .concat(estado.dias.map((d) => `<option value="${d}">${d}</option>`)).join("");
    for (const id of ["filtro-dia", "alerta-dia"]) {
      const anterior = $(id).value;
      $(id).innerHTML = opcoes;
      if (anterior) $(id).value = anterior;
    }
  } catch (erro) {
    avisar(erro.message, true);
  }
}

function ligarEventos() {
  $("abas").addEventListener("click", (evento) => {
    const aba = evento.target.closest(".aba");
    if (aba) trocarAba(aba.dataset.painel);
  });

  ["filtro-dia", "filtro-plataforma", "filtro-limite", "filtro-diversificar",
   "filtro-desconto", "filtro-tabela", "filtro-oportunidades",
   "filtro-variantes", "filtro-janela", "filtro-categoria"]
    .forEach((id) => $(id).addEventListener("change", carregarRanking));

  [...Object.keys(FILTROS_VALOR), "f-so-vitrine"].forEach((id) =>
    $(id).addEventListener("change", () => {
      contarFiltrosAtivos();
      carregarRanking();
    }));
  $("btn-limpar-filtros").addEventListener("click", limparFiltrosDeValor);
  $("btn-recarregar").addEventListener("click", recarregar);
  $("btn-coletar-agora").addEventListener("click", coletarAgora);

  // O relógio precisa andar sozinho: sem isto ficaria em "agora" para sempre.
  setInterval(desenharRelogio, 30_000);

  // F5 recarrega a página inteira e o token some da barra em alguns
  // navegadores; interceptar mantém o usuário logado.
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "F5" || (evento.ctrlKey && evento.key === "r")) {
      evento.preventDefault();
      recarregar();
    }
  });

  $("atalhos").addEventListener("click", (evento) => {
    const atalho = evento.target.closest(".atalho");
    if (!atalho) return;
    document.querySelectorAll(".atalho").forEach((a) => a.classList.remove("ativo"));
    atalho.classList.add("ativo");
    ordenacao = atalho.dataset.ordenar;
    carregarRanking();
  });

  let atraso;
  $("filtro-busca").addEventListener("input", () => {
    clearTimeout(atraso);
    atraso = setTimeout(carregarRanking, 250);
  });

  $("filtro-canal").addEventListener("change", () => {
    const livre = $("filtro-canal").value === "__outro";
    $("rotulo-canal-livre").hidden = !livre;
    if (livre) $("filtro-subid").focus();
  });

  $("btn-copiar-post").addEventListener("click", () => copiarPost(ultimoRanking));
  $("btn-vitrine-post").addEventListener("click", () => copiarPost(ultimaVitrine));
  $("btn-vitrine-recarregar").addEventListener("click", carregarVitrine);
  $("btn-saude-recarregar").addEventListener("click", carregarSaude);
  $("saude-minimo").addEventListener("change", carregarSaude);
  $("vitrine-colecao").addEventListener("change", carregarVitrine);
  $("btn-lote-vitrine").addEventListener("click", (evento) =>
    gerarLote(ultimaVitrine, evento.currentTarget));
  $("btn-lote-ranking").addEventListener("click", (evento) =>
    gerarLote(ultimoRanking, evento.currentTarget));
  $("alerta-dia").addEventListener("change", carregarAlertas);
  $("btn-recarregar-alertas").addEventListener("click", carregarAlertas);
  $("ganhos-dias").addEventListener("change", carregarGanhos);

  document.querySelectorAll("[data-comando]").forEach((botao) =>
    botao.addEventListener("click", () => executar(botao.dataset.comando)));

  $("btn-cancelar").addEventListener("click", async () => {
    await api("/api/tarefa/cancelar", { method: "POST", body: "{}" }).catch(() => {});
    acompanhar();
  });

  document.addEventListener("click", async (evento) => {
    const copiar = evento.target.closest("[data-link]");
    if (copiar) return copiarLink(copiar);

    const arte = evento.target.closest("[data-arte]");
    if (arte) return abrirArte(arte.dataset.arte);

    const vitrine = evento.target.closest("[data-vitrine]");
    if (vitrine) return alternarVitrine(vitrine);

    const alvo = evento.target.closest("[data-alvo]");
    if (alvo) return definirAlvo(alvo.dataset.alvo, Number(alvo.dataset.preco));

    const soltar = evento.target.closest("[data-remover-alvo]");
    if (soltar) return removerAlvo(soltar.dataset.removerAlvo);
  });

  $("arte-fechar").addEventListener("click", ARTE.fechar);
  $("arte-formato").addEventListener("change", ARTE.redesenhar);

  // "só o link" fica por último: o padrão passa a ser o post completo, que
  // é o que de fato se cola num grupo.
  $("filtro-modelo").innerHTML = MODELOS.lista()
    .map((m) => `<option value="${m.id}">${m.nome}</option>`).join("")
    + '<option value="so-link">só o link</option>';

  const marca = ARTE.marca();
  $("arte-assinatura").value = marca.assinatura;
  $("arte-chamada").value = marca.chamada;
  for (const id of ["arte-assinatura", "arte-chamada"]) {
    $(id).addEventListener("input", () => {
      ARTE.definirMarca($("arte-assinatura").value, $("arte-chamada").value);
      ARTE.redesenhar();
    });
  }
  $("arte-baixar").addEventListener("click", ARTE.baixar);
  $("arte-copiar-legenda").addEventListener("click", () =>
    copiarTexto($("arte-legenda").value, "legenda copiada"));
  $("arte-copiar-roteiro").addEventListener("click", () =>
    copiarTexto($("arte-roteiro").value, "roteiro copiado"));
  $("modal-arte").addEventListener("click", (evento) => {
    if (evento.target.id === "modal-arte") ARTE.fechar();
  });
  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape") ARTE.fechar();
  });
}

if (!TOKEN) {
  document.body.innerHTML = '<div class="vazio" style="margin:60px">Token ausente. Abra a URL impressa no terminal.</div>';
} else {
  ligarEventos();
  esqueleto($("area-ranking"));
  carregarEstado().then(carregarRanking);
  acompanhar();
}
