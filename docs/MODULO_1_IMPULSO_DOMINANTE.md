# Módulo 1 — Impulso dominante (ID)

Fase 1 de la algoritmización de la estrategia tendencial CDT sobre XAUUSD.
Detecta el impulso dominante y **nada más**: sin último/penúltimo, sin RSI, sin
Fibonacci, sin patrones, sin zonas, sin señales, sin entradas, sin stops, sin
targets y sin medición de rentabilidad.

> **El ID vive sólo en el Diario y en H4.** H1 y M15 no llevan detector: no se
> les marca ID, y sobre las dos se dibuja el de H4 como contexto. Las cifras de
> H1 que aparecen más abajo son de cuando H1 llevaba detector y quedan como
> registro histórico: ya no se comprueban. Los recuentos del Diario y de H4 no
> se han movido —cada temporalidad se detecta por su cuenta, y hay una
> comprobación que lo fija— pero el `config_hash` sí, porque las temporalidades
> detectadas entran en él.

## Cómo se usa

```bash
# 0. Traer el histórico de Dukascopy. Tiene que ser M1: la verificación de zona
#    horaria mide el rango medio POR MINUTO y con velas de una hora no distingue
#    las 13:30 de las 13:00.
chronos data dukascopy -g m1 --from 2018-01-01 --to 2025-12-31

# 1. Verificar la zona horaria del histórico. OBLIGATORIO antes de nada.
chronos structure verify-tz --config config/impulse.yaml

# 2. Detectar los impulsos y escribir los informes.
chronos structure detect --config config/impulse.yaml

# 3. Evidencia de las comprobaciones: esperado y obtenido, lado a lado.
chronos structure evidencia --config config/impulse.yaml
```

Cada corrida deja una carpeta en `reports/` con:

| Fichero | Qué contiene |
|---|---|
| `impulsos.csv` | La tabla de §5.1, con `config_hash` en cada fila |
| `eventos_rotura.csv` | `ROTURA_A_FAVOR` / `ROTURA_EN_CONTRA` + geometría de la vela |
| `contactos.csv` | Un registro por toque con los límites del ID, con su geometría |
| `estado_por_barra.csv` | `LIMBO` o `ID_VIGENTE` en cada cierre |
| `reporte.txt` | El informe ASCII completo (secciones A–E), desglosado por año |
| `antes_y_despues.csv` | La comparativa con la línea base provisional, temporalidad por temporalidad |
| `explorador.html` | El explorador visual de §5.3, autocontenido |
| `run.json` | Configuración exacta, origen de los datos y diagnósticos |

## Los datos

```bash
# Un fichero por día y lado: es lo que necesita la fase, ~2.900 días × 2 lados.
chronos data dukascopy -g m1 --from 2018-01-01 --to 2025-12-31

# Un fichero por mes y lado: treinta veces menos peticiones, pero sin M1 la
# verificación horaria de §1.1 no se puede hacer fina.
chronos data dukascopy -g h1 --from 2018-01-01 --to 2025-12-31
```

Los `.bi5` crudos quedan en `data/raw/dukascopy/`, así que una descarga
interrumpida se reanuda sin volver a pedir nada al servidor. Dukascopy limita el
ritmo con `503` en cuanto se le aprieta; el cliente lo detecta y va aflojando
solo, pero desde una red castigada la descarga completa puede tardar horas.

**M1 y H1 dan exactamente las mismas velas H4 y diarias** mientras la rejilla
caiga en horas en punto: agregar cuatro velas H1 y agregar doscientos cuarenta
minutos producen el mismo resultado (hay un test que lo fija). Con la sesión
anclada a las 18:00 de Nueva York la frontera sigue cayendo en hora en punto,
así que la equivalencia se mantiene.

Aun así **la fase se corre sobre M1**, por dos cosas que H1 no puede dar: el
gráfico M15 y —sobre todo— el perfil de volatilidad por minuto de §1.1. Con
velas de una hora el pico sólo puede caer en horas en punto, así que no
distingue las 13:30 de las 13:00 y la verificación horaria se queda coja. Pedir
M15 a un histórico H1 no produce datos falsos en silencio: el módulo se niega y
lo dice.

Hoy sólo hay M1 del lado **bid** descargado; mientras siga así, `structure_side`
no puede ser `ask` ni `mid` (falla con un error explícito, no se inventa el otro
lado).

### El relleno de volumen cero

Dukascopy publica **los 1.440 minutos de todos los días**, también los que el
mercado está cerrado: cuando no hubo ni un tick, repite el último precio y pone
el volumen a cero. Un sábado entero llega como 1.440 velas planas.

El descargador descarta esos registros por volumen. No es cosmética:

- con el relleno dentro, **el hueco de fin de semana desaparece**, y con él la
  única forma de verificar empíricamente la zona horaria (§1.1);
- aparecen velas H4 y diarias de sábado y domingo con `open == close`, que el
  módulo cuenta como dojis, de modo que la estructura que ve el motor deja de
  ser la que el propietario ve en su pantalla.

Verificado sobre bytes reales: viernes 2018-01-05 negocia hasta las 21:58 UTC,
el sábado no hay ni una barra, y el domingo reabre a las 23:00 UTC.

## La regla, tal como está implementada

### Se traza por cuerpos, sin mechas

`body_high = max(open, close)`, `body_low = min(open, close)`. Las mechas están
en el dataset —las usará el módulo 2— pero este módulo no las mira.

### Ciclo de vida en dos momentos

1. **Rotura.** Una pierna cierra más allá de uno de los dos límites del ID
   vigente. Eso **mata** el ID anterior y no crea ninguno. Se entra en LIMBO.
2. **Constitución.** Cierra la primera vela contraria a esa pierna. Ahí, y sólo
   ahí, nace el nuevo ID, con el extremo por cuerpo alcanzado **hasta la vela
   anterior** a la contraria.

El limbo es un estado legítimo y puede durar varias barras. De este ciclo sale
gratis la inmunidad al lookahead: el extremo no se conoce hasta que cierra la
vela contraria, así que no hay nada que desplazar.

> **Qué es «uno de los dos límites» depende de `break_by_zone` (fase 2.1).** Con
> `false` —la línea base de esta fase— son las dos líneas del ID, el extremo y el
> ancla. Con `true` la línea deja de mandar cuando existe una zona que la
> sustituya: el UL en el lado a favor y el OB confirmado en el lado en contra, y
> romper pasa a ser cerrar más allá de su borde **exterior**. El ciclo rotura →
> limbo → constitución no cambia; lo único que cambia es **cuándo** se dispara la
> rotura, y que un ID que sobrevive sigue extendiendo su extremo. Está en
> [`FASES.md`](FASES.md#fase-21--la-zona-decide-la-rotura).

### Sin umbral de tamaño

Cualquier cuerpo contrario constituye el impulso, por pequeño que sea. El
informe cuenta cuántos ID nacen de una vela contraria del decil más pequeño
precisamente para poder discutir si tu ojo aplica un filtro que la regla no
tiene.

## Decisiones tomadas donde el enunciado dejaba borde

Todas están cubiertas por tests en `tests/domain/structure/test_synthetic_day.py`.

| Caso | Decisión | Por qué |
|---|---|---|
| Rotura vs. constitución en la misma barra | **Primero la rotura.** El estado se evalúa al principio de la barra, así que una barra que rompe sólo rompe; la constitución llega como muy pronto en la siguiente | Es el orden que pide §2.6 y evita que una misma vela cierre y abra impulso |
| "Cerrar más allá" | **Desigualdad estricta.** Cerrar justo en el nivel no rompe | "Más allá" no incluye el propio nivel |
| Arranque de la pierna | Primer elemento de la racha contigua en la dirección de la pierna que acaba en la barra de rotura; los dojis no cortan la racha. Si la barra de rotura ya es contraria a la pierna nueva, la pierna arranca en ella. **Ese último caso dejó de ser una decisión cerrada: es el parámetro abierto `leg_start_mode` (R-36)** | Es lo que hace falta para que el ancla salga "donde arrancó la pierna" (§2.5) |
| Doji | Cuerpo nulo: ni constituye ni corta rachas. Sí extiende el extremo alcanzado | §2.2 |
| Igualdad `close == open` | Exacta, sin tolerancia | No inventar un umbral que el enunciado no da |
| `n_barras_limbo` | Barras cerradas entre la rotura (exclusive) y la constitución (exclusive) | Es lo que mide "cuánto tiempo el sistema está sin sesgo" |
| `n_barras_id` | Barras entre la constitución y la rotura que lo cierra | — |
| Rango negativo | No se corrige: se cuenta en `impulsos_rango_no_positivo` | Sólo aparece con huecos violentos; taparlo escondería datos malos |

## Parámetros — cuatro cerrados, cuatro abiertos

El motor no ha elegido ninguno por criterio propio. El informe los imprime todos
en cada corrida, cerrados y abiertos.

**Cerrados por el propietario. Son los que definen la línea base definitiva
(`f2f2a87f8efe`; era `8e51cd9140c8` con el detector de H1 puesto):**

| Parámetro | Valor | Por qué |
|---|---|---|
| `anchor_mode` (R-02) | `A1_last_counter_body` | El ancla va en la última vela contraria previa a la pierna, nunca en la primera vela de la pierna. Lo cerró mirando sus capturas. `A2_first_leg_bar` queda para regresión |
| `leg_start_mode` (R-36) | `L1_actual` | `L2` multiplicaba por diecisiete los extremos de color contrario y `L3` producía impulsos de rango negativo. Los tres siguen disponibles |
| `d_session_start` y origen de H4 | `NY_18:00` | Única rejilla que reproduce sus velas. Los cuatro offsets fijos en UTC dan doce horas de apertura a lo largo del año en vez de seis |
| `structure_side` | `bid` | Hoy sólo hay M1 del lado bid descargado; pedir `ask` o `mid` falla con un error explícito en vez de inventarse el otro lado |

**Abiertos todavía:**

| Parámetro | Opciones | Qué está en juego |
|---|---|---|
| `seed_mode` | `S1_first_non_doji` · `S2_first_counter_bar` | Cómo arranca la máquina al principio del histórico, donde no hay pasado a la izquierda. Afecta sólo a los primeros impulsos |
| `doji_break_mode` | `D1_doji_no_rompe` · `D2_doji_rompe_por_cierre` | §2.2 dice que el doji "no rompe nada"; §2.6 evalúa la rotura por cierre sin mirar el cuerpo. El informe cuenta cuántas barras distinguen una lectura de la otra |
| `break_by_zone` (fase 2.1) | `false` · `true` | Si la rotura es por línea o por zona. `false` es esta línea base; `true` es la de la fase 2.1 (`00e7013d627b`) |
| `overlap_priority` (fase 2.1) | `a_favor_primero` · `en_contra_primero` | Qué lado se evalúa primero cuando una vela cumple las dos condiciones de rotura. Sobre este histórico no ocurre **ni una vez**, así que la elección es cosmética; los dos órdenes están implementados |

`h4_offset_hours` sigue en el YAML pero **no pinta nada** mientras haya ancla de
sesión: H4 arranca con la sesión y avanza de cuatro en cuatro dentro de ella
(18/22/02/06/10/14 hora de Nueva York). Se conserva para las corridas de
regresión sobre la rejilla UTC.

`seed_mode`, `doji_break_mode` y `leg_start_mode` **no estaban en el enunciado**:
son huecos que aparecieron al implementar o al medir, y se exponen como
parámetros en vez de resolverlos por cuenta propia.

`L1_actual` reproduce barra por barra el comportamiento previo a R-36 y por eso
queda **fuera del hash de configuración**: las corridas ya archivadas siguen
siendo comparables. `L2` y `L3` sí entran en el hash. `break_by_zone: false` se
omite por la misma razón y con la misma consecuencia buscada —la línea base
`f2f2a87f8efe` se conserva—, y con él se omite `overlap_priority`, que sólo puede
decidir algo con la regla nueva encendida.

### La línea base definitiva y lo que sustituye

Los números publicados antes quedan archivados como **provisionales**: se
calcularon con el ancla equivocada y con el día natural en UTC, que dejaba la
hora de reapertura del domingo sola en una vela diaria propia. Cada corrida
vuelve a ejecutar el módulo entero con aquella configuración y publica la tabla
de antes y después (`antes_y_despues.csv`), así que la comparación no envejece.

Las tres columnas de recuentos son D / H4 / H1: son de cuando H1 llevaba
detector. Hoy sólo se comprueban las dos primeras.

| | provisional `e2e974c7704c` | definitivo `f2f2a87f8efe` |
|---|---|---|
| Ancla | `A2_first_leg_bar` | `A1_last_counter_body` |
| Corte diario | `00:00` UTC | `NY_18:00` (DST real) |
| Impulsos detectados | 477 / 2.068 / 7.416 | **401 / 1.914 / 7.231** |
| Publicados | 469 / 2.065 / 7.409 | **392 / 1.910 / 7.224** |
| Velas del histórico | 2.489 / 12.799 / 47.306 | 2.064 / 12.353 / 47.306 |

En H1 las velas son las mismas —la rejilla de horas en punto no depende del
corte— y todo lo que se mueve ahí es cosa del ancla.

**Sobre A1 vs A2.** Las dos candidaturas salen de velas consecutivas, así que
coinciden exactamente cuando el `open` de una es el `close` de la anterior. **Eso
no es lo habitual en este histórico**: el descargador tira los minutos sin
negociación, de modo que casi toda vela abre con un hueco respecto a la anterior.
El ancla es uno de los dos niveles que rompen el ID, así que moverla adelanta o
retrasa roturas en contra: la elección no era cosmética. Con A1 el color del
ancla es correcto **por construcción**.

Lo que A1 **no** arregla es R-36, que es otro nivel: con la configuración
definitiva el extremo sigue saliendo de una vela del color contrario en 4 ID
diarios (1,02 %), 39 de H4 (2,04 %) y 146 de H1 (2,02 %), y **los 189 casos
están en la vela de arranque de pierna**, que es la única que la máquina adopta
sin mirar su color. Queda anotado como limitación conocida.

La sección B del informe (la comparativa completa A1/A2) ya no se ejecuta por
defecto: R-02 está cerrado. Se recupera con `--comparativa-anclas`.

### Velas cortas (B)

Una vela es corta si contiene menos del 25 % de los minutos de M1 que su
temporalidad promete. Con la configuración definitiva:

| | velas | cortas | qué son |
|---|---|---|---|
| Diario | 2.064 | **0** | la vela fantasma del domingo desaparece: la reapertura entra en la sesión del lunes |
| H4 | 12.353 | 29 (0,23 %) | festivos de EE. UU. con cierre anticipado |
| H1 | 47.306 | 10 (0,02 %) | los mismos festivos |

Las 29 de H4 son **siempre la vela de las 14:00 de Nueva York**, la última de la
sesión, en días de cierre anticipado: 4 de julio, Día del Trabajo, Acción de
Gracias y el día siguiente, Memorial Day, Presidentes, MLK y Juneteenth. Su
reparto por año es 0 · 3 · 0 · 0 · 6 · 5 · 8 · 7 (2018→2025); antes de 2022 el
histórico casi no las tiene porque el bróker no paraba esos días. Su papel en la
estructura es residual: 8 anclas, 1 extremo, 6 constituciones y 1 rotura.

Con el corte anterior en `00:00` el histórico tenía 2.489 velas diarias —5,95 por
semana— y **425 de ellas eran cortas**, 413 domingos, con 106 anclas, 43 extremos,
84 constituciones y 22 roturas encima.

`chronos structure auditoria-sesion` reconstruye las velas y re-ejecuta el
detector con cada corte para poder compararlos:

| `d_session_start` | velas D | por semana | cortas | ID | % limbo | latigazos |
|---|---|---|---|---|---|---|
| 00:00 | 2.489 | 5,95 | 425 | 454 | 35,1 | 52 |
| 21:00 | 2.204 | 5,27 | 139 | 424 | 35,8 | 60 |
| 22:00 | 2.064 | 4,94 | 0 | 392 | 36,0 | 55 |
| 23:00 | 2.336 | 5,59 | 272 | 410 | 33,9 | 52 |
| `NY_17:00` | 2.064 | 4,94 | 0 | 392 | 36,0 | 55 |
| **`NY_18:00`** ← elegido | 2.064 | 4,94 | 0 | 392 | 36,0 | 55 |

El motor **no recomendó ninguno**: la salida incluye el OHLC a cuatro decimales
de las velas en discusión, y el propietario eligió cotejándolo contra su
TradingView.

### Cortes anclados a la sesión de una plaza

`d_session_start` admite dos formas: `HH:MM`, una hora fija en UTC, y
`PLAZA_HH:MM`, la hora local de una plaza resuelta con su horario de verano real.
Hoy la única plaza es `NY` (`America/New_York`), y `NY_17:00` / `NY_18:00`
reproducen la hipótesis del propietario sobre su TradingView. Con el ancla, el
corte se mueve solo dos veces al año —las 18:00 de Nueva York son las 23:00 UTC
en invierno y las 22:00 en verano— y H4 deja de mirar `h4_offset_hours` para
arrancar con la sesión.

El corte no se calcula restando horas en UTC sino comparando el reloj de la
plaza. La diferencia importa dos días al año: en marzo la sesión dura 23 horas y
en noviembre 25, y con la resta fija la última hora de la sesión larga acabaría
en la vela equivocada. Hay tests que fijan las cuatro horas UTC y las dos
duraciones anómalas.

**Con el diario no se pueden distinguir.** El histórico M1 ya trae la parada
diaria del oro: no hay ni un minuto entre las 17:00 y las 18:00 de Nueva York.
Cualquier corte dentro de esa parada reparte los mismos minutos, así que 22:00
UTC, `NY_17:00` y `NY_18:00` producen velas diarias idénticas y sólo cambia la
marca de tiempo. El informe lo detecta solo y lo dice. En H4 sí se separan (2.023
ID con `NY_17:00`, 1.910 con `NY_18:00`, 2.035 con la rejilla UTC), porque ahí
las fronteras caen dentro del horario de mercado.

## Lo que se mide y no se usa

Dos bloques que **no tocan la detección**: la tabla de impulsos sale idéntica con
ellos dentro que fuera, y la línea base D 401 / H4 1.914 / H1 7.231 sigue en pie.

### Contactos con los límites del ID

Tres categorías, todas con los datos que ya existían:

| Categoría | Qué es |
|---|---|
| `TOQUE_MECHA` | alcanza el nivel con la mecha, pero cierra dentro del rango |
| `ROTURA_FALLIDA` | cierra fuera y la barra siguiente vuelve a cerrar dentro |
| `ROTURA_REAL` | cierra fuera y no vuelve: la rotura que mata al ID |

**Hay que leer los recuentos sabiendo esto:** un ID muere en el primer cierre más
allá de uno de sus límites, así que durante su vigencia una `ROTURA_FALLIDA` es
casi imposible por construcción. Sólo puede darla un doji con
`D1_doji_no_rompe`. Sobre ocho años aparece **una vez**, en H1. No es un fallo de
la medición: es lo que la regla escrita implica, y por eso se cuenta y se
declara en vez de esconderse. Lo que queda son toques de mecha.

Con esa definición, la firma de lateralización del propietario —dos contactos
arriba y dos abajo antes de romper— la cumple el 1,3 % de los ID diarios, el
2,7 % de los de H4 y el 2,6 % de los de H1.

### Geometría (se persiste, no se lee)

Cada rotura y cada contacto llevan `cuerpo_pct`, `mecha_sup_pct`,
`mecha_inf_pct`, `cierre_mas_alla_usd` y `cierre_mas_alla_atr`. Se calculan en
`application/structure/geometry.py`, que **el paquete `domain/` no importa**: no
hay forma de que una regla los lea sin invertir la dirección de las
dependencias. Servirán para estudiar más adelante si un mechazo que rechaza una
rotura es confirmación, sin recalcular ocho años entonces.

`cierre_mas_alla_usd` va con signo: positivo si la vela cerró fuera del límite,
negativo si cerró dentro. Un `TOQUE_MECHA` se distingue de una `ROTURA_REAL` sin
mirar ninguna otra columna.

## Garantía anti-lookahead

`LookaheadError` (subclase de `DomainError`) salta de verdad en tres sitios,
cada uno con su test:

1. Pedir el extremo de un ID que aún no está constituido (estado LIMBO).
2. Pedir el estado en un instante posterior a la última barra procesada.
3. Leer el ATR en un índice que la máquina todavía no ha alcanzado.

Además hay un test estructural que altera las barras futuras y comprueba que
ningún valor pasado del ATR se mueve, y otro que comprueba que procesar más
barras no reescribe ningún impulso ya constituido.

## Verificación de zona horaria (§1.1)

`chronos structure detect` **se detiene** si la verificación falla. Comprueba
hechos que no dependen de ninguna convención del bróker:

- **A.1** el hueco semanal empieza en viernes y termina en domingo UTC;
- **A.2** el rango medio por minuto del día tiene un pico marcado hacia las
  13:30 UTC;
- **A.3** ese pico se desplaza **una hora exacta** con el horario de verano de
  EE. UU. — diagnóstico, no bloqueante.

### El pico no está donde parecía

El dato macro que hace el pico sale a las **8:30 de Nueva York**, no a una hora
de Londres. En UTC eso son las 13:30 en invierno y las **12:30 en verano**. Como
el año tiene ocho meses de horario de verano y cuatro de invierno, el máximo del
histórico completo cae en **12:30 UTC**, no en 13:30.

Con el histórico H1 esto no se veía: el pico sólo podía caer en horas en punto y
la tolerancia ampliada lo daba por bueno en 13:00. Con M1 se ve, y el desglose
por régimen horario lo confirma sin margen de duda:

| Tramo | Pico | Esperado |
|---|---|---|
| Verano EE. UU. (EDT), 1.859.279 velas | **12:30 UTC** | 12:30 |
| Invierno EE. UU. (EST), 975.763 velas | **13:30 UTC** | 13:30 |

Sesenta minutos exactos de desplazamiento: el histórico está en UTC. Por eso la
comprobación admite cualquiera de las dos horas y es A.3 la que verifica que las
dos están en su sitio. Exigir 13:30 sobre el agregado del año habría dado por
malo un histórico correcto.

Seis de los ocho años dan el pico esperado en el puesto 1 de su perfil. En 2020,
2021 y 2025 el invierno lo gana el dato de las **10:00 de Nueva York** (15:00
UTC) por milésimas —en 2025, 3,775 frente a 3,770 USD de rango medio—, y el
esperado queda segundo. Un histórico con la hora mal puesta no deja el esperado
en el puesto 2: lo deja fuera.

Saltarse la verificación exige `--skip-tz-audit`, y entonces el informe deja
constancia de que los impulsos no son auditables contra TradingView. Un offset
horario equivocado no produce ningún error visible: produce velas H4 desplazadas
y, por tanto, impulsos distintos a los que ves en tu pantalla.

## Las temporalidades y qué se ve en cada una

| Gráfico | Impulsos que dibuja | Detector propio |
|---|---|---|
| **Diario** | Diario | sí |
| **H4** | H4 (principal) + Diario (contexto) | sí |
| **H1** | H4 (contexto) | **no** |
| **M15** | H4 (contexto) | **no** |

El reparto lo fija el propietario en `config/impulse.yaml`; no es una decisión
del motor. **El ID sólo se marca en el Diario y en H4.** H1 y M15 no aportan
estructura propia: son las temporalidades en las que se mira cómo llega el
precio a la zona, así que llevan velas con el impulso de H4 encima y nada más.
M5 y M1 quedan fuera del módulo.

El **principal** de cada gráfico —el primero de su lista— es el que manda: de él
salen el sombreado del limbo y los marcadores de constitución y rotura, y va en
línea continua. El de contexto va punteado y más grueso, sin marcadores, para
que no compita con lo que se está auditando.

Un ID sólo se rompe con cierres de su propia temporalidad (§2.4), así que el
Diario y H4 son dos estructuras distintas y no dos zooms de la misma.

Sólo H4 y el diario llevan desplazamiento de rejilla: los cuartos de hora y las
horas en punto son iguales en todas las plataformas.

## El explorador: cómo se navega

`explorador.html` se abre con doble clic. Es un fichero autocontenido: lleva
dentro los datos, Plotly y la lógica, así que funciona sin conexión y se puede
archivar junto a las capturas.

| Control | Para qué |
|---|---|
| **Temporalidad** Diario / H4 / H1 / M15 | Cambia el gráfico y, con él, qué impulsos se dibujan (ver tabla de arriba) |
| **Vista** Velas / Líneas | La regla se traza por cuerpos: en "Líneas" ves la serie de cierres, que es como el propietario mira la estructura sin el ruido de las mechas |
| **Ajustar** | Suelta el zoom que hayas hecho a mano y devuelve el encuadre automático. Doble clic sobre el gráfico hace lo mismo. Ver más abajo |
| **Periodo** Todo · 5 años · 2 años · 1 año · 6 meses · 3 meses · 1 mes · 1 semana | Recorta la ventana desde el final del histórico hacia atrás |
| **◀ ▶** (o las flechas ← → del teclado) | Recorre el histórico tramo a tramo. Los tramos van pegados y sin solapar: el "hasta" de uno es el día anterior al "desde" del siguiente, así ninguna vela se audita dos veces. Las flechas se inhiben mientras escribes en un campo |
| **Desde / Hasta** | Fechas exactas en UTC. Mandan sobre el preset |
| **ID visibles** Actual · Actual + anterior · Todos | Cuántos ID se dibujan. Por defecto, el vigente en la fecha en pantalla y el inmediatamente previo; se recalcula en cada salto de ventana y se aplica también a las capas de temporalidad superior. **Es filtro de dibujo**: los demás siguen en los CSV y en los informes |
| **Arranque de pierna (R-36)** L1 · L2 · L3 | Alterna los tres `LEG_START_MODE` sobre las **mismas velas**. Cada modo trae su corrida entera —impulsos, limbo y contactos— ya calculada; cambiar de botón no recalcula nada. Sólo aparece si la corrida los embebió (`--modos-r36`, activo por defecto) |
| **Capas** ID <principal> · ID <contexto> · Limbo · Constituciones y roturas · Contactos · Nivel 50 % · Extremo de color contrario | Las casillas de impulso cambian con el gráfico. Contactos y nivel del 50 % vienen apagadas: son para mirar un ID concreto, no para navegar |
| **Replay** fecha · Empezar · ◀◀ ▶▶ · ▶ · Salir · Vela en formación · Velocidad · Velas a la vista | Reproduce la historia paso a paso desde una fecha: en cada paso sólo se dibuja **lo que el motor sabía a esa hora**. Ver más abajo |
| **Auditoría ciega** semilla · Empezar · Revelar · Salir | Apaga de golpe todas las capas y sortea una ventana dentro del rango que tengas puesto. Marcas tus impulsos a mano, pulsas **Revelar** y comparas |

### Cómo se dibuja cada ID

Las dos líneas de un ID van en **dos tramos**:

- **punteado y atenuado**, desde la vela que *define* el nivel (la del ancla o la
  del extremo, según la línea) hasta la constitución;
- **sólido**, desde la constitución hasta el fin del ID, y ni un minuto más.

Ese intervalo punteado es limbo: el nivel ya estaba en el precio, pero **el ID
todavía no existía**. Sin la distinción, el dibujo daría a entender que el
sistema conocía el nivel antes de tiempo, que es justo lo contrario de lo que
hace —el extremo no se fija hasta que cierra la vela contraria—.

La ✕ gruesa marca los extremos que fijó una vela del **color contrario** al
impulso, sobre la vela que los fijó (R-36). Es lo que la regla del propietario
prohíbe: en `L1_actual` hay 189 en el histórico (4 en D, 39 en H4, 146 en H1),
en `L3` ninguno.

### El replay

El gráfico completo enseña el resultado; el replay enseña **cómo se llegó a él**.
Eliges una fecha, pulsas **Empezar** y a partir de ahí cada pulsación de **▶▶**
avanza un paso: la vela se va armando y las capas aparecen cuando les toca.

1. Escribe la fecha y pulsa **Empezar**. El cursor se coloca en la última vela
   *anterior* a ese día, así que el primer paso descubre la primera vela de la
   fecha en vez de enseñártela ya hecha.
2. **▶▶** avanza y **◀◀** retrocede; las flechas ← → del teclado hacen lo mismo.
3. **▶** reproduce solo, a la velocidad elegida; la barra espaciadora lo arranca
   y lo para.
4. **Salir** devuelve el periodo que tenías antes de empezar.

Con **Vela en formación** encendida —lo está por defecto— la vela en curso se
arma con las velas de la temporalidad inmediatamente inferior: en H4, con las
cuatro de H1. Sale **hueca** en el borde derecho y no es una vela del motor: el
detector no ve una vela hasta que cierra, y por eso el paso que la completa es el
mismo que hace aparecer lo que el módulo hace con ella. En M15 no hay nada más
fino embebido, así que allí cada paso es una vela entera.

Lo que hace que el replay sirva para auditar es que **nada se adelanta**. Las
velas se etiquetan al inicio del intervalo, así que la cuenta va por el cierre:
la vela de `t` cierra en `t + duración`, y hasta ese minuto no se dibuja nada de
lo que ocurrió dentro. En la práctica:

- un ID no aparece —ni su tramo punteado de limbo, que sólo se conoce mirando
  hacia atrás desde la constitución— hasta que cierra la vela que lo constituye;
- la línea del ID vigente se corta en el presente, no en su rotura futura;
- sobre H4, el impulso **diario** de contexto que nace el lunes no aparece hasta
  que el lunes ha terminado;
- las constituciones, roturas, contactos y la ✕ de R-36 esperan a su hora.

**El reloj es uno solo para todas las temporalidades.** No vive en la vela que
estás mirando: es un minuto, y se enseña en las notas del pie (`reloj ... UTC`).
Cambiar de temporalidad en mitad del replay no lo mueve —se busca la última vela
de la nueva que ya hubiera cerrado a esa hora, así que saltar de H4 a Diario
enseña el diario **incompleto** que tenías en ese momento, no el de hoy— y
tampoco lo recorta: si en H1 llevas hora y cuarto corrida del día, el diario no
puede enseñarla como vela cerrada, pero al volver a H1 sigues en el mismo minuto.

La vela en formación se dibuja hasta ese reloj y con la temporalidad **más fina**
embebida, no con la del paso: en el diario cada ▶▶ avanza una H4, pero el día en
curso aparece con lo que lleve corrido aunque no haya cerrado ninguna H4 todavía.
Es lo que hace que saltar de H1 al diario no parezca un salto atrás de un día
entero. Sigue sin ser una vela del motor —se dibuja hueca—: lo que el detector ve
no cambia. Mientras el replay está en marcha los controles de periodo quedan
apagados: la ventana la manda el cursor, y un selector de fechas vivo mentiría.

El replay y la auditoría ciega son dos pruebas distintas sobre la misma ventana y
no se solapan: empezar una sale de la otra.

**El zoom se queda donde lo dejas.** Auditar de cerca exige acercarse a una vela
y quedarse ahí mientras avanzas; antes cada paso rehacía los ejes y el gráfico se
te iba de la pantalla. Ahora el encuadre que fijas con la rueda o arrastrando
—horizontal y vertical— se conserva paso a paso, y la ventana sólo se desplaza
—sin cambiar de escala— cuando la vela nueva se saldría por la derecha. Si alejas
el zoom más allá de las **velas a la vista**, el recorte se amplía para llenar lo
que se ve, en vez de dejar media pantalla vacía. Un paso atrás no mueve nada
mientras el presente siga dentro del encuadre.

Se suelta con **Ajustar** (o con doble clic sobre el gráfico), y también al pedir
otro tramo de historia —preset, fechas, ◀ ▶, empezar o salir del replay, cambiar
las velas a la vista o sortear una ventana ciega—: ahí el encuadre anterior ya no
significa nada. Alternar capas, temporalidad, modo de R-36 o vista no lo tocan.
Mientras esté tomado a mano, las notas del pie lo dicen.

### La auditoría ciega

Es la única forma de que la comparación con tu ojo signifique algo: si ves lo
que marcó el motor antes de marcar tú, ya no estás auditando nada.

1. Acota el rango con las fechas o con un preset y elige la anchura de ventana
   con el preset (con «Todo» se sortean ventanas de un mes).
2. Pulsa **Empezar**. Desaparecen impulsos, limbo, marcadores y contactos; sólo
   quedan las velas. La nota de abajo dice la **semilla**.
3. Marca tus impulsos donde los marcarías.
4. **Revelar** enseña lo que marcó el motor sobre la misma ventana.
5. **Otra ventana** sortea otra dentro del mismo rango. Escribiendo una semilla
   concreta se vuelve a abrir exactamente la misma ventana: sin eso, una ventana
   «al azar» no se puede discutir después con nadie.

Las herramientas de dibujo de Plotly están desactivadas a propósito: marcar
sobre el gráfico antes de revelar invalidaría la prueba.

La ventana **recorta los datos**, no sólo mueve el eje: los dos ejes se
autoescalan al tramo que estás auditando, así que un mes de 2018 se ve con el
mismo detalle que un mes de 2025 aunque el oro haya triplicado su precio.

Al pasar el ratón por cualquier punto salen los valores a cuatro decimales, la
hora en UTC y la hora en la zona de tu sesión. Sobre el rombo de constitución
salen además el ancla, el extremo, **las dos candidaturas A1 y A2**, el tamaño
del cuerpo de la vela contraria que creó el impulso y cuántas barras duró el
limbo previo. Ese rombo es el sitio donde comprobar si una vela minúscula está
creando impulsos que tu ojo no marcaría.

## Lo que dicen los números (2018-2025, bid, sesión NY_18:00, hash `8e51cd9140c8`, con detector en H1)

Ninguna de estas cifras es una recomendación. Son las que hay.

| | Diario | H4 | H1 |
|---|---|---|---|
| Impulsos detectados | 401 | 1.914 | 7.231 |
| Publicados (fuera del calentamiento) | 392 | 1.910 | 7.224 |
| Duración mediana del ID | 2 barras | 2 barras | 2 barras |
| Barras en LIMBO | 36,0 % | 31,2 % | 29,6 % |
| Rango mediano | 1,39 ATR | 1,54 ATR | 1,41 ATR |
| Roturas a favor / en contra | 1,48 | 1,51 | 1,44 |
| ID nacidos de un cuerpo < 0,1 ATR | 16,1 % | 14,9 % | 13,5 % |
| ID que cumplen la firma de lateralización | 1,3 % | 2,7 % | 2,6 % |
| ID con extremo sobre vela contraria (R-36) | 4 | 39 | 146 |

**Sesgos simultáneos.** Las tres temporalidades tienen ID vigente a la vez el
**36,9 %** del tiempo, y coinciden en dirección el **12,3 %**. Eso acota cuántas
oportunidades alineadas puede haber: no hay más histórico que ése donde buscar.

**Los ID enanos no son un síntoma de lateralización.** De los ID con rango
inferior a 0,25 ATR, los que tienen pegado un ID que sí cumple la firma son el
0 % en Diario, el 3,5 % en H4 y el 5,2 % en H1 — frente al 2,6 %, 5,4 % y 5,2 %
del resto de la población. La proporción es igual o **menor** que la del resto:
los impulsos minúsculos aparecen donde aparecen, no en tramos laterales
reconocibles. Es la cifra de D.4, y dice que si molestan hay que tratarlos como
un problema aparte.

## Procedimiento de auditoría (§7)

El orden importa; invertirlo destruye el valor de la prueba.

1. **Antes de correr el motor**, elige 5 ventanas del histórico y marca a mano
   tus impulsos dominantes en TradingView. Guarda las capturas. Incluye a
   propósito: una tendencia rápida, una lenta, una acumulación, un cambio de
   sesgo y un tramo que en su momento te resultara dudoso.
2. Corre el motor sobre esas mismas ventanas (`data.start` / `data.end`).
3. Abre `explorador.html` al lado de tus capturas y responde:
   - ¿Los ID coinciden con los tuyos en número y ubicación?
   - ¿El limbo aparece donde intuitivamente dirías "aquí aún no sé qué hay"?
   - ¿Hay velas contrarias minúsculas creando impulsos que tú no habrías
     marcado? **Si la respuesta es sí, hay que hablarlo antes de seguir**:
     significa que tu ojo aplica un filtro de tamaño que la regla escrita no
     tiene.
4. Envía: tus capturas manuales, `reporte.txt` y la salida de
   `chronos structure verify-tz`, indicando qué ID del explorador no te cuadran.

La auditoría se hace sobre `explorador.html`, no sobre imágenes: enseña lo mismo
sobre las velas y encima navegable —las tres temporalidades, las capas, los tres
modos de R-36 y la ventana de fechas—, así que el motor ya no emite PNG. Los
casos que más conviene mirar salen del propio informe: los ID de rango más
pequeño de Diario y H4, los que mejor cumplen la firma de lateralización y los
latigazos más severos vienen listados con su número en `reporte.txt`; se teclean
en el explorador y se ven en contexto. `reporting.captures` sigue en la
configuración, en `false`, por si alguna vez hace falta un PNG suelto para pegar
en un documento.

Si el motor no coincide con tu ojo, eso no es un fallo del proyecto: es
exactamente lo que esta fase existe para descubrir, y se arregla antes de
escribir una línea del módulo 2.

## Dónde vive el código

```
src/chronos/
├── domain/structure/          Reglas puras: sin ficheros, sin red, sin informes
│   ├── enums.py               Dirección, tipo de rotura, los modos abiertos
│   ├── body.py                La vela vista por cuerpo
│   ├── impulse.py             DominantImpulse, BreakEvent, BarState
│   ├── detector.py            La máquina de estados
│   ├── contacts.py            Clasificación de contactos (mide, no decide)
│   ├── synthetic_day.py       El día del §4, calculado a mano
│   └── errors.py              StructureError, LookaheadError
├── application/structure/     Casos de uso
│   ├── config.py              Parámetros + hash de configuración
│   ├── causal.py              ATR con frontera de lectura explícita
│   ├── timezone_audit.py      La verificación de §1.1 (A.1, A.2, A.3)
│   ├── detect_impulses.py     El caso de uso y la tabla de §5.1
│   ├── anchor_comparison.py   Las dos corridas de A1 y A2 (sección B)
│   ├── lateralization.py      La firma de lateralización (sección D)
│   ├── geometry.py            Geometría persistida (sección E, no la lee el dominio)
│   ├── evidence.py            La evidencia de la sección G
│   └── statistics.py          La estadística del informe
├── infrastructure/
│   ├── structure/loader.py    Carga de bid/ask
│   ├── structure/aggregation.py  M1 -> H4/D con offset
│   └── reporting/impulse_*    Informe ASCII, explorador, capturas y escritor
└── interface/structure_cli.py Punto de composición
```

`geometry.py` vive en `application/` a propósito: el dominio no lo importa, así
que ninguna regla del módulo 1 puede leer esos campos aunque alguien lo intente
por descuido.
