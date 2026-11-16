"""scripts/deploy_render.py: sin ID de servicio ni ruta de llave cableados, y sin tocar la llave."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
RUTA = RAIZ / "scripts" / "deploy_render.py"
_spec = importlib.util.spec_from_file_location("deploy_render", RUTA)
deploy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deploy)

SVC = "srv-abcdefghij0123456789"
LLAVE = "rnd_llave-de-prueba-que-no-debe-salir-en-pantalla"


# ---------- nada personal cableado ----------
def test_el_script_no_trae_un_id_de_servicio_ni_rutas_personales():
    fuente = RUTA.read_text(encoding="utf-8")

    assert not re.search(r"srv-[a-z0-9]{10,}", fuente)
    for rastro in ("Desktop", "Importantes", "render_api_key", "Path.home"):
        assert rastro not in fuente


def test_el_gitignore_cubre_archivos_de_llaves():
    assert "api_key" in (RAIZ / ".gitignore").read_text(encoding="utf-8")


# ---------- servicio ----------
def test_el_servicio_viene_del_argumento():
    assert deploy.servicio(["deploy_render.py", SVC], {}) == SVC


def test_o_de_la_variable_de_entorno():
    assert deploy.servicio(["deploy_render.py"], {"RENDER_SERVICE_ID": f"  {SVC}  "}) == SVC


def test_el_argumento_manda_sobre_la_variable():
    otro = "srv-zzzzzzzzzz9999999999"

    assert deploy.servicio(["deploy_render.py", otro], {"RENDER_SERVICE_ID": SVC}) == otro


def test_sin_servicio_no_adivina_ninguno():
    with pytest.raises(SystemExit) as e:
        deploy.servicio(["deploy_render.py"], {})

    assert "RENDER_SERVICE_ID" in str(e.value.code)


@pytest.mark.parametrize("malo", ["../x", "srv-abc", "SRV-ABCDEFGHIJ0123456789", f"{SVC}/../..", "srv-abc def ghijk", "x" * 30])
def test_un_id_raro_se_rechaza_porque_va_dentro_de_la_URL(malo):
    with pytest.raises(SystemExit):
        deploy.servicio(["deploy_render.py", malo], {})


# ---------- llave ----------
def test_la_llave_sale_solo_del_entorno():
    assert deploy.llave({"RENDER_API_KEY": f" {LLAVE} "}) == LLAVE


@pytest.mark.parametrize("env", [{}, {"RENDER_API_KEY": ""}, {"RENDER_API_KEY": "   "}])
def test_sin_llave_se_detiene_y_explica_donde_ponerla(env):
    with pytest.raises(SystemExit) as e:
        deploy.llave(env)

    assert "RENDER_API_KEY" in str(e.value.code)


def test_un_archivo_de_llave_en_el_escritorio_ya_no_se_lee(tmp_path, monkeypatch):
    escritorio = tmp_path / "Desktop" / "Importantes"
    escritorio.mkdir(parents=True)
    (escritorio / "render_api_key.txt").write_text(LLAVE, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with pytest.raises(SystemExit):
        deploy.llave({})


# ---------- el recorrido, con un Render simulado ----------
class _Render:
    def __init__(self, estados: list[str], envolver: bool = False):
        self.estados, self.envolver, self.llamadas = list(estados), envolver, []

    def __call__(self, ruta: str, llave: str, metodo: str = "GET", cuerpo=None) -> dict:
        self.llamadas.append((metodo, ruta, llave, cuerpo))
        if metodo == "POST":
            dep = {"id": "dep-1", "status": "created"}
        else:
            estado = self.estados.pop(0) if self.estados else "build_in_progress"
            dep = {"id": "dep-1", "status": estado, "commit": {"id": "3d88cc5aaa"}}
        return {"deploy": dep} if self.envolver else dep


def _correr(estados, envolver=False, intentos=60):
    api, salida = _Render(estados, envolver), []
    codigo = deploy.main(["deploy_render.py", SVC], {"RENDER_API_KEY": LLAVE}, llamar_api=api,
                         esperar=lambda s: None, intentos=intentos, salida=lambda *a: salida.append(" ".join(map(str, a))))
    return codigo, api, "\n".join(salida)


def test_un_deploy_que_queda_live_termina_bien_y_dice_el_commit():
    codigo, api, texto = _correr(["build_in_progress", "update_in_progress", "live"])

    assert codigo == 0
    assert "estado final: live" in texto and "3d88cc5aaa" in texto
    assert api.llamadas[0][:2] == ("POST", f"/services/{SVC}/deploys")


def test_acepta_la_respuesta_envuelta_en_deploy():
    codigo, _, _ = _correr(["live"], envolver=True)

    assert codigo == 0


@pytest.mark.parametrize("estado", ["build_failed", "canceled", "update_failed", "deactivated"])
def test_un_deploy_fallido_sale_con_error(estado):
    codigo, _, texto = _correr([estado])

    assert codigo == 1 and f"estado final: {estado}" in texto


def test_si_nunca_termina_sale_por_tiempo_agotado():
    codigo, _, texto = _correr(["build_in_progress"] * 5, intentos=3)

    assert codigo == 2 and "tiempo agotado" in texto


def test_la_llave_viaja_a_la_API_pero_nunca_a_la_pantalla():
    codigo, api, texto = _correr(["live"])

    assert all(llamada[2] == LLAVE for llamada in api.llamadas)
    assert LLAVE not in texto


def test_avisa_que_no_espera_al_CI():
    _, _, texto = _correr(["live"])

    assert "CI" in texto
