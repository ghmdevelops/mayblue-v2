/*
  Redirecionador que conta cliques.

  Por que existe: a API de afiliados da Shopee nao expoe numero de cliques
  (confirmado por introspecao do schema -- so ha relatorios de oferta,
  conversao e pedido). Sem cliques, taxa de conversao e incalculavel.

  Passando o trafego por aqui, o clique passa a ser SEU dado:

      /r/{codigo}  ->  conta no Firebase  ->  302 para o link de afiliado

  Depois, `flow02 cliques` importa os contadores e a calibracao de CVR
  volta a funcionar com numero medido em vez de heuristica.

  Roda em Netlify Functions (Node 18+, fetch nativo). Nao usa SDK do
  Firebase: a REST da Realtime Database resolve e evita node_modules.
*/

const BANCO = process.env.FIREBASE_DATABASE_URL;
const RAIZ = process.env.FLOW02_RAIZ || "flow02";
const SEGREDO = process.env.FIREBASE_DB_SECRET || "";
const DESTINO_PADRAO = process.env.FLOW02_FALLBACK || "https://shopee.com.br";

const CODIGO_VALIDO = /^[A-Za-z0-9_-]{1,64}$/;

function url(caminho) {
  const base = `${BANCO.replace(/\/$/, "")}/${caminho}.json`;
  return SEGREDO ? `${base}?auth=${encodeURIComponent(SEGREDO)}` : base;
}

function hoje() {
  // Fuso do Brasil (UTC-3, sem horário de verão desde 2019).
  return new Date(Date.now() - 3 * 3600 * 1000).toISOString().slice(0, 10);
}

async function lerLink(codigo) {
  const resposta = await fetch(url(`${RAIZ}/links/${codigo}`));
  if (!resposta.ok) return null;
  return resposta.json();
}

/** Incremento atômico. A RTDB não tem counter, então usa transação por ETag. */
async function contar(codigo, dia, link) {
  const caminho = `${RAIZ}/cliques/${dia}/${codigo}`;
  for (let tentativa = 0; tentativa < 3; tentativa++) {
    const atual = await fetch(url(caminho), { headers: { "X-Firebase-ETag": "true" } });
    const etag = atual.headers.get("ETag");
    const dados = atual.ok ? await atual.json() : null;

    const corpo = {
      total: ((dados && dados.total) || 0) + 1,
      item_id: link.item_id ?? (dados && dados.item_id) ?? null,
      canal: link.canal ?? (dados && dados.canal) ?? null,
      atualizado_em: new Date().toISOString(),
    };

    const gravacao = await fetch(url(caminho), {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...(etag ? { "if-match": etag } : {}),
      },
      body: JSON.stringify(corpo),
    });
    if (gravacao.ok) return true;
    if (gravacao.status !== 412) return false; // 412 = alguém escreveu antes
  }
  return false;
}

export default async (requisicao) => {
  const codigo = new URL(requisicao.url).pathname.split("/").filter(Boolean).pop();

  if (!codigo || !CODIGO_VALIDO.test(codigo) || !BANCO) {
    return Response.redirect(DESTINO_PADRAO, 302);
  }

  const link = await lerLink(codigo).catch(() => null);
  if (!link || !link.url) {
    return Response.redirect(DESTINO_PADRAO, 302);
  }

  // O clique é contado sem bloquear o redirecionamento: se o Firebase
  // demorar, o usuário não fica esperando. Perder um clique é melhor que
  // perder uma venda.
  contar(codigo, hoje(), link).catch(() => {});

  return new Response(null, {
    status: 302,
    headers: { Location: link.url, "Cache-Control": "no-store" },
  });
};

export const config = { path: "/r/*" };
