"""WhatsApp: las fotos y PDF llegan al motor como comprobante del anticipo."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app import config, engine, reloj, store, whatsapp_webhook
from app.whatsapp_webhook import AVISO_SIN_TEXTO

from tests.telefonos import TEL_A, TEL_B

pytestmark = pytest.mark.usefixtures("bd")

CLABE = "002010077777777771"


def _payload(tipo: str, telefono: str = TEL_A, msg_id: str = "wamid.m1", media_id: str = "media-9",
             mime: str | None = None) -> dict:
    medio = {"id": media_id, **({"mime_type": mime} if mime else {})}
    return {"entry": [{"changes": [{"value": {"messages": [
        {"id": msg_id, "from": telefono, "timestamp": "1793000000", "type": tipo, tipo: medio}]}}]}]}


class _Enviador:
    def __init__(self):
        self.salida: list[tuple[str, dict]] = []

    async def __call__(self, wa_id: str, payload: dict) -> bool:
        self.salida.append((wa_id, payload))
        return True

    def texto(self) -> str:
        return " ".join(str(p) for _, p in self.salida)


@pytest.fixture()
def anticipo_activo(monkeypatch) -> dict:
    datos = engine.cargar_datos()
    datos["anticipo"] = {"activo": True}
    monkeypatch.setattr(whatsapp_webhook, "_DATOS", datos)
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", None)
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "Banco de Prueba")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", CLABE)
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "Nombre Apellido")
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 1000)
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 1000)
    monkeypatch.setattr(config, "WA_MAX_GLOBAL_POR_DIA", 1000)
    avisos: list = []
    monkeypatch.setattr(engine.avisos, "notificar_anticipo", lambda **k: avisos.append(("anticipo", k)))
    monkeypatch.setattr(engine.avisos, "notificar_handoff", lambda sid, tel, msg: avisos.append(("handoff", msg)))
    datos["_avisos"] = avisos
    return datos


def _apartado(telefono: str = TEL_A) -> dict:
    ahora = reloj.ahora()
    return store.crear((ahora + timedelta(days=3)).strftime("%Y-%m-%dT10:00"), "gelish", "Ana", telefono,
                       pendiente_hasta=(ahora + timedelta(minutes=60)).isoformat(timespec="seconds"),
                       anticipo=200, aceptado_en=ahora.isoformat(timespec="seconds"))


def _procesar(payload: dict) -> _Enviador:
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(payload, enviador))
    return enviador


# ---------- extracción ----------
def test_una_imagen_trae_su_id_de_media():
    m = whatsapp_webhook.extraer_mensajes(_payload("image", mime="image/jpeg"))[0]

    assert (m["tipo"], m["texto"], m["media_id"], m["mime"]) == ("image", "", "media-9", "image/jpeg")


def test_un_documento_trae_su_tipo_mime():
    m = whatsapp_webhook.extraer_mensajes(_payload("document", mime="application/pdf"))[0]

    assert (m["tipo"], m["media_id"], m["mime"]) == ("document", "media-9", "application/pdf")


def test_un_mensaje_de_texto_no_cambia():
    p = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.t", "from": TEL_A, "type": "text", "text": {"body": "hola"}}]}}]}]}

    assert whatsapp_webhook.extraer_mensajes(p) == [
        {"id": "wamid.t", "wa_id": TEL_A, "texto": "hola", "tipo": "text"}]


# ---------- procesamiento ----------
def test_con_el_anticipo_apagado_una_imagen_sigue_recibiendo_el_aviso_de_solo_texto(monkeypatch):
    datos = engine.cargar_datos()
    datos["anticipo"] = {"activo": False}
    monkeypatch.setattr(whatsapp_webhook, "_DATOS", datos)
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", None)

    salida = _procesar(_payload("image", mime="image/jpeg"))

    assert AVISO_SIN_TEXTO in salida.texto()


def test_la_imagen_de_una_clienta_con_apartado_confirma_su_cita(anticipo_activo):
    cita = _apartado()

    salida = _procesar(_payload("image", mime="image/jpeg"))

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert "Recibí tu comprobante" in salida.texto()
    assert salida.salida[0][0] == TEL_A                       # la respuesta va a su teléfono
    assert [a[0] for a in anticipo_activo["_avisos"]] == ["anticipo"]


def test_un_pdf_confirma_la_cita(anticipo_activo):
    cita = _apartado()

    _procesar(_payload("document", mime="application/pdf"))

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_un_documento_que_no_es_pdf_ni_imagen_no_confirma(anticipo_activo):
    cita = _apartado()

    salida = _procesar(_payload("document", mime="application/zip"))

    assert store.obtener(cita["folio"])["estado"] == "pendiente"
    assert "foto, captura o PDF" in salida.texto()


def test_el_comprobante_de_otra_persona_no_confirma_la_cita_ajena(anticipo_activo):
    cita = _apartado(TEL_A)

    salida = _procesar(_payload("image", telefono=TEL_B, mime="image/jpeg"))

    assert store.obtener(cita["folio"])["estado"] == "pendiente"
    assert "no tengo una cita pendiente" in salida.texto().lower()
    assert [a[0] for a in anticipo_activo["_avisos"]] == ["handoff"]


def test_el_reintento_de_meta_no_confirma_ni_avisa_dos_veces(anticipo_activo):
    _apartado()
    p = _payload("image", mime="image/jpeg")

    _procesar(p)
    _procesar(p)

    assert [a[0] for a in anticipo_activo["_avisos"]] == ["anticipo"]
