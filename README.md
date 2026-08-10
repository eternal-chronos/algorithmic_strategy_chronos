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

Cada corrida deja una carpeta en `reports/` con `report.html` (el panel),
`trades.csv`, `equity.csv`, `metrics.json` y `run.json`.

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

Clean Architecture: las dependencias apuntan siempre hacia adentro.

```
src/chronos/
├── domain/            Reglas de negocio puras (sin numpy, pandas ni frameworks)
│   ├── instrument.py  Ficha del símbolo: toda la aritmética de precio y dinero
│   ├── position.py    Posición viva, invariantes protegidas
│   ├── trade.py       Operación cerrada (registro inmutable)
│   ├── account.py     Balance, equity, margen, stop out
│   ├── signal.py      Intenciones: EntrySignal / ExitSignal / ModifyStops
│   ├── strategy.py    Puerto Strategy
│   └── ports/         MarketView, MarketDataRepository
├── application/       Casos de uso y simulación
│   ├── backtest/      Motor, bróker simulado, contexto, calendario, configuración
│   ├── risk/          Políticas de dimensionamiento
│   ├── metrics/       Métricas de rendimiento
│   └── use_cases/     RunBacktest
├── infrastructure/    Detalles: YAML, parquet/CSV, panel HTML
│   └── reporting/assets/  CSS y JavaScript del panel
├── interface/         CLI (punto de composición)
└── strategies/        Estrategias concretas + indicadores (aquí van las fases)
```

Regla práctica: si un fichero de `domain/` importa pandas, algo se ha colado en
la capa equivocada.

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
# src/chronos/strategies/mi_fase.py
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
