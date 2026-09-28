"""
analizar.py
===========
Convierte los CSV generados por parse_rappicard.py en decisiones:

  1. Serie historica de los cortes (gasto, intereses, pagos, saldo, uso de linea).
  2. Gasto por categoria y por "tipo" (Fijo / Variable / Inversion / Financiero).
  3. Suscripciones recurrentes detectadas y su peso sobre el ingreso.
  4. Cronograma de pagos a meses, mes por mes, hasta que se liberen.
  5. Capacidad de gasto: cuanto puedes gastar este mes sin romper nada.
  6. Semaforo (verde / amarillo / rojo) con motivos concretos.
  7. Proyeccion del proximo pago que te va a llegar.

Entrada : data/*.csv, config/*.json, data/estado_actual.json
Salida  : data/analisis.json  (+ resumen en consola)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from common import (
    ARCHIVO_CATEGORIAS,
    ARCHIVO_ESTADO_ACTUAL,
    ARCHIVO_ESTADO_ACTUAL_EJEMPLO,
    ARCHIVO_PRESUPUESTO,
    ARCHIVO_PRESUPUESTO_EJEMPLO,
    Consola,
    DIR_DATA,
    cargar_con_plantilla,
    cargar_json,
    detectar_suscripcion,
    etiqueta_mes,
    etiqueta_mes_larga,
    formato_monto,
    guardar_json,
    leer_csv,
    parsear_fecha_es,
    porcentaje,
    sumar_meses,
)

TIPOS_GASTO = ["Fijo", "Variable", "Inversion", "Financiero"]

# Servicios que son recortables (a diferencia de internet o datos moviles).
PLATAFORMAS = {"Netflix", "Disney+", "HBO Max", "Spotify", "Paramount+", "Prime Video"}


def a_float(valor, defecto: float = 0.0) -> float:
    """Convierte de forma tolerante a float (los CSV traen texto)."""
    if valor in (None, "", "-"):
        return defecto
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def a_fecha(valor) -> date | None:
    """Convierte texto 'YYYY-MM-DD' o 'dd-mmm-yyyy' a date."""
    if not valor:
        return None
    texto = str(valor).strip()
    if len(texto) == 10 and texto[4] == "-":
        try:
            return date(int(texto[0:4]), int(texto[5:7]), int(texto[8:10]))
        except ValueError:
            return None
    return parsear_fecha_es(texto)


def redondear(valor: float | None, decimales: int = 2) -> float | None:
    return None if valor is None else round(valor + 0.0, decimales)


# ============================================================== presupuesto
def calcular_fijos(presupuesto: dict) -> tuple[float, list[dict]]:
    """Calcula el total mensual de gastos fijos que NO pasan por la tarjeta."""
    detalle: list[dict] = []
    total = 0.0
    for concepto in presupuesto.get("gastos_fijos_efectivo", []):
        if "monto_semanal" in concepto:
            mensual = concepto["monto_semanal"] * concepto.get("semanas_por_mes", 4.3333)
        else:
            mensual = concepto["monto"] / concepto.get("periodicidad_meses", 1)
        mensual = round(mensual, 2)
        total += mensual
        detalle.append({"concepto": concepto["concepto"], "mensual": mensual})
    return round(total, 2), detalle


# ============================================================ serie cortes
def serie_cortes(cortes: list[dict]) -> list[dict]:
    """Una fila limpia por corte con gasto total, costo financiero y uso de linea."""
    serie = []
    for corte in cortes:
        cargos = a_float(corte.get("cargos_regulares"))
        capital = a_float(corte.get("capital_meses"))
        intereses = a_float(corte.get("intereses_periodo"))
        iva = a_float(corte.get("iva_intereses_comisiones"))
        pagos = a_float(corte.get("pagos_abonos"))
        serie.append({
            "etiqueta_mes": corte.get("etiqueta_mes"),
            "fecha_corte": corte.get("fecha_corte"),
            "periodo": corte.get("periodo"),
            "por_mes": etiqueta_mes_larga(a_fecha(corte.get("fecha_corte"))),
            "fecha_limite_pago": corte.get("fecha_limite_pago"),
            "gasto_total": round(cargos + capital, 2),
            "cargos_regulares": redondear(cargos),
            "capital_meses": redondear(capital),
            "costo_financiero": round(intereses + iva, 2),
            "intereses": redondear(intereses),
            "iva_intereses": redondear(iva),
            "pagos": redondear(pagos),
            "saldo_deudor_total": redondear(a_float(corte.get("saldo_deudor_total"))),
            "saldo_cargos_regulares": redondear(a_float(corte.get("saldo_cargos_regulares"))),
            "saldo_cargos_meses": redondear(a_float(corte.get("saldo_cargos_meses"))),
            "pago_para_no_generar_intereses": redondear(
                a_float(corte.get("pago_para_no_generar_intereses"))),
            "pago_minimo": redondear(a_float(corte.get("pago_minimo"))),
            "limite_credito": redondear(a_float(corte.get("limite_credito"))),
            "credito_disponible": redondear(a_float(corte.get("credito_disponible"))),
            "uso_linea_pct": a_float(corte.get("uso_linea_pct")),
            "tasa_anual_ordinaria": a_float(corte.get("tasa_anual_ordinaria")),
            "cat_sin_iva": a_float(corte.get("cat_sin_iva")),
            "intereses_12m": redondear(a_float(corte.get("intereses_12m"))),
            "checksum_ok": str(corte.get("checksum_ok")).lower() == "true",
        })
    return serie


# ================================================== categorias de gasto
def analizar_categorias(movimientos: list[dict]) -> dict:
    """Agrupa los cargos por categoria y por tipo, global y mes por mes."""
    cargos = [m for m in movimientos if m.get("signo") == "cargo"]
    meses = sorted({m["etiqueta_mes"] for m in cargos})

    def agrupar(filas: list[dict]) -> list[dict]:
        acumulado: dict[tuple, dict] = {}
        for fila in filas:
            clave = (fila["categoria"], fila["tipo"])
            registro = acumulado.setdefault(clave, {"monto": 0.0, "movimientos": 0,
                                                    "comercios": {}})
            monto = a_float(fila["monto"])
            registro["monto"] = round(registro["monto"] + monto, 2)
            registro["movimientos"] += 1
            registro["comercios"][fila["descripcion"]] = round(
                registro["comercios"].get(fila["descripcion"], 0.0) + monto, 2)
        total = sum(r["monto"] for r in acumulado.values()) or 1.0
        salida = []
        for (categoria, tipo), registro in acumulado.items():
            salida.append({
                "categoria": categoria,
                "tipo": tipo,
                "monto": registro["monto"],
                "movimientos": registro["movimientos"],
                "pct": round(registro["monto"] / total * 100, 2),
                "top_comercio": max(registro["comercios"], key=registro["comercios"].get),
            })
        return sorted(salida, key=lambda x: x["monto"], reverse=True)

    por_tipo: dict[str, float] = {}
    for fila in cargos:
        por_tipo[fila["tipo"]] = round(por_tipo.get(fila["tipo"], 0.0) + a_float(fila["monto"]), 2)

    return {
        "por_mes": [
            {"etiqueta_mes": mes, "por_mes": etiqueta_mes_larga(a_fecha(mes + "-10")),
             "categorias": agrupar([m for m in cargos if m["etiqueta_mes"] == mes])}
            for mes in meses
        ],
        "global": agrupar(cargos),
        "por_tipo": por_tipo,
        "num_meses": len(meses),
    }


def top_comercios(movimientos: list[dict], limite: int = 15) -> list[dict]:
    """Los comercios que mas dinero se llevaron en todo el historial."""
    acumulado: dict[str, dict] = {}
    for fila in movimientos:
        if fila.get("signo") != "cargo":
            continue
        registro = acumulado.setdefault(fila["descripcion"], {
            "comercio": fila["descripcion"], "monto": 0.0, "movimientos": 0,
            "categoria": fila["categoria"]})
        registro["monto"] = round(registro["monto"] + a_float(fila["monto"]), 2)
        registro["movimientos"] += 1
    ordenado = sorted(acumulado.values(), key=lambda x: x["monto"], reverse=True)
    total = sum(x["monto"] for x in ordenado) or 1.0
    for fila in ordenado:
        fila["pct"] = round(fila["monto"] / total * 100, 2)
    return ordenado[:limite]


# ================================================== suscripciones recurrentes
def suscripciones_recurrentes(movimientos: list[dict], cfg_categorias: dict) -> list[dict]:
    """Detecta servicios que se cobran todos los meses y estima su costo mensual."""
    acumulado: dict[str, dict] = {}
    for fila in movimientos:
        if fila.get("signo") != "cargo":
            continue
        nombre = detectar_suscripcion(fila["descripcion"], cfg_categorias)
        if not nombre:
            continue
        registro = acumulado.setdefault(nombre, {"servicio": nombre, "total": 0.0,
                                                 "meses": set(), "ultimo_mes": None})
        registro["total"] = round(registro["total"] + a_float(fila["monto"]), 2)
        registro["meses"].add(fila["etiqueta_mes"])
        registro["ultimo_mes"] = max(registro["ultimo_mes"] or "", fila["etiqueta_mes"])

    salida = []
    for registro in acumulado.values():
        num_meses = len(registro["meses"]) or 1
        salida.append({
            "servicio": registro["servicio"],
            "total_historico": registro["total"],
            "meses_detectados": num_meses,
            "promedio_mensual": round(registro["total"] / num_meses, 2),
            "ultimo_mes": registro["ultimo_mes"],
        })
    return sorted(salida, key=lambda x: x["promedio_mensual"], reverse=True)


def cuadre_movimientos(serie: list[dict], movimientos: list[dict]) -> list[dict]:
    """Verifica que la suma de cargos extraidos cuadre con el resumen del corte."""
    resultado = []
    for corte in serie:
        cargos = [m for m in movimientos
                  if m["etiqueta_mes"] == corte["etiqueta_mes"] and m["signo"] == "cargo"]
        abonos = [m for m in movimientos
                  if m["etiqueta_mes"] == corte["etiqueta_mes"] and m["signo"] == "abono"]
        suma_cargos = round(sum(a_float(m["monto"]) for m in cargos), 2)
        suma_abonos = round(sum(a_float(m["monto"]) for m in abonos), 2)
        esperado = round(a_float(corte["cargos_regulares"]) + a_float(corte["iva_intereses"]), 2)
        resultado.append({
            "etiqueta_mes": corte["etiqueta_mes"],
            "cargos_extraidos": suma_cargos,
            "cargos_esperados": esperado,
            "abonos_extraidos": suma_abonos,
            "cuadra": abs(suma_cargos - esperado) <= 0.05,
        })
    return resultado


# =========================================================== planes a meses
CAMPOS_PLAN = ["etiqueta_plan", "tipo_plan", "tipo_gasto", "fecha_operacion",
               "monto_original", "saldo_pendiente", "saldo_real", "pago_requerido",
               "numero_pago", "plazo_total", "cuotas_restantes", "tasa_anual",
               "intereses_periodo", "mes_primer_pago", "origen"]


def consolidar_planes(planes_csv: list[dict], estado_actual: dict | None) -> list[dict]:
    """
    Une los planes leidos de los PDFs con los capturados a mano en
    estado_actual.json (necesarios para los MSI que aun no aparecen en un corte).
    """
    consolidado: list[dict] = []

    for fila in planes_csv:
        fecha_corte = a_fecha(fila.get("fecha_corte"))
        siguiente = sumar_meses(fecha_corte, 1) if fecha_corte else None
        plazo = int(a_float(fila.get("plazo_total")))
        numero = int(a_float(fila.get("numero_pago")))
        consolidado.append({
            "etiqueta_plan": fila.get("etiqueta_plan") or fila.get("descripcion"),
            "tipo_plan": fila.get("tipo_plan"),
            "tipo_gasto": fila.get("tipo_gasto") or "Deuda0",
            "fecha_operacion": fila.get("fecha_operacion"),
            "monto_original": a_float(fila.get("monto_original")),
            "saldo_pendiente": a_float(fila.get("saldo_pendiente")),
            "saldo_real": a_float(fila.get("saldo_real")),
            "pago_requerido": a_float(fila.get("pago_requerido")),
            "numero_pago": numero,
            "plazo_total": plazo,
            "cuotas_restantes": max(plazo - numero, 0),
            "tasa_anual": a_float(fila.get("tasa_anual")),
            "intereses_periodo": a_float(fila.get("intereses_periodo")),
            "mes_primer_pago": etiqueta_mes(siguiente),
            "origen": "corte " + str(fila.get("fecha_corte")),
        })

    for fila in (estado_actual or {}).get("planes_nuevos", []):
        consolidado.append({
            "etiqueta_plan": fila["etiqueta"],
            "tipo_plan": fila.get("tipo_plan", "MSI 0%"),
            "tipo_gasto": fila.get("tipo_gasto", "Deuda0"),
            "fecha_operacion": fila.get("fecha_operacion"),
            "monto_original": a_float(fila.get("monto_original")),
            "saldo_pendiente": a_float(fila.get("monto_original")),
            "saldo_real": a_float(fila.get("monto_original")),
            "pago_requerido": a_float(fila.get("pago_requerido")),
            "numero_pago": 0,
            "plazo_total": int(a_float(fila.get("plazo_total"))),
            "cuotas_restantes": int(a_float(fila.get("cuotas_restantes"))),
            "tasa_anual": a_float(fila.get("tasa_anual")),
            "intereses_periodo": 0.0,
            "mes_primer_pago": fila.get("mes_primer_pago"),
            "origen": "capturado a mano",
        })

    return sorted(consolidado, key=lambda p: str(p["mes_primer_pago"]))


def _indice_mes(clave: str, base: str) -> int:
    """Cuantos meses hay de 'base' a 'clave' (ambos en formato YYYY-MM)."""
    return ((int(clave[:4]) - int(base[:4])) * 12) + (int(clave[5:7]) - int(base[5:7]))


def cronograma_planes(planes: list[dict], meses: int = 18) -> list[dict]:
    """Cuanto se te va cada mes en cuotas de planes a meses, mes por mes."""
    arranques = [p["mes_primer_pago"] for p in planes if p.get("mes_primer_pago")]
    if not arranques:
        return []
    mes_cero = min(arranques)
    hoy = date.today()
    clave_hoy = etiqueta_mes(hoy)

    filas = []
    for i in range(meses):
        fecha = sumar_meses(date(int(mes_cero[:4]), int(mes_cero[5:7]), 1), i)
        clave = etiqueta_mes(fecha)

        detalle = []
        total = 0.0
        for plan in planes:
            if not plan.get("cuotas_restantes") or not plan.get("mes_primer_pago"):
                continue
            indice = _indice_mes(clave, plan["mes_primer_pago"])
            if 0 <= indice < plan["cuotas_restantes"]:
                detalle.append({
                    "etiqueta_plan": plan["etiqueta_plan"],
                    "tipo_plan": plan["tipo_plan"],
                    "tipo_gasto": plan["tipo_gasto"],
                    "monto": plan["pago_requerido"],
                })
                total += plan["pago_requerido"]

        filas.append({
            "etiqueta_mes": clave,
            "por_mes": etiqueta_mes_larga(fecha),
            "total": round(total, 2),
            "es_mes_actual": clave == clave_hoy,
            "detalle": detalle,
        })
    return filas


def resumen_planes(planes: list[dict]) -> dict:
    """Totales del compromiso a plazos: cuota mensual, saldo y costo financiero."""
    cuota_msi = sum(p["pago_requerido"] for p in planes if p["tipo_plan"] == "MSI 0%")
    cuota_diferidos = sum(p["pago_requerido"] for p in planes if p["tipo_plan"] != "MSI 0%")
    intereses_mes = sum(p["intereses_periodo"] for p in planes)
    prioridades = sorted([p for p in planes if p["tipo_plan"] != "MSI 0%"],
                         key=lambda p: -p["tasa_anual"])
    return {
        "cuota_msi": round(cuota_msi, 2),
        "cuota_diferidos": round(cuota_diferidos, 2),
        "cuota_total": round(cuota_msi + cuota_diferidos, 2),
        "saldo_real_total": round(sum(p["saldo_real"] for p in planes), 2),
        "saldo_msi": round(sum(p["saldo_real"] for p in planes if p["tipo_plan"] == "MSI 0%"), 2),
        "intereses_mes": round(intereses_mes, 2),
        "planes_con_intereses": len(prioridades),
        "plan_mas_caro": prioridades[0] if prioridades else None,
    }


def meses_hasta_liberarse(planes: list[dict]) -> list[dict]:
    """Por cada plan: en que mes termina y cuanta cuota mensual te libera."""
    salida = []
    for plan in planes:
        if not plan.get("cuotas_restantes") or not plan.get("mes_primer_pago"):
            continue
        base = date(int(plan["mes_primer_pago"][:4]), int(plan["mes_primer_pago"][5:7]), 1)
        ultimo = sumar_meses(base, plan["cuotas_restantes"] - 1)
        salida.append({
            "etiqueta_plan": plan["etiqueta_plan"],
            "tipo_plan": plan["tipo_plan"],
            "cuota": plan["pago_requerido"],
            "cuotas_restantes": plan["cuotas_restantes"],
            "mes_liberacion": etiqueta_mes_larga(ultimo),
            "mes_liberacion_clave": etiqueta_mes(ultimo),
            "saldo_restante": plan["saldo_real"],
        })
    return sorted(salida, key=lambda x: x["mes_liberacion_clave"])


# ======================================================= capacidad de gasto
def capacidad_gasto(presupuesto: dict, fijos_total: float, planes_resumen: dict,
                    estado_actual: dict | None) -> dict:
    """
    Calcula el techo de gasto nuevo en la tarjeta.

        Techo = Ingreso - Fijos en efectivo - Cuota de planes - Meta de ahorro
    """
    ingreso = a_float(presupuesto.get("ingreso_neto_mensual"))
    ahorro = a_float(presupuesto.get("meta_ahorro_mensual"))
    cuota = a_float(planes_resumen.get("cuota_total"))

    desglose = (estado_actual or {}).get("desglose", {})
    contado = a_float(desglose.get("compras_de_contado"))
    meses_meta = 4
    amortizacion = round(contado / meses_meta, 2) if contado else 0.0
    techo = ingreso - fijos_total - cuota - ahorro

    return {
        "ingreso_neto": ingreso,
        "gastos_fijos_efectivo": fijos_total,
        "cuota_planes": cuota,
        "meta_ahorro": ahorro,
        "techo_gasto_tarjeta": round(techo, 2),
        "techo_sin_ahorro": round(techo + ahorro, 2),
        "contado_a_liquidar": contado,
        "amortizacion_meses_objetivo": meses_meta,
        "amortizacion_sugerida": amortizacion,
        "techo_liquidando_contado": round(techo - amortizacion, 2),
    }


def uso_linea_actual(estado_actual: dict | None, corte: dict | None) -> dict:
    """Uso de linea: prefiere el estado capturado de la app y cae al ultimo corte."""
    if estado_actual:
        limite = a_float(estado_actual.get("limite_credito"))
        utilizado = a_float(estado_actual.get("utilizado"))
        disponible = a_float(estado_actual.get("saldo_disponible"))
        origen = "app (" + str(estado_actual.get("fecha_captura")) + ")"
    elif corte:
        limite = a_float(corte.get("limite_credito"))
        utilizado = a_float(corte.get("saldo_deudor_total"))
        disponible = a_float(corte.get("credito_disponible"))
        origen = "corte " + str(corte.get("fecha_corte"))
    else:
        return {}

    return {
        "origen": origen,
        "limite_credito": limite,
        "utilizado": utilizado,
        "disponible": disponible,
        "uso_pct": round(utilizado / limite * 100, 2) if limite else None,
        "disponible_pct": round(disponible / limite * 100, 2) if limite else None,
    }


def proyeccion_proximo_pago(estado_actual: dict | None, cronograma: list[dict],
                            planes: list[dict]) -> dict | None:
    """Estima cuanto te va a pedir el proximo corte y cuanto vence ahora."""
    if not estado_actual or not cronograma:
        return None
    desglose = estado_actual.get("desglose", {})
    contado = a_float(desglose.get("compras_de_contado"))
    pendientes = a_float(desglose.get("movimientos_pendientes"))
    cuota_planes = a_float(cronograma[0]["total"])
    intereses_diferidos = sum(p["intereses_periodo"] for p in planes
                              if p["tipo_plan"] != "MSI 0%")
    iva_estimado = round(intereses_diferidos * 0.16, 2)
    total = round(contado + pendientes + cuota_planes + intereses_diferidos + iva_estimado, 2)

    return {
        "mes_proyectado": cronograma[0]["por_mes"],
        "compras_de_contado_al_dia_de_hoy": contado,
        "movimientos_pendientes": pendientes,
        "cuota_de_planes": cuota_planes,
        "intereses_de_diferidos": round(intereses_diferidos, 2),
        "iva_de_intereses": iva_estimado,
        "pago_estimado_proximo_corte": total,
        "vence_ahora": a_float(estado_actual.get("pago_para_no_generar_intereses")),
        "fecha_limite_pago": estado_actual.get("fecha_limite_pago"),
        "nota": ("Estimacion: RappiCard cobra el saldo completo de compras de contado "
                 "mas las cuotas de meses en cada corte."),
    }


def costo_de_no_liquidar(saldo_contado: float, tasa_anual: float) -> dict:
    """Cuanto te cuesta al mes dejar vivo el saldo de compras de contado."""
    if not saldo_contado or not tasa_anual:
        return {}
    interes = saldo_contado * (tasa_anual / 100) / 12
    iva = interes * 0.16
    return {
        "saldo": round(saldo_contado, 2),
        "tasa_anual": tasa_anual,
        "interes_mensual": round(interes, 2),
        "iva_mensual": round(iva, 2),
        "costo_mensual": round(interes + iva, 2),
        "costo_anualizado": round((interes + iva) * 12, 2),
    }


# ================================================================== semaforo
def calcular_semaforo(serie: list[dict], planes_resumen: dict, capacidad: dict,
                      uso: dict, presupuesto: dict, suscripciones: list[dict]) -> dict:
    """Evalua el estado general contra los umbrales configurados."""
    umbrales = presupuesto.get("umbrales_alerta", {})
    rojo: list[str] = []
    amarillo: list[str] = []
    verde: list[str] = []

    ultimo = serie[-1] if serie else {}
    ingreso = a_float(capacidad.get("ingreso_neto"))
    techo = a_float(capacidad.get("techo_gasto_tarjeta"))

    costo_financiero = a_float(ultimo.get("costo_financiero"))
    if costo_financiero > 0:
        rojo.append(f"Estas pagando intereses y comisiones: {formato_monto(costo_financiero)} "
                    f"en el ultimo corte.")
    else:
        verde.append("El ultimo corte no genero intereses ni comisiones.")

    pago_minimo = a_float(ultimo.get("pago_minimo"))
    pago_requerido = a_float(ultimo.get("pago_para_no_generar_intereses"))
    if pago_minimo > 0 and abs(pago_minimo - pago_requerido) < 0.01:
        rojo.append("El pago requerido del ultimo corte es igual al pago minimo: "
                    "estas financiando el saldo completo.")

    uso_pct = a_float(uso.get("uso_pct"))
    limite_rojo = umbrales.get("uso_linea_rojo_pct", 50)
    limite_amarillo = umbrales.get("uso_linea_amarillo_pct", 30)
    if uso_pct >= limite_rojo:
        rojo.append(f"Uso de la linea en {uso_pct:.1f}% (umbral rojo: {limite_rojo}%). "
                    f"Utilizado {formato_monto(uso.get('utilizado'))} de "
                    f"{formato_monto(uso.get('limite_credito'))}.")
    elif uso_pct >= limite_amarillo:
        amarillo.append(f"Uso de la linea en {uso_pct:.1f}% (umbral amarillo: {limite_amarillo}%).")

    saldo_planes = a_float(planes_resumen.get("saldo_real_total"))
    disponible = a_float(uso.get("disponible"))
    if disponible and saldo_planes > disponible:
        rojo.append(f"Tus compras a plazos ({formato_monto(saldo_planes)}) ya superan tu "
                    f"credito disponible ({formato_monto(disponible)}).")

    cuota_total = a_float(planes_resumen.get("cuota_total"))
    pct_cuota = (cuota_total / ingreso * 100) if ingreso else 0
    if pct_cuota >= umbrales.get("msi_sobre_ingreso_rojo_pct", 30):
        rojo.append(f"Las cuotas de planes son {pct_cuota:.1f}% de tu ingreso "
                    f"({formato_monto(cuota_total)}/mes).")
    elif pct_cuota >= umbrales.get("msi_sobre_ingreso_amarillo_pct", 15):
        amarillo.append(f"Las cuotas de planes son {pct_cuota:.1f}% de tu ingreso "
                        f"({formato_monto(cuota_total)}/mes).")

    gastos = [a_float(corte.get("gasto_total")) for corte in serie]
    promedio = round(sum(gastos) / len(gastos), 2) if gastos else 0.0
    if techo and promedio > techo:
        rojo.append(f"Tu gasto promedio en tarjeta ({formato_monto(promedio)}) supera tu techo "
                    f"sostenible ({formato_monto(techo)}): estas gastando mas de lo que ganas.")
    elif techo and promedio > techo * 0.9:
        amarillo.append(f"Tu gasto promedio ({formato_monto(promedio)}) esta muy cerca de tu "
                        f"techo ({formato_monto(techo)}).")
    else:
        verde.append(f"Tu gasto promedio ({formato_monto(promedio)}) esta dentro de tu "
                     f"techo ({formato_monto(techo)}).")

    total_subs = sum(a_float(s.get("promedio_mensual")) for s in suscripciones)
    pct_subs = (total_subs / ingreso * 100) if ingreso else 0
    if pct_subs >= umbrales.get("suscripciones_sobre_ingreso_rojo_pct", 10):
        rojo.append(f"Las suscripciones y servicios son {pct_subs:.1f}% de tu ingreso "
                    f"({formato_monto(total_subs)}/mes).")
    elif pct_subs >= umbrales.get("suscripciones_sobre_ingreso_amarillo_pct", 5):
        amarillo.append(f"Las suscripciones y servicios son {pct_subs:.1f}% de tu ingreso "
                        f"({formato_monto(total_subs)}/mes).")

    nivel = "ROJO" if rojo else ("AMARILLO" if amarillo else "VERDE")
    return {
        "nivel": nivel,
        "motivos_rojo": rojo,
        "motivos_amarillo": amarillo,
        "motivos_verde": verde,
        "uso_linea_pct": uso_pct,
        "gasto_promedio": promedio,
        "pct_cuota_sobre_ingreso": round(pct_cuota, 2),
        "pct_suscripciones_sobre_ingreso": round(pct_subs, 2),
        "suscripciones_mensual": round(total_subs, 2),
    }


# ========================================================= recomendaciones
def generar_recomendaciones(categorias: dict, suscripciones: list[dict], planes_resumen: dict,
                            capacidad: dict, costo_contado: dict, serie: list[dict]) -> list[dict]:
    """Arma la lista de acciones concretas, ordenadas por impacto sobre el bolsillo."""
    num_meses = categorias.get("num_meses") or 1
    recomendaciones: list[dict] = []

    if costo_contado.get("costo_mensual"):
        recomendaciones.append({
            "prioridad": 1,
            "titulo": "Llama a RappiCard y pide convertir el saldo de compras de contado a MSI",
            "detalle": (f"Tienes {formato_monto(costo_contado['saldo'])} de 'compras de contado' "
                        f"pagando {costo_contado['tasa_anual']:.0f}% anual. Si el banco te lo "
                        f"convierte a meses sin intereses, ese costo desaparece. "
                        f"Es lo de mayor impacto y cuesta una llamada."),
            "impacto_mensual": costo_contado["costo_mensual"],
        })

    comisiones = [c for c in categorias.get("global", [])
                  if "retiro de efectivo" in c["categoria"].lower()]
    if comisiones:
        monto = round(sum(c["monto"] for c in comisiones) / num_meses, 2)
        recomendaciones.append({
            "prioridad": 2,
            "titulo": "Deja de pagar comisiones por sacar efectivo con terceros",
            "detalle": (f"{comisiones[0]['movimientos']} retiros vía Conekta/Misaldos en el "
                        f"historial. Tu tarjeta no da disposición de efectivo, así que pagas "
                        f"comisión por un servicio que no necesitas: usa tu ingreso en efectivo."),
            "impacto_mensual": monto,
        })

    if planes_resumen.get("planes_con_intereses"):
        plan = planes_resumen.get("plan_mas_caro") or {}
        recomendaciones.append({
            "prioridad": 3,
            "titulo": "Liquida primero las compras diferidas CON intereses",
            "detalle": (f"Tienes {planes_resumen['planes_con_intereses']} plan(es) cobrando "
                        f"{a_float(plan.get('tasa_anual')):.2f}% anual "
                        f"({formato_monto(planes_resumen['intereses_mes'])}/mes de intereses). "
                        f"Nunca aceptes 'diferido con intereses': solo meses sin intereses."),
            "impacto_mensual": round(planes_resumen["intereses_mes"] * 1.16, 2),
        })

    if suscripciones:
        total = sum(s["promedio_mensual"] for s in suscripciones)
        plataformas = [s for s in suscripciones if s["servicio"] in PLATAFORMAS]
        servicios = [s for s in suscripciones if s["servicio"] not in PLATAFORMAS]
        total_plataformas = round(sum(s["promedio_mensual"] for s in plataformas), 2)
        recorte = round(total_plataformas / 2, 2)

        detalle = ("Plataformas de streaming y musica: " + ", ".join(
            f"{s['servicio']} {formato_monto(s['promedio_mensual'])}/mes"
            for s in plataformas) +
            f" = {formato_monto(total_plataformas)}/mes. Quedarte con las que de verdad usas "
            f"libera cerca de la mitad.")
        if servicios:
            detalle += (" Servicios que no conviene tocar sin perder internet o datos: " + ", ".join(
                f"{s['servicio']} {formato_monto(s['promedio_mensual'])}/mes"
                for s in servicios) + ".")

        recomendaciones.append({
            "prioridad": 4,
            "titulo": "Recorta a la mitad tus plataformas de streaming",
            "detalle": detalle,
            "impacto_mensual": recorte,
        })

    variables = [c for c in categorias.get("global", []) if c["tipo"] == "Variable"]
    top = variables[:3]
    if top:
        promedio_top = round(sum(c["monto"] for c in top) / num_meses, 2)
        recomendaciones.append({
            "prioridad": 5,
            "titulo": "Ponle presupuesto fijo a tus 3 categorias mas altas",
            "detalle": (" ; ".join(f"{c['categoria']} {formato_monto(round(c['monto'] / num_meses, 2))}/mes"
                                   for c in top) +
                        f". Sumando {formato_monto(promedio_top)}/mes. Un recorte del 50% "
                        f"es realista y no te cambia la vida."),
            "impacto_mensual": round(promedio_top * 0.5, 2),
        })

    if a_float(capacidad.get("meta_ahorro")) == 0:
        recomendaciones.append({
            "prioridad": 6,
            "titulo": "Ponte una meta de ahorro distinta de cero",
            "detalle": ("Hoy tu meta de ahorro es $0. Sin colchon, cualquier imprevisto "
                        "(como la pantalla de la laptop) se convierte en deuda. Empieza con "
                        "el 10% de tu ingreso."),
            "impacto_mensual": round(a_float(capacidad.get("ingreso_neto")) * 0.10, 2),
        })

    return recomendaciones


# ====================================================================== main
def imprimir_resumen(consola: Consola, serie: list[dict], planes_resumen: dict,
                     cronograma: list[dict], capacidad: dict, uso: dict, proyeccion: dict,
                     costo_contado: dict, suscripciones: list[dict], categorias: dict,
                     comercios: list[dict], semaforo: dict, liberaciones: list[dict],
                     recomendaciones: list[dict], fijos_detalle: list[dict]) -> None:
    """Imprime el tablero completo en consola."""
    consola.titulo("1. Serie historica de cortes")
    consola.info(f"{'Corte':<12}{'Gasto':>12}{'Intereses':>12}{'Pagos':>12}"
                 f"{'Saldo':>12}{'Uso':>8}")
    for corte in serie:
        consola.info(
            f"{corte['fecha_corte']:<12}"
            f"{formato_monto(corte['gasto_total']):>12}"
            f"{formato_monto(corte['costo_financiero']):>12}"
            f"{formato_monto(corte['pagos']):>12}"
            f"{formato_monto(corte['saldo_deudor_total']):>12}"
            f"{corte['uso_linea_pct']:>7.1f}%")
    gastos = [c["gasto_total"] for c in serie]
    consola.info(f"{'PROMEDIO':<12}{formato_monto(sum(gastos) / len(gastos)):>12}")

    consola.titulo("2. A donde se va el dinero (todo el historial)")
    for fila in categorias.get("global", [])[:10]:
        consola.info(f"{formato_monto(round(fila['monto'] / categorias['num_meses'], 2)):>10}/mes  "
                     f"{fila['categoria']:<32} [{fila['tipo']}] {fila['pct']:>5.1f}%")

    consola.titulo("3. Comercios que mas te cobran")
    for fila in comercios[:8]:
        consola.info(f"{formato_monto(round(fila['monto'] / categorias['num_meses'], 2)):>10}/mes  "
                     f"{fila['comercio'][:38]:<38} {fila['movimientos']:>3} mov")

    consola.titulo("4. Suscripciones y servicios recurrentes")
    total_subs = 0.0
    for fila in suscripciones:
        total_subs += fila["promedio_mensual"]
        consola.info(f"{formato_monto(fila['promedio_mensual']):>10}/mes  {fila['servicio']}")
    consola.info(f"{formato_monto(round(total_subs, 2)):>10}/mes  TOTAL suscripciones "
                 f"({porcentaje(total_subs, capacidad['ingreso_neto'])} del ingreso)")

    consola.titulo("5. Planes a meses vigentes")
    consola.info(f"Cuota mensual MSI 0%          : {formato_monto(planes_resumen['cuota_msi'])}")
    consola.info(f"Cuota mensual con intereses   : {formato_monto(planes_resumen['cuota_diferidos'])}")
    consola.info(f"CUOTA TOTAL COMPROMETIDA      : {formato_monto(planes_resumen['cuota_total'])}"
                 f"  ({porcentaje(planes_resumen['cuota_total'], capacidad['ingreso_neto'])} del ingreso)")
    consola.info(f"Saldo pendiente total a meses : {formato_monto(planes_resumen['saldo_real_total'])}")
    consola.info("")
    for fila in liberaciones:
        consola.info(f"  {fila['etiqueta_plan'][:42]:<42} {formato_monto(fila['cuota']):>9}/mes  "
                     f"{fila['cuotas_restantes']:>2} cuotas  termina {fila['mes_liberacion']}")

    consola.titulo("6. Cronograma del proximo año (cuotas de planes)")
    for fila in cronograma[:12]:
        barra = "#" * int(min(fila["total"] / 100, 40))
        consola.info(f"{fila['por_mes']:<18}{formato_monto(fila['total']):>10}  {barra}")

    consola.titulo("7. Capacidad de gasto")
    consola.info(f"Ingreso neto mensual            : {formato_monto(capacidad['ingreso_neto']):>12}")
    for fijo in fijos_detalle:
        consola.info(f"  - {fijo['concepto'][:36]:<36}{formato_monto(fijo['mensual']):>12}")
    consola.info(f"  = Fijos en efectivo           : {formato_monto(capacidad['gastos_fijos_efectivo']):>12}")
    consola.info(f"  - Cuota de planes             : {formato_monto(capacidad['cuota_planes']):>12}")
    consola.info(f"  - Meta de ahorro              : {formato_monto(capacidad['meta_ahorro']):>12}")
    consola.info(f"  = TECHO DE GASTO EN TARJETA   : {formato_monto(capacidad['techo_gasto_tarjeta']):>12}")
    consola.info("")
    consola.info(f"Si ademas liquidas el contado en "
                 f"{capacidad['amortizacion_meses_objetivo']} meses "
                 f"({formato_monto(capacidad['amortizacion_sugerida'])}/mes):")
    consola.info(f"  = TECHO REALISTA              : "
                 f"{formato_monto(capacidad['techo_liquidando_contado']):>12}")

    if costo_contado:
        consola.titulo("8. Costo de no liquidar el saldo de contado")
        consola.info(f"Saldo de contado       : {formato_monto(costo_contado['saldo'])}")
        consola.info(f"Tasa anual             : {costo_contado['tasa_anual']:.2f}%")
        consola.info(f"Interes mensual        : {formato_monto(costo_contado['interes_mensual'])}")
        consola.info(f"+ IVA 16%              : {formato_monto(costo_contado['iva_mensual'])}")
        consola.info(f"= Te cuesta al mes     : {formato_monto(costo_contado['costo_mensual'])}"
                     f"  ({formato_monto(costo_contado['costo_anualizado'])} al año)")

    if proyeccion:
        consola.titulo("9. Lo que viene")
        consola.info(f"Uso de linea ({uso['origen']}): {uso['uso_pct']:.1f}%  "
                     f"utilizado {formato_monto(uso['utilizado'])} de "
                     f"{formato_monto(uso['limite_credito'])}")
        consola.info(f"Vence ahora ({proyeccion['fecha_limite_pago']}): "
                     f"{formato_monto(proyeccion['vence_ahora'])}")
        consola.info(f"Pago estimado del proximo corte ({proyeccion['mes_proyectado']}): "
                     f"{formato_monto(proyeccion['pago_estimado_proximo_corte'])}")
        consola.info(f"   = compras de contado {formato_monto(proyeccion['compras_de_contado_al_dia_de_hoy'])} "
                     f"+ pendientes {formato_monto(proyeccion['movimientos_pendientes'])} "
                     f"+ cuotas {formato_monto(proyeccion['cuota_de_planes'])} "
                     f"+ intereses {formato_monto(proyeccion['intereses_de_diferidos'])}")

    consola.titulo(f"10. Semaforo: {semaforo['nivel']}")
    for motivo in semaforo["motivos_rojo"]:
        consola.alerta(motivo)
    for motivo in semaforo["motivos_amarillo"]:
        consola.aviso(motivo)
    for motivo in semaforo["motivos_verde"]:
        consola.ok(motivo)

    consola.titulo("11. Plan de accion recomendado")
    for fila in recomendaciones:
        consola.info(f"[{fila['prioridad']}] {fila['titulo']}")
        consola.info(f"    impacto estimado: {formato_monto(fila['impacto_mensual'])}/mes")
        consola.info(f"    {fila['detalle']}")
        consola.info("")
    ahorro_total = sum(f["impacto_mensual"] for f in recomendaciones)
    consola.info(f"AHORRO POTENCIAL TOTAL: {formato_monto(round(ahorro_total, 2))}/mes")


def main() -> int:
    consola = Consola()
    cortes_csv = leer_csv(DIR_DATA / "cortes.csv")
    if not cortes_csv:
        consola.alerta("No existe data/cortes.csv. Corre primero: python src/parse_rappicard.py")
        return 1

    movimientos = leer_csv(DIR_DATA / "movimientos.csv")
    planes_csv = leer_csv(DIR_DATA / "msi.csv")
    cfg_categorias = cargar_json(ARCHIVO_CATEGORIAS)
    presupuesto, plantilla_presupuesto = cargar_con_plantilla(
        ARCHIVO_PRESUPUESTO, ARCHIVO_PRESUPUESTO_EJEMPLO)
    estado_actual, plantilla_estado = cargar_con_plantilla(
        ARCHIVO_ESTADO_ACTUAL, ARCHIVO_ESTADO_ACTUAL_EJEMPLO)

    if plantilla_presupuesto:
        consola.aviso("No existe config/presupuesto.json: se usan los valores de la "
                      "plantilla. Copia config/presupuesto.ejemplo.json a "
                      "config/presupuesto.json y pon TUS numeros.")
    if plantilla_estado:
        consola.aviso("No existe data/estado_actual.json: el semaforo se calcula con el "
                      "ultimo corte. Copia data/estado_actual.ejemplo.json a "
                      "data/estado_actual.json y captura los numeros de la app.")
        estado_actual = None

    if not presupuesto:
        consola.alerta("Falta config/presupuesto.json y tambien su plantilla "
                       "config/presupuesto.ejemplo.json. No se puede continuar.")
        return 1

    fijos_total, fijos_detalle = calcular_fijos(presupuesto)
    serie = serie_cortes(cortes_csv)
    categorias = analizar_categorias(movimientos)
    comercios = top_comercios(movimientos)
    suscripciones = suscripciones_recurrentes(movimientos, cfg_categorias)
    cuadres = cuadre_movimientos(serie, movimientos)

    planes = consolidar_planes(planes_csv, estado_actual)
    planes_resumen = resumen_planes(planes)
    cronograma = cronograma_planes(planes, 18)
    liberaciones = meses_hasta_liberarse(planes)

    capacidad = capacidad_gasto(presupuesto, fijos_total, planes_resumen, estado_actual)
    uso = uso_linea_actual(estado_actual, serie[-1] if serie else None)
    proyeccion = proyeccion_proximo_pago(estado_actual, cronograma, planes)

    tasa = a_float(serie[-1].get("tasa_anual_ordinaria")) if serie else 0.0
    desglose = (estado_actual or {}).get("desglose", {})
    costo_contado = costo_de_no_liquidar(a_float(desglose.get("compras_de_contado")), tasa)

    semaforo = calcular_semaforo(serie, planes_resumen, capacidad, uso, presupuesto, suscripciones)
    recomendaciones = generar_recomendaciones(categorias, suscripciones, planes_resumen,
                                              capacidad, costo_contado, serie)

    analisis = {
        "generado": date.today().isoformat(),
        "presupuesto": presupuesto,
        "total_movimientos": len(movimientos),
        "gastos_fijos_detalle": fijos_detalle,
        "serie_cortes": serie,
        "categorias": categorias,
        "top_comercios": comercios,
        "suscripciones": suscripciones,
        "cuadres": cuadres,
        "planes": planes,
        "planes_resumen": planes_resumen,
        "cronograma": cronograma,
        "liberaciones": liberaciones,
        "capacidad": capacidad,
        "uso_linea": uso,
        "proyeccion_proximo_pago": proyeccion,
        "costo_contado": costo_contado,
        "semaforo": semaforo,
        "recomendaciones": recomendaciones,
        "estado_actual": estado_actual,
    }
    guardar_json(DIR_DATA / "analisis.json", analisis)

    imprimir_resumen(consola, serie, planes_resumen, cronograma, capacidad, uso, proyeccion,
                     costo_contado, suscripciones, categorias, comercios, semaforo,
                     liberaciones, recomendaciones, fijos_detalle)
    consola.titulo("Listo")
    consola.info(f" -> {DIR_DATA / 'analisis.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
