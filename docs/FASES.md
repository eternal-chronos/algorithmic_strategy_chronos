# Desarrollo de la estrategia por fases

Registro vivo del trabajo. Cada fase es un incremento pequeño, medible y
verificable por separado. No se pasa a la siguiente sin que la anterior esté
implementada, testeada y con un backtest reproducible.

## Cómo se trabaja una fase

1. **Especificación** — qué añade la fase, en términos de reglas de mercado:
   condición de entrada, de salida, filtro o gestión. Una idea por fase.
2. **Implementación** — normalmente un fichero en `src/chronos/domain/strategies/`,
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
| 1 | Impulso dominante | Detección del ID en Diario, H4 y H1 (M15 se dibuja con el de H1): rotura → limbo → constitución | Implementada y corrida sobre 2018–2025 de Dukascopy; **pendiente de auditoría visual del propietario** | `chronos structure detect` |
| 2 | — | *(pendiente: último / penúltimo impulso)* | — | — |

### Fase 1 — impulso dominante

No es una estrategia registrada con `@register`: no emite señales. Es un módulo
de estructura de mercado, con su propia configuración (`config/impulse.yaml`) y
su propio subcomando (`chronos structure`). El detalle completo —reglas,
decisiones de borde, parámetros abiertos y procedimiento de auditoría— está en
[`MODULO_1_IMPULSO_DOMINANTE.md`](MODULO_1_IMPULSO_DOMINANTE.md).

Antes de pasar a la fase 2 hacen falta dos cosas del propietario:

1. Cerrar los parámetros abiertos. Quedan dos: `seed_mode` y `doji_break_mode`.
   `anchor_mode`, `leg_start_mode`, `structure_side` y la rejilla de agregación
   ya están decididos y son los valores por defecto del proyecto.
2. La auditoría visual del §7: comparar el explorador con capturas de
   TradingView marcadas **antes** de correr el motor. El explorador trae ahora
   un modo de **auditoría ciega** que apaga todas las capas y sortea una ventana
   con semilla registrada, para que la comparación no esté contaminada.

### Cierre de la fase 1 — lo que ha salido de medirla

**Línea base definitiva: `8e51cd9140c8` · D 401 / H4 1.914 / H1 7.231 impulsos**
(A1 + L1 + sesión `NY_18:00` + bid). Sustituye a las anteriores: `4c299bcf2fba`
con 477 / 2.068 / 7.416 (ancla A2 y día natural en UTC, con las velas fantasma
del domingo dentro) y `368ad3617bd9` con 462 / 2.038 / 7.231 (ya con A1). Cada
corrida publica la tabla de antes y después con las dos columnas calculadas en
el momento, no copiadas.

Tres cosas cambian lo que había que dar por supuesto:

- **La verificación horaria pedía M1 y ahora se hace sobre M1.** El pico de
  volatilidad del histórico completo está en **12:30 UTC**, no en 13:30: el dato
  macro sale a las 8:30 de Nueva York y el año tiene más meses de horario de
  verano que de invierno. Desglosado por régimen, verano da 12:30 y invierno
  13:30, sesenta minutos exactos. El histórico está en UTC, confirmado.
- **R-02 cerrado: `A1_last_counter_body`.** No era cosmético: el ancla es uno de
  los dos niveles que rompen el ID, así que moverla adelanta o retrasa roturas
  en contra y cambia el recuento. A1 produce siempre menos impulsos.
- **La rejilla cerrada: `NY_18:00`, para el diario y para H4.** Es la única que
  reproduce las velas del propietario; los cuatro offsets fijos en UTC dan doce
  horas de apertura a lo largo del año en vez de seis. Se lleva por delante las
  425 velas diarias fantasma del domingo: con el corte anclado, el diario no
  tiene ni una vela corta.
- **Los ID enanos no son un síntoma de lateralización.** De los que tienen rango
  inferior a 0,25 ATR, los que están pegados a un ID con firma de lateralización
  son el 0 % / 3,5 % / 5,2 % (D / H4 / H1), frente al 2,6 % / 5,4 % / 5,2 % del
  resto. Si molestan, son un problema aparte y hará falta una palanca propia.

- **R-36 sigue abierto como limitación conocida.** 189 impulsos (4 en D, 39 en
  H4, 146 en H1) tienen el extremo sobre una vela del color contrario a su
  dirección, y los 189 caen sobre la vela que abre la pierna, la única que la
  máquina adopta sin comprobar el color. A1 arregla el ancla y no toca esto.
  `leg_start_mode` se queda en `L1_actual`: `L2` multiplicaba el defecto por
  diecisiete y `L3` producía impulsos de rango negativo. **No se corrige en la
  fase 1.**

Ninguna de esas mediciones ha tocado la detección, y `chronos structure
evidencia` comprueba la línea base y el `config_hash` con el esperado y el
obtenido a la vista.

## Antes de pensar en demo

- Backtest sobre varios años y varios regímenes de mercado, no solo el último.
- Análisis fuera de muestra: ajustar en un tramo, verificar en otro que no se tocó.
- Sensibilidad de parámetros: si el resultado se desploma al mover un parámetro un
  10%, está sobreajustado.
- Sensibilidad de costes: repetir con el doble de horquilla y comisión.
- Comprobar la ficha del símbolo contra la cuenta real.
