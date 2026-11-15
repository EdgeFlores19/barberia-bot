"""Concentrado de depósitos: qué entra, cuánto suma y cuándo se dispara. Sin I/O."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import concentrado, store

DATOS = {
    "servicios": [
        {"id": "manicura", "nombre": "Manicura", "precio": 250, "duracion": 60},
        {"id": "gelish", "nombre": "Gelish", "precio": 300, "duracion": 60},
    ],
    "anticipo": {
        "activo": True,
        "monto": 200,
        "descuenta_del_total": True,
        "resumen_hora": "20:00",
        "resumen_diario": False,
    },
}


def _cita(**kw):
    base = {
        "folio": "AMB-AAAA",
        "slot": "2026-11-16T10:00",
        "servicio_id": "manicura",
        "nombre": "Ana",
        "telefono": "5215551234567",
        "estado": "confirmada",
        "anticipo_monto": 200,
        "comprobante_en": "2026-11-15T18:42:05",
        "comprobante_tipo": "image",
    }
    base.update(kw)
    return base


def _reporte(citas, desde="2026-11-15", hasta="2026-11-15", datos=DATOS):
    return concentrado.reporte(datos, citas, desde=desde, hasta=hasta)


# ---------- qué entra ----------
def test_solo_entran_las_citas_con_comprobante():
    r = _reporte([_cita(), _cita(folio="AMB-BBBB", comprobante_en=None)])

    assert [f["folio"] for f in r["filas"]] == ["AMB-AAAA"]


def test_sin_anticipo_no_entra():
    r = _reporte([_cita(anticipo_monto=0)])

    assert r["filas"] == [] and r["n"] == 0


def test_cancelada_sigue_en_el_concentrado_pero_marcada():
    """El dinero entró: la dueña necesita verlo para decidir la devolución."""
    r = _reporte([_cita(estado="cancelada")])

    assert r["n"] == 1 and r["filas"][0]["anulada"] is True


def test_expirada_sin_comprobante_no_aparece():
    r = _reporte([_cita(estado="expirada", comprobante_en=None)])

    assert r["filas"] == []


# ---------- cuánto ----------
def test_anticipo_resta_y_precio():
    f = _reporte([_cita()])["filas"][0]

    assert (f["precio"], f["anticipo"], f["resta"]) == (250, 200, 50)


def test_si_el_anticipo_no_se_descuenta_la_resta_es_el_precio():
    datos = {**DATOS, "anticipo": {**DATOS["anticipo"], "descuenta_del_total": False}}

    f = _reporte([_cita()], datos=datos)["filas"][0]

    assert (f["anticipo"], f["resta"]) == (200, 250)


def test_totales_suman_las_filas():
    r = _reporte([_cita(), _cita(folio="AMB-BBBB", servicio_id="gelish")])

    assert (r["n"], r["total_anticipo"], r["total_resta"]) == (2, 400, 150)


def test_servicio_desconocido_no_truena():
    """Una cita vieja de un servicio que ya no está en el JSON no puede tumbar el reporte."""
    f = _reporte([_cita(servicio_id="fantasma")])["filas"][0]

    assert f["servicio"] == "fantasma" and f["precio"] == 0


def test_filas_ordenadas_por_cita():
    r = _reporte([_cita(folio="AMB-BBBB", slot="2026-11-16T09:00"), _cita()])

    assert [f["folio"] for f in r["filas"]] == ["AMB-BBBB", "AMB-AAAA"]


# ---------- ventana de fechas (fecha del depósito) ----------
def test_filtra_por_fecha_del_deposito():
    cita = _cita(comprobante_en="2026-11-14T09:00:00")

    assert _reporte([cita])["filas"] == []
    assert _reporte([cita], desde="2026-11-14", hasta="2026-11-14")["n"] == 1


def test_la_ventana_incluye_los_extremos():
    r = _reporte([_cita(comprobante_en="2026-11-13T23:59:59")], desde="2026-11-13", hasta="2026-11-15")

    assert r["n"] == 1


def test_hasta_vacio_es_el_mismo_dia():
    r = concentrado.reporte(DATOS, [_cita()], desde="2026-11-15")

    assert (r["desde"], r["hasta"]) == ("2026-11-15", "2026-11-15") and r["n"] == 1


@pytest.mark.parametrize("desde,hasta", [("2026-13-01", None), ("ayer", None), ("2026-11-16", "2026-11-15")])
def test_fechas_invalidas(desde, hasta):
    with pytest.raises(ValueError):
        concentrado.reporte(DATOS, [], desde=desde, hasta=hasta)


# ---------- el texto que se manda ----------
def test_texto_vacio_lo_dice():
    t = concentrado.reporte(DATOS, [], desde="2026-11-15")["texto"]

    assert "Sin depósitos" in t


def test_texto_trae_lo_que_la_duena_necesita_para_cuadrar():
    t = _reporte([_cita()])["texto"]

    for dato in ("AMB-AAAA", "Ana", "Manicura", "200", "Tel +5215551234567", "16 nov"):
        assert dato in t


def test_texto_de_un_dia_completo_lista_todos():
    citas = [_cita(folio=f"AMB-{i:04d}", comprobante_en="2026-11-15T10:00:00") for i in range(200)]
    t = _reporte(citas)["texto"]

    assert len(t) <= concentrado.LIMITE_TELEGRAM
    assert "más" in t or "ver el reporte" in t


# ---------- quién lo dispara ----------
def test_hhmm():
    assert concentrado.hhmm(datetime(2026, 11, 15, 8, 5)) == "08:05"


def test_no_manda_antes_de_la_hora():
    assert concentrado.toca_enviar({"resumen_hora": "20:00"}, datetime(2026, 11, 15, 19, 59), False) is False


def test_manda_a_la_hora():
    assert concentrado.toca_enviar({"resumen_hora": "20:00"}, datetime(2026, 11, 15, 20, 0), False) is True


def test_manda_tarde_si_el_proceso_estuvo_abajo():
    assert concentrado.toca_enviar({"resumen_hora": "20:00"}, datetime(2026, 11, 15, 23, 30), False) is True


def test_una_sola_vez_al_dia():
    assert concentrado.toca_enviar({"resumen_hora": "20:00"}, datetime(2026, 11, 15, 20, 3), True) is False


@pytest.mark.parametrize("hora", ["", "20", "25:99", "ocho"])
def test_hora_mal_configurada_no_manda_nada(hora):
    """resumen_hora es config muerta si está mal escrita: mejor no mandar que mandar a locas."""
    assert concentrado.toca_enviar({"resumen_hora": hora}, datetime(2026, 11, 15, 20, 0), False) is False


# ---------- el "ya se mandó" vive en la base ----------
def test_marca_y_consulta_del_dia(bd):
    assert store.resumen_hecho("2026-11-15") is False

    store.resumen_marcar("2026-11-15", total=600)

    assert store.resumen_hecho("2026-11-15") is True
    assert store.resumen_hecho("2026-11-16") is False


def test_marcar_dos_veces_no_revienta(bd):
    store.resumen_marcar("2026-11-15", total=600)
    store.resumen_marcar("2026-11-15", total=800)

    assert store.resumen_hecho("2026-11-15") is True


def test_reset_limpia_los_resumenes(bd):
    store.resumen_marcar("2026-11-15", total=600)

    store.reset()

    assert store.resumen_hecho("2026-11-15") is False


# ---------- el teléfono se lee como se marca ----------
def test_telefono_de_whatsapp_se_muestra_con_un_solo_mas():
    """WhatsApp guarda `521…` sin `+`; ponerle otro daba `Tel ++52…`."""
    t = concentrado.texto(_reporte([_cita(telefono="5215551234567")]))

    assert "Tel +5215551234567" in t and "++" not in t


def test_telefono_que_ya_trae_mas_no_se_duplica():
    t = concentrado.texto(_reporte([_cita(telefono="+5215551234567")]))

    assert "Tel +5215551234567" in t and "++" not in t


def test_sin_telefono_lo_dice_en_vez_de_dejar_el_hueco():
    t = concentrado.texto(_reporte([_cita(telefono="")]))

    assert "sin teléfono" in t
