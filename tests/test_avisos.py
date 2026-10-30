from __future__ import annotations

import logging

import httpx

from app import avisos, config

from tests.telefonos import TEL_A


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        pass


def test_el_log_enmascara_el_telefono(caplog):
    with caplog.at_level(logging.WARNING):
        avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")

    assert TEL_A not in caplog.text
    assert "0011" in caplog.text


def test_telegram_lleva_el_telefono_completo_y_el_enlace(monkeypatch):
    enviados = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(avisos.httpx, "post",
                        lambda url, json, timeout: enviados.append(json) or _Resp())

    avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")

    assert TEL_A in enviados[0]["text"]
    assert f"https://wa.me/{TEL_A}" in enviados[0]["text"]


def test_telegram_rechazado_no_tumba_ni_filtra_el_token(monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")

    def rechaza(url, json, timeout):
        req = httpx.Request("POST", url)
        raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))

    monkeypatch.setattr(avisos.httpx, "post", rechaza)

    with caplog.at_level(logging.ERROR):
        avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")      # no debe lanzar

    assert "T0KEN" not in caplog.text
