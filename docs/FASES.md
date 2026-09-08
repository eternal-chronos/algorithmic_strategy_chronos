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
| 2.0 | Zonas UL, PUL y APUL | Detección y dibujo de las dos zonas de cada ID —en contra, el PUL si el ID anterior iba igual, y si no el APUL: la zona heredada de aquél, su UL cuando su extremo quedó por detrás del ancla, o la del retroceso tras una constitución abortada—; la rotura sigue siendo por línea | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure zonas` |
| 2.1 | Rotura por zona | La zona sustituye a la línea como nivel de rotura del ID: el UL a favor, el PUL —o el APUL— en contra | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure rotura-por-zona` |
| 2.2 | — | *(aparcada: FVG)* | — | — |
| 3.0 | Señales de entrada | La cascada H4 → H1 rehecha desde cero, con la rotura del propietario —**el UL manda a favor y el ancla en contra**— y **sólo señales**: toque de la zona en contra de H4 → se espera a que el **ID de H1** —que en esta fase lleva detector propio— se ponga en la dirección del de H4 y se marca su PUL → **el precio toca ese PUL de H1 y ahí salta la señal**, en el instante del toque y no al cierre de la vela. Con el PUL diario de **veto** direccional. Sin entradas, sin stops, sin targets, sin métricas | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure entradas` |
| 3.1 | Entradas | Las operaciones: **H4 dice hacia dónde se busca** —hacia su zona en contra o hacia su UL, según dónde esté el precio—, **H1 arma el setup** —su zona en contra si va en la dirección buscada, su UL si va al revés y lo rechaza— y **M15 afina** con un OB o un FVG. Límite en el borde cercano del patrón, stop en el borde exterior de la zona de H1 —el **interior** en el rechazo del UL afinado con un OB— y objetivo siempre a 1:3. Una por ID de H1, nunca dos vivas. **El viernes al cerrar el mercado se cierra todo.** El Diario queda fuera | Implementada sobre 2018–2025; **pendiente de auditoría visual del propietario** | `chronos structure operaciones` |

### Fase 3.1 — entradas

Es lo primero del proyecto que **abre y cierra posiciones**. No publica ni una
métrica y es a propósito: lo pidió el propietario. Hasta que el dibujo esté
ajustado, una expectativa en R sólo diría lo buena que es una regla que todavía
se está escribiendo. Lo que se entrega es el explorador con la capa «Entradas»
encendida y el replay, para mirarlas una a una.

**No es la cascada de la 3.0 convertida en órdenes.** La 3.0 baja de un toque de
la zona de H4 a un toque del PUL de H1 y señala ahí. Esta fase lee otra cosa: un
ID de H4 vive **entre dos sitios** —su zona en contra, a donde el precio vuelve,
y su UL, a donde iba— y se busca siempre **en la dirección del sitio al que el
precio va**. La cascada sigue calculándose y dibujándose al lado, sin tocar nada.

**El régimen de H4**, con un ID alcista (el bajista es su espejo):

1. recién constituido, se buscan **ventas** hasta que el precio toque su zona en
   contra —el PUL o el APUL—;
2. tocada, se buscan **compras** hasta que llegue a su UL;
3. llegado al UL **no se busca nada** y se espera al **cierre de esa vela de
   H4**: si cierra sin atravesar el UL entero lo ha rechazado y se vuelven a
   buscar ventas; si cierra más allá, ha roto el ID y ahí se acaba.

Tocar se mide con mechas en M15, como en la 3.0. El cierre de la vela de H4 es la
única de las tres transiciones que espera a un cierre.

**El setup de H1, de dos maneras y sólo dos.** Con ventas buscadas: o el ID de H1
es **bajista** y se mira su **zona en contra** (forma `ZONA`), o es **alcista** y
ha **rechazado su UL** —mecha dentro, cuerpo fuera— y se mira ese **UL** (forma
`RECHAZO_UL`). No hay tercera.

**M15 afina.** Dentro de esa zona se busca un **OB** —la última vela contraria
antes de que el precio se fuera— o un **FVG** —el hueco de tres velas—, y de los
disponibles se coge el **más reciente**, que es «lo más cercano que hay ahí». Un
patrón que el precio ya atravesó deja de valer.

**Los precios.** Límite en el borde **cercano** del patrón; **stop en el borde
exterior de la zona de H1**, que es el sitio más cercano en el que el setup deja
de existir —lo dijo el propietario para el rechazo («el UL de H1») y vale igual
para la zona en contra—; objetivo **siempre a 1:3**.

**La excepción del rechazo afinado con un OB.** Ahí ni el límite ni el stop van
donde van siempre. El límite sale del **cierre de la vela que crea el OB** —la
que se va y convierte a la anterior en bloque—, separado la holgura fija de la
config (0,175 $), y queda por tanto fuera del recuadro del patrón: lo pidió el
propietario mirando la operación nº 855, donde el OB sobresalía del UL y el
límite acababa en un precio al que el mercado ya no volvía. Y el **stop va al
borde INTERIOR** de la zona, donde arranca la mecha del UL: cuando se baja a M15
a esperar el OB, la punta de esa mecha queda tan lejos que el riesgo es casi todo
mecha vieja. El FVG y la forma `ZONA` no cambian.

**Una y sólo una.** Una operación por ID de H1 y nunca más de una viva. El límite
se quita cuando muere el ID de H1 del que cuelga, cuando cambia el régimen de H4
o cuando **cierra el mercado el viernes**. La operación abierta la cierran el
objetivo, el stop o ese mismo cierre del viernes.

**El viernes se cierra todo.** Al cerrar el mercado —viernes a las 17:00 de Nueva
York— no queda nada vivo: la posición abierta se cierra **al precio de la última
vela de la semana**, sin esperar al stop ni al objetivo, y el límite puesto se
quita. El fin de semana no se opera y el hueco de la apertura del domingo no lo
decide ninguna regla de esta estrategia. El cierre semanal no manda sobre el
stop: si esa misma vela llegó al stop o al objetivo, la operación acabó ahí. Y la
última vela del histórico no es un cierre semanal aunque caiga en viernes —ahí lo
que se acabó son los datos—. El corte se resuelve sobre la última vela que hay
antes del viernes a las 17:00, no sobre una vela de las 17:00 que no existe: así
cae donde el histórico dice, con sus festivos y sus huecos.

**Lo que se ha supuesto y no está cerrado.** El propietario dijo «pon el stop
donde mejor lo veas y luego iremos afinando», así que queda declarado:

- el stop va a un borde de la zona de H1, sin holgura, y no al borde del patrón
  de M15: con éste salían stops de céntimos —un FVG de M15 mide a veces dos velas
  de nada— y con eso no se puede auditar un dibujo. Cuál de los dos bordes lo
  decide el setup: el exterior siempre, salvo en el rechazo afinado con un OB,
  donde el propietario pidió el interior por distancia;
- el límite va en el borde cercano del patrón, no dentro de él, salvo en ese
  mismo rechazo con OB;
- si una vela de M15 toca el stop y el objetivo, manda el **stop**; y una vela
  que llena el límite y alcanza el stop en el mismo cuarto de hora entra y sale
  perdiendo;
- **la franja de operativa de la 3.0 sigue puesta**: sólo se arma límite de 03:00
  a 12:00 de Nueva York. Uno ya puesto se llena a cualquier hora **de la
  semana**, porque una orden en el mercado no mira el reloj —pero el viernes se
  quita, que es cuando el mercado sí lo mira—. Si el propietario quiere las
  entradas de madrugada, se quita la franja y no se toca nada más;
- el OB le da al precio **tres velas de M15** para irse; es el único número que
  no sale de la geometría.

**En el explorador** son dos capas nuevas: «Entradas», con el límite punteado
mientras estuvo puesto, el rectángulo rojo del riesgo y el verde del objetivo
desde que entró hasta que salió y un punto en el final —✦ objetivo, ✕ stop y ⧗
el cierre del viernes—; y «Régimen de H4», el
fondo verde/rojo/gris que dice hacia dónde se buscaba en cada tramo. La **cuenta
simulada** abre en esta fase con **50 $ y el 17 %** —8,50 $ por operación, 25,50 $
de objetivo—, que es con lo que el propietario quiere mirarlas; el dinero escrito
en cada operación sale de ahí y cambiarlo no cambia ni una operación.

### Fase 3.0 — señales de entrada

No emite operaciones y es a propósito: lo que se entrega es la capa «Cascada de
entrada» del explorador, para que el propietario mire si la máquina está viendo
lo que ve él antes de que nada abra una posición.

**Son dos escalones, no tres.** El Diario no es un paso de la cascada: no hace
falta tocar su zona en contra para poder mirar H4. La lectura, con un ID **alcista** (el
bajista es su espejo):

1. el precio **toca la zona en contra de un ID de H4** —el `TOQUE_PUL` de la
   fase 2.0, sin redefinirlo, sobre el PUL o el APUL de ese ID— y se abre la
   búsqueda en H1;
2. en H1 se **espera a que el ID de H1 vaya en la misma dirección que el de H4**:
   si al bajar manda un ID bajista, hay que esperar a que se rompa y se constituya
   el alcista. En cuanto ese ID de H1 existe, se marca su PUL.
   Da igual que el ID de H1 ya viniera alineado al llegar el toque: lo que se pide
   es que lo esté. **Una confirmación por ventana y ni una más.**
3. y **el precio toca ese PUL de H1: ahí salta la señal**. Es el mismo `TOQUE_PUL`
   de la fase 2.0, leído sobre el PUL de H1 y sin redefinir nada. **No se espera
   al cierre de la vela de H1**, igual que no se espera al de la de H4 ni al de
   la diaria: la señal se fecha en la vela fina —M15— en la que el precio entró
   en la zona. Se espera **mientras dure la ventana de H4** y no más: cerrada la
   búsqueda, el PUL de H1 marcado deja de valer aunque su ID siga vivo. **Un toque
   por confirmación y ni uno más.**

**La rotura del ID, tal como la fija el propietario.** El lado a favor lo manda
el **UL**: el ID no cambia mientras una vela no **cierre más allá del rectángulo
entero**, así que una mecha que lo perfora y vuelve a cerrar dentro no rompe
nada. El lado en contra se queda en el **ancla**, que es donde arranca el ID: ahí
manda la línea y no la zona. La zona en contra —el PUL o el APUL— se sigue
marcando y dibujando porque de ella cuelgan el toque de la cascada y el veto
diario, pero ya no mata a ningún ID. Es `break_by_zone: true` con
`break_against_by_zone: false`, y lo fija la corrida de la fase, no el YAML.

**El lado en contra ya no es siempre el PUL.** El PUL es el UL del ID
inmediatamente anterior **cuando aquél iba en el mismo sentido**: su extremo
quedó por detrás y es el nivel al que el precio vuelve. Si iba al revés hay que
mirar dónde quedó ese extremo: cuando es el ancla de éste no sirve y se
**hereda** la zona en contra que llevaba aquel ID, y cuando quedó por detrás del
ancla —murió por rotura a favor y el giro lo trajo una constitución abortada
posterior— el nivel es su propio UL. Las dos se llaman APUL. La cascada lee «la zona en contra» sin
preguntar cuál de las dos es. Además la **punta** de esa zona ya no es la de una
sola vela: llega hasta la mecha más lejana que el precio alcanzó mientras el ID
que la fijó estuvo vivo, sin contar la vela que lo rompió. Los detalles y los
casos límite están en [`MODULO_2_ZONAS.md`](MODULO_2_ZONAS.md).

**H1 lleva ID propio en esta fase.** Es el cambio de fondo: en H1 se marca el ID
exactamente igual que en el Diario y en H4 —mismo detector, mismas reglas, mismas
dos zonas—, así que el segundo escalón es estructura y no un patrón de velas. El
turtle soup y el OB «de dos oportunidades» que confirmaban antes están **borrados
del proyecto**. El **UL de H1 se dibuja pero no interviene**: la cascada sólo lee
el PUL.

Encender el detector de H1 mete su temporalidad en el hash de configuración, así
que la fase 3.0 corre con un hash distinto del de la línea base `e27d20d0fa4e` y
a propósito: el reparto por defecto de las fases 1 y 2 no se toca.

**El Diario veta, no autoriza.** Mientras el precio esté **dentro** del PUL de un
ID diario no se mira ninguna confirmación que vaya en contra de él: en el PUL de
un ID diario alcista, todo toque bajista de H4 se desecha hasta que el precio
salga de esa zona. Fuera de esos tramos vale cualquier toque de H4, alcista o
bajista. El tramo del veto y los toques descartados se dibujan para poder
auditarlo.

**La búsqueda dura hasta romper el PUL o hasta llegar al UL.** Decisión del
propietario. La ventana se abre en el toque y se cierra con lo primero de tres:

1. una vela de H4 **cierra más allá del borde exterior de la zona en contra**
   —el PUL o el APUL—, o sea la atraviesa entera;
2. el precio **toca el UL del mismo ID de H4** —el extremo, el sitio al que se
   iba—, y basta con tocarlo: se mide con mechas en la vela fina y no espera al
   cierre de la vela de H4. Es lo que impide seguir buscando indefinidamente
   cuando H1 no llega a girar o gira demasiado tarde porque el precio sube sin
   parar;
3. muere el ID de H4.

**Salir del PUL hacia arriba no cierra nada**: el precio puede irse a favor y la
búsqueda sigue viva hasta el UL. Cada visita nueva la vuelve a armar y anula la
anterior; si al volver no ha nacido ningún ID de H1 nuevo, se vuelve a coger el
mismo con su mismo PUL.

El **tramo diario del veto se mide distinto y es a propósito**: ahí la pregunta es
estar dentro del PUL diario o no estarlo, así que se levanta con el cierre de una
vela **diaria** fuera de la zona, por el lado que sea.

Parámetros abiertos declarados: que valga un ID de H1 que **ya venía alineado**
cuando el precio tocó la zona de H4 —lo decidió el propietario, y es lo que hace
que muchas marcas caigan en la misma vela del toque— y que una confirmación cierre
la ventana, de modo que lo que llega después no se dibuja aunque el motor lo vea.

**Las zonas tienen su propio reparto en el explorador.** No es el de los
impulsos: en **M15** se dibujan el PUL y el UL **de H1** y sólo ésos —ahí se fecha
el toque que dispara la señal, y la caja de H4 a esa escala es cuatro velas de
alto que sólo tapan—, y en **H1** se dibujan las suyas **y las de H4**, para ver
si el precio está dentro de la zona grande. Cada temporalidad lleva **su color**
—violeta el Diario, naranja H4, azul H1—, con el UL en el tono fuerte y el PUL
aclarado, y el fondo al 25-30 % para que se entienda sin tapar el precio. Es
DIBUJO de zonas ya calculadas: no mide ningún toque, no añade ninguna señal y no
cambia la cascada.

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

### Fase 2.0 — zonas UL y PUL, sólo detección

Tampoco es una estrategia registrada con `@register`: no emite señales. Añade
dos zonas de precio a cada impulso ya detectado y las dibuja. El detalle
completo —reglas, casos límite, garantía anti-lookahead y procedimiento— está en
[`MODULO_2_ZONAS.md`](MODULO_2_ZONAS.md).

La línea base de la fase 1 se conserva intacta y verificada: mismo hash
`e27d20d0fa4e` y mismos 401 / 2.027 impulsos con las zonas encendidas y
apagadas. Encenderlas no puede mover un impulso, y el comando lo comprueba antes
de escribir nada.

> **Las cifras de abajo son de la definición anterior de la segunda zona** —el
> OB, la vela del ancla entera de `low` a `high` y con confirmación—. El
> propietario la sustituyó por el **PUL** (la vela del extremo del ID
> inmediatamente anterior, por el tramo que mira al ID nuevo: su cuerpo si aquel
> ID iba al revés, su mecha —el UL viejo— si iba en el mismo sentido) y todas las
> mediciones de esta fase y de la 2.1 hay que **volver a generarlas**:
> `chronos structure zonas` y `chronos structure rotura-por-zona`.
> Lo único que no cambia es lo que sólo depende del UL.

Lo que salió de medirla con el OB:

- **El 5,4 % / 5,0 % / 4,4 % de los ID (D / H4 / H1) nunca llegaba a tener OB
  confirmado.** Con el PUL esa cifra se va a **uno por temporalidad**: sólo se
  queda sin zona el primer ID del histórico, que no tiene ID anterior.
- **El UL se extiende a la vela siguiente en un 54 % / 44 % / 44 % de los casos.**
  La regla de la vela de margen no es un detalle de borde: afecta a la mitad de
  las zonas. **Esta cifra sigue valiendo**: sólo depende del UL.
- **El OB era mayor que el UL en el 90,0 % / 84,7 % / 85,2 %**, porque incluía la
  vela entera. El PUL es sólo un tramo de otra vela —su cuerpo, o su mecha cuando
  el ID anterior iba igual—, así que la comparación hay que rehacerla.
- **El solape entre las dos zonas del mismo ID era del 10,0 % / 7,4 % / 9,2 %.**
  Con el PUL en el extremo anterior, la población que se solapa es otra.
- **Anticipo de la fase 2.1 (sólo medición):** el 44,1 % / 41,9 % / 45,8 % de las
  roturas del histórico cerraron dentro de su zona sin atravesarla entera. La
  mitad a favor (50-55 %) sigue en pie —es el UL—; la de en contra (29-35 %) hay
  que volver a medirla contra el PUL.

Antes de pasar a la fase 2.1 hace falta que el propietario audite las capturas y
el explorador y confirme que las zonas se dibujan donde él las dibuja.

### Fase 2.1 — la zona decide la rotura

> Las cifras de esta sección se midieron con el **OB**. Sustituido por el PUL,
> hay que volver a generarlas.

Primer **cambio de comportamiento** del proyecto desde que se fijó la línea base.
Hasta aquí un ID moría cuando una vela cerraba más allá de una de sus dos líneas.
A partir de `break_by_zone: true` la línea deja de ser el nivel de rotura cuando
existe una zona que la sustituya:

| Lado | Si la zona existe | Si no existe |
|---|---|---|
| A favor (extremo) | manda el **UL**, que existe siempre | — |
| En contra (ancla) | manda el **PUL** si el ID tiene uno | manda la línea (sólo el primer ID) |

Romper una zona es **cerrar más allá de su borde exterior**, atravesándola
entera: perforarla con mecha y cerrar dentro no rompe, y cerrar dentro tampoco.
Un UL de altura cero tiene los dos bordes en la línea y se comporta exactamente
como ella.

No es un filtro posterior: es un cambio en la máquina de estados. Salvar una
rotura deja el ID vivo y **su extremo sigue extendiéndose**, pero **ninguna de
las dos zonas se remarca**: el UL lo fija la vela del extremo con la que el ID se
constituyó y el PUL, la del extremo del ID anterior. Por eso la comparación de abajo son dos
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
- **Casi toda rotura es por zona.** Por línea sólo morían 2 / 7 / 23 ID en todo el
  histórico (0,8 % / 0,6 % / 0,6 % de las roturas), y siempre por el mismo motivo:
  su OB nunca llegó a confirmarse. Con el PUL ese motivo desaparece salvo en el
  primer ID de cada temporalidad. En el lado a favor no ocurre nunca, porque el
  UL existe siempre.
- **Las zonas solapadas se caen solas**, que era la hipótesis: del 10,0 % / 7,4 % /
  9,2 % de los ID con OB al 1,7 % / 1,2 % / 1,9 % (37 → 4, 135 → 14, 637 → 79).
- **`OVERLAP_PRIORITY` es cosmético sobre estos datos, y hay una razón.** El
  conflicto de evaluación simultánea **no se da ni una vez**. Que dos zonas se
  solapen no basta: para cumplir las dos condiciones hacen falta los dos bordes
  exteriores *invertidos*, y eso es lo contrario de solaparse. En un ID alcista se
  cumple siempre `borde exterior del UL >= extremo > ancla >= borde exterior del
  PUL` mientras el rango sea positivo, y con la regla nueva no queda ningún impulso
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
Diario → H4 → H1 → M15, las tres definiciones de rechazo, el turtle soup, el PUL
suelto de M15, la ejecución sobre M1, los costes, las métricas en R, el informe,
los arquetipos y sus capas del explorador. También el comando
`chronos structure entradas` y el bloque `entries:` del YAML. Con él se han ido
las cifras que publicaba: el embudo, el resultado por configuración y los
arquetipos ya no están medidos por nada que corra hoy.

La razón no es un bug: la cascada se montó sobre una lectura de la estrategia que
hay que volver a fijar antes de escribir una línea de código. Lo que toca ahora,
y en este orden, es comprobar que la base se entiende:

1. **el toque de una zona** —cuándo el precio toca un UL y cuándo toca un PUL, y en
   qué se distingue tocar de atravesar—. El explorador ya lo dibuja: la capa
   «Señales de zona» marca el toque del PUL, el rechazo del UL y la rotura del UL
   sobre el Diario y H4. Son **dibujo**, no entradas
   ([MODULO_2_ZONAS.md](MODULO_2_ZONAS.md#señales-de-zona-sólo-dibujo));
2. **los ID** —qué ID está vigente en cada instante y con qué identificador;
3. **la multitemporalidad** —qué dice el Diario mientras H4 dice otra cosa, y cómo
   se lee eso sobre H1 y M15, que entonces no llevaban ID propio (a H1 se lo
   devuelve la fase 3.0).

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
