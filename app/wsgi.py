"""
Punto de entrada WSGI para gunicorn — Fase P5, bloque 8 (empaquetado).

`python app/server.py` carga el grafo en su bloque `__main__` antes de
aceptar conexiones. Un `gunicorn app.server:app` NO pasa por ahí, así que
tendría el grafo sin cargar y /health en 503 hasta la primera petición.

Este módulo cierra ese hueco: al importarse (una vez, en el proceso máster
con `--preload`), carga el grafo; los workers heredan el estado por fork.

    gunicorn --preload -w 1 -b 0.0.0.0:8080 app.wsgi:app
"""
from app.server import app, _cargar_grafo_al_arrancar

_cargar_grafo_al_arrancar()

__all__ = ["app"]
