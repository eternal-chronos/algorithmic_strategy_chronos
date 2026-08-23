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
| 1 | Impulso dominante | Detección del ID en Diario y H4 (H1 y M15 se dibujan con el de H4): rotura → limbo → constitución | Implementada y corrida sobre 2018–2025 de Dukascopy; **pendiente de auditoría visual del propietario** | `chronos structure detect` |
| 2.0 | Zonas UL y OB | Detección y dibujo de las dos zonas de cada ID; la rotura sigue siendo por línea | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure zonas` |
| 2.1 | Rotura por zona | La zona sustituye a la línea como nivel de rotura del ID: el UL a favor, el OB en contra | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure rotura-por-zona` |
| 2.2 | — | *(aparcada: FVG)* | — | — |
| 3.x | Entradas | **RETIRADA.** La cascada Diario → H4 → H1 → M15, la ejecución en M1 y sus métricas se han borrado del proyecto: se rehacen desde cero | — | — |

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

**Línea base definitiva: `e27d20d0fa4e` · D 401 / H4 2.027 impulsos**
(A1 + L1 + sesión `NY_17:00` + bid; era `f2f2a87f8efe` · D 401 / H4 1.914 con la
sesión en `NY_18:00`, y `8e51cd9140c8` cuando H1 llevaba detector). Sustituye
también a las anteriores: `4c299bcf2fba` con 477 / 2.068 / 7.416 (ancla A2 y día natural en UTC, con las velas fantasma
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
- **La rejilla cerrada: `NY_17:00`, para el diario y para H4.** Es la de
  cTrader/Pepperstone, la plataforma de ejecución en vivo, comprobada vela a vela
  contra el M1. TradingView corta una hora después (`NY_18:00`, la elección
  anterior) porque arma las velas con la sesión del símbolo y no con la hora del
  servidor del bróker; manda la de ejecución, porque la estrategia tiene que ver
  las mismas velas en backtest y en live. En el diario los dos cortes son
  indistinguibles; en H4 no. Los cuatro offsets fijos en UTC dan doce horas de
  apertura a lo largo del año en vez de seis. Se lleva por delante las
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
`e27d20d0fa4e` y mismos 401 / 2.027 impulsos con las zonas encendidas y
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
rotura deja el ID vivo y **su extremo sigue extendiéndose**, pero **ninguna de
las dos zonas se remarca**: el UL lo fija la vela del extremo con la que el ID se
constituyó y el OB, la vela del ancla. Por eso la comparación de abajo son dos
ejecuciones completas del módulo sobre las mismas velas, no una tabla
reetiquetada.

**Ajuste posterior — el UL no se remarca.** En la primera versión de esta fase
cada rechazo a favor movía la vela del extremo y con ella el UL, así que el borde
exterior contra el que se juzgaba al ID se alejaba en cada rechazo y el ID podía
ir subiendo escalón a escalón sin morirse. Ahora la zona se marca **una vez**, al
constituirse el ID, y el mismo borde exterior juzga todas las velas de su vida.
La línea del extremo sigue yendo en escalera —eso no ha cambiado— y por eso a
partir de la primera extensión ya no coincide con el borde interior del UL.

**Regresión verificada:** con `break_by_zone: false` el sistema reproduce
`e27d20d0fa4e` con D 401 / H4 2.027 detectados y 392 / 2.023 publicados. **Línea
base nueva: `48138aa438f1` · D 251 / H4 1.268 detectados y 245 / 1.264
publicados.**

⚠️ **Las cifras de abajo son de antes del ajuste del UL** —se midieron con la zona
remarcándose en cada rechazo, sobre D 239 / H4 1.214 / H1 4.148—. Los recuentos de
impulsos ya están actualizados arriba; el resto **está pendiente de volver a
medir** con `chronos structure rotura-por-zona`. El sentido de cada hallazgo se
mantiene, los números no.

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
  historia (hashes `48138aa438f1` y `f51fb871d50a`).
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

### Fase 3 — entradas: RETIRADA, se rehace desde cero

Todo el módulo de entradas se ha **borrado del proyecto**: la cascada
Diario → H4 → H1 → M15, las tres definiciones de rechazo, el turtle soup, el OB
suelto de M15, la ejecución sobre M1, los costes, las métricas en R, el informe,
los arquetipos y sus capas del explorador. También el comando
`chronos structure entradas` y el bloque `entries:` del YAML. Con él se han ido
las cifras que publicaba: el embudo, el resultado por configuración y los
arquetipos ya no están medidos por nada que corra hoy.

La razón no es un bug: la cascada se montó sobre una lectura de la estrategia que
hay que volver a fijar antes de escribir una línea de código. Lo que toca ahora,
y en este orden, es comprobar que la base se entiende:

1. **el toque de una zona** —cuándo el precio toca un UL y cuándo toca un OB, y en
   qué se distingue tocar de atravesar—. El explorador ya lo dibuja: la capa
   «Señales de zona» marca el toque del OB, el rechazo del UL y la rotura del UL
   sobre el Diario y H4. Son **dibujo**, no entradas
   ([MODULO_2_ZONAS.md](MODULO_2_ZONAS.md#señales-de-zona-sólo-dibujo));
2. **los ID** —qué ID está vigente en cada instante y con qué identificador;
3. **la multitemporalidad** —qué dice el Diario mientras H4 dice otra cosa, y cómo
   se lee eso sobre H1 y M15, que ya no llevan ID propio.

Hasta que eso esté auditado sobre el explorador no se vuelve a escribir la
cascada. El proyecto queda, por tanto, **sin señales, sin entradas, sin stops,
sin targets y sin medición de rentabilidad**: la última fase viva es la 2.1.

## Antes de pensar en demo

- Backtest sobre varios años y varios regímenes de mercado, no solo el último.
- Análisis fuera de muestra: ajustar en un tramo, verificar en otro que no se tocó.
- Sensibilidad de parámetros: si el resultado se desploma al mover un parámetro un
  10%, está sobreajustado.
- Sensibilidad de costes: repetir con el doble de horquilla y comisión.
- Comprobar la ficha del símbolo contra la cuenta real.
