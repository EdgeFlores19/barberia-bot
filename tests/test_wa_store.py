"""Tests de las tablas nuevas del adaptador."""
from __future__ import annotations

from app import store


def test_registrar_evento_acepta_la_primera_vez(bd):
    assert store.registrar_evento("wamid.A") is True


def test_registrar_evento_rechaza_el_reintento(bd):
    store.registrar_evento("wamid.A")
    assert store.registrar_evento("wamid.A") is False


def test_handoff_queda_registrado(bd):
    store.registrar_handoff("wa:5215500000001", "quiero hablar con una persona")
    filas = store.handoffs()
    assert len(filas) == 1
    assert filas[0]["sid"] == "wa:5215500000001"
    assert "persona" in filas[0]["mensaje"]
