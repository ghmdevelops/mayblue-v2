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


PAGINA_MINIMA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>.</title>
<style>
  body { background:#0f1117; color:#3a4150; font:14px system-ui, sans-serif;
         display:grid; place-items:center; height:100vh; margin:0; }
</style>
</head>
<body><p>.</p></body>
</html>
"""


def preparar_netlify(cfg: Config, pasta: Path | None = None,
                     com_vitrine: bool = True) -> dict:
    """Monta a pasta que o Netlify publica.

    A vitrine vira `index.html` porque o Netlify serve isso na raiz. A
    Function do redirecionador ja mora em netlify/functions e nao precisa
    ser copiada -- o netlify.toml aponta para la.

    `com_vitrine=False` sobe so o redirecionador. A diferenca importa: a
    Function le as credenciais de `process.env` e roda no servidor, entao
    nada dela chega ao navegador. A vitrine, por ser pagina cliente, carrega
    a URL e a apiKey do Firebase visiveis para qualquer visitante -- e uma
    URL do Netlify e publica mesmo sem ser divulgada.

    Para quem so quer medir cliques, subir a vitrine e expor sem ganho.
    """
    pasta = pasta or (RAIZ / "publicado")
    pasta.mkdir(parents=True, exist_ok=True)
    indice = pasta / "index.html"
    indice.write_text(gerar(cfg) if com_vitrine else PAGINA_MINIMA,
                      encoding="utf-8")

    (pasta / "_headers").write_text(
        "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n"
        + ("" if com_vitrine else "  X-Robots-Tag: noindex\n"),
        encoding="utf-8",
    )
    return {
        "pasta": pasta,
        "indice": indice,
        "com_vitrine": com_vitrine,
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
