"""Con varias agendas, cada una lleva sus citas, su espejo y sus bloqueos.

Lo que se agenda con Ana no le quita el horario a Brenda, y el evento espejo se crea en
el calendario de la agenda de la cita. Sin agenda (el modo de siempre) todo sigue igual:
un solo calendario, un solo conjunto de horarios ocupados y ni una migración.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from app import calendar_sync, config, engine, reloj, store, whatsapp_formato

ANA = "ana@group.calendar.google.com"
BRENDA = "brenda@group.calendar.google.com"

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return engine.cargar_datos()


@pytest.fixture()
def agenda() -> dict[str, str]:
    """Dos agendas dadas de alta con sus claves cortas (sin hablar con Google)."""
    store.agendas_sincronizar(
        [{"calendar_id": ANA, "nombre": "Ana"}, {"calendar_id": BRENDA, "nombre": "Brenda"}]
    )
    return {
        "ana": store.agenda_de(ANA)["clave"],
        "brenda": store.agenda_de(BRENDA)["clave"],
    }


def _enviar(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


def _primer_slot(datos: dict, servicio_id: str = "manicura") -> str:
    """Primer hueco con margen de sobra (si no, cae en la ventana de cancelación)."""
    limite = reloj.ahora() + timedelta(hours=datos["reglas_agenda"]["cancelacion_libre_hasta_horas"] + 1)
    libres = [s for s in engine.slots_disponibles(datos, servicio_id) if datetime.fromisoformat(s) >= limite]
    assert libres, "debería haber disponibilidad con margen en los próximos 7 días"
    return libres[0]


def test_una_cita_no_le_quita_el_horario_a_la_otra_agenda(datos, agenda):
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])

    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])
    # El modo de siempre (sin agenda) sigue viendo todo lo vendido, como antes.
    assert slot not in engine.slots_disponibles(datos, "manicura")


def test_sin_agenda_la_cita_bloquea_a_todas(datos, agenda):
    """Las citas viejas, sin agenda, no pueden reaparecer libres al encender el modo nuevo."""
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Clienta de siempre")

    assert slot not in engine.slots_disponibles(datos, "manicura")
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])


def test_el_evento_espejo_va_al_calendario_de_la_agenda(datos, agenda, monkeypatch):
    vistos: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        calendar_sync, "crear",
        lambda cita, d, calendar_id=None: vistos.append((cita["folio"], calendar_id)) or "ev-ana",
    )
    # Sin Calendar habilitado el motor ni lo intenta: basta con que la creación pase por aquí.
    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)

    _enviar("s1", "agendar", datos)
    _enviar("s1", "manicura", datos)
    _enviar("s1", "Ana", datos)          # ¿con quién? — el paso nuevo de la Fase 4
    slot = _primer_slot(datos)
    _enviar("s1", engine._etiqueta_slot(slot), datos)
    resp, _ = _enviar("s1", "Edgar Flores", datos)

    folio = resp[0]["texto"].split("Tu folio es ")[1].split(".")[0].strip()
    assert vistos == [(folio, ANA)]
    cita = store.obtener(folio)
    assert cita["agenda_id"] == agenda["ana"]
    assert cita["agenda_nombre"] == "Ana"


def test_sin_agenda_el_espejo_usa_el_calendario_de_siempre(datos, monkeypatch):
    vistos: list[str | None] = []
    monkeypatch.setattr(
        calendar_sync, "crear",
        lambda cita, d, calendar_id=None: vistos.append(calendar_id) or "ev-1",
    )
    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)

    _enviar("s1", "agendar", datos)
    _enviar("s1", "manicura", datos)
    slot = _primer_slot(datos)
    _enviar("s1", engine._etiqueta_slot(slot), datos)
    _enviar("s1", "Edgar Flores", datos)

    assert vistos == [None], "sin agenda se usa CALENDAR_ID, como siempre"


def test_los_bloqueos_de_calendar_se_leen_por_agenda(datos, agenda, monkeypatch):
    pedidos: list[str | None] = []
    monkeypatch.setattr(
        calendar_sync, "bloqueos", lambda calendar_id=None: pedidos.append(calendar_id) or []
    )

    engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])
    assert pedidos == [BRENDA]

    engine.slots_disponibles(datos, "manicura")
    assert pedidos[-1] is None


class _Respuesta:
    status_code = 200

    def __init__(self, cuerpo: dict):
        self._cuerpo = cuerpo

    def json(self) -> dict:
        return self._cuerpo

    def raise_for_status(self) -> None:
        return None


class _GoogleFalso:
    """events.list de mentira: un evento manual por calendario, o un fallo de red."""

    def __init__(self, eventos: dict[str, list[dict]], fallar: tuple[str, ...] = ()):
        self.eventos = eventos
        self.fallar = set(fallar)
        self.llamadas: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        cal = ANA if ANA in url else BRENDA
        self.llamadas.append(cal)
        if cal in self.fallar:
            raise httpx.HTTPError("sin red")
        return _Respuesta({"items": list(self.eventos.get(cal, []))})


def _evento_manual(slot: str) -> dict:
    """Un evento escrito a mano en el calendario de esa agenda (no lo creó el bot)."""
    ini = datetime.fromisoformat(slot)
    return {
        "description": "cita en el consultorio",
        "start": {"dateTime": ini.isoformat(timespec="minutes")},
        "end": {"dateTime": (ini + timedelta(hours=1)).isoformat(timespec="minutes")},
    }


@pytest.fixture()
def bloqueos(monkeypatch):
    """Deja el bloqueo por calendario encendido y con Google de mentira."""
    monkeypatch.setattr(config, "CALENDAR_ID", ANA)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_TTL_S", 60.0)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_REINTENTO_S", 120.0)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "tok")
    monkeypatch.setattr(calendar_sync, "_mono", lambda: reloj.ahora())
    monkeypatch.setattr(calendar_sync.avisos, "notificar_alerta", lambda texto: None)
    calendar_sync.reiniciar_bloqueos()
    yield
    calendar_sync.reiniciar_bloqueos()


def test_un_evento_manual_solo_bloquea_el_horario_de_su_agenda(datos, agenda, bloqueos, monkeypatch):
    slot_ana = _primer_slot(datos)
    google = _GoogleFalso({ANA: [_evento_manual(slot_ana)]})
    monkeypatch.setattr(calendar_sync.httpx, "get", google.get)

    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=ANA) is True
    calendar_sync.reiniciar_bloqueos(BRENDA)  # Brenda sigue sin leer nada de Google

    assert slot_ana not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot_ana in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])


def test_si_el_calendario_de_ana_falla_brenda_sigue_disponible(datos, agenda, bloqueos, monkeypatch):
    slot = _primer_slot(datos)
    google = _GoogleFalso({BRENDA: [_evento_manual(slot)]}, fallar=(ANA,))
    monkeypatch.setattr(calendar_sync.httpx, "get", google.get)

    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=ANA) is False
    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=BRENDA) is True

    # Ana se queda sin bloqueos (cae a lo que hay en SQLite, que está vacío): no se inventa
    # un horario ocupado ni se cae el bot.
    assert slot in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    # Brenda sí ve su evento manual y no ofrece ese hueco.
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])


# ---------- M9 · "Cualquiera disponible": asignar al apartar y al confirmar ----------
def _agendar_con(sid: str, datos: dict, slot: str, nombre: str = "Clienta Prueba",
                 tel: str = "", canal: str = "web", con: str = "Cualquiera"):
    """Agenda por texto como una clienta, contestando el paso de "¿con quién?"."""
    engine.SESIONES.pop(sid, None)
    asyncio.run(engine.manejar(sid, "agendar", datos, canal=canal, telefono=tel))
    asyncio.run(engine.manejar(sid, "manicura", datos, canal=canal, telefono=tel))
    asyncio.run(engine.manejar(sid, con, datos, canal=canal, telefono=tel))
    asyncio.run(engine.manejar(sid, slot, datos, canal=canal, telefono=tel))
    return asyncio.run(engine.manejar(sid, nombre, datos, canal=canal, telefono=tel))


def test_cualquiera_ofrece_lo_que_queda_en_cualquier_agenda(datos, agenda):
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])

    # Ana ya no puede, Brenda sí: con "cualquiera" el horario sigue en la lista.
    assert slot in engine.slots_disponibles(datos, "manicura", agenda_id=engine.CUALQUIERA)
    store.crear(slot, "manicura", "Clienta de Brenda", agenda_id=agenda["brenda"])
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=engine.CUALQUIERA)


def test_cualquiera_asigna_agenda_calendario_y_lo_dice(datos, agenda, monkeypatch):
    vistos: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        calendar_sync, "crear",
        lambda cita, d, calendar_id=None: vistos.append((cita["folio"], calendar_id)) or "ev-1",
    )
    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)

    slot = _primer_slot(datos)
    resp, _ = _agendar_con("c1", datos, slot)

    cita = store.listar()[-1]
    assert cita["agenda_id"] in {agenda["ana"], agenda["brenda"]}
    assert cita["agenda_nombre"] == ("Ana" if cita["agenda_id"] == agenda["ana"] else "Brenda")
    assert vistos == [(cita["folio"], ANA if cita["agenda_id"] == agenda["ana"] else BRENDA)]
    assert f" con {cita['agenda_nombre']} " in resp[0]["texto"]


def test_dos_clientas_por_el_mismo_horario_terminan_en_agendas_distintas(datos, agenda):
    slot = _primer_slot(datos)
    _agendar_con("c1", datos, slot, "Primera")
    resp, _ = _agendar_con("c2", datos, slot, "Segunda")

    citas = [c for c in store.listar() if c["slot"] == slot and c["estado"] == "confirmada"]
    assert len(citas) == 2, "la segunda prueba la siguiente candidata, no se rinde"
    assert {c["agenda_id"] for c in citas} == {agenda["ana"], agenda["brenda"]}
    assert "con " in resp[0]["texto"]


def test_sin_candidatas_dice_que_se_ocupo(datos, agenda):
    """El horario se llenó entre mostrarlo y confirmar: el bot lo dice, no inventa agenda."""
    slot = _primer_slot(datos)
    engine.SESIONES.pop("c1", None)
    asyncio.run(engine.manejar("c1", "agendar", datos))
    asyncio.run(engine.manejar("c1", "manicura", datos))
    s = engine.SESIONES["c1"]
    s.agenda_id = engine.CUALQUIERA
    s.slot, s.paso = slot, "agendar_nombre"

    store.crear(slot, "manicura", "Ana ocupada", agenda_id=agenda["ana"])
    store.crear(slot, "manicura", "Brenda ocupada", agenda_id=agenda["brenda"])

    resp, _ = _enviar("c1", "Tarde", datos)
    texto = " ".join(r["texto"] for r in resp)
    assert "se acaba de ocupar" in texto
    assert not [c for c in store.listar() if c["nombre"] == "Tarde"]


def test_cualquiera_con_anticipo_aparta_en_la_agenda_asignada(datos, agenda, monkeypatch):
    datos["anticipo"] = {"activo": True}
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", None)
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "Banco de Prueba")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", "002010077777777771")
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "Nombre Apellido")
    monkeypatch.setattr(engine.avisos, "notificar_anticipo", lambda **k: None)

    slot = _primer_slot(datos)
    tel = "+5215500000001"
    _agendar_con("wa:1", datos, slot, "Con Anticipo", tel=tel, canal="wa")
    resp, _ = asyncio.run(engine.manejar("wa:1", "acepto_anticipo", datos, canal="wa", telefono=tel))

    cita = store.listar()[-1]
    assert cita["estado"] == "pendiente"
    assert cita["agenda_id"] in {agenda["ana"], agenda["brenda"]}
    assert cita["agenda_nombre"] in {"Ana", "Brenda"}
    assert cita["folio"] in " ".join(r["texto"] for r in resp)


# ---------- M10 · el paso "¿con quién?" ----------
def _hasta_con_quien(sid: str, datos: dict, servicio: str = "manicura"):
    engine.SESIONES.pop(sid, None)
    _enviar(sid, "agendar", datos)
    return _enviar(sid, servicio, datos)


def test_con_dos_agendas_pregunta_con_quien_y_no_muestra_ids(datos, agenda):
    resp, s = _hasta_con_quien("c1", datos)
    r = resp[0]

    assert s.paso == "agendar_agenda"
    assert "¿Con quién prefieres?" in r["texto"]
    assert [o["id"] for o in r["opciones"]] == [agenda["ana"], agenda["brenda"], engine.CUALQUIERA]
    assert [o["label"] for o in r["opciones"]] == ["Ana", "Brenda", "Cualquiera"]
    # Nunca se le enseña el ID del calendario, solo la clave corta y el nombre.
    assert ANA not in " ".join(o["id"] + o["label"] for o in r["opciones"])


def test_elegir_agenda_muestra_solo_sus_horarios(datos, agenda):
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Ocupa a Ana", agenda_id=agenda["ana"])

    _hasta_con_quien("c1", datos)
    _, s = _enviar("c1", "Brenda", datos)          # por nombre
    assert s.agenda_id == agenda["brenda"]
    _, s = _enviar("c1", slot, datos)
    assert s.paso == "agendar_nombre", "Brenda sí tiene ese horario libre"

    _hasta_con_quien("c2", datos)
    _, s = _enviar("c2", "1", datos)               # por el número de la fila
    assert s.agenda_id == agenda["ana"]
    _, s = _enviar("c2", slot, datos)
    assert s.paso == "agendar_dia", "para Ana ese horario está tomado"

    _hasta_con_quien("c3", datos)
    _, s = _enviar("c3", "2", datos)
    assert s.agenda_id == agenda["brenda"]


def test_cualquiera_desde_el_paso_usa_la_union(datos, agenda):
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Ocupa a Ana", agenda_id=agenda["ana"])

    _hasta_con_quien("c1", datos)
    _, s = _enviar("c1", "Cualquiera", datos)
    assert s.agenda_id == engine.CUALQUIERA
    _, s = _enviar("c1", slot, datos)
    assert s.paso == "agendar_nombre", "la unión sí lo ofrece"


def test_con_una_sola_agenda_no_pregunta(datos):
    store.agendas_sincronizar([{"calendar_id": ANA, "nombre": "Ana"}])
    resp, s = _hasta_con_quien("c1", datos)

    assert s.agenda_id == store.agenda_de(ANA)["clave"]
    assert "¿Con quién" not in resp[0]["texto"]
    assert s.paso in {"agendar_dia", "agendar_slot"}


def test_sin_agendas_no_pregunta_y_queda_como_siempre(datos):
    assert store.agendas(solo_activas=True) == []
    resp, s = _hasta_con_quien("c1", datos)

    assert s.agenda_id == ""
    assert "¿Con quién" not in resp[0]["texto"]


def test_con_una_frase_libre_pregunta_con_quien_sin_perder_la_hora(datos, agenda):
    resp, s = _hasta_con_quien("c1", datos, "quiero manicura el viernes a las 5")
    if s.paso != "agendar_agenda":
        pytest.skip("el detector de día/hora no entendió la frase en este entorno")

    assert "¿Con quién prefieres?" in resp[0]["texto"]
    engine.SESIONES["c1"].hora_sugerida = "17:00"
    _, s = _enviar("c1", "Ana", datos)
    assert s.agenda_id == agenda["ana"]
    assert s.paso in {"agendar_slot", "agendar_nombre"}


def test_con_mas_de_nueve_agendas_muestra_nueve_y_avisa(datos, caplog):
    cals = [
        {"calendar_id": f"ag{i}@group.calendar.google.com", "nombre": f"Agenda {i}"}
        for i in range(1, 11)
    ]
    store.agendas_sincronizar(cals)

    with caplog.at_level("WARNING", logger="ambar.engine"):
        resp, _ = _hasta_con_quien("c1", datos)

    filas = [(o["id"], o["label"]) for o in resp[0]["opciones"]]
    esperadas = [
        (store.agenda_de(f"ag{i}@group.calendar.google.com")["clave"], f"Agenda {i}")
        for i in range(1, 10)
    ]
    assert filas == esperadas + [(engine.CUALQUIERA, "Cualquiera")]
    assert "10 agendas activas" in caplog.text


def test_los_nombres_largos_se_recortan_al_mostrar_pero_se_guardan_completos(datos):
    largo = "Alejandra del Carmen Rodríguez"
    store.agendas_sincronizar([
        {"calendar_id": ANA, "nombre": largo},
        {"calendar_id": BRENDA, "nombre": "Brenda"},
        {"calendar_id": "caro@group.calendar.google.com", "nombre": "Caro"},
        {"calendar_id": "dani@group.calendar.google.com", "nombre": "Dani"},
    ])

    resp, _ = _hasta_con_quien("c1", datos)
    payload = whatsapp_formato.a_payloads(resp)[0]
    filas = payload["interactive"]["action"]["sections"][0]["rows"]

    assert payload["interactive"]["type"] == "list", "más de 3 opciones van como lista"
    assert all(len(f["title"]) <= whatsapp_formato.MAX_TITULO_FILA for f in filas)
    assert filas[0]["title"] != largo
    assert store.agenda_de(ANA)["nombre"] == largo


# ---------- M11 · mover y cancelar respetan la agenda de la cita ----------
def test_mover_ofrece_solo_horarios_de_la_misma_agenda(datos, agenda):
    slot = _primer_slot(datos)
    cita = store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])
    otro = next(x for x in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"]) if x != slot)
    store.crear(otro, "manicura", "Otra de Ana", agenda_id=agenda["ana"])
    assert otro in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])

    resp, s = _enviar("s1", f"mover {cita['folio']}", datos)
    ids = [o["id"] for o in resp[0]["opciones"]]

    assert s.paso == "citas_slot"
    assert otro not in ids, "ese horario es de Brenda, no de Ana"
    assert slot not in ids, "el horario actual no se ofrece"


def test_mover_no_saca_horarios_libres_de_otra_agenda(datos, agenda):
    """Ana solo trabaja una hora ese día: Brenda no se la presta."""
    slot = _primer_slot(datos)
    cita = store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])
    del_dia = [x for x in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
               if x[:10] == slot[:10]]
    for x in del_dia:
        if x != slot:
            store.crear(x, "manicura", "Cupo lleno de Ana", agenda_id=agenda["ana"])

    resp, _ = _enviar("s1", f"mover {cita['folio']}", datos)
    ids = [o["id"] for o in resp[0]["opciones"]]
    assert all(i == "menu" or not i.startswith(slot[:10]) for i in ids), \
        "ese día ya no tiene nada para Ana"


def test_mover_con_agenda_dada_de_baja_pasa_a_la_manicurista(datos, agenda):
    slot = _primer_slot(datos)
    cita = store.crear(slot, "manicura", "Clienta de Brenda", agenda_id=agenda["brenda"])
    for _ in range(store.AUSENCIAS_PARA_BAJA):
        store.agendas_sincronizar([{"calendar_id": ANA, "nombre": "Ana"}])
    assert store.agenda_por_clave(agenda["brenda"])["activa"] == 0

    resp, s = _enviar("s1", f"mover {cita['folio']}", datos)

    assert s.handoff is True
    assert s.paso == "humano"
    assert "reubicarte" in resp[0]["texto"]
    assert store.obtener(cita["folio"])["slot"] == slot, "no se movió nada"


@pytest.mark.parametrize("estado_http", [404, 410])
def test_cancelar_borra_en_el_calendario_de_la_agenda_y_tolera_que_no_exista(
    datos, agenda, monkeypatch, estado_http
):
    urls: list[str] = []

    class R:
        status_code = estado_http

    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)
    monkeypatch.setattr(calendar_sync, "_cabeceras", lambda: {"Authorization": "Bearer x"})
    monkeypatch.setattr(httpx, "delete", lambda url, **k: urls.append(url) or R())

    slot = _primer_slot(datos)
    cita = store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])
    store.set_evento(cita["folio"], "ev-ana")

    resp, s = _enviar("s1", f"cancelar {cita['folio']}", datos)

    assert urls and ANA in urls[0] and "ev-ana" in urls[0], "borra en el calendario de Ana"
    assert store.obtener(cita["folio"])["estado"] == "cancelada"
    assert "Cancelada" in resp[0]["texto"]
