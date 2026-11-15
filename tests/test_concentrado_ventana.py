"""El resumen automático cubre "desde el último corte": ningún depósito se pierde entre días."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from app import concentrado, config, main, reloj, store

from tests.telefonos import TEL_A

DATOS = {
    "servicios": [{"id": "manicura", "nombre": "Manicura rusa", "precio": 350, "minutos": 60}],
    "anticipo": {"activo": True, "monto": 200},
}


def _cita(folio: str, comprobante_en: str, slot: str = "2026-11-20T11:00") -> dict:
    return {"folio": folio, "estado": "confirmada", "slot": slot, "servicio_id": "manicura", "nombre": "Ana",
            "telefono": TEL_A, "anticipo_monto": 200, "comprobante_en": comprobante_en}


def _folios(r: dict) -> list[str]:
    return [f["folio"] for f in r["filas"]]


# ---------- la ventana ----------
def test_un_deposito_posterior_al_corte_anterior_entra_y_uno_anterior_no():
    citas = [_cita("AMB-AAAA", "2026-11-16T14:00:00"), _cita("AMB-BBBB", "2026-11-16T21:03:00")]

    r = concentrado.reporte_ventana(DATOS, citas, despues="2026-11-16T20:00:30", hasta="2026-11-17T20:00:30")

    assert _folios(r) == ["AMB-BBBB"]


def test_los_bordes_cuentan_una_sola_vez():
    citas = [_cita("AMB-EN-EL-CORTE", "2026-11-16T20:00:30")]

    primero = concentrado.reporte_ventana(DATOS, citas, despues="2026-11-16T00:00:00", hasta="2026-11-16T20:00:30")
    segundo = concentrado.reporte_ventana(DATOS, citas, despues="2026-11-16T20:00:30", hasta="2026-11-17T20:00:30")

    assert _folios(primero) == ["AMB-EN-EL-CORTE"] and _folios(segundo) == []


def test_ventanas_consecutivas_reparten_cada_deposito_exactamente_una_vez():
    horas = ["2026-11-16T09:00:00", "2026-11-16T20:00:30", "2026-11-16T20:00:31", "2026-11-16T23:59:59",
             "2026-11-17T00:00:00", "2026-11-17T12:30:00", "2026-11-17T20:00:30", "2026-11-18T08:15:00"]
    citas = [_cita(f"AMB-{i:04d}", h) for i, h in enumerate(horas)]
    cortes = ["2026-11-16T00:00:00", "2026-11-16T20:00:30", "2026-11-17T20:00:30", "2026-11-18T20:00:30"]

    vistos: list[str] = []
    for antes, ahora in zip(cortes, cortes[1:]):
        vistos += _folios(concentrado.reporte_ventana(DATOS, citas, despues=antes, hasta=ahora))

    assert sorted(vistos) == sorted(c["folio"] for c in citas)          # ni uno de menos ni repetido


def test_una_cancelada_con_deposito_tambien_entra_como_en_el_reporte_por_fecha():
    c = _cita("AMB-CCCC", "2026-11-16T10:00:00")
    c["estado"] = "cancelada"

    r = concentrado.reporte_ventana(DATOS, [c], despues="2026-11-16T00:00:00", hasta="2026-11-16T20:00:00")

    assert _folios(r) == ["AMB-CCCC"] and r["filas"][0]["anulada"] is True


def test_sin_hora_de_comprobante_valida_no_entra():
    c = _cita("AMB-DDDD", "no-es-una-fecha")

    r = concentrado.reporte_ventana(DATOS, [c], despues="2026-11-16T00:00:00", hasta="2026-11-17T00:00:00")

    assert r["n"] == 0


# ---------- el texto ----------
def test_la_cabecera_dice_el_periodo_cubierto():
    r = concentrado.reporte_ventana(DATOS, [_cita("AMB-AAAA", "2026-11-16T21:03:00")],
                                    despues="2026-11-16T20:00:30", hasta="2026-11-17T20:00:30")

    assert "desde" in r["texto"] and "20:00" in r["texto"]
    assert r["desde"] == "2026-11-16T20:00:30" and r["hasta"] == "2026-11-17T20:00:30"


def test_sin_depositos_lo_dice():
    r = concentrado.reporte_ventana(DATOS, [], despues="2026-11-16T20:00:30", hasta="2026-11-17T20:00:30")

    assert "Sin depósitos" in r["texto"] and r["n"] == 0


def test_cada_linea_trae_la_hora_del_deposito_para_cuadrar_con_el_banco():
    r = concentrado.reporte_ventana(DATOS, [_cita("AMB-AAAA", "2026-11-16T21:03:44")],
                                    despues="2026-11-16T20:00:30", hasta="2026-11-17T20:00:30")

    assert "21:03" in r["texto"]


# ---------- mensajes largos: nada se corta ----------
def test_pocos_depositos_caben_en_un_solo_mensaje_igual_al_texto():
    r = concentrado.reporte_ventana(DATOS, [_cita(f"AMB-{i:04d}", f"2026-11-16T1{i}:00:00") for i in range(3)],
                                    despues="2026-11-16T00:00:00", hasta="2026-11-16T20:00:00")

    assert r["partes"] == [r["texto"]]


def test_muchos_depositos_se_reparten_en_varios_mensajes_sin_perder_ninguno():
    citas = [_cita(f"AMB-{i:04d}", f"2026-11-16T{8 + i // 60:02d}:{i % 60:02d}:00", slot=f"2026-11-{20 + i % 8}T11:00")
             for i in range(90)]

    r = concentrado.reporte_ventana(DATOS, citas, despues="2026-11-16T00:00:00", hasta="2026-11-16T23:59:59")

    assert len(r["partes"]) >= 2
    assert all(len(p) <= concentrado.LIMITE_TELEGRAM for p in r["partes"])
    junto = "\n".join(r["partes"])
    assert all(junto.count(f"AMB-{i:04d}") == 1 for i in range(90))
    assert "parte 2 de" in r["partes"][1]
    assert "90 depósitos" in r["partes"][0]


# ---------- la tabla de envíos ----------
def test_ultimo_corte_sin_envios_es_none(bd):
    assert store.ultimo_corte() is None


def test_ultimo_corte_es_el_del_envio_mas_reciente(bd):
    store.resumen_marcar("2026-11-15", 2, corte="2026-11-15T20:00:30")
    store.resumen_marcar("2026-11-16", 1, corte="2026-11-16T20:01:02")

    assert store.ultimo_corte() == "2026-11-16T20:01:02"


def test_un_envio_viejo_sin_corte_usa_su_hora_de_envio(bd):
    store.resumen_marcar("2026-11-15", 2)
    with store._con() as con:
        con.execute("UPDATE resumen_envios SET corte = NULL, enviado = '2026-11-15T20:00:40'")

    assert store.ultimo_corte() == "2026-11-15T20:00:40"


def test_una_base_con_la_tabla_de_envios_vieja_gana_la_columna_sin_perder_filas(tmp_path, monkeypatch):
    viejo = tmp_path / "viejo.db"
    con = sqlite3.connect(viejo)
    con.executescript("""
        CREATE TABLE resumen_envios (dia TEXT PRIMARY KEY, enviado TEXT NOT NULL, total_citas INTEGER NOT NULL DEFAULT 0);
        INSERT INTO resumen_envios VALUES ('2026-11-15', '2026-11-15T20:00:40', 3);
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DB_FILE", viejo)
    monkeypatch.setattr(store, "_inicializada", False)

    store.init()
    store.init()                                                   # idempotente

    assert store.resumen_hecho("2026-11-15") is True
    assert store.ultimo_corte() == "2026-11-15T20:00:40"
    store.resumen_marcar("2026-11-16", 1, corte="2026-11-16T20:00:00")
    assert store.ultimo_corte() == "2026-11-16T20:00:00"


# ---------- el disparador: el caso que se perdía ----------
@pytest.fixture()
def a_las(bd, monkeypatch):
    """Fija la hora del negocio y captura lo que saldría por Telegram (cada mensaje, ok o no)."""
    enviados: list[str] = []
    estado = {"ok": [True]}

    def fijar(hora: str, resultados: list[bool] | None = None):
        monkeypatch.setattr(reloj, "ahora", lambda: datetime.fromisoformat(hora))
        monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token-de-prueba")
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
        cola = list(resultados or [True] * 50)

        def enviar(texto: str) -> bool:
            enviados.append(texto)
            return cola.pop(0) if cola else True

        monkeypatch.setattr("app.avisos._telegram", enviar)
        return enviados

    return fijar


def _deposito(hora: str, slot: str) -> dict:
    c = store.crear(slot=slot, servicio_id="manicura", nombre="Ana", telefono=TEL_A,
                    pendiente_hasta="2026-11-30T23:59:59", anticipo=200)
    return store.registrar_comprobante(c["folio"], "image", "media-1", ahora=hora)


def test_un_deposito_de_despues_del_resumen_sale_en_el_del_dia_siguiente(a_las):
    enviados = a_las("2026-11-16T20:00:30")
    a = _deposito("2026-11-16T18:42:05", "2026-11-18T10:00")
    main._concentrado_si_toca()
    assert len(enviados) == 1 and a["folio"] in enviados[0]

    b = _deposito("2026-11-16T21:03:00", "2026-11-19T10:00")          # llega DESPUÉS del resumen del lunes
    enviados = a_las("2026-11-17T20:00:30")
    enviados.clear()
    main._concentrado_si_toca()

    assert len(enviados) == 1
    assert b["folio"] in enviados[0] and a["folio"] not in enviados[0]


def test_los_dias_sin_resumen_se_recuperan_en_el_siguiente(a_las):
    enviados = a_las("2026-11-15T20:00:30")
    _deposito("2026-11-15T18:00:00", "2026-11-18T10:00")
    main._concentrado_si_toca()                                       # domingo: sale
    perdido = _deposito("2026-11-16T12:00:00", "2026-11-19T10:00")    # lunes: el proceso estuvo caído
    enviados.clear()

    a_las("2026-11-17T20:00:30")                                      # martes
    main._concentrado_si_toca()

    assert len(enviados) == 1 and perdido["folio"] in enviados[0]


def test_si_telegram_falla_el_corte_no_avanza_y_el_reintento_no_pierde_nada(a_las):
    enviados = a_las("2026-11-16T20:00:30", resultados=[False])
    a = _deposito("2026-11-16T18:00:00", "2026-11-18T10:00")
    main._concentrado_si_toca()
    assert store.resumen_hecho("2026-11-16") is False and store.ultimo_corte() is None

    b = _deposito("2026-11-16T20:02:00", "2026-11-19T10:00")          # llega entre el intento y el reintento
    enviados.clear()
    a_las("2026-11-16T20:05:00")
    main._concentrado_si_toca()

    assert store.resumen_hecho("2026-11-16") is True
    assert a["folio"] in enviados[0] and b["folio"] in enviados[0]


def test_un_resumen_largo_manda_todas_las_partes_y_solo_se_marca_si_salieron_todas(a_las):
    for i in range(60):
        _deposito(f"2026-11-16T{8 + i // 60:02d}:{i % 60:02d}:00", f"2026-11-{17 + i // 24:02d}T{i % 24:02d}:00")        # un horario distinto por cita
    enviados = a_las("2026-11-16T20:00:30", resultados=[True, False])          # la 2.ª parte falla

    main._concentrado_si_toca()
    assert len(enviados) == 2 and store.resumen_hecho("2026-11-16") is False

    enviados.clear()
    a_las("2026-11-16T20:05:00")                                               # reintento: todo sale
    main._concentrado_si_toca()

    assert len(enviados) >= 2 and store.resumen_hecho("2026-11-16") is True
    assert sum(t.count("Folio AMB-") for t in enviados) == 60
