"""Servidor HTTP local do painel.

Um servidor local que executa comandos e um alvo classico: qualquer site
aberto no navegador pode disparar requisicoes para 127.0.0.1. As protecoes:

- escuta so em 127.0.0.1, nunca em 0.0.0.0;
- token aleatorio por sessao, exigido na pagina (query) e em toda rota de API
  (header). `/estatico/*` fica de fora porque o navegador pede CSS e JS por
  <link> e <script src>, sem enviar header nem query -- exigir token ali
  simplesmente quebra a pagina. Sao arquivos de codigo, sem dado do usuario;
- valida o header Host contra loopback, o que bloqueia DNS rebinding;
- nao emite cabecalhos CORS, entao pagina de outra origem nao le a resposta;
- comandos vem de whitelist com argumentos validados (ver tarefas.py).
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from . import api
from .tarefas import COMANDOS, Executor

ESTATICO = Path(__file__).parent / "estatico"
HOSTS_PERMITIDOS = ("127.0.0.1", "localhost", "[::1]")


def _ip_na_rede() -> str | None:
    """IP da maquina na rede local, sem depender de resolucao de nome.

    Abre um socket UDP para um endereco externo -- nao envia nada, so faz o
    sistema escolher a interface de saida e revelar o IP dela.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tomada:
        try:
            tomada.connect(("8.8.8.8", 80))
            return tomada.getsockname()[0]
        except OSError:
            return None
TAMANHO_MAXIMO_CORPO = 64 * 1024

ROTAS_GET = {
    "/api/estado": lambda cfg, p: api.estado(cfg),
    "/api/top": api.top,
    "/api/alertas": api.alertas,
    "/api/ganhos": api.ganhos,
    "/api/historico": api.historico,
    "/api/categorias": api.categorias,
    "/api/vitrine": api.vitrine,
    "/api/saude": api.saude,
    "/api/compartilhados": api.compartilhados,
    "/api/categorias": api.lista_categorias,
}


class Painel(BaseHTTPRequestHandler):
    server_version = "flow02"
    sys_version = ""
    cfg: Config
    token: str
    executor: Executor

    def log_message(self, formato, *args):
        pass

    hosts_permitidos: tuple[str, ...] = HOSTS_PERMITIDOS

    def _host_valido(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in self.hosts_permitidos

    def _token_valido(self, parametros: dict) -> bool:
        enviado = self.headers.get("X-Token") or parametros.get("t", "")
        return secrets.compare_digest(str(enviado), self.token)

    def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _json(self, dados, status: int = HTTPStatus.OK) -> None:
        corpo = json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8")
        self._responder(status, corpo, "application/json; charset=utf-8")

    def _drenar_corpo(self) -> None:
        """Le e descarta o corpo antes de responder um erro.

        Responder sem consumir o corpo faz o cliente levar reset de conexao no
        meio do envio, e ele ve um erro de socket em vez do status HTTP.

        So drena uma vez: ler de novo um corpo ja consumido bloqueia esperando
        bytes que nao virao, e a requisicao morre por timeout.
        """
        if self.command not in ("POST", "PUT", "PATCH"):
            return
        if getattr(self, "_corpo_consumido", False):
            return
        self._corpo_consumido = True
        try:
            restante = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        while restante > 0:
            pedaco = self.rfile.read(min(restante, 65536))
            if not pedaco:
                break
            restante -= len(pedaco)

    def _erro(self, status: int, mensagem: str) -> None:
        self._drenar_corpo()
        self._json({"erro": mensagem}, status)

    def _estatico(self, nome: str) -> None:
        caminho = (ESTATICO / nome).resolve()
        if not caminho.is_file() or ESTATICO.resolve() not in caminho.parents:
            self._erro(HTTPStatus.NOT_FOUND, "nao encontrado")
            return
        tipo = mimetypes.guess_type(caminho.name)[0] or "application/octet-stream"
        self._responder(HTTPStatus.OK, caminho.read_bytes(), f"{tipo}; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_valido():
            self._erro(HTTPStatus.FORBIDDEN, "host nao permitido")
            return
        url = urlparse(self.path)
        parametros = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path.startswith("/estatico/"):
            self._estatico(url.path.removeprefix("/estatico/"))
            return

        if not self._token_valido(parametros):
            self._erro(HTTPStatus.UNAUTHORIZED,
                       "token invalido -- abra a URL impressa no terminal")
            return

        if url.path in ("/", "/index.html"):
            self._estatico("index.html")
            return
        if url.path == "/api/tarefa":
            tarefa = self.executor.atual()
            desde = int(parametros.get("desde") or 0)
            self._json({"tarefa": tarefa.para_dict(desde) if tarefa else None,
                        "comandos": {c: d["rotulo"] for c, d in COMANDOS.items()}})
            return

        rota = ROTAS_GET.get(url.path)
        if rota is None:
            self._erro(HTTPStatus.NOT_FOUND, "rota desconhecida")
            return
        try:
            self._json(rota(self.cfg, parametros))
        except Exception as exc:
            self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        if not self._host_valido():
            self._erro(HTTPStatus.FORBIDDEN, "host nao permitido")
            return
        url = urlparse(self.path)
        parametros = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not self._token_valido(parametros):
            self._erro(HTTPStatus.UNAUTHORIZED, "token invalido")
            return

        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > TAMANHO_MAXIMO_CORPO:
            # Nao drena: o objetivo aqui e justamente nao ler corpo gigante.
            self._json({"erro": "corpo grande demais"},
                       HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        bruto = self.rfile.read(tamanho)
        self._corpo_consumido = True
        try:
            corpo = json.loads(bruto or b"{}")
        except json.JSONDecodeError:
            self._erro(HTTPStatus.BAD_REQUEST, "json invalido")
            return

        if url.path == "/api/tarefa":
            self._iniciar_tarefa(corpo)
            return
        if url.path == "/api/tarefa/cancelar":
            self._json({"cancelada": self.executor.cancelar()})
            return
        if url.path == "/api/link":
            self._gerar_link(corpo)
            return
        if url.path == "/api/vitrine":
            try:
                self._json(api.alternar_vitrine(self.cfg, corpo))
            except ValueError as exc:
                self._erro(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if url.path == "/api/alvo":
            try:
                self._json(api.definir_alvo(self.cfg, corpo))
            except ValueError as exc:
                self._erro(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._erro(HTTPStatus.NOT_FOUND, "rota desconhecida")

    def _gerar_link(self, corpo: dict) -> None:
        try:
            resultado = api.gerar_link(
                self.cfg, str(corpo.get("url", "")), corpo.get("sub_ids") or []
            )
        except ValueError as exc:
            self._erro(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._erro(HTTPStatus.BAD_GATEWAY, str(exc))
        else:
            self._json(resultado)

    def _iniciar_tarefa(self, corpo: dict) -> None:
        try:
            tarefa = self.executor.iniciar(
                str(corpo.get("comando", "")), corpo.get("opcoes") or {}
            )
        except ValueError as exc:
            self._erro(HTTPStatus.BAD_REQUEST, str(exc))
        except RuntimeError as exc:
            self._erro(HTTPStatus.CONFLICT, str(exc))
        else:
            self._json({"tarefa": tarefa.para_dict()}, HTTPStatus.ACCEPTED)


def construir(
    cfg: Config, porta: int = 8765, na_rede: bool = False
) -> tuple[ThreadingHTTPServer, str]:
    """Por padrao escuta so em loopback.

    `na_rede=True` e opt-in consciente: o painel executa comandos, entao
    abri-lo para a rede local significa que qualquer maquina no mesmo Wi-Fi
    pode tentar usa-lo. O token continua obrigatorio, mas a superficie deixa
    de ser apenas esta maquina.
    """
    token = secrets.token_urlsafe(24)
    permitidos = HOSTS_PERMITIDOS
    endereco_escuta = "127.0.0.1"
    if na_rede:
        # Liberar a rede so faz sentido se o Host da maquina tambem passar na
        # checagem -- caso contrario o celular receberia 403 em tudo.
        ip = _ip_na_rede()
        permitidos = HOSTS_PERMITIDOS + ((ip,) if ip else ())
        endereco_escuta = "0.0.0.0"  # noqa: S104 -- opt-in explicito do usuario

    manipulador = type("PainelConfigurado", (Painel,), {
        "cfg": cfg, "token": token, "executor": Executor(),
        "hosts_permitidos": permitidos,
    })
    servidor = ThreadingHTTPServer((endereco_escuta, porta), manipulador)
    servidor.daemon_threads = True
    return servidor, token


def iniciar(cfg: Config, porta: int = 8765, abrir: bool = True,
            na_rede: bool = False) -> int:
    try:
        servidor, token = construir(cfg, porta, na_rede)
    except OSError as exc:
        print(f"nao foi possivel abrir a porta {porta}: {exc}")
        return 1

    endereco = f"http://127.0.0.1:{servidor.server_port}/?t={token}"
    print(f"painel em {endereco}", flush=True)
    if na_rede:
        ip = _ip_na_rede()
        print(f"no celular : http://{ip}:{servidor.server_port}/?t={token}",
              flush=True)
        print("ATENCAO: aberto para a rede local. Qualquer aparelho no mesmo "
              "Wi-Fi alcanca este painel, e ele executa comandos.", flush=True)
        print("         so use em rede de casa, e feche quando terminar.",
              flush=True)
    print("o token muda a cada execucao. Ctrl+C para parar.", flush=True)
    if abrir:
        threading.Timer(0.5, webbrowser.open, args=(endereco,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando")
    finally:
        servidor.shutdown()
        servidor.server_close()
    return 0
