"""Utilitarios de data/hora.

Usa offset fixo UTC-3 em vez de zoneinfo("America/Sao_Paulo") porque o Windows
nao embarca a base IANA e o pacote tzdata nao pode ser instalado neste ambiente.
O Brasil nao adota horario de verao desde 2019, entao UTC-3 e estavel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

FUSO_BRASIL = timezone(timedelta(hours=-3))


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def agora_brasil() -> datetime:
    return datetime.now(FUSO_BRASIL)


def dia_brasil(momento: datetime | None = None) -> str:
    """Retorna o dia civil brasileiro no formato YYYY-MM-DD."""
    momento = momento or agora_utc()
    return momento.astimezone(FUSO_BRASIL).strftime("%Y-%m-%d")


def iso_utc(momento: datetime | None = None) -> str:
    momento = momento or agora_utc()
    return momento.astimezone(timezone.utc).replace(microsecond=0).isoformat()
