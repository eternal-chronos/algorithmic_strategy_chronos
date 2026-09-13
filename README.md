# chronos-strategy

Laboratorio de backtesting para una estrategia operable en **XAUUSD CFD** con
**Pepperstone / cTrader**.

Estado actual: **solo backtest**. El camino previsto es backtest → demo → live, y
cada salto exige trabajo explícito (ver *Hoja de ruta*). La configuración tiene
una puerta que rechaza cualquier `mode` distinto de `backtest`.

## Instalación

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
source .venv/bin/activate
```

## Uso rápido

```bash
# 1. Datos sintéticos para comprobar que todo funciona de punta a punta
chronos data synth --periods 200000

# 2. Backtest de la estrategia de referencia sobre esos datos
chronos backtest --config config/backtest.synthetic.yaml

# 3. Importar tu histórico real y correr sobre él
chronos data import ruta/al/XAUUSD_M1.csv --tz UTC
chronos backtest --config config/backtest.yaml

# Simular un periodo concreto (vuelve a ejecutar la estrategia sobre ese tramo)
chronos backtest --start 2024-01-01 --end 2024-06-30

# Otros
chronos data info data/processed/XAUUSD_M1.parquet
chronos strategy list
```

### Estructura de mercado (fase 1)

```bash
# Traer el histórico de Dukascopy. Tiene que ser M1: la verificación horaria mide
# el rango medio POR MINUTO y con velas de una hora no distingue 13:30 de 13:00.
chronos data dukascopy -g m1 --from 2018-01-01 --to 2025-12-31

# Verificar la zona horaria del histórico. Obligatorio antes de calcular nada.
chronos structure verify-tz --config config/impulse.yaml

# La ESTRUCTURA —el ID del Diario, de H4, de H1 y de M15 con sus zonas UL, PUL y
# APUL, con la regla del propietario (el UL manda a favor y el ancla en contra)—
# y LAS ENTRADAS del propietario encima (bloque `entries` del YAML): rotura del
# PUL y toque de la zona en H1 con M15 detrás, franja de Nueva York, una al día
# a 1:4. M5 se dibuja con los ID de M15, H1 y H4 detrás. Es lo que hay en `now/`.
# El explorador embebe los tres LEG_START_MODE de R-36 para poder alternarlos
# sobre las mismas velas; `--sin-modos-r36` se ahorra las dos corridas extra.
chronos structure detect --config config/impulse.yaml

# Evidencia de las comprobaciones: esperado y obtenido, lado a lado.
chronos structure evidencia --config config/impulse.yaml

# Zonas UL y OB de cada impulso (fase 2.0). Sólo detección y dibujo.
chronos structure zonas --config config/impulse.yaml

# Fase 2.1: la zona decide la rotura del ID en vez de la línea. Ejecuta el módulo
# entero con las dos reglas sobre las mismas velas y las compara. Antes de nada
# verifica que con `break_by_zone: false` sale la línea base exacta; si no sale,
# para y avisa.
chronos structure rotura-por-zona --config config/impulse.yaml
```

El módulo de estructura detecta **el impulso dominante del Diario, de H4, de H1
y de M15** con sus tres zonas —el UL a favor y el PUL o el APUL en contra— y,
desde el 2026-09-13, **las entradas del propietario** en su primera forma
(`src/chronos/application/entries/trades.py`): el Diario manda sobre H4 y H4
sobre H1, se entra en H1 por rotura del PUL o por toque de la zona con M15
confirmando, sólo de las 02:00 a las 11:59 de Nueva York, una al día y a 1:4.
**Sin medición de rentabilidad**: se audita operación a operación en el
explorador. M5 no lleva detector y sobre ella se dibujan los de M15, H1 y H4.
Ver [`docs/MODULO_1_IMPULSO_DOMINANTE.md`](docs/MODULO_1_IMPULSO_DOMINANTE.md) y
[`docs/MODULO_2_ZONAS.md`](docs/MODULO_2_ZONAS.md).

Cada corrida de estructura deja una carpeta en `reports/` con `reporte.txt`,
`explorador.html`, los CSV de impulsos, roturas, contactos, estado, zonas,
entradas y búsqueda, y `run.json`. La auditoría se hace sobre el explorador, no sobre imágenes: el
motor no emite PNG (`reporting.captures: false`). Las del backtest llevan `report.html`
(el panel), `trades.csv`, `equity.csv`, `metrics.json` y `run.json`.

## El panel

`report.html` es un fichero autocontenido —datos, Plotly y lógica van dentro— que
se abre con doble clic y funciona sin conexión. Permite:

- acotar el **periodo** con presets (año actual, 1 año, 6/3/1 meses) o con fechas
  concretas; métricas, gráficos y tabla se recalculan al instante;
- filtrar por **lado** (largos / cortos) y por **motivo de salida** (stop loss,
  take profit, señal contraria...);
- ordenar la tabla de operaciones por cualquier columna.

Dos cosas distintas, y conviene no confundirlas:

| | Qué hace |
|---|---|
| Filtrar en el panel | Mira lo que ya ocurrió dentro de ese tramo. La estrategia tomó sus decisiones sobre la corrida completa. Instantáneo. |
| `--start` / `--end` | Vuelve a simular: el calentamiento y el estado arrancan de cero dentro del periodo. Es el resultado real de operar solo ese tramo. |

Las métricas del panel se calculan en JavaScript, así que son una segunda
implementación de las fórmulas de Python. `tests/infrastructure/test_dashboard.py`
ejecuta ese JavaScript con node sobre una corrida real y compara las 33 métricas
contra `compute_performance`: si divergen, el test falla.

## Estructura

`infrastructure → application → domain`. Nunca al revés. Las reglas completas
están en `CLAUDE.md`.

```
src/chronos/
├── domain/            Funciones puras sobre arrays y DataFrames
│   ├── bars.py        Contrato canónico de barras (normalizar, validar, resamplear)
│   ├── context.py     BarContext: lo que la estrategia ve en cada barra
│   ├── instrument.py  Ficha del símbolo: toda la aritmética de precio y dinero
│   ├── position.py    Posición viva, invariantes protegidas
│   ├── trade.py       Operación cerrada (registro inmutable)
│   ├── account.py     Balance, equity, margen, stop out
│   ├── quote.py       Bid/ask ejecutables
│   ├── signal.py      Intenciones: EntrySignal / ExitSignal / ModifyStops
│   ├── strategy.py    Contrato Strategy
│   ├── strategies/    Estrategias concretas + indicadores (aquí van las fases)
│   └── structure/     Impulso dominante: máquina de estados y anti-lookahead
├── application/       Puertos y orquestación
│   ├── ports.py       Clock, MarketData.bars(...), Broker
│   ├── run_backtest.py  Corrida completa: barras → motor → métricas
│   ├── backtest/      Motor, calendario, configuración, resultado
│   ├── risk/          Políticas de dimensionamiento
│   ├── metrics/       Métricas de rendimiento
│   └── structure/     Detección de impulsos, verificación horaria, estadística
├── infrastructure/    Adaptadores: brokers, feeds, storage, informes
│   ├── clock.py       SystemClock (real) y FixedClock (simulado)
│   ├── broker/        SimulatedBroker
│   ├── data/          Parquet, CSV, sintético, Dukascopy
│   ├── structure/     Carga de bid/ask y agregación M1 → H4/Diario
│   └── reporting/     Panel HTML, informes y explorador
└── interface/         CLI (punto de composición: aquí se inyectan los puertos)
```

pandas es del dominio: `DataFrame` y `ndarray` son tipos de valor, no
infraestructura. Lo que `domain/` no puede tocar es red, disco, base de datos ni
`datetime.now()`.

## Qué modela el backtest

| Aspecto | Modelo |
|---|---|
| Ejecución | Señal en el cierre de la barra N → fill en la apertura de la N+1 |
| Horquilla | `price_basis: bid` → se compra en ask, se vende en bid |
| Comisión | Por lote y por lado, cargada al balance en apertura y cierre |
| Swap | Puntos por lote y noche, con triple el día configurado |
| Deslizamiento | Puntos en contra en órdenes a mercado y en stops |
| Stop loss | Se dispara con el recorrido de la barra; con hueco, fill en la apertura |
| Take profit | Orden limitada: se llena al nivel, sin deslizamiento |
| Barra que toca SL y TP | `intrabar_priority: worst` → se asume el stop (conservador) |
| Margen | Requerido al abrir; stop out por nivel de margen |
| Riesgo | Pérdida diaria máxima y drawdown máximo cortan la operativa |

Limitaciones conocidas, para no engañarse con los resultados:

- Sin datos intrabar más finos, el orden real de SL/TP dentro de una vela es
  desconocido; por eso la asunción es la pesimista.
- El spread es fijo salvo que el dataset traiga columna `spread`. Los picos de
  spread en noticias y en el cierre diario no se modelan si no están en los datos.
- No hay rechazos del bróker, requotes ni latencia.
- El swap se aplica por día de calendario del servidor, no por sesión exacta.

## Configuración

- `config/backtest.yaml` — cuenta, datos, ejecución, riesgo, estrategia, informe.
- `config/instruments/xauusd.yaml` — ficha del símbolo.
- `config/impulse.yaml` — módulo de impulso dominante (fase 1).

**Verifica la ficha del símbolo contra tu cuenta real** (cTrader → clic derecho en
el símbolo → *Symbol Information*): comisión, swap, apalancamiento y tamaño de
contrato cambian por entidad regulatoria y tipo de cuenta, y mueven el resultado
del backtest más que la mayoría de parámetros de la estrategia.

## Datos

El formato canónico es un parquet con índice `DatetimeIndex` en UTC y columnas
`open, high, low, close, volume` (+ `spread` opcional, en precio). `chronos data
import` acepta CSV de cTrader, MT5 o Dukascopy y hace la conversión.

Los datos sintéticos (`chronos data synth`) existen para ejercitar el motor, no
para evaluar señales: un movimiento browniano no tiene la microestructura del oro.

## Añadir una estrategia (una fase nueva)

```python
# src/chronos/domain/strategies/mi_fase.py
@register("mi_fase")
class MiFase(IndicatorStrategy):
    @property
    def warmup_bars(self) -> int: ...
    def compute_indicators(self, *, open_, high, low, close, volume): ...
    def on_bar(self, ctx): return (EntrySignal(...),)
```

Dos reglas que evitan resultados falsos:

1. Los indicadores se calculan en `compute_indicators` sobre la serie completa,
   pero **nunca con datos futuros** (nada de `shift(-1)`); `on_bar` solo lee el
   índice actual y anteriores.
2. Los stops se expresan como **distancia** (`stop_distance`), no como precio
   absoluto: el fill ocurre en la barra siguiente y un nivel calculado sobre el
   cierre anterior puede quedar del lado equivocado.

`ema_cross` es la referencia verificable, no la estrategia del proyecto.

## Calidad

```bash
make check          # lint + tipos + tests
make smoke          # datos sintéticos + corrida completa de punta a punta
```

Los tests del panel necesitan `node` en el PATH; si no está, se saltan solos.

## Hoja de ruta

1. **Backtest** (actual) — construir y validar la estrategia por fases.
2. **Demo** — adaptador de cTrader (Open API), la misma estrategia sin tocarla,
   contrastando fills reales contra los simulados.
3. **Live** — solo después de que demo confirme el comportamiento del backtest.
