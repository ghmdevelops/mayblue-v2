"""Execucao de comandos disparados pelo painel.

Regras de seguranca, porque isto e um servidor local que executa processos:

- so comandos de uma whitelist rodam, nunca string arbitraria;
- cada comando declara as flags aceitas, e cada valor e validado;
- os argumentos vao como lista para o subprocess, sem `shell=True`, entao
  nao existe superficie de injecao de shell;
- uma tarefa por vez, porque duas coletas simultaneas competiriam pelo mesmo
  banco e pelo mesmo rate limit da API.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..config import RAIZ
from ..tempo import iso_utc

PLATAFORMAS = ("shopee", "mercadolivre")
TIPOS_NOTIFICACAO = ("top", "alertas")
PADRAO_DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$|^hoje$")
LIMITE_LINHAS = 500

RODANDO = "rodando"
OK = "ok"
ERRO = "erro"
CANCELADA = "cancelada"


def _validar_dia(valor: str) -> list[str]:
    if not PADRAO_DIA.match(valor):
        raise ValueError(f"dia invalido: {valor}")
    return ["--dia", valor]


def _validar_plataforma(valor: str) -> list[str]:
    if valor not in PLATAFORMAS:
        raise ValueError(f"plataforma invalida: {valor}")
    return ["--plataforma", valor]


def _validar_tipo(valor: str) -> list[str]:
    if valor not in TIPOS_NOTIFICACAO:
        raise ValueError(f"tipo invalido: {valor}")
    return ["--tipo", valor]


def _validar_quantidade(valor) -> list[str]:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        raise ValueError(f"quantidade invalida: {valor}")
    if not 1 <= numero <= 5000:
        raise ValueError(f"quantidade fora da faixa 1..5000: {numero}")
    return ["--quantidade", str(numero)]


def _bandeira(nome: str):
    def construir(valor) -> list[str]:
        return [nome] if valor in (True, "true", "1", "sim") else []
    return construir


COMANDOS: dict[str, dict] = {
    "coletar": {
        "rotulo": "Coletar ofertas",
        "opcoes": {
            "dia": _validar_dia,
            "plataforma": _validar_plataforma,
            "quantidade": _validar_quantidade,
            "com_conversoes": _bandeira("--com-conversoes"),
        },
    },
    "calibrar": {"rotulo": "Recalibrar CVR", "opcoes": {}},
    "notificar": {
        "rotulo": "Enviar no Telegram",
        "opcoes": {"tipo": _validar_tipo, "dia": _validar_dia,
                   "plataforma": _validar_plataforma},
    },
    "sincronizar": {
        "rotulo": "Sincronizar Firebase",
        "opcoes": {"dia": _validar_dia, "plataforma": _validar_plataforma},
    },
    "doctor": {"rotulo": "Diagnostico", "opcoes": {"plataforma": _validar_plataforma}},
    "cliques": {
        "rotulo": "Importar cliques",
        "opcoes": {"por_canal": _bandeira("--por-canal")},
    },
    "publicar": {
        "rotulo": "Gerar pagina publica",
        "opcoes": {"netlify": _bandeira("--netlify")},
    },
    "categorias": {"rotulo": "Listar categorias", "opcoes": {}},
    "saude": {"rotulo": "Checagem de saude", "opcoes": {}},
}


def montar_argumentos(comando: str, opcoes: dict | None = None) -> list[str]:
    """Traduz o pedido do front em argv validado. Levanta ValueError se algo
    nao estiver na whitelist."""
    if comando not in COMANDOS:
        raise ValueError(f"comando nao permitido: {comando}")
    permitidas = COMANDOS[comando]["opcoes"]
    argv = [comando]
    for chave, valor in (opcoes or {}).items():
        if chave not in permitidas:
            raise ValueError(f"opcao nao permitida para {comando}: {chave}")
        if valor in (None, "", False):
            continue
        argv.extend(permitidas[chave](valor))
    return argv


@dataclass
class Tarefa:
    comando: str
    argv: list[str]
    iniciado_em: str
    estado: str = RODANDO
    codigo: int | None = None
    finalizado_em: str | None = None
    linhas: list[str] = field(default_factory=list)

    def para_dict(self, desde: int = 0) -> dict:
        return {
            "comando": self.comando,
            "rotulo": COMANDOS.get(self.comando, {}).get("rotulo", self.comando),
            "argv": self.argv,
            "estado": self.estado,
            "codigo": self.codigo,
            "iniciado_em": self.iniciado_em,
            "finalizado_em": self.finalizado_em,
            "linhas": self.linhas[desde:],
            "total_linhas": len(self.linhas),
        }


class Executor:
    """Roda um comando do flow02 por vez, capturando a saida linha a linha."""

    def __init__(self, python: str | None = None, raiz: Path = RAIZ) -> None:
        self._python = python or sys.executable
        self._raiz = raiz
        self._lock = threading.Lock()
        self._tarefa: Tarefa | None = None
        self._processo: subprocess.Popen | None = None

    @property
    def ocupado(self) -> bool:
        with self._lock:
            return self._tarefa is not None and self._tarefa.estado == RODANDO

    def atual(self) -> Tarefa | None:
        with self._lock:
            return self._tarefa

    def iniciar(self, comando: str, opcoes: dict | None = None) -> Tarefa:
        argv = montar_argumentos(comando, opcoes)
        with self._lock:
            if self._tarefa is not None and self._tarefa.estado == RODANDO:
                raise RuntimeError(
                    f"ja existe uma tarefa em andamento: {self._tarefa.comando}"
                )
            tarefa = Tarefa(comando=comando, argv=argv, iniciado_em=iso_utc())
            self._tarefa = tarefa
        threading.Thread(target=self._executar, args=(tarefa,), daemon=True).start()
        return tarefa

    def _ambiente(self) -> dict:
        import os

        ambiente = dict(os.environ)
        src = str(self._raiz / "src")
        ambiente["PYTHONPATH"] = src + os.pathsep + ambiente.get("PYTHONPATH", "")
        ambiente["PYTHONIOENCODING"] = "utf-8"
        ambiente["PYTHONUNBUFFERED"] = "1"
        return ambiente

    def _executar(self, tarefa: Tarefa) -> None:
        try:
            processo = subprocess.Popen(
                [self._python, "-X", "utf8", "-m", "flow02", *tarefa.argv],
                cwd=self._raiz,
                env=self._ambiente(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._encerrar(tarefa, ERRO, -1, f"falha ao iniciar: {exc}")
            return

        with self._lock:
            self._processo = processo

        for linha in processo.stdout or ():
            with self._lock:
                tarefa.linhas.append(linha.rstrip("\n"))
                if len(tarefa.linhas) > LIMITE_LINHAS:
                    del tarefa.linhas[: len(tarefa.linhas) - LIMITE_LINHAS]
        codigo = processo.wait()

        with self._lock:
            self._processo = None
            if tarefa.estado == CANCELADA:
                return
        self._encerrar(tarefa, OK if codigo == 0 else ERRO, codigo)

    def _encerrar(self, tarefa: Tarefa, estado: str, codigo: int,
                  mensagem: str | None = None) -> None:
        with self._lock:
            if mensagem:
                tarefa.linhas.append(mensagem)
            tarefa.estado = estado
            tarefa.codigo = codigo
            tarefa.finalizado_em = iso_utc()

    def cancelar(self) -> bool:
        with self._lock:
            processo, tarefa = self._processo, self._tarefa
            if processo is None or tarefa is None or tarefa.estado != RODANDO:
                return False
            tarefa.estado = CANCELADA
            tarefa.finalizado_em = iso_utc()
            tarefa.linhas.append("-- cancelada pelo usuario --")
        processo.terminate()
        return True
