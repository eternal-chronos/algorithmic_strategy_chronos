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
| 2.0 | Zonas UL y OB | Detección y dibujo de las dos zonas de cada ID; la rotura sigue siendo por línea | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure zonas` |
| 2.1 | Rotura por zona | La zona sustituye a la línea como nivel de rotura del ID: el UL a favor, el OB en contra | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure rotura-por-zona` |
| 2.2 | — | *(aparcada: FVG)* | — | — |
| 3.0 | Entradas | La cascada Diario → H4 → H1 → M15 y la **primera medición de resultados** del proyecto | Implementada sobre 2018–2025; **pendiente de auditoría visual y de cerrar los parámetros abiertos** | `chronos structure entradas` |

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

### Fase 2.0 — zonas UL y OB, sólo detección

Tampoco es una estrategia registrada con `@register`: no emite señales. Añade
dos zonas de precio a cada impulso ya detectado y las dibuja. El detalle
completo —reglas, casos límite, garantía anti-lookahead y procedimiento— está en
[`MODULO_2_ZONAS.md`](MODULO_2_ZONAS.md).

La línea base de la fase 1 se conserva intacta y verificada: mismo hash
`8e51cd9140c8` y mismos 401 / 1.914 / 7.231 impulsos con las zonas encendidas y
apagadas. Encenderlas no puede mover un impulso, y el comando lo comprueba antes
de escribir nada.

Lo que salió de medirla:

- **El 5,4 % / 5,0 % / 4,4 % de los ID (D / H4 / H1) nunca llega a tener OB
  confirmado.** Es la cifra que manda: en la fase 2.1 esos impulsos se romperán
  por línea, porque no tienen zona con la que romper.
- **El UL se extiende a la vela siguiente en un 54 % / 44 % / 44 % de los casos.**
  La regla de la vela de margen no es un detalle de borde: afecta a la mitad de
  las zonas.
- **El OB es mayor que el UL en el 90,0 % / 84,7 % / 85,2 %**, como se esperaba,
  porque incluye el cuerpo. Las excepciones apenas se explican por la extensión
  del UL —su porcentaje se mueve poco entre las dos poblaciones— sino porque ahí
  el UL es unas 2,5 veces más alto de lo normal y el OB, más bajo: una vela de
  extremo con mechazo contra una vela de ancla pequeña.
- **El solape entre las dos zonas del mismo ID es del 10,0 % / 7,4 % / 9,2 %, no
  cero.** Son ID cuyo rango mediano es unas tres veces menor que el del resto
  (0,45-0,54 ATR frente a 1,54-1,68): tan cortos que caben dentro de la vela del
  ancla. Es la población enana que C.5 ya contaba, mirada desde otro sitio.
- **Anticipo de la fase 2.1 (sólo medición):** el 44,1 % / 41,9 % / 45,8 % de las
  roturas del histórico cerraron dentro de su zona sin atravesarla entera. La
  diferencia entre las dos clases es grande: 50-55 % en las roturas a favor y
  29-35 % en las de en contra, donde además 17 / 76 / 253 no tienen siquiera OB
  con el que romper.

Antes de pasar a la fase 2.1 hace falta que el propietario audite las capturas y
el explorador y confirme que las zonas se dibujan donde él las dibuja.

### Fase 2.1 — la zona decide la rotura

Primer **cambio de comportamiento** del proyecto desde que se fijó la línea base.
Hasta aquí un ID moría cuando una vela cerraba más allá de una de sus dos líneas.
A partir de `break_by_zone: true` la línea deja de ser el nivel de rotura cuando
existe una zona que la sustituya:

| Lado | Si la zona existe | Si no existe |
|---|---|---|
| A favor (extremo) | manda el **UL**, que existe siempre | — |
| En contra (ancla) | manda el **OB** si está confirmado | manda la línea |

Romper una zona es **cerrar más allá de su borde exterior**, atravesándola
entera: perforarla con mecha y cerrar dentro no rompe, y cerrar dentro tampoco.
Un UL de altura cero tiene los dos bordes en la línea y se comporta exactamente
como ella.

No es un filtro posterior: es un cambio en la máquina de estados. Salvar una
rotura deja el ID vivo, **su extremo sigue extendiéndose** y el UL se recalcula
sobre la vela nueva; el OB no se mueve nunca, porque lo fija la vela del ancla.
Por eso la comparación de abajo son dos ejecuciones completas del módulo sobre
las mismas velas, no una tabla reetiquetada.

**Regresión verificada:** con `break_by_zone: false` el sistema reproduce
`8e51cd9140c8` con D 401 / H4 1.914 / H1 7.231 detectados y 392 / 1.910 / 7.224
publicados. **Línea base nueva: `801951b9cc26` · D 239 / H4 1.214 / H1 4.148
detectados y 233 / 1.211 / 4.141 publicados.**

Lo que salió de medirla (D / H4 / H1, totales de 2018–2025):

- **Los ID enanos se van casi del todo.** ID con rango inferior a 0,25 ATR:
  7 → 1, 57 → 1, 174 → 11. En fracción sobre los ID del periodo, del 1,8 % / 3,0 %
  / 2,4 % al 0,4 % / 0,1 % / 0,3 %. Es la cifra que se perseguía desde la fase 1.
- **El latigazo se reduce a la mitad.** Episodios en que el sesgo se invierte y se
  deshace: 55 → 28, 267 → 177, 1.079 → 630. En ≤ 2 barras, 10 → 3, 37 → 11 y
  175 → 50.
- **Los impulsos viven más y el limbo se encoge.** Duración mediana 2 → 3, 2 → 4 y
  2 → 4 barras; máximo 39 → 65, 180 → 181 y 191 → 227. Barras en limbo, del 36,0 %
  / 31,2 % / 29,6 % al 21,4 % / 19,5 % / 16,9 %.
- **Con las tres temporalidades vigentes se pasa del 36,9 % al 58,9 % del tiempo**,
  y alineadas en la misma dirección, del 12,3 % al 19,0 %.
- **Casi toda rotura es por zona.** Por línea sólo mueren 2 / 7 / 23 ID en todo el
  histórico (0,8 % / 0,6 % / 0,6 % de las roturas), y siempre por el mismo motivo:
  su OB nunca llegó a confirmarse. En el lado a favor no ocurre nunca, porque el
  UL existe siempre.
- **Las zonas solapadas se caen solas**, que era la hipótesis: del 10,0 % / 7,4 % /
  9,2 % de los ID con OB al 1,7 % / 1,2 % / 1,9 % (37 → 4, 135 → 14, 637 → 79).
- **`OVERLAP_PRIORITY` es cosmético sobre estos datos, y hay una razón.** El
  conflicto de evaluación simultánea **no se da ni una vez**. Que dos zonas se
  solapen no basta: para cumplir las dos condiciones hacen falta los dos bordes
  exteriores *invertidos*, y eso es lo contrario de solaparse. En un ID alcista se
  cumple siempre `borde exterior del UL >= extremo > ancla >= borde exterior del
  OB` mientras el rango sea positivo, y con la regla nueva no queda ningún impulso
  de rango no positivo. Los dos órdenes quedan implementados y producen la misma
  historia (hashes `801951b9cc26` y `f4714ba0b488`).
- **R-36 sigue abierto y cambia de puerta.** Extremos sobre vela de color contrario:
  4 → 3, 39 → 17, 146 → 22. Los que quedan ya no entran sólo por el arranque de la
  pierna: 3 / 4 / 9 de ellos entran por la vela que **extendió** el extremo con el
  ID ya vigente, una puerta que en la fase 1 no existía. **No se corrige aquí.**
- **La estimación de la fase 2.0 se quedó corta, como se avisó.** Estimaba un
  44,1 % / 41,9 % / 45,8 % de roturas salvadas; de verdad se han evitado 214 / 938
  / 3.836 velas frente a las 177 / 802 / 3.311 que salían de aplicar aquel
  porcentaje. La estimación miraba cada rotura *en su instante*, con todo lo demás
  igual; al re-ejecutar, cada rotura salvada cambia lo que viene después.

Sigue sin haber señales, entradas, stops ni targets, y `OVERLAP_PRIORITY` es el
único parámetro que esta fase deja abierto. Antes de la fase 2.2 (FVG) hace falta
que el propietario audite `now/fase21/`, empezando por las capturas de roturas
evitadas.

### Fase 3.0 — entradas, y la primera medición de resultados

Primera fase que **decide operar**. Todo lo anterior era estructura. El detalle
completo —reglas, casos límite, garantía anti-lookahead y parámetros abiertos—
está en [`MODULO_3_ENTRADAS.md`](MODULO_3_ENTRADAS.md).

    Diario  contexto OPCIONAL: confirma y permite alargar, NUNCA dispara.
            En conflicto, MANDA H4 y el conflicto se registra.
    H4      el motor: el precio toca una zona del ID vigente y queda en
            observación. RESPETO, o ROTURA Y RETESTEO (sólo el UL).
    H1      confirma: ID de H1, OB de H1, o rechazo (§2, tres definiciones).
    M15     afina: OB suelto, sin exigir ID de M15.

**Regresión verificada:** con `entries.enabled: false` la corrida reproduce la
fase 2.1 exacta —`801951b9cc26`, D 239 / H4 1.214 / H1 4.148 detectados y
233 / 1.211 / 4.141 publicados— y encender las señales no mueve un solo impulso.
La fase 3 lee la estructura y no la toca.

⚠️ **Corrida hecha sin fichero de ask.** El §4 pide longs al ask y shorts al bid, y
sólo está descargado el M1 del lado bid. El comando **se detiene por defecto**; la
corrida de `now/fase30/` está autorizada explícitamente y usa el bid para los dos
lados, declarado en portada. **Todos los costes van marcados VERIFICAR**: ninguno
está calibrado contra Pepperstone Razor.

**El embudo, de 2018 a 2025:** 2.414 zonas de H4 → 1.970 tocadas → 2.420
observaciones (una zona tocada produce hasta dos: la de respeto y la de rotura y
retesteo) → 1.776 confirman en H1 → 1.186 con entrada de H1 y 1.566 con entrada de
M15 → **3.702 operaciones** en las tres configuraciones que existen. Dónde mueren
las descartadas: 644 sin confirmación en H1, 590 sin OB de H1, 470 con el OB roto,
283 sin retesteo, 210 sin OB de M15 y 93 con el precio ya al otro lado del stop
cuando tocaba ejecutar.

Lo que salió de medirla (en R, **neto**, con el bruto al lado):

- **Las tres configuraciones (entrada, stop), por separado.** Entrada H1 / stop H1:
  1.160 operaciones, 22,2 % de aciertos, bruto −0,044 R y neto −0,124 R. Entrada
  M15 / stop H1: 985, 23,6 %, bruto +0,013 R y neto −0,056 R. Entrada M15 / stop
  M15: 1.555, 22,5 %, bruto −0,032 R y neto −0,223 R. **Entrada en H1 con stop de
  M15 no existe y no es un olvido:** la zona de M15 se forma *después* de decidir
  la entrada de H1, así que su stop no se puede leer sin lookahead.
- **El coste se lo come casi todo, y no por igual.** Coste medio 0,075 R con stop
  de H1 frente a 0,191 R con stop de M15: el stop de M15 es más ajustado, así que
  el mismo coste en dólares pesa mucho más en R. Es aritmética de costes y se ve
  porque el bruto va al lado.
- **El contexto diario: la hipótesis del propietario se sostiene en el signo.**
  Con contexto a favor, 1.005 operaciones a 24,3 % y neto −0,074 R; sin contexto,
  1.288 a 22,5 % y −0,149 R; en conflicto, 1.407 a 21,7 % y −0,198 R. El orden es
  el que él predijo, pero los tres intervalos de confianza se solapan.
- **Rotura y retesteo es la población más pequeña y la única con bruto positivo:**
  251 operaciones, 25,1 % de aciertos, bruto +0,079 R y neto −0,028 R, frente a
  3.449 respetos a 22,5 % y −0,156 R. Con 251 operaciones el intervalo va de
  −0,268 a +0,211 R: no separa nada todavía.
- **Los largos y los cortos no se parecen.** Largos: 1.997, 26,2 %, neto +0,010 R.
  Cortos: 1.703, 18,6 %, neto −0,332 R. Es el desglose con la diferencia más
  grande de los ocho, y el oro subió en casi todo el periodo.
- **Por año no se sostiene nada.** El neto va de −0,359 R (2022) a +0,111 R (2025)
  y cambia de signo cinco veces en ocho años.
- **Las tres definiciones de rechazo se solapan mucho menos de lo que parece.**
  R1 marca 1.067 velas, R3 marca 1.997 y R2 va de 1.787 (P60) a 426 (P90). R1 ∩ R3
  = 642; R1 ∩ R2(P75) = 268. Son tres criterios distintos, no tres nombres del
  mismo. **Ninguna está adoptada** y el motor no recomienda ninguna.
- **La distribución del 1R es el aviso que pedía el §3.** Mediana 5,72 USD (1,79
  ATR, 0,29 % del precio), pero **21 operaciones tienen el 1R por debajo de una
  horquilla entera** y el mínimo es de 1 céntimo. Ahí el desenlace no es fiable, y
  menos aún sin fichero de ask.
- **Dos guardarraíles salen a cero por construcción, no por la muestra.** Una zona
  de entrada no puede medir cero —la vela que la define tiene cuerpo— y el ID de
  H4 no puede morirse antes de que su observación tenga una sola vela de H1 salvo
  en el borde del histórico. Se dejan en la tabla: un guardarraíl que no puede
  dispararse dice algo sobre las reglas.
- **Frecuencia:** 8,88 operaciones por semana y sólo el 6,0 % de semanas sin
  ninguna señal.

**Los cinco arquetipos del §9 existen todos** en la muestra —el caso de manual, el
caso en contra, los tres guardarraíles, los dos bordes y el conflicto— y cada uno
tiene su ficha y su captura en `now/fase30/`.

Antes de la fase 3.1 hacen falta tres cosas del propietario, y ninguna la puede
decidir el motor: cerrar los parámetros abiertos (definición de rechazo y su
percentil, ventana de retesteo, ventana de búsqueda en M15) **mirando gráficos**,
calibrar los cuatro costes contra Pepperstone Razor, y revisar los stops de
`now/fase30/` **antes** de mirar los resultados.

## Antes de pensar en demo

- Backtest sobre varios años y varios regímenes de mercado, no solo el último.
- Análisis fuera de muestra: ajustar en un tramo, verificar en otro que no se tocó.
- Sensibilidad de parámetros: si el resultado se desploma al mover un parámetro un
  10%, está sobreajustado.
- Sensibilidad de costes: repetir con el doble de horquilla y comisión.
- Comprobar la ficha del símbolo contra la cuenta real.
