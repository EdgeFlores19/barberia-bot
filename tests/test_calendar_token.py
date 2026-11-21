"""El token de Calendar puede venir como ruta o como JSON pegado en la variable.

Render monta los Secret Files en /etc/secrets; si el archivo no aparece, el bot
seguía agendando en silencio y sin espejo. Aceptar el JSON completo en la variable
quita esa dependencia, y /health nunca debe devolver su contenido.
"""
import json

from app import calendar_sync, config

TOKEN = {
    "client_id": "id-de-prueba",
    "client_secret": "SECRETO_INLINE",
    "refresh_token": "refresh-de-prueba",
}


def test_token_pegado_en_la_variable(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", json.dumps(TOKEN))
    monkeypatch.setattr(config, "CALENDAR_ID", "primary")

    assert calendar_sync._leer_token() == TOKEN

    salud = calendar_sync.salud()
    assert salud["token_en_variable"] is True
    assert salud["token_legible"] is True
    assert salud["tiene_refresh"] is True
    assert salud["problema_config"] == ""
    # El valor de la variable no se filtra ni se imprime como ruta.
    assert "SECRETO_INLINE" not in json.dumps(salud)


def test_json_invalido_no_truena(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{esto no es json")
    monkeypatch.setattr(config, "CALENDAR_ID", "primary")

    assert calendar_sync._leer_token() is None
    salud = calendar_sync.salud()
    assert salud["token_legible"] is False
    assert "no se pudo leer" in salud["problema_config"]


def test_ruta_inexistente_se_reporta(monkeypatch, tmp_path):
    ruta = tmp_path / "no-existe.json"
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", str(ruta))
    monkeypatch.setattr(config, "CALENDAR_ID", "primary")

    assert calendar_sync._leer_token() is None
    salud = calendar_sync.salud()
    assert salud["token_en_variable"] is False
    assert salud["token_file_existe"] is False
    assert salud["token_legible"] is False


# ---------- el estado de /health con multiagenda ----------
def test_multiagenda_sana_no_reporta_falta_de_calendar_id(monkeypatch):
    """Con multiagenda el calendar_id se descubre: /health no debe decir que falta."""
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", json.dumps(TOKEN))
    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "Agenda - ")
    monkeypatch.setattr(calendar_sync, "_hay_agendas", lambda: True)

    salud = calendar_sync.salud()

    assert salud["problema_config"] == ""
    assert salud["habilitado"] is True


def test_prefijo_sin_agendas_avisa_del_alcance_del_token(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", json.dumps(TOKEN))
    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "Agenda - ")
    monkeypatch.setattr(calendar_sync, "_hay_agendas", lambda: False)

    salud = calendar_sync.salud()

    assert "no encontró agendas" in salud["problema_config"]
    assert salud["habilitado"] is False


def test_sin_id_y_sin_prefijo_lo_dice(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", json.dumps(TOKEN))
    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")

    salud = calendar_sync.salud()

    assert "GOOGLE_CALENDAR_ID" in salud["problema_config"]
    assert "CALENDAR_AGENDAS_PREFIJO" in salud["problema_config"]
