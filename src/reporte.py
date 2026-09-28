"""
reporte.py
==========
Genera un reporte HTML autocontenido (sin internet, sin librerias externas) a
partir de data/analisis.json. Las graficas se dibujan como SVG generado en
Python, asi que el archivo se abre con doble clic en cualquier navegador.

Uso:  python src/reporte.py
Salida: reportes/AAAA-MM_<nivel del semaforo>.html
"""
from __future__ import annotations

import html
import sys
from datetime import date
from pathlib import Path

from common import (
    DIR_DATA,
    DIR_REPORTES,
    Consola,
    cargar_json,
    etiqueta_mes,
    formato_monto,
    porcentaje,
)

COLOR_SEMAFORO = {
    "ROJO": "#dc2626",
    "AMARILLO": "#d97706",
    "VERDE": "#059669",
}

COLOR_TIPO = {
    "Fijo": "#0ea5e9",
    "Variable": "#6366f1",
    "Inversion": "#8b5cf6",
    "Financiero": "#dc2626",
    "Deuda0": "#14b8a6",
    "Pago": "#64748b",
}

CSS = """
:root{--tinta:#0f172a;--suave:#64748b;--linea:#e2e8f0;--fondo:#f8fafc;--blanco:#fff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 60px;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
     color:var(--tinta);background:var(--fondo);line-height:1.5}
header{background:var(--tinta);color:#fff;padding:28px 32px}
header h1{margin:0;font-size:26px;letter-spacing:-.3px}
header p{margin:6px 0 0;color:#cbd5e1;font-size:14px}
main{max-width:1080px;margin:0 auto;padding:24px 16px}
section{background:var(--blanco);border:1px solid var(--linea);border-radius:12px;
        padding:20px 22px;margin:0 0 20px}
h2{margin:0 0 4px;font-size:19px}
h2 .sub{font-size:13px;color:var(--suave);font-weight:400;display:block;margin-top:3px}
h3{margin:22px 0 8px;font-size:15px;color:#334155;text-transform:uppercase;letter-spacing:.4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0 4px}
.kpi{border:1px solid var(--linea);border-radius:10px;padding:14px 16px;background:var(--blanco)}
.kpi .t{font-size:12px;color:var(--suave);text-transform:uppercase;letter-spacing:.5px}
.kpi .v{font-size:22px;font-weight:600;margin-top:6px;letter-spacing:-.5px}
.kpi .n{font-size:12px;color:var(--suave);margin-top:4px}
.kpi.rojo{border-left:4px solid #dc2626}
.kpi.ambar{border-left:4px solid #d97706}
.kpi.verde{border-left:4px solid #059669}
.kpi.azul{border-left:4px solid #6366f1}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--linea)}
th{background:#f1f5f9;font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:#475569}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{font-weight:700;background:#f8fafc;border-top:2px solid var(--tinta)}
.badge{display:inline-block;padding:5px 14px;border-radius:999px;color:#fff;font-weight:700;
       font-size:13px;letter-spacing:.6px}
.aviso{border-left:4px solid #dc2626;background:#fef2f2;padding:11px 14px;border-radius:6px;
       margin:8px 0;font-size:14px}
.aviso.ambar{border-color:#d97706;background:#fffbeb}
.aviso.verde{border-color:#059669;background:#ecfdf5}
.accion{border:1px solid var(--linea);border-radius:10px;padding:14px 16px;margin:10px 0;
        background:var(--blanco)}
.accion .cab{display:flex;justify-content:space-between;gap:14px;align-items:baseline}
.accion .tit{font-weight:600}
.accion .imp{color:#059669;font-weight:700;white-space:nowrap}
.accion p{margin:7px 0 0;font-size:13.5px;color:#334155}
.etq{font-size:12px;fill:#334155}
.val{font-size:12px;fill:#64748b}
.leyenda{font-size:12.5px;color:var(--suave);margin-top:10px}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12.5px}
footer{max-width:1080px;margin:0 auto;padding:0 16px;color:var(--suave);font-size:12.5px}
.pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;
      background:#eef2ff;color:#4338ca;margin-left:6px;vertical-align:middle}
.pill.rojo{background:#fee2e2;color:#b91c1c}
.pill.verde{background:#dcfce7;color:#15803d}
"""


def e(texto) -> str:
    """Escapa texto para HTML."""
    return html.escape(str("" if texto is None else texto))


# ================================================================ helpers
def kpi(titulo: str, valor: str, nota: str = "", clase: str = "") -> str:
    return (f'<div class="kpi {clase}"><div class="t">{e(titulo)}</div>'
            f'<div class="v">{e(valor)}</div><div class="n">{e(nota)}</div></div>')


def tabla(cabeceras: list[tuple[str, bool]], filas: list[list[str]],
          total: list[str] | None = None) -> str:
    """cabeceras = [(texto, es_numerica)]; las celdas ya vienen con HTML escapado."""
    encabezados = "".join(
        f'<th class="{"num" if numerica else ""}">{e(texto)}</th>'
        for texto, numerica in cabeceras)
    cuerpo = []
    for fila in filas:
        celdas = "".join(
            f'<td class="{"num" if cabeceras[i][1] else ""}">{celda}</td>'
            for i, celda in enumerate(fila))
        cuerpo.append(f"<tr>{celdas}</tr>")
    if total:
        celdas = "".join(
            f'<td class="{"num" if cabeceras[i][1] else ""}">{celda}</td>'
            for i, celda in enumerate(total))
        cuerpo.append(f'<tr class="total">{celdas}</tr>')
    return (f"<table><thead><tr>{encabezados}</tr></thead>"
            f'<tbody>{"".join(cuerpo)}</tbody></table>')


def barras_horizontales(filas: list[tuple[str, float, str]], color: str = "#6366f1",
                        ancho: int = 820, alto_fila: int = 24,
                        ancho_etiqueta: int = 200, ancho_valor: int = 120) -> str:
    """filas = [(etiqueta, valor, texto_a_mostrar), ...]"""
    if not filas:
        return ""
    maximo = max(valor for _, valor, _ in filas) or 1.0
    ancho_barra = ancho - ancho_etiqueta - ancho_valor
    alto = len(filas) * alto_fila + 6
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%">']
    for indice, (etiqueta, valor, texto) in enumerate(filas):
        y = indice * alto_fila + 3
        largo = max(1.0, valor / maximo * ancho_barra)
        partes.append(
            f'<text class="etq" x="0" y="{y + 13}">{e(etiqueta[:32])}</text>'
            f'<rect x="{ancho_etiqueta}" y="{y + 2}" width="{round(largo, 1)}" '
            f'height="{alto_fila - 8}" rx="3" fill="{color}"/>'
            f'<text class="val" x="{round(ancho_etiqueta + largo + 7, 1)}" '
            f'y="{y + 13}">{e(texto)}</text>')
    partes.append("</svg>")
    return "".join(partes)


def grafico_columnas(filas: list[tuple], ancho: int = 820, alto: int = 235,
                     color: str = "#6366f1") -> str:
    """filas = [(etiqueta, valor, texto_encima, resaltar), ...]"""
    if not filas:
        return ""
    maximo = max(valor for _, valor, _, _ in filas) or 1.0
    paso = ancho / len(filas)
    ancho_columna = paso * 0.6
    base = alto - 28
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" width="100%">',
              f'<line x1="0" y1="{base}" x2="{ancho}" y2="{base}" '
              f'stroke="#cbd5e1" stroke-width="1"/>']
    for indice, (etiqueta, valor, texto, resaltar) in enumerate(filas):
        x = indice * paso + (paso - ancho_columna) / 2
        altura = max(1.5, valor / maximo * (base - 26))
        y = base - altura
        relleno = "#dc2626" if resaltar else color
        centro = round(x + ancho_columna / 2, 1)
        partes.append(
            f'<rect x="{round(x, 1)}" y="{round(y, 1)}" width="{round(ancho_columna, 1)}" '
            f'height="{round(altura, 1)}" rx="3" fill="{relleno}"/>'
            f'<text class="val" x="{centro}" y="{round(y - 5, 1)}" '
            f'text-anchor="middle">{e(texto)}</text>'
            f'<text class="etq" x="{centro}" y="{base + 15}" '
            f'text-anchor="middle">{e(etiqueta)}</text>')
    partes.append("</svg>")
    return "".join(partes)


# ================================================================ secciones
def seccion_encabezado(a: dict) -> str:
    sem = a["semaforo"]
    capacidad = a["capacidad"]
    planes = a["planes_resumen"]
    uso = a["uso_linea"]
    ahorro = sum(r["impacto_mensual"] for r in a["recomendaciones"])

    clase = {"ROJO": "rojo", "AMARILLO": "ambar", "VERDE": "verde"}[sem["nivel"]]
    tarjeta = (a["presupuesto"].get("tarjetas") or [{}])[0]

    tarjetas = "".join([
        kpi("Semaforo", sem["nivel"], "diagnostico general", clase),
        kpi("Techo de gasto (sano)", formato_monto(capacidad["techo_gasto_tarjeta"]),
            "para no endeudarte mas", "azul"),
        kpi("Techo liquidando contado", formato_monto(capacidad["techo_liquidando_contado"]),
            "plan recomendado", "verde"),
        kpi("Cuota de planes al mes", formato_monto(planes["cuota_total"]),
            f"{planes['planes_con_intereses']} con intereses", "azul"),
        kpi("Saldo a plazos", formato_monto(planes["saldo_real_total"]),
            "ya comprometido", "ambar"),
        kpi("Costo financiero actual", formato_monto(a["costo_contado"].get("costo_mensual", 0.0)),
            "al mes por no liquidar el contado", "rojo"),
        kpi("Uso de la linea", f"{uso.get('uso_pct', 0):.1f}%",
            f"{formato_monto(uso.get('utilizado'))} de {formato_monto(uso.get('limite_credito'))}"),
        kpi("Ahorro potencial", formato_monto(round(ahorro, 2)),
            "sumando las acciones sugeridas", "verde"),
    ])

    bloques = []
    for nivel, _titulo in (("rojo", "Critico"), ("ambar", "Vigilar"), ("verde", "En orden")):
        clave = "amarillo" if nivel == "ambar" else nivel
        clase_aviso = "" if nivel == "rojo" else nivel
        for motivo in sem.get(f"motivos_{clave}", []):
            bloques.append(f'<div class="aviso {clase_aviso}">{e(motivo)}</div>')

    return (f'<section><h2>Diagnostico de hoy'
            f'<span class="sub">{e(tarjeta.get("nombre", "Tarjeta"))} '
            f'{"**** " + str(tarjeta.get("ultimos4", "")) if tarjeta.get("ultimos4") else ""}'
            f' &middot; estado capturado el {e((a.get("estado_actual") or {}).get("fecha_captura", "-"))}'
            f' &middot; datos de {a["categorias"]["num_meses"]} cortes</span></h2>'
            f'<div class="kpis">{tarjetas}</div>{"".join(bloques)}</section>')


def seccion_serie(a: dict) -> str:
    serie = a["serie_cortes"]
    filas = []
    for corte in serie:
        filas.append([
            e(corte["fecha_corte"]),
            e(corte["periodo"]),
            formato_monto(corte["gasto_total"]),
            formato_monto(corte["costo_financiero"]),
            formato_monto(corte["pagos"]),
            formato_monto(corte["saldo_deudor_total"]),
            f'{corte["uso_linea_pct"]:.1f}%',
        ])
    totales = [
        "TOTAL / PROMEDIO",
        f'{len(serie)} cortes',
        formato_monto(round(sum(c["gasto_total"] for c in serie), 2)),
        formato_monto(round(sum(c["costo_financiero"] for c in serie), 2)),
        formato_monto(round(sum(c["pagos"] for c in serie), 2)),
        "",
        "",
    ]
    tabla_html = tabla([
        ("Fecha de corte", False), ("Periodo facturado", False), ("Gasto del periodo", True),
        ("Intereses + IVA", True), ("Pagos hechos", True), ("Saldo deudor", True),
        ("Uso linea", True),
    ], filas, totales)

    columnas = grafico_columnas([
        (c["fecha_corte"][5:], c["gasto_total"], f'{c["gasto_total"] / 1000:.1f}k',
         c["gasto_total"] > a["semaforo"]["gasto_promedio"])
        for c in serie
    ])
    tasa = serie[-1]["tasa_anual_ordinaria"] if serie else 0
    cat = serie[-1]["cat_sin_iva"] if serie else 0

    return (f'<section><h2>Historia mes a mes'
            f'<span class="sub">Lo que realmente gastaste en la tarjeta en cada periodo '
            f'(corte del dia 10). Barras rojas = meses por encima de tu promedio.</span></h2>'
            f'{columnas}{tabla_html}'
            f'<div class="leyenda">Tasa anual ordinaria: <strong>{tasa:.2f}%</strong> &middot; '
            f'CAT sin IVA: <strong>{cat:.1f}%</strong>. En '
            f'{a["categorias"]["num_meses"]} cortes pagaste '
            f'<strong>{formato_monto(round(sum(c["costo_financiero"] for c in serie), 2))}</strong> '
            f'de intereses y comisiones.</div></section>')


def seccion_categorias(a: dict) -> str:
    categorias = a["categorias"]
    meses = categorias["num_meses"] or 1
    por_tipo = sorted(categorias["por_tipo"].items(), key=lambda x: -x[1])

    grafico = barras_horizontales(
        [(tipo, monto, formato_monto(round(monto / meses, 2)) + "/mes") for tipo, monto in por_tipo],
        color="#0f172a", ancho_etiqueta=130, ancho_valor=130)

    filas = [[
        e(c["categoria"]), e(c["tipo"]),
        formato_monto(round(c["monto"] / meses, 2)),
        formato_monto(c["monto"]),
        str(c["movimientos"]),
        f'{c["pct"]:.1f}%',
        e(c["top_comercio"][:26]),
    ] for c in categorias["global"][:14]]

    total = round(sum(c["monto"] for c in categorias["global"]), 2)
    totales = ["TOTAL", "", formato_monto(round(total / meses, 2)), formato_monto(total),
               str(sum(c["movimientos"] for c in categorias["global"])), "100%", ""]

    tabla_cat = tabla([
        ("Categoria", False), ("Tipo", False), ("Promedio/mes", True), ("Total historico", True),
        ("Movs", True), ("% del gasto", True), ("Comercio que mas cobra", False),
    ], filas, totales)

    filas_comercios = [[
        e(c["comercio"][:34]), e(c["categoria"]),
        formato_monto(round(c["monto"] / meses, 2)),
        formato_monto(c["monto"]), str(c["movimientos"]), f'{c["pct"]:.1f}%',
    ] for c in a["top_comercios"][:12]]

    tabla_comercios = tabla([
        ("Comercio", False), ("Categoria", False), ("Promedio/mes", True),
        ("Total", True), ("Movs", True), ("% del total", True),
    ], filas_comercios)

    return (f'<section><h2>A donde se va el dinero'
            f'<span class="sub">Clasificado por tipo de gasto. "Variable" es lo que puedes '
            f'ajustar; "Inversion" es herramienta y educacion, no ocio.</span></h2>'
            f'{grafico}'
            f'<h3>Detalle por categoria</h3>{tabla_cat}'
            f'<h3>Los comercios que mas te cobran</h3>{tabla_comercios}</section>')


def seccion_suscripciones(a: dict) -> str:
    suscripciones = a["suscripciones"]
    if not suscripciones:
        return ""
    ingreso = a["capacidad"]["ingreso_neto"] or 1
    filas = [[
        e(s["servicio"]), formato_monto(s["promedio_mensual"]),
        str(s["meses_detectados"]), formato_monto(s["total_historico"]),
        porcentaje(s["promedio_mensual"], ingreso),
    ] for s in suscripciones]

    total = round(sum(s["promedio_mensual"] for s in suscripciones), 2)
    totales = ["TOTAL", formato_monto(total), "",
               formato_monto(round(sum(s["total_historico"] for s in suscripciones), 2)),
               porcentaje(total, ingreso)]

    tabla_html = tabla([
        ("Servicio", False), ("Promedio/mes", True), ("Meses detectados", True),
        ("Total en el historial", True), ("% del ingreso", True),
    ], filas, totales)

    return (f'<section><h2>Suscripciones y servicios recurrentes'
            f'<span class="sub">Cobros que se repiten cada mes: son el gasto mas facil de '
            f'recortar porque no los notas.</span></h2>{tabla_html}'
            f'<div class="leyenda">Estos {formato_monto(total)}/mes equivalen a '
            f'<strong>{formato_monto(round(total * 12, 2))} al año</strong> &middot; '
            f'<strong>{porcentaje(total, ingreso)}</strong> de tu ingreso.</div></section>')


def seccion_planes(a: dict) -> str:
    planes = a["planes"]
    resumen = a["planes_resumen"]

    filas = []
    for plan in planes:
        es_msi = plan["tipo_plan"] == "MSI 0%"
        etiqueta_plan = (f'{e(plan["etiqueta_plan"])}'
                         f'<span class="pill {"verde" if es_msi else "rojo"}">'
                         f'{"0% interes" if es_msi else str(round(plan["tasa_anual"], 2)) + "%"}</span>')
        filas.append([
            etiqueta_plan,
            e(plan["tipo_gasto"]),
            formato_monto(plan["monto_original"]),
            formato_monto(plan["saldo_real"]),
            formato_monto(plan["pago_requerido"]),
            f'{(plan["numero_pago"] or 0)} de {plan["plazo_total"]}' if plan["numero_pago"] else
            f'0 de {plan["plazo_total"]}',
            str(plan["cuotas_restantes"]),
            e(plan["origen"]),
        ])

    totales = ["TOTAL", "",
               formato_monto(round(sum(p["monto_original"] for p in planes), 2)),
               formato_monto(resumen["saldo_real_total"]),
               formato_monto(resumen["cuota_total"]), "", "", ""]

    tabla_planes = tabla([
        ("Plan", False), ("Tipo de gasto", False), ("Monto original", True),
        ("Saldo real", True), ("Cuota", True), ("Avance", True),
        ("Cuotas rest.", True), ("Origen", False),
    ], filas, totales)

    filas_liberacion = [[
        e(l["etiqueta_plan"][:42]),
        e(l["tipo_plan"]),
        formato_monto(l["cuota"]),
        str(l["cuotas_restantes"]),
        e(l["mes_liberacion"]),
        formato_monto(l["saldo_restante"]),
    ] for l in a["liberaciones"]]

    tabla_liberacion = tabla([
        ("Plan", False), ("Tipo", False), ("Cuota", True), ("Cuotas que faltan", True),
        ("Mes en que se libera", False), ("Saldo pendiente", True),
    ], filas_liberacion)

    return (f'<section><h2>Compromisos a meses sin intereses y diferidos'
            f'<span class="sub">Esto ya esta comprado: no es "gasto del mes", es dinero que '
            f'ya debes. La columna de avance muestra que cuota va y cuantas faltan.</span></h2>'
            f'<div class="kpis">'
            f'{kpi("Cuota MSI al 0%", formato_monto(resumen["cuota_msi"]), "sin costo financiero", "verde")}'
            f'{kpi("Cuota con intereses", formato_monto(resumen["cuota_diferidos"]), "esto es lo que hay que matar", "rojo")}'
            f'{kpi("Saldo total a plazos", formato_monto(resumen["saldo_real_total"]), "capital + proximo pago")}'
            f'{kpi("Intereses de planes / mes", formato_monto(resumen["intereses_mes"]), "solo de los diferidos", "rojo")}'
            f'</div>{tabla_planes}'
            f'<h3>Cuando se te libera cada cuota</h3>{tabla_liberacion}</section>')


def seccion_cronograma(a: dict) -> str:
    cronograma = a["cronograma"]
    if not cronograma:
        return ""

    columnas = grafico_columnas([
        (fila["por_mes"][:3], fila["total"],
         formato_monto(fila["total"]).replace("$", "").replace(".00", ""),
         fila["es_mes_actual"])
        for fila in cronograma[:18]
    ])

    filas = []
    for fila in cronograma[:18]:
        detalle = " + ".join(
            f'{d["etiqueta_plan"][:24]} {formato_monto(d["monto"])}' for d in fila["detalle"])
        filas.append([
            e(fila["por_mes"]),
            formato_monto(fila["total"]),
            str(len(fila["detalle"])),
            e(detalle) if detalle else "-",
        ])

    tabla_html = tabla([
        ("Mes", False), ("Cuotas a pagar", True), ("Planes activos", True), ("Detalle", False),
    ], filas)

    total_periodo = round(sum(f["total"] for f in cronograma[:12]), 2)
    total_todo = round(sum(f["total"] for f in cronograma), 2)

    return (f'<section><h2>Cronograma de cuotas mes por mes'
            f'<span class="sub">Lo que ya esta firmado. La barra roja es el mes en curso. '
            f'Esta es la parte de tu ingreso que NO puedes usar en otra cosa.</span></h2>'
            f'{columnas}'
            f'<div class="leyenda">Proximos 12 meses: '
            f'<strong>{formato_monto(total_periodo)}</strong> comprometidos en cuotas. '
            f'Horizonte completo mostrado (18 meses): '
            f'<strong>{formato_monto(total_todo)}</strong>.</div>'
            f'<h3>Detalle del cronograma</h3>{tabla_html}</section>')


def seccion_capacidad(a: dict) -> str:
    cap = a["capacidad"]
    filas = [
        ["Ingreso neto mensual", f'<strong>{formato_monto(cap["ingreso_neto"])}</strong>'],
        ["Gastos fijos en efectivo (no tarjeta)",
         f'<span style="color:#dc2626">-{formato_monto(cap["gastos_fijos_efectivo"])}</span>'],
        ["Cuota de planes a meses",
         f'<span style="color:#dc2626">-{formato_monto(cap["cuota_planes"])}</span>'],
        ["Meta de ahorro",
         f'<span style="color:#dc2626">-{formato_monto(cap["meta_ahorro"])}</span>'],
    ]
    tabla_cuenta = tabla([("Concepto", False), ("Mensual", True)], filas)

    filas_fijos = [[e(f["concepto"]), f'{formato_monto(f["mensual"])}']
                   for f in a["gastos_fijos_detalle"]]
    filas_fijos.append(["TOTAL fijos en efectivo", formato_monto(cap["gastos_fijos_efectivo"])])
    tabla_fijos = tabla([("Gasto fijo", False), ("Mensual", True)], filas_fijos)

    return (f'<section><h2>Cuanto puedes gastar realmente'
            f'<span class="sub">La resta que importa. Si tu gasto nuevo en tarjeta pasa de '
            f'este numero, el saldo vuelve a subir.</span></h2>'
            f'<div class="kpis">'
            f'{kpi("Techo de gasto (sano)", formato_monto(cap["techo_gasto_tarjeta"]), "sin amortizar deuda", "azul")}'
            f'{kpi("Techo del plan recomendado", formato_monto(cap["techo_liquidando_contado"]), f'liquidando el contado en {cap["amortizacion_meses_objetivo"]} meses', "verde")}'
            f'{kpi("Amortizacion sugerida al contado", formato_monto(cap["amortizacion_sugerida"]), "al mes, adicional al pago normal", "ambar")}'
            f'</div>{tabla_cuenta}'
            f'<h3>Como se arma tu gasto fijo</h3>{tabla_fijos}</section>')


def seccion_proyeccion(a: dict) -> str:
    proy = a.get("proyeccion_proximo_pago")
    if not proy:
        return ""
    costo = a.get("costo_contado") or {}

    filas = [
        ["Compras de contado acumuladas (pendientes de facturar)",
         formato_monto(proy["compras_de_contado_al_dia_de_hoy"])],
        ["Movimientos pendientes de aplicar", formato_monto(proy["movimientos_pendientes"])],
        ["Cuotas de planes a meses del periodo", formato_monto(proy["cuota_de_planes"])],
        ["Intereses de compras diferidas", formato_monto(proy["intereses_de_diferidos"])],
        ["IVA de esos intereses", formato_monto(proy["iva_de_intereses"])],
    ]
    tabla_html = tabla(
        [("Concepto", False), ("Monto", True)], filas,
        ["PAGO ESTIMADO DEL PROXIMO CORTE",
         f'<strong>{formato_monto(proy["pago_estimado_proximo_corte"])}</strong>'])

    aviso_costo = ""
    if costo.get("costo_mensual"):
        aviso_costo = (
            f'<div class="aviso">Si no liquidas el saldo de contado '
            f'<strong>{formato_monto(costo["saldo"])}</strong>, pagas '
            f'<strong>{formato_monto(costo["costo_mensual"])} al mes</strong> solo de intereses '
            f'e IVA ({costo["tasa_anual"]:.0f}% anual + 16% de IVA). Eso equivale a '
            f'<strong>{formato_monto(costo["costo_anualizado"])} al año</strong>.</div>')

    return (f'<section><h2>Lo que viene'
            f'<span class="sub">RappiCard te cobra el saldo completo de compras de contado '
            f'mas las cuotas de meses en cada corte. Esto es lo que va a pedir el proximo.</span></h2>'
            f'<div class="kpis">'
            f'{kpi("Vence ahora", formato_monto(proy["vence_ahora"]), f'limite {proy["fecha_limite_pago"]}', "rojo")}'
            f'{kpi("Proximo corte", formato_monto(proy["pago_estimado_proximo_corte"]), proy["mes_proyectado"], "ambar")}'
            f'{kpi("Suma de los dos", formato_monto(proy["vence_ahora"] + proy["pago_estimado_proximo_corte"]), "en menos de 60 dias", "rojo")}'
            f'</div>{tabla_html}{aviso_costo}'
            f'<div class="leyenda">{e(proy["nota"])}</div></section>')


def seccion_recomendaciones(a: dict) -> str:
    bloques = []
    for rec in a["recomendaciones"]:
        bloques.append(
            f'<div class="accion"><div class="cab">'
            f'<span class="tit">{rec["prioridad"]}. {e(rec["titulo"])}</span>'
            f'<span class="imp">{formato_monto(rec["impacto_mensual"])}/mes</span></div>'
            f'<p>{e(rec["detalle"])}</p></div>')

    total = round(sum(r["impacto_mensual"] for r in a["recomendaciones"]), 2)
    return (f'<section><h2>Plan de accion'
            f'<span class="sub">Ordenado por impacto sobre tu bolsillo. El impacto es cuanto '
            f'liberas al mes si lo haces.</span></h2>{"".join(bloques)}'
            f'<div class="avisos"><div class="aviso verde">'
            f'<strong>Ahorro potencial total: {formato_monto(total)} al mes</strong> '
            f'({formato_monto(round(total * 12, 2))} al año) sin tocar tus herramientas de '
            f'trabajo ni tu despensa.</div></div></section>')


def seccion_validacion(a: dict) -> str:
    filas = [[
        e(c["etiqueta_mes"]),
        formato_monto(c["cargos_extraidos"]),
        formato_monto(c["cargos_esperados"]),
        '<span style="color:#059669">OK</span>' if c["cuadra"] else
        '<span style="color:#dc2626">REVISAR</span>',
    ] for c in a["cuadres"]]

    tabla_html = tabla([
        ("Corte", False), ("Cargos leidos del PDF", True),
        ("Cargos segun el resumen del banco", True), ("Cuadre", False),
    ], filas)

    cortes_ok = sum(1 for c in a["cuadres"] if c["cuadra"])
    texto_cuadres = f"{cortes_ok} de {len(a['cuadres'])}"
    return (f'<section><h2>Validacion de los datos'
            f'<span class="sub">Los montos no se creen: se cuadran contra los totales que el '
            f'propio banco imprime en cada estado de cuenta.</span></h2>'
            f'<div class="kpis">'
            f'{kpi("Cortes procesados", str(len(a["serie_cortes"])), "de mayo a septiembre 2026", "azul")}'
            f'{kpi("Cuadres contra el banco", texto_cuadres, "cargos leidos = cargos del resumen", "verde")}'
            f'{kpi("Movimientos leidos", str(a.get("total_movimientos", 0)), "cargos y abonos en los 5 cortes")}'
            f'{kpi("Planes a meses detectados", str(len(a["planes"])), "incluye los capturados a mano", "azul")}'
            f'</div>{tabla_html}'
            f'<div class="leyenda">Cada corte cierra con la identidad contable del banco: '
            f'<code>adeudo anterior + cargos + capital de meses + intereses + IVA - pagos = '
            f'pago para no generar intereses</code>. Si esa identidad no cuadrara, el corte se '
            f'marcaria como <strong>REVISAR</strong>.</div></section>')


# ================================================================ armado
def construir_html(a: dict) -> str:
    """Ensambla el documento HTML completo."""
    nivel = a["semaforo"]["nivel"]
    tarjeta = (a["presupuesto"].get("tarjetas") or [{}])[0]
    titulo = (f'Control de gastos - {tarjeta.get("nombre", "Tarjeta")} '
              f'****{tarjeta.get("ultimos4", "")}')

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titulo)}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>{e(titulo)}</h1>
  <p>Reporte generado el {e(a["generado"])} &middot; semaforo
     <span class="badge" style="background:{COLOR_SEMAFORO[nivel]}">{nivel}</span></p>
</header>
<main>
{seccion_encabezado(a)}
{seccion_serie(a)}
{seccion_categorias(a)}
{seccion_suscripciones(a)}
{seccion_planes(a)}
{seccion_cronograma(a)}
{seccion_capacidad(a)}
{seccion_proyeccion(a)}
{seccion_recomendaciones(a)}
{seccion_validacion(a)}
</main>
<footer>
  <p>Credi-card &middot; datos extraidos automaticamente de los estados de cuenta en PDF de
     RappiCard (Banorte) y cuadrados contra los totales impresos por el banco.</p>
  <p>Reporte local y autocontenido: no usa internet y no sale de tu computadora.</p>
</footer>
</body>
</html>"""


def main() -> int:
    consola = Consola()
    analisis = cargar_json(DIR_DATA / "analisis.json", obligatorio=False)
    if not analisis:
        consola.alerta("No existe data/analisis.json. Corre primero: python src/analizar.py")
        return 1

    documento = construir_html(analisis)
    nivel = analisis["semaforo"]["nivel"]
    nombre = f"reporte_{etiqueta_mes(date.today())}_{nivel}.html"
    DIR_REPORTES.mkdir(parents=True, exist_ok=True)
    ruta = DIR_REPORTES / nombre
    ruta.write_text(documento, encoding="utf-8")

    consola.titulo("Credi-card - Reporte HTML")
    consola.ok(f"Reporte generado: {ruta}")
    consola.info(f"Semaforo: {nivel}")
    consola.info(f"Tamaño: {len(documento) / 1024:.1f} KB (autocontenido, abrelo con doble clic)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
