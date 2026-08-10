# Desarrollo de la estrategia por fases

Registro vivo del trabajo. Cada fase es un incremento pequeño, medible y
verificable por separado. No se pasa a la siguiente sin que la anterior esté
implementada, testeada y con un backtest reproducible.

## Cómo se trabaja una fase

1. **Especificación** — qué añade la fase, en términos de reglas de mercado:
   condición de entrada, de salida, filtro o gestión. Una idea por fase.
2. **Implementación** — normalmente un fichero en `src/chronos/strategies/`,
   registrado con `@register("nombre")`. Si la fase evoluciona la anterior, se
   crea una versión nueva en vez de mutar la anterior: así se pueden comparar.
3. **Tests** — al menos uno que fije el comportamiento nuevo con datos
   construidos a mano (`tests/conftest.py::make_frame`), no con datos aleatorios.
4. **Backtest** — `chronos backtest --config config/backtest.yaml -s <nombre>`.
   Se guarda la carpeta del informe y se anota abajo el resultado.
5. **Lectura crítica** — ¿mejora frente a la fase anterior en expectativa por R,
   drawdown y número de operaciones? ¿O solo en el retorno total, que es la
   métrica más fácil de sobreajustar?

## Reglas que se mantienen en todas las fases

- **Sin lookahead.** Los indicadores se calculan sobre la serie completa en
  `compute_indicators`, pero nunca con datos futuros. `on_bar` solo lee el índice
  actual y anteriores.
- **Stops por distancia**, no por precio absoluto: la señal se decide en el cierre
  de la barra N y se ejecuta en la apertura de la N+1.
- **Costes siempre activos** al evaluar. Un backtest sin horquilla, comisión y
  swap no dice nada sobre XAUUSD.
- **Los parámetros van en el YAML**, no incrustados en el código.
- **Una fase, un cambio.** Si una fase toca entrada y salida a la vez, no se sabrá
  cuál de las dos aportó.

## Qué mirar en cada informe

| Métrica | Por qué importa |
|---|---|
| Expectativa en R | Resultado por unidad de riesgo; comparable entre fases y sizings |
| Profit factor | Debe sobrevivir a los costes, no solo al bruto |
| Drawdown máximo y su duración | Lo que determina si la estrategia es operable en real |
| Nº de operaciones | Menos de ~100 y las métricas son ruido |
| Exposición | Tiempo en mercado; a más exposición, más swap |
| Señales descartadas | Aparece en el informe: revela stops inválidos o falta de margen |

## Estado

| Fase | Nombre | Descripción | Estado | Informe |
|---|---|---|---|---|
| 0 | `ema_cross` | Baseline de referencia para validar el motor | Hecha | — |
| 1 | — | *(pendiente de especificación)* | — | — |

## Antes de pensar en demo

- Backtest sobre varios años y varios regímenes de mercado, no solo el último.
- Análisis fuera de muestra: ajustar en un tramo, verificar en otro que no se tocó.
- Sensibilidad de parámetros: si el resultado se desploma al mover un parámetro un
  10%, está sobreajustado.
- Sensibilidad de costes: repetir con el doble de horquilla y comisión.
- Comprobar la ficha del símbolo contra la cuenta real.
