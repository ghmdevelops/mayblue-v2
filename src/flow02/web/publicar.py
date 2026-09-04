"""Gera a pagina publica que le o Firebase, com a configuracao embutida.

A pagina e um arquivo unico e autocontido. Nao usa o SDK do Firebase porque
o registry do npm esta bloqueado nesta rede (403), assim como o PyPI -- e a
API REST da Realtime Database resolve tudo o que a tela precisa com `fetch`.

Consequencia pratica: o arquivo abre direto do disco, do celular, ou de
qualquer hospedagem estatica. Sem build, sem node_modules.
"""

from __future__ import annotations

from pathlib import Path

from ..config import RAIZ, Config

MODELO = Path(__file__).parent / "estatico" / "publico.html"


class ErroPublicacao(RuntimeError):
    pass


def gerar(cfg: Config) -> str:
    if not cfg.firebase.url:
        raise ErroPublicacao(
            "sem URL do Realtime Database. Preencha [firebase].database_url ou "
            "[firebase].projeto no config.toml. Rode `flow02 doctor` -- ele sonda "
            "as regioes e descobre a URL certa."
        )
    substituicoes = {
        "__DATABASE_URL__": cfg.firebase.url,
        "__API_KEY__": cfg.firebase.api_key or "",
        "__RAIZ__": cfg.firebase.raiz,
    }
    html = MODELO.read_text(encoding="utf-8")
    for marcador, valor in substituicoes.items():
        html = html.replace(marcador, valor)
    return html


def escrever(cfg: Config, destino: Path | None = None) -> Path:
    destino = destino or (cfg.caminho_saida / "painel.html")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(gerar(cfg), encoding="utf-8")
    return destino


def preparar_netlify(cfg: Config, pasta: Path | None = None) -> dict:
    """Monta a pasta que o Netlify publica.

    A vitrine vira `index.html` porque o Netlify serve isso na raiz. A
    Function do redirecionador ja mora em netlify/functions e nao precisa
    ser copiada -- o netlify.toml aponta para la.
    """
    pasta = pasta or (RAIZ / "publicado")
    pasta.mkdir(parents=True, exist_ok=True)
    indice = pasta / "index.html"
    indice.write_text(gerar(cfg), encoding="utf-8")

    (pasta / "_headers").write_text(
        "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n",
        encoding="utf-8",
    )
    return {
        "pasta": pasta,
        "indice": indice,
        "funcao": RAIZ / "netlify" / "functions" / "r.js",
        "config": RAIZ / "netlify.toml",
    }


def instrucoes(cfg: Config, destino: Path) -> str:
    projeto = cfg.firebase.projeto or cfg.firebase.url.split("//")[-1].split(".")[0]
    projeto = projeto.removesuffix("-default-rtdb")
    return "\n".join([
        f"pagina gerada em {destino}",
        "",
        "Como usar:",
        "  1. abra o arquivo direto no navegador (funciona do disco), ou",
        "  2. hospede em qualquer lugar estatico para acessar do celular:",
        f"       firebase deploy --only hosting --project {projeto}",
        "     (copie o arquivo para a pasta public/ como index.html)",
        "",
        "A pagina so LE o banco. Para funcionar sem login, as regras precisam",
        "permitir leitura publica em /" + cfg.firebase.raiz + ":",
        "",
        '  { "rules": { "' + cfg.firebase.raiz + '": {',
        '      ".read": true,',
        '      ".write": "auth != null && auth.uid === \'UID_DO_ROBO\'" } } }',
        "",
        "Se preferir manter a leitura restrita, a pagina mostra um formulario",
        "de login e autentica pelo Firebase Auth (e-mail/senha).",
    ])
