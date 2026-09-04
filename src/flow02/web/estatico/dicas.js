"use strict";

/*
  Dicas ao passar o mouse.

  Por que não usar o `title` nativo: ele demora ~1s para aparecer, não pode ser
  formatado, some sozinho depois de alguns segundos e não abre no teclado.
  Para uma ferramenta cheia de conceito próprio (EPC, CVR, desconto real,
  sub-ID) a explicação precisa aparecer na hora e caber em duas linhas.

  Uso: `data-dica="texto"` em qualquer elemento. Funciona também em conteúdo
  criado depois, porque escuta no documento em vez de em cada elemento.

  Acessibilidade: abre no `focusin` também, então quem navega por Tab lê a
  mesma explicação. Fecha no Escape.
*/

const DICAS = (() => {
  const MARGEM = 10;
  let caixa = null;
  let alvoAtual = null;

  function criar() {
    if (caixa) return caixa;
    caixa = document.createElement("div");
    caixa.className = "dica-flutuante";
    caixa.setAttribute("role", "tooltip");
    caixa.hidden = true;
    document.body.appendChild(caixa);
    return caixa;
  }

  function posicionar(elemento) {
    const area = elemento.getBoundingClientRect();
    const propria = caixa.getBoundingClientRect();

    // Prefere abaixo; sobe se não couber na parte de baixo da janela.
    let topo = area.bottom + MARGEM;
    if (topo + propria.height > window.innerHeight - MARGEM) {
      topo = area.top - propria.height - MARGEM;
    }
    // Centraliza no elemento, sem deixar sair pelas laterais.
    let esquerda = area.left + area.width / 2 - propria.width / 2;
    esquerda = Math.max(MARGEM, Math.min(
      esquerda, window.innerWidth - propria.width - MARGEM));

    caixa.style.top = `${Math.max(MARGEM, topo)}px`;
    caixa.style.left = `${esquerda}px`;
  }

  function mostrar(elemento) {
    const texto = elemento.dataset.dica;
    if (!texto) return;
    alvoAtual = elemento;
    criar();
    caixa.textContent = texto;
    caixa.hidden = false;
    caixa.style.top = "-9999px";  // mede fora da tela antes de posicionar
    posicionar(elemento);
  }

  function esconder() {
    alvoAtual = null;
    if (caixa) caixa.hidden = true;
  }

  function ligar() {
    document.addEventListener("mouseover", (evento) => {
      const elemento = evento.target.closest("[data-dica]");
      if (elemento && elemento !== alvoAtual) mostrar(elemento);
    });
    document.addEventListener("mouseout", (evento) => {
      const elemento = evento.target.closest("[data-dica]");
      if (elemento && !elemento.contains(evento.relatedTarget)) esconder();
    });
    document.addEventListener("focusin", (evento) => {
      const elemento = evento.target.closest("[data-dica]");
      if (elemento) mostrar(elemento);
    });
    document.addEventListener("focusout", esconder);
    document.addEventListener("keydown", (evento) => {
      if (evento.key === "Escape") esconder();
    });
    // Rolar ou redimensionar deixaria a dica apontando para o lugar errado.
    window.addEventListener("scroll", esconder, true);
    window.addEventListener("resize", esconder);
  }

  return { ligar, mostrar, esconder };
})();

document.addEventListener("DOMContentLoaded", DICAS.ligar);
