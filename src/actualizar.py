"""
actualizar.py
=============
Corre el pipeline completo de Credi-card con un solo comando:

    1. parse_rappicard -> lee los PDFs y regenera los CSV
    2. analizar        -> calcula capacidad de gasto, semaforo y recomendaciones
    3. reporte         -> escribe el HTML autocontenido

Uso:  python src/actualizar.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Permite ejecutar este archivo desde cualquier directorio de trabajo.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analizar  # noqa: E402
import parse_rappicard  # noqa: E402
import reporte  # noqa: E402
from common import Consola  # noqa: E402


def main() -> int:
    consola = Consola()
    consola.titulo("Credi-card - actualizacion completa")

    pasos = (
        ("Leyendo los PDFs de data/cortes", parse_rappicard.main),
        ("Analizando gastos, MSI y capacidad de gasto", analizar.main),
        ("Generando el reporte HTML", reporte.main),
    )

    for descripcion, funcion in pasos:
        consola.info(f"> {descripcion}...")
        codigo = funcion()
        if codigo != 0:
            consola.alerta(f"Fallo en el paso: {descripcion}")
            return codigo

    consola.titulo("Todo listo")
    consola.info("Abre el archivo mas reciente de la carpeta reportes/ con doble clic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
