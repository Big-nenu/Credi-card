"""
Utilidades compartidas del proyecto Credi-card.

Contiene rutas, lectura de configuracion, normalizacion y parseo de montos
y fechas del formato que usa Banorte / RappiCard en los estados de cuenta.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

# --------------------------------------------------------------------- rutas
RAIZ = Path(__file__).resolve().parent.parent
DIR_DATA = RAIZ / "data"
DIR_CORTES = DIR_DATA / "cortes"
DIR_CONFIG = RAIZ / "config"
DIR_REPORTES = RAIZ / "reportes"

ARCHIVO_PRESUPUESTO = DIR_CONFIG / "presupuesto.json"
ARCHIVO_PRESUPUESTO_EJEMPLO = DIR_CONFIG / "presupuesto.ejemplo.json"
ARCHIVO_CATEGORIAS = DIR_CONFIG / "categorias.json"
ARCHIVO_ESTADO_ACTUAL = DIR_DATA / "estado_actual.json"
ARCHIVO_ESTADO_ACTUAL_EJEMPLO = DIR_DATA / "estado_actual.ejemplo.json"

# --------------------------------------------------------------------- meses
MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}

MESES_NOMBRE = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
    12: "Diciembre",
}

# ------------------------------------------------------------------ patrones
RE_MONTO = re.compile(r"^[+\-]?\$[\d,]+\.\d{2}$")
RE_FECHA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_FECHA_ES = re.compile(r"^(\d{1,2})-([a-z]{3,4})-(\d{4})$")
RE_FECHA_ES_LARGA = re.compile(r"(\d{1,2})-([a-z]{3,4})-(\d{4})")
RE_PLAZO = re.compile(r"^(\d{1,2})\s+de\s+(\d{1,2})$")
RE_PORCENTAJE = re.compile(r"^[\d.]+%$")


# ===================================================================== JSON
def cargar_json(ruta: Path, obligatorio: bool = True):
    """Lee un archivo JSON. Si no existe y no es obligatorio, regresa None."""
    if not ruta.exists():
        if obligatorio:
            raise FileNotFoundError(f"No se encontro el archivo de configuracion: {ruta}")
        return None
    with ruta.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def cargar_con_plantilla(ruta_real: Path, ruta_ejemplo: Path) -> tuple[dict | None, bool]:
    """
    Carga un archivo de configuracion con respaldo en su plantilla.

    Devuelve (datos, uso_plantilla). Los archivos con datos personales
    (presupuesto.json, estado_actual.json) estan en .gitignore, asi que al
    clonar el repositorio no existen: en ese caso se usa la plantilla
    '*.ejemplo.json' y se avisa al usuario.
    """
    if ruta_real.exists():
        return cargar_json(ruta_real), False
    return cargar_json(ruta_ejemplo, obligatorio=False), True


def guardar_json(ruta: Path, datos) -> None:
    """Escribe un JSON con formato legible y acentos intactos."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2, default=str)


# ==================================================================== montos
def parsear_monto(texto: str) -> float | None:
    """Convierte '+$1,234.56' / '-$50.00' / '$0.00' a float: 1234.56 o -50.0."""
    if texto is None:
        return None
    limpio = texto.strip().replace("$", "").replace(",", "").replace(" ", "")
    if limpio in ("", "-", "+"):
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def formato_monto(valor: float | None) -> str:
    """Devuelve '$1,234.56'; usa un guion cuando el valor es None."""
    if valor is None:
        return "-"
    return f"${valor:,.2f}"


def porcentaje(parte: float | None, total: float | None, decimales: int = 1) -> str:
    """Devuelve '12.3%' de parte sobre total, tolerando divisiones invalidas."""
    if not parte or not total:
        return "-"
    return f"{(parte / total) * 100:.{decimales}f}%"


# ==================================================================== fechas
def parsear_fecha_es(texto: str) -> date | None:
    """Convierte '10-sept-2026' o '2026-09-10' a date."""
    if not texto:
        return None
    texto = texto.strip()
    if RE_FECHA_ISO.match(texto):
        return datetime.strptime(texto, "%Y-%m-%d").date()
    encontrado = RE_FECHA_ES.match(texto)
    if not encontrado:
        return None
    dia, mes_txt, anio = encontrado.groups()
    mes = MESES_ES.get(mes_txt.lower())
    if mes is None:
        return None
    return date(int(anio), mes, int(dia))


def extraer_fecha_de_texto(texto: str) -> date | None:
    """Busca la primera fecha en formato 'dd-mmm-yyyy' dentro de un texto libre."""
    if not texto:
        return None
    encontrado = RE_FECHA_ES_LARGA.search(texto)
    if not encontrado:
        return None
    return parsear_fecha_es(encontrado.group(0))


def etiqueta_mes(fecha: date | None) -> str:
    """Devuelve '2026-09' para ordenar cronologicamente."""
    if fecha is None:
        return "0000-00"
    return f"{fecha.year:04d}-{fecha.month:02d}"


def etiqueta_mes_larga(fecha: date | None) -> str:
    """Devuelve 'Septiembre 2026'."""
    if fecha is None:
        return "-"
    return f"{MESES_NOMBRE[fecha.month]} {fecha.year}"


def sumar_meses(fecha: date, meses: int) -> date:
    """Suma meses conservando el dia (ajusta a fin de mes cuando aplica)."""
    total = fecha.month - 1 + meses
    anio = fecha.year + total // 12
    mes = total % 12 + 1
    dias_mes = [31, 29 if anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    dia = min(fecha.day, dias_mes[mes - 1])
    return date(anio, mes, dia)


# ===================================================================== texto
def normalizar_espacios(texto: str) -> str:
    """Colapsa saltos de linea y espacios multiples en un solo espacio."""
    return re.sub(r"\s+", " ", texto or "").strip()


MARCAS_FIN_DESCRIPCION = (
    "FECHA DE LA OPERACIÓN",
    "FECHA DE LA OPERACION",
    "DESCRIPCIÓN DEL MOVIMIENTO",
    "DESCRIPCION DEL MOVIMIENTO",
    "COMPRA EN EL EXTRANJERO",
    "TASA DE CONVERSIÓN",
)


def limpiar_descripcion(texto: str) -> str:
    """
    Normaliza la descripcion del comercio.

    Cuando una tabla se parte entre dos paginas, el PDF repite el encabezado de
    columnas y esas etiquetas terminan pegadas a la descripcion anterior; por eso
    se corta el texto en la primera etiqueta de encabezado que aparezca.
    """
    limpio = normalizar_espacios(texto).upper()
    for marca in MARCAS_FIN_DESCRIPCION:
        posicion = limpio.find(marca)
        if posicion != -1:
            limpio = limpio[:posicion]
    limpio = re.sub(r";?\s*RFC:?\s*[A-Z0-9]+", "", limpio)
    return normalizar_espacios(limpio).strip(" ;,")


# ======================================================================= CSV
def escribir_csv(ruta: Path, filas: list[dict], campos: list[str]) -> None:
    """Escribe una lista de diccionarios a CSV en UTF-8 con BOM (abre bien en Excel)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, extrasaction="ignore")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow(fila)


def leer_csv(ruta: Path) -> list[dict]:
    """Lee un CSV escrito por escribir_csv."""
    if not ruta.exists():
        return []
    with ruta.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ============================================================== clasificacion
def compilar_reglas(cfg_categorias: dict) -> list[tuple[re.Pattern, str, str]]:
    """Precompila las reglas de categorias.json (el orden importa)."""
    compiladas = []
    for regla in cfg_categorias.get("reglas", []):
        patron = re.compile(regla["patron"], re.IGNORECASE)
        compiladas.append((patron, regla["categoria"], regla.get("tipo", "Variable")))
    return compiladas


def clasificar(descripcion: str, reglas: list[tuple[re.Pattern, str, str]]) -> tuple[str, str]:
    """Devuelve (categoria, tipo) segun la primera regla que coincida."""
    for patron, categoria, tipo in reglas:
        if patron.search(descripcion or ""):
            return categoria, tipo
    return "Sin clasificar", "Variable"


def detectar_suscripcion(descripcion: str, cfg_categorias: dict) -> str | None:
    """Devuelve el nombre de la suscripcion recurrente detectada, si aplica."""
    for item in cfg_categorias.get("suscripciones_patrones", []):
        if re.search(item["patron"], descripcion or "", re.IGNORECASE):
            return item["nombre"]
    return None


# ================================================================ consola
class Consola:
    """Salida a consola con color si 'rich' esta disponible, texto plano si no."""

    def __init__(self) -> None:
        try:
            from rich.console import Console as RichConsole
            # markup=False evita que textos como "[Fijo]" se pierdan como etiquetas
            # y soft_wrap=True evita que rich corte las lineas a media tabla.
            self._c = RichConsole(markup=False, highlight=False, soft_wrap=True)
            self._rich = True
        except ImportError:
            self._c = None
            self._rich = False

    def titulo(self, texto: str) -> None:
        # Se usa un banner ASCII a proposito: los caracteres de caja no se
        # renderizan bien en consolas de Windows con codepage distinto a UTF-8.
        ancho = 78
        print("\n" + "=" * ancho)
        print(texto)
        print("=" * ancho)

    def ok(self, texto: str) -> None:
        if self._rich:
            self._c.print("OK  " + texto, style="green")
        else:
            print(f"[OK] {texto}")

    def info(self, texto: str) -> None:
        if self._rich:
            self._c.print(texto)
        else:
            print(texto)

    def alerta(self, texto: str) -> None:
        if self._rich:
            self._c.print("!!  " + texto, style="bold red")
        else:
            print(f"ALERTA: {texto}")

    def aviso(self, texto: str) -> None:
        if self._rich:
            self._c.print("*   " + texto, style="yellow")
        else:
            print(f"AVISO: {texto}")
