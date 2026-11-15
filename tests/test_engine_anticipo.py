"""Motor: agendar con anticipo por WhatsApp (aviso, cuenta, comprobante) y sus políticas."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app import anticipo, config, engine, reloj, store

from tests.telefonos import TEL_A, TEL_B

pytestmark = pytest.mark.usefixtures("bd")

CLABE = "002010077777777771"


@pytest.fixture()
def datos(monkeypatch) -> dict:
    d = engine.cargar_datos()
    d["anticipo"] = {"activo": True}
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", None)
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "Banco de Prueba")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", CLABE)
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "Nombre Apellido")
    return d


@pytest.fixture()
def dueno(monkeypatch) -> dict:
    """Lo que se le avisaría al dueño (Telegram simulado)."""
    reg = {"anticipo": [], "cancelacion": [], "handoff": []}
    monkeypatch.setattr(engine.avisos, "notificar_anticipo", lambda **k: reg["anticipo"].append(k))
    monkeypatch.setattr(engine.avisos, "notificar_cancelacion_anticipo", lambda **k: reg["cancelacion"].append(k))
    monkeypatch.setattr(engine.avisos, "notificar_handoff", lambda sid, tel, msg: reg["handoff"].append(msg))
    return reg


def _decir(texto, datos, tel=TEL_A, media=None, canal="wa"):
    respuestas, _ = asyncio.run(engine.manejar(
        f"wa:{tel}" if canal == "wa" else "web-1", texto, datos,
        canal=canal, telefono=tel if canal == "wa" else "", media=media))
    return respuestas


def _textos(respuestas) -> str:
    return " ".join(r["texto"] for r in respuestas)


def _slot_lejano(datos, horas=49) -> str:
    sv = datos["servicios"][0]["id"]
    limite = reloj.ahora() + timedelta(hours=horas)
    libres = [x for x in engine.slots_disponibles(datos, sv) if datetime.fromisoformat(x) >= limite]
    assert libres, "debería haber huecos a más de 2 días en la semana"
    return libres[0]


def _hasta_el_aviso(datos, tel=TEL_A, slot=None):
    sv = datos["servicios"][0]["id"]
    slot = slot or _slot_lejano(datos)
    _decir("agendar", datos, tel)
    _decir(sv, datos, tel)
    _decir(slot, datos, tel)
    return _decir("Cliente de Prueba", datos, tel), slot


def _apartar(datos, tel=TEL_A, slot=None):
    respuestas, slot = _hasta_el_aviso(datos, tel, slot)
    pago = _decir("acepto_anticipo", datos, tel)
    return store.listar()[-1], pago, slot


IMG = {"tipo": "image", "id": "media-1", "mime": "image/jpeg"}
PDF = {"tipo": "document", "id": "media-2", "mime": "application/pdf"}


def _vencer(folio: str) -> None:
    with store._con() as con:
        con.execute("UPDATE citas SET vence = ? WHERE folio = ?",
                    ((reloj.ahora() - timedelta(minutes=5)).isoformat(timespec="seconds"), folio))


def _confirmada(datos, tel=TEL_A, slot=None) -> dict:
    cita, _, _ = _apartar(datos, tel, slot)
    _decir("", datos, tel, media=IMG)
    return store.obtener(cita["folio"])


# ---------- aviso y aceptación ----------
def test_antes_de_dar_la_cuenta_pide_aceptar_las_condiciones(datos, dueno):
    respuestas, _ = _hasta_el_aviso(datos)

    texto = _textos(respuestas)
    assert "$200" in texto and "no se devuelve" in texto
    assert [o["id"] for o in respuestas[-1]["opciones"]] == ["acepto_anticipo", "rechazo_anticipo"]
    assert store.listar() == []                                  # todavía no se aparta nada


def test_al_aceptar_se_aparta_el_horario_y_se_da_la_cuenta(datos, dueno):
    cita, pago, slot = _apartar(datos)

    assert cita["estado"] == "pendiente" and cita["slot"] == slot
    assert cita["anticipo_monto"] == 200 and cita["aceptado_en"]
    esperado = anticipo.apartado_minutos(anticipo.cfg(datos), slot, reloj.ahora())
    assert datetime.fromisoformat(cita["vence"]) - datetime.fromisoformat(cita["aceptado_en"]) == timedelta(minutes=esperado)
    texto = _textos(pago)
    for dato in ("Banco de Prueba", CLABE, "Nombre Apellido", cita["folio"], f"{esperado} min"):
        assert dato in texto
    assert slot not in engine.slots_disponibles(datos, datos["servicios"][0]["id"])   # apartado: nadie más lo ve


def test_si_la_clienta_rechaza_no_se_aparta_nada(datos, dueno):
    _hasta_el_aviso(datos)

    respuestas = _decir("rechazo_anticipo", datos)

    assert store.listar() == []
    assert "Sin problema" in _textos(respuestas)


def _otro_slot(datos, distinto_de: str) -> str:
    sv = datos["servicios"][0]["id"]
    return [x for x in engine.slots_disponibles(datos, sv)
            if x != distinto_de and datetime.fromisoformat(x) > reloj.ahora() + timedelta(hours=50)][0]


def test_pedir_agendar_de_nuevo_libera_el_apartado_anterior(datos, dueno):
    primera, _, slot = _apartar(datos)
    segunda, _, _ = _apartar(datos, slot=_otro_slot(datos, slot))

    assert store.obtener(primera["folio"])["estado"] == "cancelada"
    assert store.obtener(segunda["folio"])["estado"] == "pendiente"


def test_aunque_la_sesion_se_haya_reiniciado_solo_queda_un_apartado(datos, dueno):
    primera, _, slot = _apartar(datos)
    engine.SESIONES.clear()                                       # la sesión caducó, el apartado sigue vigente
    segunda, _, _ = _apartar(datos, slot=_otro_slot(datos, slot))

    assert store.obtener(primera["folio"])["estado"] == "cancelada"
    assert store.obtener(segunda["folio"])["estado"] == "pendiente"


def test_el_menu_libera_el_apartado(datos, dueno):
    cita, _, slot = _apartar(datos)

    _decir("menu", datos)

    assert store.obtener(cita["folio"])["estado"] == "cancelada"
    assert slot in engine.slots_disponibles(datos, datos["servicios"][0]["id"])


def test_hablar_mientras_espera_el_comprobante_recuerda_que_falta(datos, dueno):
    _apartar(datos)

    texto = _textos(_decir("ya pagué", datos))

    assert "comprobante" in texto and "cancelar" in texto


# ---------- comprobante ----------
def test_el_comprobante_confirma_la_cita_y_avisa_al_dueno(datos, dueno):
    cita, _, _ = _apartar(datos)

    respuestas = _decir("", datos, media=IMG)

    guardada = store.obtener(cita["folio"])
    assert guardada["estado"] == "confirmada"
    assert (guardada["comprobante_tipo"], guardada["comprobante_media_id"]) == ("image", "media-1")
    assert "Recibí tu comprobante" in _textos(respuestas) and "verificará" in _textos(respuestas)
    assert len(dueno["anticipo"]) == 1
    assert dueno["anticipo"][0]["cita"]["folio"] == cita["folio"] and dueno["anticipo"][0]["tardio"] is False


def test_un_pdf_tambien_vale(datos, dueno):
    cita, _, _ = _apartar(datos)

    _decir("", datos, media=PDF)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


@pytest.mark.parametrize("media", [
    {"tipo": "document", "id": "m", "mime": "application/msword"},
    {"tipo": "video", "id": "m", "mime": "video/mp4"},
    {"tipo": "document", "id": "m", "mime": ""},
])
def test_otros_archivos_no_cuentan_como_comprobante(datos, dueno, media):
    cita, _, _ = _apartar(datos)

    texto = _textos(_decir("", datos, media=media))

    assert store.obtener(cita["folio"])["estado"] == "pendiente"
    assert "foto, captura o PDF" in texto


def test_el_comprobante_funciona_aunque_la_sesion_ya_se_haya_reiniciado(datos, dueno):
    cita, _, _ = _apartar(datos)
    engine.SESIONES.clear()                                       # reinicio o TTL de la sesión

    _decir("", datos, media=IMG)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_un_comprobante_sin_apartado_pasa_a_la_manicurista(datos, dueno):
    respuestas = _decir("", datos, media=IMG)

    assert store.listar() == []
    assert "no tengo una cita pendiente" in _textos(respuestas).lower()
    assert len(dueno["handoff"]) == 1


def test_un_comprobante_tardio_confirma_si_el_horario_sigue_libre(datos, dueno):
    cita, _, _ = _apartar(datos)
    _vencer(cita["folio"])

    _decir("", datos, media=IMG)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert dueno["anticipo"][0]["tardio"] is True


def test_un_comprobante_tardio_con_el_horario_ocupado_se_resuelve_a_mano(datos, dueno):
    cita, _, slot = _apartar(datos)
    _vencer(cita["folio"])
    store.crear(slot, datos["servicios"][0]["id"], "Otra clienta", TEL_B)        # alguien más lo tomó

    respuestas = _decir("", datos, media=IMG)

    assert store.obtener(cita["folio"])["estado"] != "confirmada"
    assert [c["estado"] for c in store.listar() if c["slot"] == slot].count("confirmada") == 1
    assert "ya no está disponible" in _textos(respuestas)
    assert len(dueno["handoff"]) == 1 and cita["folio"] in dueno["handoff"][0]


def test_con_resumen_diario_solo_lo_urgente_se_avisa_en_el_momento(datos, dueno):
    datos["anticipo"]["resumen_diario"] = True
    _confirmada(datos)                                           # a más de 24 h
    assert dueno["anticipo"] == []

    sv = datos["servicios"][0]["id"]
    cercano = [x for x in engine.slots_disponibles(datos, sv)
               if reloj.ahora() + timedelta(hours=2) <= datetime.fromisoformat(x) < reloj.ahora() + timedelta(hours=23)]
    if cercano:                                                  # depende de la hora a la que corra la prueba
        _confirmada(datos, tel=TEL_B, slot=cercano[0])
        assert len(dueno["anticipo"]) == 1 and dueno["anticipo"][0]["urgente"] is True


# ---------- el pago mal configurado no se queda a medias ----------
def test_si_faltan_los_datos_de_pago_no_se_aparta_y_se_pasa_a_la_manicurista(datos, dueno, monkeypatch):
    monkeypatch.setattr(config, "ANTICIPO_CLABE", "")
    _hasta_el_aviso(datos)

    respuestas = _decir("acepto_anticipo", datos)

    assert store.listar() == []
    assert "manicurista" in _textos(respuestas)
    assert len(dueno["handoff"]) == 1


# ---------- cancelar ----------
def test_cancelar_con_anticipo_avisa_la_consecuencia_y_pide_confirmar(datos, dueno):
    cita = _confirmada(datos)

    respuestas = _decir(f"cancelar {cita['folio']}", datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert "pierdes" in _textos(respuestas).lower() or "no se devuelve" in _textos(respuestas)
    assert [o["id"] for o in respuestas[-1]["opciones"]][0] == "cancelar_si"


def test_confirmada_la_cancelacion_se_libera_el_horario_y_se_avisa(datos, dueno):
    cita = _confirmada(datos)
    _decir(f"cancelar {cita['folio']}", datos)

    respuestas = _decir("cancelar_si", datos)

    assert store.obtener(cita["folio"])["estado"] == "cancelada"
    assert "no se devuelve" in _textos(respuestas)
    assert len(dueno["cancelacion"]) == 1 and dueno["cancelacion"][0]["devuelve"] is False


def test_conservar_la_cita_no_cancela_nada(datos, dueno):
    cita = _confirmada(datos)
    _decir(f"cancelar {cita['folio']}", datos)

    _decir("cancelar_no", datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert dueno["cancelacion"] == []


def test_con_reembolso_por_aviso_se_devuelve_si_hay_tiempo(datos, dueno):
    datos["anticipo"]["cancelar"] = {"reembolso": "hasta_horas", "horas": 24}
    cita = _confirmada(datos)
    _decir(f"cancelar {cita['folio']}", datos)

    respuestas = _decir("cancelar_si", datos)

    assert "devuelve" in _textos(respuestas) and "no se devuelve" not in _textos(respuestas)
    assert dueno["cancelacion"][0]["devuelve"] is True


def test_cancelar_un_apartado_sin_pagar_no_cuesta_nada(datos, dueno):
    cita, _, _ = _apartar(datos)

    respuestas = _decir(f"cancelar {cita['folio']}", datos)

    assert store.obtener(cita["folio"])["estado"] == "cancelada"
    assert "pierdes" not in _textos(respuestas).lower()
    assert dueno["cancelacion"] == []


def test_otra_clienta_no_puede_cancelar_con_el_folio(datos, dueno):
    cita = _confirmada(datos)

    _decir(f"cancelar {cita['folio']}", datos, tel=TEL_B)
    _decir("cancelar_si", datos, tel=TEL_B)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


# ---------- mover ----------
def test_mover_una_vez_con_tiempo_conserva_el_anticipo(datos, dueno):
    cita = _confirmada(datos)
    sv = datos["servicios"][0]["id"]
    destino = [x for x in engine.slots_disponibles(datos, sv, excluir_folio=cita["folio"])
               if x != cita["slot"] and datetime.fromisoformat(x) >= reloj.ahora() + timedelta(hours=60)][0]
    _decir(f"mover {cita['folio']}", datos)

    respuestas = _decir(destino, datos)

    movida = store.obtener(cita["folio"])
    assert (movida["slot"], movida["reagendadas"], movida["estado"]) == (destino, 1, "confirmada")
    assert "Movida" in _textos(respuestas)


def test_la_segunda_vez_ya_no_se_puede_mover(datos, dueno):
    cita = _confirmada(datos)
    with store._con() as con:
        con.execute("UPDATE citas SET reagendadas = 1 WHERE folio = ?", (cita["folio"],))

    respuestas = _decir(f"mover {cita['folio']}", datos)

    assert "ya se movió" in _textos(respuestas)
    assert "cancelar" in [o["id"] for o in respuestas[-1]["opciones"]]


def test_con_menos_de_24_horas_no_se_puede_mover_sin_perder_el_anticipo(datos, dueno):
    cita = _confirmada(datos)
    cerca = (reloj.ahora() + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M")
    with store._con() as con:
        con.execute("UPDATE citas SET slot = ? WHERE folio = ?", (cerca, cita["folio"]))

    respuestas = _decir(f"mover {cita['folio']}", datos)

    assert "24 h" in _textos(respuestas)
    assert store.obtener(cita["folio"])["slot"] == cerca


def test_si_el_negocio_no_permite_mover_se_dice(datos, dueno):
    datos["anticipo"]["reagendar"] = {"permitido": False}
    cita = _confirmada(datos)

    respuestas = _decir(f"mover {cita['folio']}", datos)

    assert "no se puede mover" in _textos(respuestas)


# ---------- canales y compatibilidad ----------
def test_en_el_chat_web_no_se_agenda_con_anticipo(datos, dueno):
    sv = datos["servicios"][0]["id"]
    _decir("agendar", datos, canal="web")
    _decir(sv, datos, canal="web")
    _decir(_slot_lejano(datos), datos, canal="web")

    respuestas = _decir("Cliente Web", datos, canal="web")

    assert store.listar() == []
    assert "WhatsApp" in _textos(respuestas)


def test_con_el_anticipo_apagado_agendar_funciona_como_siempre(datos, dueno):
    datos["anticipo"] = {"activo": False}

    _hasta_el_aviso(datos)

    cita = store.listar()[-1]
    assert (cita["estado"], cita["anticipo_monto"]) == ("confirmada", 0)


def test_un_servicio_mas_barato_que_el_anticipo_cobra_solo_su_precio(datos, dueno):
    barato = min(datos["servicios"], key=lambda x: x["precio"])
    assert barato["precio"] < 200, "los datos de ejemplo traen un servicio de menos de $200"
    slot = [x for x in engine.slots_disponibles(datos, barato["id"])
            if datetime.fromisoformat(x) >= reloj.ahora() + timedelta(hours=49)][0]
    _decir("agendar", datos)
    _decir(barato["id"], datos)
    _decir(slot, datos)
    aviso = _decir("Cliente de Prueba", datos)

    pago = _decir("acepto_anticipo", datos)

    assert f"${barato['precio']}" in _textos(aviso) and "cubre el total" in _textos(aviso).lower()
    assert store.listar()[-1]["anticipo_monto"] == barato["precio"]
    assert f"Transfiere ${barato['precio']}" in _textos(pago)
