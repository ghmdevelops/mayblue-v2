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

  async function abrir(item, link) {
    itemAtual = item;
    linkAtual = link || item.link || "";
    document.getElementById("arte-titulo").textContent = item.nome.slice(0, 60);
    document.getElementById("arte-legenda").value = montarLegenda(item, linkAtual);
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

  /**
   * Gera um ZIP com uma arte por produto, mais um .txt com todas as legendas
   * e roteiros. Reaproveita o mesmo canvas em sequência: 20 canvases de
   * 1080x1350 na memória ao mesmo tempo travariam o navegador.
   */
  async function gerarLote(itens, formato, links = {}, aoProgredir = () => {}) {
    const arquivos = [];
    const textos = [];

    for (const [indice, item] of itens.entries()) {
      aoProgredir(indice + 1, itens.length, item.nome);
      await desenhar(item, formato);
      const base = nomeArquivo(item, indice + 1);
      arquivos.push({ nome: `artes/${base}.png`, dados: await paraBytes(canvas()) });

      const link = links[item.item_id] || item.link || "";
      textos.push([
        `${"=".repeat(70)}`,
        `${indice + 1}. ${item.nome}`,
        `arquivo: ${base}.png`,
        "",
        "--- LEGENDA ---",
        montarLegenda(item, link),
        "",
        "--- ROTEIRO ---",
        montarRoteiro(item),
        "",
      ].join("\n"));
    }

    const codificador = new TextEncoder();
    arquivos.push({
      nome: "legendas-e-roteiros.txt",
      dados: codificador.encode(textos.join("\n")),
    });
    return ZIP.criar(arquivos);
  }

  return { abrir, fechar, redesenhar, baixar, gerarLote, calcularLayout,
           definirMarca, marca, montarLegenda, montarRoteiro };
})();
