# Credi-card · Control de gastos de RappiCard

Herramienta local para entender **a dónde se va tu dinero**, **qué tienes comprometido a
meses** y **cuánto puedes gastar este mes sin seguirte endeudando**.

Lee directamente los estados de cuenta en PDF que descargas de RappiCard (Banorte), los
convierte a CSV, los **valida contra los totales impresos por el propio banco** y produce un
reporte HTML que abres con doble clic.

Todo corre en tu computadora. **No usa internet, no manda datos a ningún lado y no necesita
instalar nada** (usa `PyMuPDF`, `pandas` y `rich`, que ya tienes).

---

## Uso mensual (30 segundos)

1. Descarga el nuevo estado de cuenta de RappiCard y déjalo en `data/cortes/`
   (el nombre da igual: `octubre.pdf`, `2026-10.pdf`, etc.).
2. Corre:

```powershell
python "src/actualizar.py"
```

3. Abre el archivo más reciente de `reportes/` con doble clic.

Eso es todo: re-extrae todos los cortes, recalcula todo y regenera el reporte.

---

## Estructura del proyecto

```
Credi-card/
├── data/
│   ├── cortes/                ← TUS PDFs (entrada). Suelta aquí el nuevo cada mes.
│   ├── cortes.csv             ← salida: 1 fila por corte con los 34 KPIs del banco
│   ├── movimientos.csv        ← salida: 1 fila por cargo / abono (191 movimientos)
│   ├── msi.csv                ← salida: estado vigente de cada plan a meses
│   ├── msi_historial.csv      ← salida: cada plan observado en cada corte
│   ├── incidencias.txt        ← salida: lo que el parser no pudo interpretar
│   ├── analisis.json          ← salida: todo el análisis en un solo JSON
│   └── estado_actual.json     ← TÚ actualizas esto (ver más abajo)
├── config/
│   ├── presupuesto.json       ← TÚ actualizas esto: ingreso, fijos, ahorro, umbrales
│   └── categorias.json        ← TÚ actualizas esto: reglas de clasificación
├── src/
│   ├── common.py              ← utilidades compartidas (montos, fechas, CSV, consola)
│   ├── parse_rappicard.py     ← PDF → CSV con checksum automático
│   ├── analizar.py            ← métricas, MSI, capacidad de gasto, semáforo, recomendaciones
│   ├── reporte.py             ← análisis → reporte HTML autocontenido
│   └── actualizar.py          ← corre los tres pasos anteriores en orden
├── reportes/                  ← salida: reporte_AAAA-MM_<SEMAFORO>.html
├── resumen-mes/               ← tus capturas originales (referencia)
└── gastos-septiembre/         ← tus capturas originales (referencia)
```

---

## Cómo se valida que los datos estén bien

Esto no es un parser que "más o menos" lee el PDF. Hay **tres cuadres independientes** y si
alguno falla, el corte se marca y se reporta:

| Validación | Qué comprueba |
|---|---|
| **Checksum contable** | `adeudo anterior + cargos + capital de meses + intereses + IVA − pagos = pago para no generar intereses` |
| **Cuadre de subtotales** | `saldo cargos regulares + saldo cargos a meses = saldo deudor total` |
| **Cuadre de movimientos** | La suma de los cargos que leyó el parser = el `Total de cargos` impreso en el PDF |

En los 5 cortes de mayo a septiembre 2026 **los tres cuadres pasan al centavo**, y
`data/incidencias.txt` queda vacío.

---

## `data/estado_actual.json` — el archivo que mantiene vivo el reporte

Los PDFs te dicen dónde **estabas** al corte. Este archivo le dice al reporte dónde **estás hoy**.
Cópialo de la app de RappiCard → *Detalle de línea* + *Detalle de pago*. Toma 30 segundos.

```json
{
  "fecha_captura": "2026-09-28",
  "limite_credito": 30000.00,
  "saldo_disponible": 10425.72,
  "utilizado": 19574.28,
  "desglose": {
    "compras_de_contado": 5332.32,
    "movimientos_pendientes": 452.03,
    "deuda_del_mes": 1779.82,
    "compras_diferidas": 1502.41,
    "meses_sin_intereses": 10507.70
  },
  "pago_para_no_generar_intereses": 1779.82,
  "fecha_limite_pago": "2026-09-30"
}
```

### `planes_nuevos`

Aquí van los MSI que **todavía no aparecen en ningún corte procesado** (como la laptop y la
webcam, compradas después del corte del 10 de septiembre). Cuando proceses el corte de
octubre, esos planes ya vendrán del PDF: **bórralos de aquí** para no contarlos dos veces
(el reporte te avisa si detecta el mismo plan por los dos caminos).

---

## `config/presupuesto.json` — tus supuestos

| Campo | Para qué sirve |
|---|---|
| `ingreso_neto_mensual` | La base de todo el cálculo de capacidad de gasto |
| `meta_ahorro_mensual` | Se resta del techo. Sugerencia: súbelo a $1,000 |
| `gastos_fijos_efectivo` | Lo que pagas **fuera** de la tarjeta (luz, camiones, efectivo) |
| `tarjetas[].limite_credito` | Para el porcentaje de uso de línea |
| `umbrales_alerta` | Ajusta cuándo el semáforo pasa a amarillo o rojo |

**Ojo:** si un gasto lo pagas con la tarjeta, **no** lo pongas aquí; ya viene en los PDFs y lo
estarías contando dos veces. Aquí solo van los gastos pagados en efectivo o débito.

---

## `config/categorias.json` — cómo se clasifica cada comercio

- `reglas`: lista de `{ "patron": regex, "categoria": ..., "tipo": ... }`.
  **El orden importa: se aplica la primera que coincida.** Si un comercio cae en
  "Sin clasificar", agrega una regla arriba de las genéricas.
- `tipos`:
  - **Fijo** → recurrente y necesario (luz, internet, despensa, transporte diario)
  - **Variable** → lo que puedes ajustar (delivery, gaming, ropa, compras en línea)
  - **Inversion** → herramienta de trabajo, educación, certificaciones, salud
  - **Financiero** → intereses, IVA de intereses, comisiones, retiros de efectivo
  - **Deuda0** → cuotas de meses sin intereses
  - **Pago** → pagos y abonos (no son gasto)
- `planes`: nombra tus planes a meses. `coincide_con` + `fecha_operacion` = llave exacta.
  Los que marques con `"tipo": "Inversion"` se separan del ocio en el reporte.
- `suscripciones_patrones`: patrones de cobros recurrentes para el análisis de suscripciones.

---

## Qué contestan los reportes

1. **Diagnóstico de hoy** — semáforo + 8 KPIs, con los motivos concretos de cada alerta.
2. **Historia mes a mes** — gráfica de columnas y tabla con gasto, intereses, pagos,
   saldo y uso de línea en cada corte.
3. **A dónde se va el dinero** — por tipo de gasto y por categoría, con el comercio que más
   te cobra en cada una.
4. **Suscripciones y servicios** — lo que se repite cada mes y su peso sobre tu ingreso.
5. **Compromisos a meses** — cada plan con su avance (`1 de 6`), su tasa y su saldo real,
   y **el mes exacto en que se libera**.
6. **Cronograma mes por mes** — cuánto se te va en cuotas hasta septiembre 2027.
7. **Cuánto puedes gastar realmente** — el techo sano y el techo del plan recomendado.
8. **Lo que viene** — el pago que vence ahora y el que va a pedir el próximo corte,
   con el costo mensual de no liquidar.

---

## Limitaciones conocidas

- **Es específico de RappiCard / Banorte.** El parser depende de las etiquetas textuales de
  ese formato. Funciona con los cortes de Banorte; otro banco necesita su propio parseo.
- **Solo funciona con PDF de texto**, no con escaneados o fotos (no hay OCR instalado).
- **Los planes a meses de estados de cuenta futuros no se adivinan**: solo se proyectan los
  que ya aparecieron en un corte o los que capturas en `estado_actual.json`.
- **El cronograma es una estimación**: para las compras diferidas con intereses, la cuota baja
  ligeramente cada mes porque el interés se calcula sobre el saldo. El corte real manda.
- **No es asesoría financiera profesional.** Es una herramienta de contabilidad personal:
  los números son tuyos, las decisiones también.
