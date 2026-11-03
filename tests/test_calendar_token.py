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
