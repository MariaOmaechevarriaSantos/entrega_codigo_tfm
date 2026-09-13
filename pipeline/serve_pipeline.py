"""
Registra pipeline.run_pipeline.run como deployment de Prefect (Cloud o servidor
local, según el perfil activo — ver `prefect profile inspect`) y se queda
escuchando ejecuciones lanzadas desde la UI ("Run" en la pestaña
Deployments). No hace falta work pool ni worker aparte: `flow.serve()` los
sustituye para uso local — el propio proceso de este script ejecuta el
flow cuando se dispara desde la UI.

Uso:
    python pipeline/serve_pipeline.py

Deja la terminal abierta mientras quieras poder lanzar ejecuciones desde
la UI; cerrarla no afecta a las ejecuciones ya completadas, solo impide
lanzar nuevas hasta que lo vuelvas a arrancar.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.run_pipeline import run

if __name__ == "__main__":
    run.serve(name="tfm-madrid-bomberos")
