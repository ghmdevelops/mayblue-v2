"use strict";

/*
  Gerador de arte de campanha.

  Compõe a peça direto no <canvas> do navegador. Isso só é possível porque o
  CDN da Shopee responde `Access-Control-Allow-Origin: *` -- sem esse header o
  canvas ficaria "tainted" e o export em PNG seria bloqueado pelo navegador.

  Vídeo real não é gerado: a máquina não tem ffmpeg e não há biblioteca de
  codificação disponível. No lugar, o painel entrega o roteiro pronto para
  gravar, com os números do produto já preenchidos.
*/

const ARTE = (() => {
  const COR = {
    fundo: "#0f1117",
    fundoAlt: "#1b2030",
    texto: "#ffffff",
    suave: "#aab2c5",
    destaque: "#6ea8fe",
    dinheiro: "#56d4a0",
    alerta: "#f0b429",
  };

  let itemAtual = null;
  let linkAtual = "";
  // Ficam no navegador: são preferências de quem usa, não dado do sistema.
  let assinaturaAtual = localStorage.getItem("flow02_assinatura") || "";
  let chamadaAtual = localStorage.getItem("flow02_chamada")
    || "LINK NA DESCRIÇÃO";

  function definirMarca(assinatura, chamada) {
    assinaturaAtual = (assinatura || "").trim().slice(0, 40);
    chamadaAtual = (chamada || "").trim().slice(0, 34).toUpperCase()
      || "LINK NA DESCRIÇÃO";
    localStorage.setItem("flow02_assinatura", assinaturaAtual);
    localStorage.setItem("flow02_chamada", chamadaAtual);
  }

  function marca() {
    return { assinatura: assinaturaAtual, chamada: chamadaAtual };
  }

  const canvas = () => document.getElementById("arte-canvas");

  function moedaBR(valor) {
    return (valor ?? 0).toLocaleString("pt-BR",
      { style: "currency", currency: "BRL" });
  }

  function carregarImagem(url) {
    return new Promise((resolve) => {
      if (!url) return resolve(null);
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = url;
    });
  }

  /** Quebra o texto em linhas que cabem na largura, respeitando palavras. */
  function quebrar(ctx, texto, largura, maxLinhas) {
    const palavras = String(texto).split(/\s+/);
    const linhas = [];
    let atual = "";
    for (const palavra of palavras) {
      const tentativa = atual ? `${atual} ${palavra}` : palavra;
      if (ctx.measureText(tentativa).width > largura && atual) {
        linhas.push(atual);
        atual = palavra;
        if (linhas.length === maxLinhas - 1) break;
      } else {
        atual = tentativa;
      }
    }
    if (atual && linhas.length < maxLinhas) linhas.push(atual);
    if (linhas.length === maxLinhas) {
      const ultima = linhas[maxLinhas - 1];
      if (ctx.measureText(ultima).width > largura) {
        linhas[maxLinhas - 1] = ultima.slice(0, -3) + "…";
      }
    }
    return linhas;
  }

  function arredondado(ctx, x, y, largura, altura, raio) {
    ctx.beginPath();
    ctx.roundRect(x, y, largura, altura, raio);
  }

  /** Faixa arredondada com texto centralizado. Devolve a largura ocupada. */
  function pastilha(ctx, x, y, texto, fundo, cor, fonte, altura) {
    ctx.font = fonte;
    const largura = ctx.measureText(texto).width + altura;
    ctx.fillStyle = fundo;
    arredondado(ctx, x, y, largura, altura, altura / 2.6);
    ctx.fill();
    ctx.fillStyle = cor;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(texto, x + largura / 2, y + altura / 2 + 1);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    return largura;
  }

  /** Estrelas desenhadas em vez de escritas: leem mais rápido que "nota 4.9". */
  function estrelas(ctx, x, y, nota, tamanho) {
    const pontas = 5;
    for (let i = 0; i < 5; i++) {
      const cheia = nota >= i + 0.75;
      const meia = !cheia && nota >= i + 0.25;
      ctx.fillStyle = cheia || meia ? COR.alerta : "rgba(255,255,255,.18)";
      ctx.beginPath();
      const cx = x + i * tamanho * 1.22 + tamanho / 2;
      const cy = y + tamanho / 2;
      for (let p = 0; p < pontas * 2; p++) {
        const raio = p % 2 === 0 ? tamanho / 2 : tamanho / 4.6;
        const angulo = (Math.PI / pontas) * p - Math.PI / 2;
        ctx[p === 0 ? "moveTo" : "lineTo"](
          cx + Math.cos(angulo) * raio, cy + Math.sin(angulo) * raio);
      }
      ctx.closePath();
      ctx.fill();
    }
    return 5 * tamanho * 1.22;
  }

  /**
   * Calcula a geometria antes de desenhar.
   *
   * Separado do desenho porque o defeito mais comum aqui é sobreposição: o
   * nome cobrindo a foto, o preço saindo da tela. Com a geometria em dados,
   * dá para testar que os blocos não colidem — desenho puro não é testável
   * sem renderizar.
   */
  function calcularLayout(largura, altura, temNota, temAssinatura) {
    const esc = (v) => Math.round(largura * v);
    const margem = esc(0.06);
    const util = largura - margem * 2;

    const rodape = { altura: esc(0.088) };
    rodape.y = altura - rodape.altura - margem;

    const assinatura = temAssinatura
      ? { y: rodape.y - esc(0.03), altura: esc(0.03) }
      : null;

    // De baixo para cima: o rodapé é fixo, o resto se acomoda acima dele.
    const preco = { altura: esc(0.115) };
    preco.y = (assinatura ? assinatura.y : rodape.y) - esc(0.035);

    const nome = { altura: esc(0.048) * 1.26 * 2, linhas: 2 };
    nome.y = preco.y - preco.altura - esc(0.045);

    const nota = temNota ? { altura: esc(0.034) } : null;
    if (nota) nota.y = nome.y - nome.altura - esc(0.02);

    // A foto ocupa o que sobrou, sempre quadrada e nunca maior que o espaço.
    const topo = (nota ? nota.y - nota.altura : nome.y - nome.altura) - esc(0.03);
    const lado = Math.min(util, Math.max(esc(0.3), topo - margem));

    return {
      esc, margem, util,
      foto: { x: Math.round((largura - lado) / 2), y: margem, lado },
      nota, nome, preco, assinatura, rodape,
    };
  }

  async function desenhar(item, formato) {
    const [largura, altura] = formato.split("x").map(Number);
    const tela = canvas();
    tela.width = largura;
    tela.height = altura;
    const ctx = tela.getContext("2d");
    const esc = (v) => Math.round(largura * v);

    ctx.fillStyle = COR.fundo;
    ctx.fillRect(0, 0, largura, altura);

    // Brilho sutil atrás da foto, na cor da marca. Dá profundidade sem
    // depender da imagem, que varia de produto para produto.
    const halo = ctx.createRadialGradient(
      largura / 2, altura * 0.28, 0, largura / 2, altura * 0.28, largura * 0.7);
    halo.addColorStop(0, "rgba(124,176,255,.14)");
    halo.addColorStop(1, "rgba(124,176,255,0)");
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, largura, altura);

    const L = calcularLayout(largura, altura, Boolean(item.rating),
                             Boolean(assinaturaAtual));
    const { margem, util } = L;
    const desconto = Math.round(item.desconto_real ?? item.desconto_declarado ?? 0);

    // ---- foto num cartão claro --------------------------------------------
    // Foto de produto de marketplace vem quase sempre recortada em fundo
    // branco. Antes eu sangrava a imagem no fundo escuro e o branco batia
    // num corte reto, comendo o texto por baixo. Num cartão claro o branco
    // vira intenção, não acidente -- e `contain` mostra o produto inteiro
    // em vez de cortá-lo como `cover` fazia.
    const foto = await carregarImagem(item.imagem);
    ctx.save();
    arredondado(ctx, L.foto.x, L.foto.y, L.foto.lado, L.foto.lado, esc(0.045));
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.clip();
    if (foto) {
      const respiro = L.foto.lado * 0.06;
      const disponivel = L.foto.lado - respiro * 2;
      const escala = Math.min(disponivel / foto.width, disponivel / foto.height);
      const largFoto = foto.width * escala;
      const altFoto = foto.height * escala;
      ctx.drawImage(foto, L.foto.x + (L.foto.lado - largFoto) / 2,
                    L.foto.y + (L.foto.lado - altFoto) / 2, largFoto, altFoto);
    } else {
      ctx.fillStyle = "#e6e9ef";
      ctx.fillRect(L.foto.x, L.foto.y, L.foto.lado, L.foto.lado);
    }
    ctx.restore();

    const alturaPastilha = esc(0.066);

    // ---- economia em reais, flutuando sobre o cartão ----------------------
    // "Economize R$ 72" convence mais que "33% OFF": porcentagem exige conta
    // mental, valor absoluto não.
    if (desconto > 0) {
      const antes = item.preco / (1 - desconto / 100);
      const usado = pastilha(
        ctx, L.foto.x + esc(0.025), L.foto.y + esc(0.025),
        `ECONOMIZE ${moedaBR(antes - item.preco)}`,
        COR.alerta, "#20180a", `800 ${esc(0.032)}px system-ui, sans-serif`,
        alturaPastilha);
      pastilha(ctx, L.foto.x + esc(0.025) + usado + esc(0.015),
               L.foto.y + esc(0.025), `-${desconto}%`,
               "#131722", "#ffffff",
               `800 ${esc(0.032)}px system-ui, sans-serif`, alturaPastilha);
    }

    // ---- prova social, no rodapé do cartão --------------------------------
    if (item.vendas > 100) {
      pastilha(ctx, L.foto.x + esc(0.025),
               L.foto.y + L.foto.lado - alturaPastilha - esc(0.025),
               `${item.vendas.toLocaleString("pt-BR")} JÁ COMPRARAM`,
               "rgba(10,12,17,.88)", "#ffffff",
               `700 ${esc(0.029)}px system-ui, sans-serif`, alturaPastilha);
    }

    // ---- nota -------------------------------------------------------------
    if (L.nota) {
      const tamanho = esc(0.032);
      const usado = estrelas(ctx, margem, L.nota.y - tamanho, item.rating, tamanho);
      ctx.fillStyle = COR.suave;
      ctx.font = `600 ${esc(0.03)}px system-ui, sans-serif`;
      ctx.fillText(item.rating.toFixed(1).replace(".", ","),
                   margem + usado + esc(0.016), L.nota.y);
    }

    // ---- nome -------------------------------------------------------------
    const fonteNome = esc(0.048);
    ctx.fillStyle = COR.texto;
    ctx.font = `650 ${fonteNome}px system-ui, sans-serif`;
    let y = L.nome.y - L.nome.altura + fonteNome;
    for (const linha of quebrar(ctx, item.nome, util, L.nome.linhas)) {
      ctx.fillText(linha, margem, y);
      y += Math.round(fonteNome * 1.26);
    }

    // ---- preço ------------------------------------------------------------
    const fontePreco = esc(0.115);
    ctx.fillStyle = COR.dinheiro;
    ctx.font = `750 ${fontePreco}px system-ui, sans-serif`;
    ctx.fillText(moedaBR(item.preco), margem, L.preco.y);

    if (desconto > 0) {
      const antes = item.preco / (1 - desconto / 100);
      const xAntes = margem + ctx.measureText(moedaBR(item.preco)).width + esc(0.026);
      const fonteAntes = esc(0.04);
      ctx.fillStyle = COR.suave;
      ctx.font = `400 ${fonteAntes}px system-ui, sans-serif`;
      const texto = moedaBR(antes);
      const yAntes = L.preco.y - fontePreco * 0.14;
      ctx.fillText(texto, xAntes, yAntes);
      ctx.strokeStyle = COR.suave;
      ctx.lineWidth = Math.max(2, esc(0.0035));
      ctx.beginPath();
      ctx.moveTo(xAntes, yAntes - fonteAntes * 0.3);
      ctx.lineTo(xAntes + ctx.measureText(texto).width, yAntes - fonteAntes * 0.3);
      ctx.stroke();
    }

    // Assinatura: conteúdo com identidade constrói marca; sem ela você
    // compete só por preço, e sempre vai existir quem poste mais barato.
    if (L.assinatura) {
      ctx.fillStyle = COR.suave;
      ctx.font = `600 ${esc(0.029)}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillText(assinaturaAtual, largura / 2, L.assinatura.y);
      ctx.textAlign = "left";
    }

    // ---- rodapé -----------------------------------------------------------
    const botao = ctx.createLinearGradient(margem, 0, margem + util, 0);
    botao.addColorStop(0, COR.destaque);
    botao.addColorStop(1, COR.dinheiro);
    ctx.fillStyle = botao;
    arredondado(ctx, margem, L.rodape.y, util, L.rodape.altura, esc(0.022));
    ctx.fill();
    ctx.fillStyle = "#06121f";
    ctx.font = `800 ${esc(0.038)}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(chamadaAtual, largura / 2,
                 L.rodape.y + L.rodape.altura / 2 + 1);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
  }

  function montarLegenda(item, link) {
    const desconto = Math.round(item.desconto_real ?? item.desconto_declarado ?? 0);
    const partes = [
      item.nome,
      "",
      `De olho nesse: ${moedaBR(item.preco)}` + (desconto ? ` — ${desconto}% OFF` : ""),
    ];
    if (item.vendas > 500) {
      partes.push(`Já são ${item.vendas.toLocaleString("pt-BR")} vendidos.`);
    }
    if (item.rating >= 4.5) {
      partes.push(`Nota ${item.rating.toFixed(1)} de quem comprou.`);
    }
    partes.push("", "Link para comprar:", link || "(gere o link primeiro)", "");
    partes.push("#achadinhos #promocao #ofertas #shopee #achadosdashopee");
    return partes.join("\n");
  }

  function montarRoteiro(item) {
    const desconto = Math.round(item.desconto_real ?? item.desconto_declarado ?? 0);
    const gancho = desconto >= 30
      ? `Isso aqui tá ${desconto}% mais barato e ninguém tá falando.`
      : `Achei por ${moedaBR(item.preco)} e não acreditei.`;
    return [
      "0-3s  GANCHO (mostre o produto na mão ou na tela)",
      `      "${gancho}"`,
      "",
      "3-10s  O QUE É",
      `      "${item.nome}"`,
      `      Preço: ${moedaBR(item.preco)}`,
      item.vendas ? `      Prova social: ${item.vendas.toLocaleString("pt-BR")} pessoas já compraram` : "",
      item.rating ? `      Nota ${item.rating.toFixed(1)}` : "",
      "",
      "10-20s  POR QUE VALE",
      "      Mostre 2 usos reais. Fale de um defeito pequeno —",
      "      isso aumenta a confiança mais do que só elogiar.",
      "",
      "20-25s  CHAMADA",
      "      \"Link na descrição. Se acabar o estoque não é comigo.\"",
      "",
      `Sua comissão por venda: ${moedaBR(item.comissao_valor)}`,
    ].filter(Boolean).join("\n");
  }

  /** Preenche o seletor de modelos e aplica o escolhido. */
  function aplicarModelo() {
    if (!itemAtual) return;
    const escolha = document.getElementById("arte-modelo");
    document.getElementById("arte-legenda").value =
      MODELOS.montar(escolha.value, itemAtual, linkAtual);
    const modelo = MODELOS.lista().find((m) => m.id === escolha.value);
    document.getElementById("arte-modelo-quando").textContent =
      modelo ? modelo.quando : "";
  }

  function prepararModelos() {
    const escolha = document.getElementById("arte-modelo");
    if (escolha.options.length) return;  // já montado
    escolha.innerHTML = MODELOS.lista()
      .map((m) => `<option value="${m.id}">${m.nome}</option>`).join("");
    escolha.addEventListener("change", aplicarModelo);
  }

  async function abrir(item, link) {
    itemAtual = item;
    linkAtual = link || item.link || "";
    document.getElementById("arte-titulo").textContent = item.nome.slice(0, 60);
    prepararModelos();
    aplicarModelo();
    document.getElementById("arte-roteiro").value = montarRoteiro(item);
    document.getElementById("modal-arte").hidden = false;
    await desenhar(item, document.getElementById("arte-formato").value);
  }

  function fechar() {
    document.getElementById("modal-arte").hidden = true;
    itemAtual = null;
  }

  async function redesenhar() {
    if (itemAtual) {
      await desenhar(itemAtual, document.getElementById("arte-formato").value);
    }
  }

  function baixar() {
    if (!itemAtual) return;
    canvas().toBlob((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `flow02-${itemAtual.item_id}.png`;
      a.click();
      URL.revokeObjectURL(url);
    }, "image/png");
  }

  function nomeArquivo(item, indice) {
    const limpo = String(item.nome || item.item_id)
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48)
      .toLowerCase();
    return `${String(indice).padStart(2, "0")}-${limpo || item.item_id}`;
  }

  async function paraBytes(tela) {
    const blob = await new Promise((r) => tela.toBlob(r, "image/png"));
    return new Uint8Array(await blob.arrayBuffer());
  }

  // Largura da miniatura embutida no HTML. A arte sai em 1080px; embutir
  // nesse tamanho geraria um HTML de dezenas de megabytes com 40 produtos.
  const LARGURA_PREVIA = 320;

  /**
   * Miniatura em JPEG, como data URI, para embutir na página.
   *
   * Sem isto o HTML depende dos arquivos em `artes/`: abrir sem
   * descompactar, mover o arquivo ou mandar só ele para alguém deixa a
   * página cheia de imagem quebrada.
   *
   * JPEG e não PNG porque a diferença é grande em foto: a mesma prévia sai
   * cerca de cinco vezes menor, e para conferir enquadramento e legibilidade
   * a perda não aparece. O PNG em tamanho cheio continua no ZIP, e é ele que
   * o botão "Baixar imagem" entrega.
   */
  function previaEmbutida(tela) {
    const escala = LARGURA_PREVIA / tela.width;
    const mini = document.createElement("canvas");
    mini.width = LARGURA_PREVIA;
    mini.height = Math.round(tela.height * escala);
    const ctx = mini.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(tela, 0, 0, mini.width, mini.height);
    return mini.toDataURL("image/jpeg", 0.72);
  }

  const escaparHtml = (t) => String(t ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  /**
   * Página que monta arte e texto lado a lado.
   *
   * Antes o ZIP entregava as imagens numa pasta e todas as legendas num .txt
   * corrido — para postar era preciso abrir os dois e casar arquivo com
   * texto na mão, item por item. Aqui cada produto aparece com a arte, a
   * legenda e o roteiro juntos, cada um com seu botão de copiar.
   *
   * Detalhe que decide a implementação: o arquivo é aberto por `file://`,
   * que o Chrome NÃO considera contexto seguro. `navigator.clipboard` fica
   * indisponível ali, então é preciso o caminho antigo com `execCommand`.
   */
  function paginaDoLote(entradas, formato) {
    const cartoes = entradas.map((e, i) => `
  <article class="cartao">
    <img src="${e.previa}" alt="" loading="lazy">
    <div class="lado">
      <h2>${i + 1}. ${escaparHtml(e.nome)}</h2>
      <p class="meta">${escaparHtml(e.meta)}</p>
      <label>Legenda</label>
      <textarea rows="11" data-legenda>${escaparHtml(e.legenda)}</textarea>
      <div class="botoes">
        <button data-copiar="legenda">Copiar legenda</button>
        <a download href="artes/${escaparHtml(e.arquivo)}"
           title="PNG em tamanho cheio, na pasta artes/">Baixar PNG</a>
      </div>
      <details>
        <summary>Roteiro para vídeo</summary>
        <textarea rows="14" data-roteiro>${escaparHtml(e.roteiro)}</textarea>
        <button data-copiar="roteiro">Copiar roteiro</button>
      </details>
    </div>
  </article>`).join("");

    return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${entradas.length} artes — flow02</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: #0f1117; color: #f4f6fa;
         font: 15px/1.55 system-ui, -apple-system, Segoe UI, sans-serif; }
  header { display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
           margin-bottom: 24px; }
  h1 { font-size: 20px; margin: 0; }
  .sub { color: #8a94a6; font-size: 13.5px; }
  .nota { flex-basis: 100%; margin: 4px 0 0; color: #8a94a6; font-size: 13px;
          line-height: 1.5; max-width: 70ch; }
  .nota code { background: #1a1f2b; padding: 1px 6px; border-radius: 4px; }
  button, a[download] {
    background: #4f8cff; color: #06121f; border: 0; border-radius: 8px;
    padding: 9px 16px; font: inherit; font-weight: 600; cursor: pointer;
    text-decoration: none; display: inline-block;
  }
  button.secundario, a[download] { background: #1a1f2b; color: #b3bccc;
    border: 1px solid #262c3a; }
  button:hover, a[download]:hover { filter: brightness(1.1); }
  button.ok { background: #4fd6a4; }
  .cartao { display: grid; grid-template-columns: 300px 1fr; gap: 20px;
    background: #141821; border: 1px solid #1f2531; border-radius: 14px;
    padding: 18px; margin-bottom: 18px; }
  .cartao img { width: 100%; border-radius: 10px; background: #fff; }
  .lado { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  h2 { font-size: 16px; margin: 0; line-height: 1.35; }
  .meta { margin: 0; color: #4fd6a4; font-size: 13.5px; font-weight: 600; }
  label { font-size: 11.5px; text-transform: uppercase; letter-spacing: .7px;
    color: #a7b2c4; font-weight: 650; }
  textarea { width: 100%; background: #0f1117; color: #f4f6fa;
    border: 1px solid #262c3a; border-radius: 8px; padding: 12px;
    font: inherit; font-size: 13.5px; resize: vertical; }
  .botoes { display: flex; gap: 8px; flex-wrap: wrap; }
  details { margin-top: 4px; }
  summary { cursor: pointer; color: #b3bccc; font-size: 13.5px; padding: 6px 0; }
  details textarea { margin: 8px 0; font-family: ui-monospace, monospace;
    font-size: 12.5px; }
  @media (max-width: 720px) { .cartao { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>${entradas.length} artes prontas</h1>
  <span class="sub">${escaparHtml(formato)} · gerado em ${new Date().toLocaleString("pt-BR")}</span>
  <button id="tudo">Copiar todas as legendas</button>
  <p class="nota">As imagens aqui são prévias reduzidas, embutidas nesta
  página — ela funciona sozinha, mesmo movida de lugar. Para postar, use
  <b>Baixar PNG</b>: entrega a arte em ${escaparHtml(formato)}, na pasta
  <code>artes/</code>.</p>
</header>
${cartoes}
<script>
// file:// nao e contexto seguro: navigator.clipboard fica indisponivel no
// Chrome. O execCommand e obsoleto, mas e o unico que funciona aqui.
function copiar(texto) {
  const campo = document.createElement("textarea");
  campo.value = texto;
  campo.style.position = "fixed";
  campo.style.opacity = "0";
  document.body.appendChild(campo);
  campo.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  campo.remove();
  return ok;
}

function avisar(botao, ok) {
  const original = botao.textContent;
  botao.textContent = ok ? "copiado" : "falhou — copie manualmente";
  botao.classList.toggle("ok", ok);
  setTimeout(() => {
    botao.textContent = original;
    botao.classList.remove("ok");
  }, 1600);
}

document.addEventListener("click", (evento) => {
  const botao = evento.target.closest("[data-copiar]");
  if (!botao) return;
  const campo = botao.closest(".lado, details")
    .querySelector("[data-" + botao.dataset.copiar + "]");
  avisar(botao, copiar(campo.value));
});

document.getElementById("tudo").addEventListener("click", (evento) => {
  const textos = [...document.querySelectorAll("[data-legenda]")]
    .map((c, i) => (i + 1) + ". " + c.value);
  avisar(evento.target, copiar(textos.join("\\n\\n" + "-".repeat(40) + "\\n\\n")));
});
</script>
</body>
</html>`;
  }

  /**
   * Gera um ZIP com uma arte por produto e um `abrir.html` que mostra tudo
   * montado. Reaproveita o mesmo canvas em sequência: 20 canvases de
   * 1080x1350 na memória ao mesmo tempo travariam o navegador.
   */
  async function gerarLote(itens, formato, links = {}, aoProgredir = () => {}) {
    const arquivos = [];
    const entradas = [];

    for (const [indice, item] of itens.entries()) {
      aoProgredir(indice + 1, itens.length, item.nome);
      await desenhar(item, formato);
      const base = nomeArquivo(item, indice + 1);
      arquivos.push({ nome: `artes/${base}.png`, dados: await paraBytes(canvas()) });

      const link = links[item.item_id] || item.link || "";
      entradas.push({
        arquivo: `${base}.png`,
        previa: previaEmbutida(canvas()),
        nome: item.nome,
        meta: `${moedaBR(item.preco)} · você ganha ${moedaBR(item.comissao_valor)}`
          + (item.vendas ? ` · ${item.vendas.toLocaleString("pt-BR")} vendidos` : ""),
        legenda: montarLegenda(item, link),
        roteiro: montarRoteiro(item),
      });
    }

    const codificador = new TextEncoder();
    arquivos.push({
      nome: "abrir.html",
      dados: codificador.encode(paginaDoLote(entradas, formato)),
    });
    // O .txt continua indo: serve para quem prefere trabalhar no editor, e
    // e o unico formato que sobrevive a copiar so um arquivo para o celular.
    arquivos.push({
      nome: "legendas-e-roteiros.txt",
      dados: codificador.encode(entradas.map((e, i) => [
        "=".repeat(70), `${i + 1}. ${e.nome}`, `arquivo: ${e.arquivo}`, "",
        "--- LEGENDA ---", e.legenda, "", "--- ROTEIRO ---", e.roteiro, "",
      ].join("\n")).join("\n")),
    });
    return ZIP.criar(arquivos);
  }

  return { abrir, fechar, redesenhar, baixar, gerarLote, calcularLayout,
           paginaDoLote, definirMarca, marca, montarLegenda, montarRoteiro };
})();
