#!/usr/bin/env python3
"""CLI de Raifen Discovery.

Uso:
  python cli.py init-db    crea la base SQLite y siembra los participantes de config/proyecto.yaml
  python cli.py serve      levanta el servidor en http://127.0.0.1:8020
"""
import os
import sys

from app import settings, store


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "init-db":
        store.init_db()
        proyecto = settings.load_proyecto()
        creados = store.sembrar_participantes(proyecto.get("participantes", []))
        print(f"DB lista. Participantes sembrados: {len(creados)}")
        for p in creados:
            print(f"  - {p['nombre']}: {settings.PUBLIC_BASE_URL}/r/{p['token']}")

    elif cmd == "serve":
        import uvicorn
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8020"))
        print(f"Raifen Discovery en http://{host}:{port}  (Ctrl+C para cortar)")
        uvicorn.run("app.api:app", host=host, port=port, reload=False)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
