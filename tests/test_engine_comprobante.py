"""Motor: con `validar_comprobante` activo, la cita solo se confirma si el comprobante cuadra (ADR 0011)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app import anticipo, config, engine, reloj, store

from tests.telefonos import TEL_A

pytestmark = pytest.mark.usefixtures("bd")

CLABE = "002010077777777771"
IMG = {"tipo": "image", "id": "media-1", "mime": "image/jpeg"}
PDF = {"tipo": "document", "id": "media-2", "mime": "application/pdf"}


@pytest.fixture()
def datos(monkeypatch) -> dict:
    d = engine.cargar_datos()
    d["anticipo"] = {"activo": True, "validar_comprobante": True}
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", None)
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "Banco de Prueba")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", CLABE)
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "Nombre Apellido")
    return d


@pytest.fixture()
def dueno(monkeypatch) -> dict:
    reg = {"anticipo": [], "revision": [], "handoff": []}
    monkeypatch.setattr(engine.avisos, "notificar_anticipo", lambda **k: reg["anticipo"].append(k))
    monkeypatch.setattr(engine.avisos, "notificar_comprobante_revision", lambda **k: reg["revision"].append(k))
    monkeypatch.setattr(engine.avisos, "notificar_handoff", lambda sid, tel, msg: reg["handoff"].append(msg))
    return reg


@pytest.fixture()
def lector(monkeypatch) -> dict:
    """Simula la bajada del archivo y al modelo con visión: `estado["lectura"]` es lo que 'leyó'."""
    estado = {"lectura": None, "bajadas": 0, "lecturas": 0, "archivo": (b"imagen", "image/jpeg")}

    async def bajar(media_id):
        estado["bajadas"] += 1
        return estado["archivo"]

    async def leer(contenido, mime):
        estado["lecturas"] += 1
        return estado["lectura"]

    monkeypatch.setattr(engine.wa_cliente, "descargar_media", bajar)
    monkeypatch.setattr(engine.llm, "leer_comprobante", leer)
    return estado


def _lectura(cita: dict, **kw) -> dict:
    base = {"monto": 200.0, "fecha": reloj.ahora().date(), "clave_rastreo": "MBAN01ABCDEF1234",
            "cuenta_digitos": "7771", "beneficiario": "Nombre Apellido", "concepto": f"Ana {cita['folio']}"}
    return {**base, **kw}


def _apartado(minutos: int = 60, dias: int = 3) -> dict:
    ahora = reloj.ahora()
    return store.crear((ahora + timedelta(days=dias)).strftime("%Y-%m-%dT10:00"), "gelish", "Ana", TEL_A,
                       pendiente_hasta=(ahora + timedelta(minutes=minutos)).isoformat(timespec="seconds"),
                       anticipo=200, aceptado_en=ahora.isoformat(timespec="seconds"))


def _enviar(datos: dict, media=IMG, tel=TEL_A) -> str:
    respuestas, _ = asyncio.run(engine.manejar(f"wa:{tel}", "", datos, canal="wa", telefono=tel, media=media))
    return " ".join(r["texto"] for r in respuestas)


# ---------- cuadra ----------
def test_un_comprobante_que_cuadra_confirma_y_avisa_lo_que_valido(datos, lector, dueno):
    cita = _apartado()
    lector["lectura"] = _lectura(cita)

    texto = _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert "Recibí tu comprobante" in texto and "revisando" not in texto
    assert len(dueno["anticipo"]) == 1 and dueno["revision"] == []
    assert "validó" in dueno["anticipo"][0]["validacion"]


def test_sin_el_folio_confirma_y_se_lo_marca_a_la_duena(datos, lector, dueno):
    cita = _apartado()
    lector["lectura"] = _lectura(cita, concepto=None)

    _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert "folio" in dueno["anticipo"][0]["validacion"]


# ---------- no cuadra: la cita queda apartada y la duena revisa ----------
def test_un_monto_distinto_deja_la_cita_apartada_y_avisa(datos, lector, dueno):
    cita = _apartado(minutos=60)
    lector["lectura"] = _lectura(cita, monto=100.0)

    texto = _enviar(datos)

    guardada = store.obtener(cita["folio"])
    assert guardada["estado"] == "pendiente" and guardada["comprobante_en"] is None
    assert "revisando" in texto
    assert dueno["anticipo"] == [] and len(dueno["revision"]) == 1
    assert any("$100.00" in m for m in dueno["revision"][0]["motivos"])
    assert dueno["revision"][0]["hasta"]                                     # el apartado se alargó


def test_el_apartado_se_alarga_para_dar_tiempo_de_revisar(datos, lector, dueno):
    cita = _apartado(minutos=60)
    lector["lectura"] = _lectura(cita, monto=100.0)

    _enviar(datos)

    horas = (datetime.fromisoformat(store.obtener(cita["folio"])["vence"]) - reloj.ahora()).total_seconds() / 3600
    assert 11 < horas <= 12.1


def test_el_apartado_no_se_alarga_mas_alla_de_la_propia_cita(datos, lector, dueno):
    ahora = reloj.ahora()
    cita = store.crear((ahora + timedelta(hours=3)).strftime("%Y-%m-%dT%H:00"), "gelish", "Ana", TEL_A,
                       pendiente_hasta=(ahora + timedelta(minutes=30)).isoformat(timespec="seconds"),
                       anticipo=200, aceptado_en=ahora.isoformat(timespec="seconds"))
    lector["lectura"] = _lectura(cita, monto=100.0)

    _enviar(datos)

    assert store.obtener(cita["folio"])["vence"] <= cita["slot"] + ":00"


def test_una_clave_repetida_en_otra_cita_no_confirma(datos, lector, dueno):
    primera = _apartado()
    lector["lectura"] = _lectura(primera)
    _enviar(datos)
    assert store.obtener(primera["folio"])["estado"] == "confirmada"
    segunda = _apartado(dias=4)
    lector["lectura"] = _lectura(segunda)                                    # misma clave de rastreo

    _enviar(datos)

    assert store.obtener(segunda["folio"])["estado"] == "pendiente"
    assert any(primera["folio"] in m for m in dueno["revision"][0]["motivos"])


def test_reenviar_el_mismo_comprobante_de_la_misma_cita_no_cuenta_como_repetido(datos, lector, dueno):
    cita = _apartado()
    lector["lectura"] = _lectura(cita, monto=100.0)          # la primera vez no cuadró...
    _enviar(datos)
    lector["lectura"] = _lectura(cita)                        # ...y manda otro con la misma clave y el monto bien

    _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_un_pdf_va_a_revision_sin_gastar_el_lector(datos, lector, dueno):
    cita = _apartado()

    texto = _enviar(datos, PDF)

    assert store.obtener(cita["folio"])["estado"] == "pendiente"
    assert "revisando" in texto
    assert lector["bajadas"] == 0 and lector["lecturas"] == 0
    assert "PDF" in dueno["revision"][0]["motivos"][0]


@pytest.mark.parametrize("falla", ["descarga", "lectura"])
def test_si_algo_falla_no_se_confirma_a_ciegas(datos, lector, dueno, falla):
    cita = _apartado()
    if falla == "descarga":
        lector["archivo"] = None
    lector["lectura"] = None

    _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "pendiente"
    assert len(dueno["revision"]) == 1


def test_un_apartado_vencido_que_no_cuadra_no_se_alarga_y_se_avisa_como_tardio(datos, lector, dueno):
    ahora = reloj.ahora()
    cita = store.crear((ahora + timedelta(days=3)).strftime("%Y-%m-%dT10:00"), "gelish", "Ana", TEL_A,
                       pendiente_hasta=(ahora + timedelta(minutes=60)).isoformat(timespec="seconds"),
                       anticipo=200, aceptado_en=ahora.isoformat(timespec="seconds"))
    store.expirar_vencidas((ahora + timedelta(hours=2)).isoformat(timespec="seconds"))
    lector["lectura"] = _lectura(cita, monto=100.0)

    _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "expirada"
    assert dueno["revision"][0]["tardio"] is True and dueno["revision"][0]["hasta"] is None


# ---------- apagado: nada cambia ----------
def test_con_la_validacion_apagada_confirma_como_siempre_sin_tocar_al_lector(lector, dueno, datos):
    datos["anticipo"] = {"activo": True}
    cita = _apartado()

    _enviar(datos)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert lector["bajadas"] == 0 and lector["lecturas"] == 0
    assert dueno["anticipo"][0]["validacion"] == ""


# ---------- configuración ----------
def test_validar_sin_la_key_del_modelo_es_un_problema_de_arranque(datos, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")

    assert anticipo.problemas_de_validacion(datos)


def test_con_la_key_no_hay_problema_y_apagada_tampoco(datos, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    assert anticipo.problemas_de_validacion(datos) == []

    datos["anticipo"] = {"activo": True}
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    assert anticipo.problemas_de_validacion(datos) == []


# ---------- interruptor desde el entorno (Render) ----------
def test_la_variable_apaga_la_validacion_aunque_el_json_la_encienda(datos, lector, dueno, monkeypatch):
    monkeypatch.setattr(config, "VALIDAR_COMPROBANTE", False)
    cita = _apartado()

    _enviar(datos)

    assert not anticipo.validar_comprobante(datos)
    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert lector["bajadas"] == 0 and lector["lecturas"] == 0


def test_la_variable_enciende_la_validacion_aunque_el_json_la_apague(datos, monkeypatch):
    datos["anticipo"] = {"activo": True, "validar_comprobante": False}
    monkeypatch.setattr(config, "VALIDAR_COMPROBANTE", True)

    assert anticipo.validar_comprobante(datos)


def test_la_variable_no_enciende_nada_si_el_anticipo_esta_apagado(datos, monkeypatch):
    datos["anticipo"] = {"activo": False}
    monkeypatch.setattr(config, "VALIDAR_COMPROBANTE", True)

    assert not anticipo.validar_comprobante(datos)


def test_apagada_por_variable_ya_no_exige_la_key_del_modelo(datos, monkeypatch):
    monkeypatch.setattr(config, "VALIDAR_COMPROBANTE", False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")

    assert anticipo.problemas_de_validacion(datos) == []
