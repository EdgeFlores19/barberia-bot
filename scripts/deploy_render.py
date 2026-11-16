"""Dispara el deploy en Render del commit ya empujado y espera a que quede live.

Uso:   python scripts/deploy_render.py <service_id>
       (o define RENDER_SERVICE_ID y llámalo sin argumentos)

Requiere RENDER_API_KEY en el entorno. Imprime el estado y el commit servido, nunca la llave.

La llave de la API de Render es de la CUENTA, no de este servicio: puede leer las variables
secretas de todos tus servicios. Por eso solo se acepta por el entorno (no hay archivo ni ruta
por defecto) y conviene guardarla en un gestor de contraseñas, no en un archivo suelto.

Ojo: esto despliega lo que ya esté empujado en la rama del servicio SIN esperar al CI. El
camino normal es autoDeployTrigger: checksPass (render.yaml); este script es para forzarlo a mano.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from typing import Any, Callable, Mapping

API = "https://api.render.com/v1"
ESTADOS_FINALES = {"live", "build_failed", "canceled", "deactivated", "update_failed"}
# El id va dentro de la URL: solo se acepta con la forma que tienen los de Render.
_ID_OK = re.compile(r"^srv-[a-z0-9]{10,40}$")


def servicio(argv: list[str], env: Mapping[str, str]) -> str:
    """El id del servicio: argumento o RENDER_SERVICE_ID. No hay uno por defecto."""
    crudo = (argv[1] if len(argv) > 1 else env.get("RENDER_SERVICE_ID", "")).strip()
    if not crudo:
        raise SystemExit("Falta el id del servicio: pásalo como argumento o define RENDER_SERVICE_ID (srv-...).")
    if not _ID_OK.match(crudo):
        raise SystemExit("El id del servicio no es válido: empieza con srv- y sigue con letras minúsculas y números.")
    return crudo


def llave(env: Mapping[str, str]) -> str:
    """La API key, solo del entorno."""
    valor = (env.get("RENDER_API_KEY") or "").strip()
    if not valor:
        raise SystemExit(
            "Falta RENDER_API_KEY en el entorno. Defínela en tu terminal (export RENDER_API_KEY=... "
            "o $env:RENDER_API_KEY='...' en PowerShell); no la guardes en archivos de carpetas sincronizadas."
        )
    return valor


def llamar(ruta: str, key: str, metodo: str = "GET", cuerpo: dict | None = None) -> dict[str, Any]:
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        API + ruta, data=datos, method=metodo,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _deploy(respuesta: dict[str, Any]) -> dict[str, Any]:
    """La API responde el deploy tal cual, o a veces envuelto en {"deploy": ...}."""
    interno = respuesta.get("deploy")
    return interno if isinstance(interno, dict) else respuesta


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    llamar_api: Callable[..., dict[str, Any]] = llamar,
    esperar: Callable[[float], Any] = time.sleep,
    intentos: int = 60,
    salida: Callable[..., Any] = print,
) -> int:
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    svc, key = servicio(argv, env), llave(env)

    salida("Ojo: despliega lo que ya esté empujado en la rama del servicio, sin esperar al CI.")
    dep = _deploy(llamar_api(f"/services/{svc}/deploys", key, "POST", {"clearCache": "do_not_clear"}))
    dep_id = dep.get("id")
    salida("deploy:", dep_id)

    ultimo = None
    for _ in range(intentos):
        esperar(10)
        dep = _deploy(llamar_api(f"/services/{svc}/deploys/{dep_id}", key))
        estado = dep.get("status")
        if estado != ultimo:
            salida(f"  {estado}")
            ultimo = estado
        if estado in ESTADOS_FINALES:
            salida(f"estado final: {estado}")
            salida("commit:", (dep.get("commit") or {}).get("id"))
            return 0 if estado == "live" else 1
    salida("tiempo agotado esperando el deploy")
    return 2


if __name__ == "__main__":
    sys.exit(main())
