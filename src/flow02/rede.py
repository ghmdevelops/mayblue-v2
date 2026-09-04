"""Camada HTTP compartilhada.

Por que existe um contexto SSL customizado: o Python 3.13+ passou a ligar
`ssl.VERIFY_X509_STRICT` por padrao em `create_default_context()`. Proxies
corporativos com inspecao TLS costumam emitir certificados que nao passam
nessa checagem estrita de conformidade com a RFC 5280, produzindo
`CERTIFICATE_VERIFY_FAILED: CA cert does not include key usage extension`.

Relaxar apenas essa flag restaura o comportamento do Python 3.12: a validacao
de cadeia, de validade e de hostname continua totalmente ativa. Em nenhum
momento este modulo desabilita a verificacao de certificado.

Variaveis de ambiente:
    FLOW02_CA_BUNDLE  caminho de um .pem extra a confiar (CA corporativa)
    FLOW02_TLS_STRICT 1 para reativar VERIFY_X509_STRICT
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

USER_AGENT = "flow02/0.1 (+https://github.com/flow02)"


class ErroHTTP(RuntimeError):
    def __init__(self, status: int | None, corpo: str, url: str) -> None:
        super().__init__(f"HTTP {status} em {url}: {corpo[:200]}")
        self.status = status
        self.corpo = corpo
        self.url = url


@dataclass(frozen=True, slots=True)
class Resposta:
    status: int
    corpo: str

    def json(self) -> dict:
        return json.loads(self.corpo) if self.corpo else {}


@lru_cache(maxsize=4)
def contexto_ssl(ca_bundle: str | None = None, strict: bool | None = None) -> ssl.SSLContext:
    ca_bundle = ca_bundle or os.environ.get("FLOW02_CA_BUNDLE") or None
    if strict is None:
        strict = os.environ.get("FLOW02_TLS_STRICT", "0") == "1"

    contexto = ssl.create_default_context()
    if ca_bundle and os.path.exists(ca_bundle):
        contexto.load_verify_locations(cafile=ca_bundle)
    if not strict:
        contexto.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return contexto


def requisitar(
    url: str,
    dados: bytes | None = None,
    cabecalhos: dict[str, str] | None = None,
    metodo: str = "GET",
    timeout_s: float = 30.0,
) -> Resposta:
    requisicao = urllib.request.Request(
        url,
        data=dados,
        method=metodo,
        headers={"User-Agent": USER_AGENT, **(cabecalhos or {})},
    )
    try:
        with urllib.request.urlopen(
            requisicao, timeout=timeout_s, context=contexto_ssl()
        ) as resposta:
            return Resposta(resposta.status, resposta.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        corpo = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise ErroHTTP(exc.code, corpo, url) from exc


def status_de(url: str, cabecalhos: dict[str, str] | None = None,
              timeout_s: float = 15.0) -> int | str:
    """Retorna o status HTTP (ou a descricao do erro de rede). Usado no probe."""
    try:
        return requisitar(url, cabecalhos=cabecalhos, timeout_s=timeout_s).status
    except ErroHTTP as exc:
        return exc.status or "sem status"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"erro de rede: {exc}"
