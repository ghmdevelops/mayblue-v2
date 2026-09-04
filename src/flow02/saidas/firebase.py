"""Sincronizacao com o Firebase Realtime Database via API REST.

Por que REST e nao o SDK `firebase-admin`: o PyPI esta bloqueado neste
ambiente, entao nenhuma dependencia externa pode ser instalada. A RTDB expoe
uma API REST completa que funciona com a stdlib.

Autenticacao, em ordem de preferencia:

1. Conta de servico do Firebase Auth (email/senha). O login e feito pela API
   REST do Identity Toolkit e devolve um idToken de 1h. E a opcao recomendada,
   porque permite regras do tipo `auth.uid === '...'`.
2. Database secret legado (`FIREBASE_DB_SECRET`). Ainda funciona, mas o Google
   o considera obsoleto e ele da acesso total ao banco.
3. Sem autenticacao. So funciona com regras abertas -- nesse caso qualquer
   pessoa que descubra a URL le e apaga seus dados. Nao use fora de teste.

A `apiKey` do Firebase Web nao e um segredo: ela vai no bundle de qualquer
app web. Quem protege os dados sao as regras do banco.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from ..rede import ErroHTTP, requisitar

ENDPOINT_LOGIN = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key="
)
MARGEM_RENOVACAO_S = 60


class ErroFirebase(RuntimeError):
    pass


@dataclass
class ClienteFirebase:
    database_url: str
    api_key: str | None = None
    email: str | None = None
    senha: str | None = None
    segredo: str | None = None
    timeout_s: float = 30.0
    _token: str | None = field(default=None, init=False, repr=False)
    _expira_em: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.database_url:
            raise ErroFirebase(
                "sem URL do Realtime Database. Preencha [firebase].database_url "
                "ou ao menos [firebase].projeto no config.toml -- a URL pode ser "
                "inferida do projectId. Note que o snippet de config do console so "
                "traz databaseURL depois que o Realtime Database e criado."
            )
        self.database_url = self.database_url.rstrip("/")

    @property
    def modo_auth(self) -> str:
        if self.email and self.senha and self.api_key:
            return "conta de servico"
        if self.segredo:
            return "database secret (legado)"
        return "sem autenticacao"

    def _autenticar(self) -> str | None:
        if self.segredo:
            return self.segredo
        if not (self.email and self.senha and self.api_key):
            return None
        if self._token and time.time() < self._expira_em - MARGEM_RENOVACAO_S:
            return self._token

        corpo = json.dumps({
            "email": self.email, "password": self.senha, "returnSecureToken": True,
        }).encode("utf-8")
        try:
            resposta = requisitar(
                ENDPOINT_LOGIN + self.api_key, dados=corpo, metodo="POST",
                cabecalhos={"Content-Type": "application/json"},
                timeout_s=self.timeout_s,
            ).json()
        except ErroHTTP as exc:
            raise ErroFirebase(
                f"login no Firebase falhou: {exc.corpo[:200]}. Confira "
                "FIREBASE_EMAIL/FIREBASE_SENHA e se o provedor Email/Senha esta "
                "habilitado em Authentication > Sign-in method"
            ) from exc

        self._token = resposta.get("idToken")
        self._expira_em = time.time() + float(resposta.get("expiresIn", 3600))
        if not self._token:
            raise ErroFirebase(f"login sem idToken: {resposta}")
        return self._token

    def _url(self, caminho: str) -> str:
        limpo = "/".join(quote(p, safe="") for p in caminho.strip("/").split("/") if p)
        url = f"{self.database_url}/{limpo}.json"
        token = self._autenticar()
        return f"{url}?auth={quote(token, safe='')}" if token else url

    def _enviar(self, caminho: str, dados, metodo: str) -> None:
        try:
            requisitar(
                self._url(caminho),
                dados=json.dumps(dados, ensure_ascii=False).encode("utf-8"),
                metodo=metodo,
                cabecalhos={"Content-Type": "application/json"},
                timeout_s=self.timeout_s,
            )
        except ErroHTTP as exc:
            if exc.status == 401:
                raise ErroFirebase(
                    f"401 do Realtime Database em /{caminho}. As regras negaram a "
                    f"escrita (modo atual: {self.modo_auth}). Veja a secao de regras "
                    "no README."
                ) from exc
            if exc.status == 404:
                raise ErroFirebase(
                    f"404 em {self.database_url}. A URL do Realtime Database esta "
                    "errada ou o banco nao foi criado no console."
                ) from exc
            raise ErroFirebase(f"{metodo} /{caminho} falhou: {exc}") from exc

    def escrever(self, caminho: str, dados) -> None:
        """PUT: substitui o conteudo do caminho."""
        self._enviar(caminho, dados, "PUT")

    def mesclar(self, caminho: str, dados: dict) -> None:
        """PATCH: atualiza so as chaves enviadas."""
        self._enviar(caminho, dados, "PATCH")

    def ler(self, caminho: str):
        try:
            return requisitar(self._url(caminho), timeout_s=self.timeout_s).json()
        except ErroHTTP as exc:
            raise ErroFirebase(f"GET /{caminho} falhou: {exc}") from exc

    def url_console(self, caminho: str = "") -> str:
        projeto = self.database_url.split("//")[-1].split(".")[0]
        projeto = projeto.removesuffix("-default-rtdb")
        base = f"https://console.firebase.google.com/project/{projeto}/database"
        return f"{base}/data/{caminho.strip('/')}" if caminho else f"{base}/data"


def sondar(url: str, timeout_s: float = 12.0) -> tuple[str, str]:
    """Descobre se existe um Realtime Database nessa URL.

    Distinguir 401 de 404 e o que importa: 401 significa que o banco existe e
    as regras negaram (URL certa), 404 significa que nao ha banco ali (URL
    errada, provavelmente outra regiao).
    """
    try:
        resposta = requisitar(f"{url.rstrip('/')}/.json?shallow=true",
                              timeout_s=timeout_s)
    except ErroHTTP as exc:
        if exc.status == 401:
            return "existe", "existe, mas as regras negam leitura anonima"
        if exc.status == 404:
            return "ausente", "nao ha Realtime Database nessa URL"
        return "erro", f"HTTP {exc.status}"
    except (TimeoutError, OSError) as exc:
        return "erro", f"rede: {exc}"
    conteudo = (resposta.corpo or "").strip()
    if conteudo in ("null", ""):
        return "existe", "existe e esta vazio (leitura publica)"
    return "existe", f"existe e legivel: {conteudo[:60]}"


def _linha_para_dict(linha, link: str | None = None) -> dict:
    return {
        "nome": linha["nome"],
        "preco": round(linha["preco"], 2),
        "taxa_comissao": linha["taxa_comissao"],
        "comissao_valor": round(linha["comissao_valor"], 2),
        "epc": linha["score"],
        "cvr": linha["cvr_estimado"],
        "cvr_calibrado": bool(linha["calibrado"]),
        "vendas": linha["vendas"],
        "rating": linha["rating"],
        "desconto_pct": linha["desconto_pct"],
        "loja": linha["loja_nome"],
        "link": link or linha["link_oferta"] or linha["link_produto"],
    }


def sincronizar_ranking(
    cliente: ClienteFirebase,
    raiz: str,
    dia: str,
    linhas,
    links: dict[str, str] | None = None,
) -> dict[str, int]:
    """Publica o ranking do dia, agrupado por plataforma."""
    links = links or {}
    por_plataforma: dict[str, dict[str, dict]] = {}
    for posicao, linha in enumerate(linhas, start=1):
        item = _linha_para_dict(linha, links.get(linha["item_id"]))
        item["posicao"] = posicao
        por_plataforma.setdefault(linha["plataforma"], {})[linha["item_id"]] = item

    for plataforma, itens in por_plataforma.items():
        cliente.escrever(f"{raiz}/ranking/{dia}/{plataforma}", itens)

    melhor = max((l["score"] for l in linhas), default=0.0)
    cliente.mesclar(f"{raiz}/resumo/{dia}", {
        "total_itens": len(linhas),
        "melhor_epc": melhor,
        "plataformas": sorted(por_plataforma),
        "atualizado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    return {p: len(itens) for p, itens in por_plataforma.items()}


def sincronizar_links(
    cliente: ClienteFirebase, raiz: str, entradas: dict[str, dict]
) -> int:
    """Publica o mapa codigo -> destino que o redirecionador consulta.

    Usa PATCH para nao apagar codigos de posts antigos que ainda circulam.
    """
    if not entradas:
        return 0
    cliente.mesclar(f"{raiz}/links", entradas)
    return len(entradas)


def sincronizar_vitrine(
    cliente: ClienteFirebase, raiz: str, linhas,
    links: dict[str, str] | None = None,
    indisponiveis: set[tuple[str, str]] | None = None,
) -> int:
    """Publica a selecao curada, agrupada por colecao.

    Usa PUT porque a vitrine e uma lista fechada: item que voce tirou tem que
    sumir da pagina publica, ao contrario do historico de ganhos.

    Item marcado como indisponivel nao sobe. Publicar link que leva a "produto
    indisponivel" queima a confianca de quem clicou -- e melhor a vitrine ter
    menos itens do que ter item morto.
    """
    links = links or {}
    indisponiveis = indisponiveis or set()
    por_colecao: dict[str, dict[str, dict]] = {}
    for linha in linhas:
        if not linha["nome"]:
            continue  # item curado que ainda nao apareceu em nenhuma coleta
        if (linha["plataforma"], linha["item_id"]) in indisponiveis:
            continue
        por_colecao.setdefault(linha["colecao"], {})[linha["item_id"]] = {
            "posicao": linha["posicao"],
            "nome": linha["nome"],
            "preco": round(linha["preco"] or 0, 2),
            "taxa_comissao": linha["taxa_comissao"],
            "comissao_valor": round(linha["comissao_valor"] or 0, 2),
            "epc": linha["epc"],
            "vendas": linha["vendas"],
            "rating": linha["rating"],
            "desconto_pct": linha["desconto_pct"],
            "loja": linha["loja_nome"],
            "imagem": linha["imagem"],
            "link": links.get(linha["item_id"]) or linha["link_oferta"]
            or linha["link_produto"],
        }

    cliente.escrever(f"{raiz}/vitrine", por_colecao)
    return sum(len(itens) for itens in por_colecao.values())


def ler_cliques(cliente: ClienteFirebase, raiz: str, desde: str | None = None) -> list[dict]:
    """Le os contadores gravados pela Netlify Function."""
    dados = cliente.ler(f"{raiz}/cliques") or {}
    registros = []
    for dia, por_codigo in (dados.items() if isinstance(dados, dict) else []):
        if desde and str(dia) < desde:
            continue
        for codigo, valores in (por_codigo or {}).items():
            if not isinstance(valores, dict):
                continue
            registros.append({
                "plataforma": "shopee",
                "codigo": str(codigo),
                "dia": str(dia),
                "item_id": valores.get("item_id"),
                "canal": valores.get("canal"),
                "total": int(valores.get("total") or 0),
            })
    return registros


def sincronizar_ganhos(cliente: ClienteFirebase, raiz: str, linhas) -> int:
    """Publica o retorno realizado por dia."""
    if not linhas:
        return 0
    por_dia: dict[str, dict] = {}
    for linha in linhas:
        chave = f"{linha['dia']}_{linha['plataforma']}"
        por_dia[chave] = {
            "dia": linha["dia"],
            "plataforma": linha["plataforma"],
            "cliques": linha["cliques"],
            "pedidos": linha["pedidos"],
            "comissao": round(linha["comissao"], 2),
            "validada": bool(linha["validada"]),
        }
    cliente.mesclar(f"{raiz}/ganhos", por_dia)
    return len(por_dia)
