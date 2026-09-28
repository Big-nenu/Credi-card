"""
parse_rappicard.py
==================
Lee los estados de cuenta en PDF de RappiCard (emitidos por Banorte) y genera
los CSV de trabajo del proyecto Credi-card.

Entradas : data/cortes/*.pdf
Salidas  : data/cortes.csv          -> 1 fila por corte (KPIs de la pagina 1)
           data/movimientos.csv     -> 1 fila por cargo / abono
           data/msi.csv             -> ultimo estado conocido de cada plan a meses
           data/msi_historial.csv   -> cada observacion de cada plan, corte por corte
           data/incidencias.txt     -> lineas que el parser no pudo interpretar

El parser se apoya en las etiquetas textuales del propio estado de cuenta
(Limite de credito, Saldo deudor total, RESUMEN DE CARGOS Y ABONOS, etc.) y en
un checksum contable que el banco ya trae impreso:

    adeudo_anterior + cargos_regulares + capital_meses + intereses + iva
        - pagos_abonos  ==  pago_para_no_generar_intereses

Si ese checksum no cuadra, el corte se marca y se reporta como incidencia.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF

from common import (
    ARCHIVO_CATEGORIAS,
    DIR_CORTES,
    DIR_DATA,
    RE_FECHA_ES,
    RE_FECHA_ISO,
    RE_MONTO,
    RE_PLAZO,
    RE_PORCENTAJE,
    Consola,
    cargar_json,
    clasificar,
    compilar_reglas,
    escribir_csv,
    extraer_fecha_de_texto,
    etiqueta_mes,
    limpiar_descripcion,
    normalizar_espacios,
    parsear_fecha_es,
    parsear_monto,
)

# ------------------------------------------------------------------ secciones
SECCION_SIN = "COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES"
SECCION_CON = "COMPRAS Y CARGOS DIFERIDOS A MESES CON INTERESES"
SECCION_REG = "CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)"

TERMINADORES = (
    "Total de cargos",
    "Total de abonos",
    "ATENCION DE QUEJAS",
    "ATENCIÓN DE QUEJAS",
    "INFORMACION DEL COMPROBANTE FISCAL",
    "INFORMACIÓN DEL COMPROBANTE FISCAL",
    "NOTAS ACLARATORIAS",
    "Aclaraciones",
    "Glosario",
)

RUIDO = (
    re.compile(r"^Página \d+ de \d+"),
    re.compile(r"^Número de cuenta:"),
    re.compile(r"^Ver notas en la"),
    re.compile(r"^Notas:?$"),
    re.compile(r"^Todos los importes están expresados"),
    re.compile(r"^Tarjeta digital titular"),
    re.compile(r"^Esta tabla te ayuda a entender"),
    re.compile(r"^La Primavera|^Colonia|^Titular/Adicional"),
)


# ======================================================= lectura del PDF
def extraer_lineas(ruta: Path) -> tuple[list[str], str]:
    """Devuelve (lineas limpias, texto normalizado completo) de un PDF."""
    with fitz.open(ruta) as doc:
        crudas: list[str] = []
        for pagina in doc:
            crudas.extend(pagina.get_text().split("\n"))

    # Hay fechas que el PDF parte en dos lineas: "2026-09-" + "01".
    unidas: list[str] = []
    i = 0
    while i < len(crudas):
        linea = crudas[i].strip()
        if (re.fullmatch(r"\d{4}-\d{2}-", linea)
                and i + 1 < len(crudas)
                and re.fullmatch(r"\d{2}", crudas[i + 1].strip())):
            unidas.append(linea + crudas[i + 1].strip())
            i += 2
            continue
        unidas.append(linea)
        i += 1

    limpias = [
        linea for linea in unidas
        if linea and not any(ruido.search(linea) for ruido in RUIDO)
    ]
    return limpias, normalizar_espacios(" ".join(limpias))


def _buscar(patron: str, texto: str) -> str | None:
    """Primera coincidencia del patron (grupo 1) dentro del texto normalizado."""
    encontrado = re.search(patron, texto, re.IGNORECASE)
    return encontrado.group(1).strip() if encontrado else None


def _monto_de(patron: str, texto: str) -> float | None:
    """Igual que _buscar pero devuelve el monto ya convertido a float."""
    return parsear_monto(_buscar(patron, texto) or "")


# ======================================================= KPIs de la pagina 1
ETIQUETAS_RESUMEN = {
    "adeudo_anterior": "Adeudo del periodo anterior",
    "cargos_regulares": "Cargos regulares (no a meses)",
    "capital_meses": "Cargos compras a meses (capital)",
    "intereses_periodo": "Monto de intereses",
    "comisiones_periodo": "Monto de comisiones",
    "iva_intereses_comisiones": "IVA de intereses y comisiones",
    "pagos_abonos": "Pagos y abonos",
    "pago_requerido_resumen": "PAGO PARA NO GENERAR INTERESES",
}


def parsear_kpis(texto: str, archivo: str, incidencias: list[str]) -> dict:
    """Extrae los indicadores del encabezado y del resumen de cargos y abonos."""
    kpis: dict = {"archivo": archivo}

    periodo = _buscar(r"Periodo\s*(\d{1,2}-[a-z]{3,5}-\d{4} al \d{1,2}-[a-z]{3,5}-\d{4})", texto)
    kpis["periodo"] = periodo
    if periodo:
        partes = re.split(r"\s+al\s+", periodo)
        kpis["periodo_inicio"] = parsear_fecha_es(partes[0]) if len(partes) == 2 else None
        kpis["periodo_fin"] = parsear_fecha_es(partes[1]) if len(partes) == 2 else None
    else:
        kpis["periodo_inicio"] = kpis["periodo_fin"] = None

    kpis["fecha_corte"] = extraer_fecha_de_texto(
        _buscar(r"Fecha de corte\s*([\d]{1,2}-[a-z]{3,5}-\d{4})", texto) or "")
    kpis["dias_periodo"] = _buscar(r"Número de días en el periodo\s*(\d+)\s*días", texto)
    kpis["fecha_limite_pago"] = extraer_fecha_de_texto(
        _buscar(r"Fecha límite de pago\d?\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+,\s*\d{1,2}-[a-z]{3,5}-\d{4})", texto) or "")

    kpis["pago_para_no_generar_intereses"] = _monto_de(
        r"Pago para no generar intereses\d?\s*(\$[\d,]+\.\d{2})", texto)
    kpis["pago_minimo_mas_diferidos"] = _monto_de(
        r"Pago mínimo \+ compras y cargos diferidos a meses\d?\s*(\$[\d,]+\.\d{2})", texto)
    kpis["pago_minimo"] = _monto_de(
        r"Pago mínimo\d\s*(\$[\d,]+\.\d{2})", texto)

    kpis["limite_credito"] = _monto_de(r"Límite de crédito\s*(\$[\d,]+\.\d{2})", texto)
    kpis["credito_disponible"] = _monto_de(r"Crédito disponible\s*(\$[\d,]+\.\d{2})", texto)
    kpis["saldo_cargos_regulares"] = _monto_de(r"Saldo cargos regulares:?\s*(\$[\d,]+\.\d{2})", texto)
    kpis["saldo_cargos_meses"] = _monto_de(r"Saldo cargos a meses:?\s*(\$[\d,]+\.\d{2})", texto)
    kpis["saldo_deudor_total"] = _monto_de(r"Saldo deudor total\d{0,2}\s*(\$[\d,]+\.\d{2})", texto)

    for clave, etiqueta in ETIQUETAS_RESUMEN.items():
        patron = re.escape(etiqueta) + r"\d?\s*[=+\-]\s*(\$[\d,]+\.\d{2})"
        kpis[clave] = _monto_de(patron, texto)

    kpis["total_cargos_pdf"] = sum(
        parsear_monto(m) or 0.0
        for m in re.findall(r"Total de cargos\s*\+(\$[\d,]+\.\d{2})", texto))
    kpis["total_abonos_pdf"] = sum(
        parsear_monto(m) or 0.0
        for m in re.findall(r"Total de abonos\s*-(\$[\d,]+\.\d{2})", texto))

    kpis["intereses_12m"] = _monto_de(
        r"Monto de intereses pagados en los últimos 12\s*meses:\d{0,2}\s*(\$[\d,]+\.\d{2})", texto)
    kpis["comisiones_12m"] = _monto_de(
        r"Monto de comisiones totales pagadas en los últimos 12\s*meses:\d{0,2}\s*(\$[\d,]+\.\d{2})", texto)
    kpis["cat_sin_iva"] = _buscar(r"CAT\d{0,2}\s*([\d.]+)%\s*Sin IVA", texto)
    kpis["tasa_anual_ordinaria"] = _buscar(r"TASA DE INTERÉS\s*ANUAL\s*ORDINARIA\s*([\d.]+)%", texto)

    _validar_kpis(kpis, archivo, incidencias)
    return kpis


def _casi_igual(a: float | None, b: float | None, tolerancia: float = 0.02) -> bool:
    """Compara dos montos admitiendo el redondeo a centavos del banco."""
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerancia


def _validar_kpis(kpis: dict, archivo: str, incidencias: list[str]) -> None:
    """Verifica que los KPIs clave existan y que el checksum contable cuadre."""
    obligatorios = ["fecha_corte", "limite_credito", "saldo_deudor_total",
                    "pago_para_no_generar_intereses"]
    faltantes = [campo for campo in obligatorios if kpis.get(campo) in (None, "")]
    if faltantes:
        incidencias.append(f"[{archivo}] KPIs no encontrados: {', '.join(faltantes)}")

    adeudo = kpis.get("adeudo_anterior") or 0.0
    cargos = kpis.get("cargos_regulares") or 0.0
    capital = kpis.get("capital_meses") or 0.0
    intereses = kpis.get("intereses_periodo") or 0.0
    iva = kpis.get("iva_intereses_comisiones") or 0.0
    pagos = kpis.get("pagos_abonos") or 0.0
    esperado = kpis.get("pago_para_no_generar_intereses")

    calculado = round(adeudo + cargos + capital + intereses + iva - pagos, 2)
    kpis["checksum_calculado"] = calculado

    if not _casi_igual(calculado, esperado):
        kpis["checksum_ok"] = False
        incidencias.append(
            f"[{archivo}] CHECKSUM no cuadra: resumen calculado ${calculado:,.2f} "
            f"vs 'Pago para no generar intereses' ${(esperado or 0):,.2f}"
        )
    else:
        kpis["checksum_ok"] = True

    # En RappiCard el "pago para no generar intereses" cubre SOLO los cargos
    # regulares; el saldo de compras a meses va por separado.
    regulares = kpis.get("saldo_cargos_regulares")
    meses = kpis.get("saldo_cargos_meses")
    total = kpis.get("saldo_deudor_total")

    if not _casi_igual(esperado, regulares):
        incidencias.append(
            f"[{archivo}] 'Pago para no generar intereses' ${(esperado or 0):,.2f} "
            f"no coincide con 'Saldo cargos regulares' ${(regulares or 0):,.2f}"
        )

    if regulares is not None and meses is not None and total is not None:
        if not _casi_igual(round(regulares + meses, 2), total):
            incidencias.append(
                f"[{archivo}] 'Saldo cargos regulares' + 'Saldo cargos a meses' = "
                f"${regulares + meses:,.2f} pero 'Saldo deudor total' es ${total:,.2f}"
            )

    if kpis.get("limite_credito") and kpis.get("saldo_deudor_total"):
        kpis["uso_linea_pct"] = round(
            kpis["saldo_deudor_total"] / kpis["limite_credito"] * 100, 2)
    else:
        kpis["uso_linea_pct"] = None


# ================================================= segmentacion por secciones
def segmentar(lineas: list[str]) -> list[tuple[str, list[str]]]:
    """Divide las lineas del PDF en bloques por seccion de movimientos."""
    segmentos: list[tuple[str, list[str]]] = []
    seccion: str | None = None
    buffer: list[str] = []

    def cerrar() -> None:
        if seccion and buffer:
            segmentos.append((seccion, list(buffer)))

    for linea in lineas:
        if any(linea.startswith(t) or t in linea for t in TERMINADORES):
            cerrar()
            seccion, buffer = None, []
            continue

        detectada = None
        if linea.startswith(SECCION_SIN):
            detectada = "MSI_SIN"
        elif linea.startswith(SECCION_CON):
            detectada = "MSI_CON"
        elif linea.startswith(SECCION_REG):
            detectada = "REGULARES"

        if detectada:
            cerrar()
            seccion, buffer = detectada, []
            continue

        if seccion is not None:
            buffer.append(linea)

    cerrar()
    return segmentos


def agrupar_registros(buffer: list[str]) -> list[list[str]]:
    """
    Agrupa las lineas de una seccion en registros.

    OJO: la tabla de movimientos regulares trae DOS columnas de fecha
    (fecha de la operacion y fecha del cargo), por lo que un cambio de fecha
    NO siempre abre un registro nuevo. La regla real es:

        un registro nuevo comienza cuando aparece una fecha Y el registro en
        curso YA tiene al menos un monto (es decir, el anterior esta completo).
    """
    registros: list[list[str]] = []
    actual: list[str] = []

    for linea in buffer:
        es_fecha = bool(RE_FECHA_ISO.match(linea) or RE_FECHA_ES.match(linea))
        ya_tiene_monto = any(RE_MONTO.match(anterior) for anterior in actual)

        if es_fecha and (not actual or ya_tiene_monto):
            if actual:
                registros.append(actual)
            actual = [linea]
        elif actual:
            actual.append(linea)

    if actual:
        registros.append(actual)
    return registros


# ========================================================= parseo de registros
def separar_lineas(registro: list[str]) -> tuple[list, list, list, list, list]:
    """Clasifica cada linea del registro: fechas, montos, plazos, % y descripcion."""
    fechas, montos, plazos, porcentajes, otros = [], [], [], [], []
    for linea in registro:
        if RE_FECHA_ISO.match(linea) or RE_FECHA_ES.match(linea):
            fechas.append(linea)
        elif RE_MONTO.match(linea):
            montos.append(linea)
        elif RE_PLAZO.match(linea):
            plazos.append(linea)
        elif RE_PORCENTAJE.match(linea):
            porcentajes.append(linea)
        else:
            otros.append(linea)
    return fechas, montos, plazos, porcentajes, otros


def _slug(texto: str) -> str:
    """Convierte una descripcion en un identificador estable para el plan a meses."""
    return re.sub(r"[^A-Z0-9]+", "_", texto.upper()).strip("_")[:40]


def _identificador_plan(descripcion: str, fecha_operacion: date | None) -> str:
    """Llave estable del plan: descripcion + fecha de la operacion."""
    fecha_txt = fecha_operacion.isoformat() if fecha_operacion else "SIN_FECHA"
    return f"{_slug(descripcion)}_{fecha_txt}"


def parsear_secciones(segmentos: list[tuple[str, list[str]]], archivo: str,
                      reglas, incidencias: list[str]) -> tuple[list[dict], list[dict]]:
    """Devuelve (movimientos, planes_a_meses) de todas las secciones del corte."""
    movimientos: list[dict] = []
    planes: list[dict] = []

    for seccion, buffer in segmentos:
        for registro in agrupar_registros(buffer):
            fechas, montos, plazos, porcentajes, otros = separar_lineas(registro)
            descripcion = limpiar_descripcion(" ".join(otros))

            if not descripcion or not montos:
                incidencias.append(
                    f"[{archivo}] [{seccion}] registro incompleto: {' | '.join(registro)}")
                continue

            if seccion == "REGULARES":
                fecha_operacion = parsear_fecha_es(fechas[0]) if fechas else None
                fecha_cargo = parsear_fecha_es(fechas[1]) if len(fechas) > 1 else None
                texto_monto = montos[-1]
                monto = parsear_monto(texto_monto)
                if monto is None:
                    incidencias.append(
                        f"[{archivo}] [{seccion}] monto ilegible en: {' | '.join(registro)}")
                    continue
                signo = "cargo" if texto_monto.strip().startswith("+") else "abono"
                categoria, tipo = clasificar(descripcion, reglas)
                movimientos.append({
                    "descripcion": descripcion,
                    "fecha_operacion": fecha_operacion,
                    "fecha_cargo": fecha_cargo,
                    "signo": signo,
                    "monto": abs(monto),
                    "monto_firmado": abs(monto) if signo == "cargo" else -abs(monto),
                    "categoria": categoria,
                    "tipo": tipo,
                })
                continue

            # ---- secciones de compras y cargos diferidos a meses
            valores = [parsear_monto(m) for m in montos]
            fecha_operacion = parsear_fecha_es(fechas[0]) if fechas else None
            numero_pago = plazo_total = None
            if plazos:
                coincidencia = RE_PLAZO.match(plazos[0])
                if coincidencia:
                    numero_pago = int(coincidencia.group(1))
                    plazo_total = int(coincidencia.group(2))
            tasa = float(porcentajes[0].replace("%", "")) if porcentajes else None

            if seccion == "MSI_SIN":
                esperados = 3
                intereses = iva = 0.0
            else:
                esperados = 5
                intereses = iva = 0.0
            if len(valores) != esperados:
                incidencias.append(
                    f"[{archivo}] [{seccion}] se esperaban {esperados} montos y llegaron "
                    f"{len(valores)} en: {' | '.join(registro)}")
                continue

            monto_original = valores[0]
            saldo_pendiente = valores[1]
            if seccion == "MSI_SIN":
                pago_requerido = valores[2]
            else:
                intereses, iva, pago_requerido = valores[2], valores[3], valores[4]

            planes.append({
                "msi_id": _identificador_plan(descripcion, fecha_operacion),
                "descripcion": descripcion,
                "fecha_operacion": fecha_operacion,
                "monto_original": monto_original,
                "saldo_pendiente": saldo_pendiente,
                "intereses_periodo": intereses,
                "iva_intereses": iva,
                "pago_requerido": pago_requerido,
                "numero_pago": numero_pago,
                "plazo_total": plazo_total,
                "tasa_anual": tasa,
                "tipo_plan": "MSI 0%" if seccion == "MSI_SIN" else "Diferido con intereses",
            })

    return movimientos, planes


# ==================================================================== salidas
CAMPOS_CORTES = [
    "etiqueta_mes", "archivo", "periodo", "periodo_inicio", "periodo_fin", "dias_periodo",
    "fecha_corte", "fecha_limite_pago",
    "limite_credito", "credito_disponible", "uso_linea_pct",
    "saldo_cargos_regulares", "saldo_cargos_meses", "saldo_deudor_total",
    "pago_para_no_generar_intereses", "pago_minimo", "pago_minimo_mas_diferidos",
    "adeudo_anterior", "cargos_regulares", "capital_meses", "intereses_periodo",
    "comisiones_periodo", "iva_intereses_comisiones", "pagos_abonos",
    "total_cargos_pdf", "total_abonos_pdf", "suma_cargos_extraidos", "suma_abonos_extraidos",
    "intereses_12m", "comisiones_12m", "cat_sin_iva", "tasa_anual_ordinaria",
    "checksum_calculado", "checksum_ok",
]

CAMPOS_MOVIMIENTOS = [
    "etiqueta_mes", "fecha_corte", "archivo", "fecha_operacion", "fecha_cargo",
    "descripcion", "categoria", "tipo", "signo", "monto", "monto_firmado",
]

CAMPOS_MSI = [
    "msi_id", "etiqueta_plan", "tipo_plan", "tipo_gasto", "descripcion", "fecha_operacion",
    "monto_original", "saldo_pendiente", "saldo_real", "pago_requerido",
    "numero_pago", "plazo_total", "cuotas_pagadas", "cuotas_restantes",
    "intereses_periodo", "iva_intereses", "tasa_anual",
    "etiqueta_mes", "fecha_corte", "archivo_origen",
]


def clasificar_plan(descripcion: str, fecha_operacion: date | None, cfg: dict) -> tuple[str, str]:
    """Devuelve (etiqueta_plan, tipo_gasto) segun la tabla de planes conocidos."""
    fecha_txt = fecha_operacion.isoformat() if fecha_operacion else ""
    for plan in cfg.get("planes", []):
        if plan["coincide_con"].upper() in descripcion.upper():
            if not plan.get("fecha_operacion") or plan["fecha_operacion"] == fecha_txt:
                return plan.get("etiqueta", descripcion), plan.get("tipo", "Deuda0")
    return descripcion, "Deuda0"


def decorar_plan(plan: dict, kpis: dict, archivo: str, cfg_categorias: dict) -> dict:
    """Agrega campos derivados al plan a meses."""
    fila = dict(plan)
    pago = plan.get("numero_pago")
    plazo = plan.get("plazo_total")
    saldo_pendiente = plan.get("saldo_pendiente") or 0.0
    pago_requerido = plan.get("pago_requerido") or 0.0

    fila["saldo_real"] = round(saldo_pendiente + pago_requerido, 2)
    fila["cuotas_pagadas"] = (pago - 1) if pago else None
    fila["cuotas_restantes"] = (plazo - pago + 1) if (pago and plazo) else None
    fila["etiqueta_mes"] = kpis.get("etiqueta_mes")
    fila["fecha_corte"] = kpis.get("fecha_corte")
    fila["archivo_origen"] = archivo

    etiqueta, tipo_gasto = clasificar_plan(fila["descripcion"], fila.get("fecha_operacion"),
                                           cfg_categorias)
    fila["etiqueta_plan"] = etiqueta
    fila["tipo_gasto"] = tipo_gasto
    return fila


# ====================================================================== main
def main() -> int:
    consola = Consola()
    cfg_categorias = cargar_json(ARCHIVO_CATEGORIAS)
    reglas = compilar_reglas(cfg_categorias)

    pdfs = sorted(DIR_CORTES.glob("*.pdf"))
    consola.titulo("Credi-card - Lectura de estados de cuenta RappiCard")
    if not pdfs:
        consola.alerta(f"No hay PDFs en {DIR_CORTES}")
        return 1
    consola.info(f"Encontrados {len(pdfs)} archivos PDF en {DIR_CORTES}")

    filas_cortes: list[dict] = []
    filas_movimientos: list[dict] = []
    historial_planes: list[dict] = []
    incidencias: list[str] = []

    for ruta in pdfs:
        lineas, texto = extraer_lineas(ruta)
        kpis = parsear_kpis(texto, ruta.name, incidencias)
        kpis["etiqueta_mes"] = etiqueta_mes(kpis.get("fecha_corte"))

        segmentos = segmentar(lineas)
        if not segmentos:
            incidencias.append(f"[{ruta.name}] No se detecto ninguna seccion de movimientos.")
        movimientos, planes = parsear_secciones(segmentos, ruta.name, reglas, incidencias)

        cargos = round(sum(m["monto"] for m in movimientos if m["signo"] == "cargo"), 2)
        abonos = round(sum(m["monto"] for m in movimientos if m["signo"] == "abono"), 2)
        kpis["suma_cargos_extraidos"] = cargos
        kpis["suma_abonos_extraidos"] = abonos
        if not _casi_igual(cargos, kpis.get("total_cargos_pdf"), 0.05):
            incidencias.append(
                f"[{ruta.name}] Suma de cargos extraidos ${cargos:,.2f} != "
                f"'Total de cargos' del PDF ${(kpis.get('total_cargos_pdf') or 0):,.2f}")
        if not _casi_igual(abonos, kpis.get("total_abonos_pdf"), 0.05):
            incidencias.append(
                f"[{ruta.name}] Suma de abonos extraidos ${abonos:,.2f} != "
                f"'Total de abonos' del PDF ${(kpis.get('total_abonos_pdf') or 0):,.2f}")

        for mov in movimientos:
            mov.update({
                "etiqueta_mes": kpis["etiqueta_mes"],
                "fecha_corte": kpis.get("fecha_corte"),
                "archivo": ruta.name,
            })
        filas_movimientos.extend(movimientos)

        for plan in planes:
            historial_planes.append(decorar_plan(plan, kpis, ruta.name, cfg_categorias))

        filas_cortes.append(kpis)
        estado = "OK" if kpis.get("checksum_ok") else "REVISAR"
        consola.info(
            f"  {ruta.name:<16} corte {kpis.get('fecha_corte')}  "
            f"saldo {kpis.get('saldo_deudor_total'):>12,.2f}  "
            f"cargos {len([m for m in movimientos if m['signo'] == 'cargo']):>3}  "
            f"planes {len(planes)}  checksum: {estado}"
        )

    # ----------------------------------------------------- ordenar y escribir
    filas_cortes.sort(key=lambda fila: fila["etiqueta_mes"])
    filas_movimientos.sort(key=lambda fila: (fila["etiqueta_mes"], str(fila["fecha_operacion"])))
    historial_planes.sort(key=lambda fila: (fila["etiqueta_mes"], str(fila["descripcion"])))

    ultimo_por_plan: dict[str, dict] = {}
    for fila in historial_planes:
        ultimo_por_plan[fila["msi_id"]] = fila
    planes_vigentes = sorted(ultimo_por_plan.values(),
                             key=lambda fila: str(fila["fecha_operacion"]), reverse=True)

    escribir_csv(DIR_DATA / "cortes.csv", filas_cortes, CAMPOS_CORTES)
    escribir_csv(DIR_DATA / "movimientos.csv", filas_movimientos, CAMPOS_MOVIMIENTOS)
    escribir_csv(DIR_DATA / "msi.csv", planes_vigentes, CAMPOS_MSI)
    escribir_csv(DIR_DATA / "msi_historial.csv", historial_planes, CAMPOS_MSI)

    ruta_incidencias = DIR_DATA / "incidencias.txt"
    with ruta_incidencias.open("w", encoding="utf-8") as fh:
        if incidencias:
            fh.write("\n".join(incidencias) + "\n")
        else:
            fh.write("Sin incidencias: todos los cortes cuadraron contra los totales del PDF.\n")

    # ------------------------------------------------------------- resumen
    consola.titulo("Resultado")
    consola.info(f"Cortes procesados          : {len(filas_cortes)}")
    consola.info(f"Movimientos extraidos      : {len(filas_movimientos)}")
    consola.info(f"Planes a meses detectados  : {len(planes_vigentes)} (vigentes)")
    consola.info(f"Cuadres OK                 : "
                 f"{sum(1 for f in filas_cortes if f.get('checksum_ok'))}/{len(filas_cortes)}")
    consola.info(f" -> data/cortes.csv, data/movimientos.csv, data/msi.csv")
    consola.info(f" -> data/msi_historial.csv, data/incidencias.txt")

    if incidencias:
        consola.aviso(f"Hay {len(incidencias)} incidencia(s). Revisa {ruta_incidencias}")
        for linea in incidencias[:8]:
            consola.info(f"   - {linea}")
    else:
        consola.ok("Todos los cortes cuadraron contra los totales impresos en el PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
