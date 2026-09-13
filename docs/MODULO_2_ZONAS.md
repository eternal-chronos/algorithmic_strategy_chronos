# Módulo 2 — ZONAS UL, PUL Y APUL (fase 2.0: sólo detección)

Cada impulso dominante lleva asociadas dos zonas de precio, calculadas sobre
velas de **su misma temporalidad** (D y H4, las dos únicas con detector; en H1 y
en M15 no hay ID y por tanto tampoco zonas propias). Esta fase las **detecta y
las dibuja**. No hace nada más.

En la **fase 2.1** estas zonas pasaron a decidir la vida y la muerte de los
impulsos, y esta fase existe por separado precisamente para que su detección
pudiera auditarse **antes** de darles ese poder. Lo que hicieron con él está en
[`FASES.md`](FASES.md#fase-21--la-zona-decide-la-rotura); nada de lo de aquí ha
cambiado: la fase 2.1 no toca cómo se detecta una zona, sólo qué se hace con ella.

## Lo que esta fase NO hace

- No toca la lógica de detección del impulso dominante.
- No cambia la regla de rotura del ID: sigue siendo **por línea**.
- No implementa FVG.
- No genera entradas, stops ni targets. Las «señales de zona» de más abajo son
  marcas de dibujo para auditar el toque; no operan nada.
- No optimiza ni busca parámetros.

**Línea base preservada y verificada:** `config_hash = e27d20d0fa4e`,
D 401 / H4 2.027 detectados, 392 / 2.023 publicados. Con las
zonas encendidas salen exactamente los mismos números y el mismo hash; con
`zones.enabled: false` la fase 1 sale además byte a byte —tabla, eventos, estado
por barra y diagnósticos—. Los dos casos tienen test.

## Las tres zonas

Las tres son **tramos de mecha**: van del **borde del cuerpo** a la **punta de la
mecha** de la vela de un extremo y nunca cubren el cuerpo. Lo único que cambia
entre ellas es de qué extremo salen y desde qué lado se leen.

### UL (último)

El extremo del ID, sobre la vela que fija `precio_extremo` (la que el detector
registra en `ts_extreme`).

- **ID alcista** — del **techo del cuerpo** `max(open, close)` hacia arriba hasta
  la **punta de la mecha superior** `high`.
- **ID bajista** — de la **base del cuerpo** `min(open, close)` hacia abajo hasta
  la **punta de la mecha inferior** `low`.

El borde interior coincide exactamente con la línea del ID.

**Extensión.** Si la vela inmediatamente posterior tiene la mecha más extrema en
la misma dirección, la zona se estira hasta ella. **Una sola vela de margen.** El
borde interior no se mueve. La comparación es estricta: un empate no extiende.

**Nace** al constituirse el ID. Durante el limbo no existe ninguna zona. Y **no
se remarca mientras el ID vive**: es el nivel contra el que se juzga su rotura a
favor, así que no puede moverse bajo los pies del ID vigente aunque el extremo se
estire o aunque el precio deje mechas más largas.

**Altura cero.** Si la vela del extremo cerró en su propio máximo (o mínimo), no
hay mecha y la zona mide cero. **Decisión: se conserva como zona degenerada**,
con los dos bordes en el mismo precio, que es exactamente la línea del ID.
Descartarla diría que el ID no tiene UL, y sí lo tiene: lo que no tiene es
mecha. Se cuentan aparte en el informe.

### PUL (penúltimo)

El **UL del ID inmediatamente anterior**, y sólo cuando aquel ID iba **en el
mismo sentido** que éste: murió por rotura a favor, éste nació más allá y su
extremo quedó **por detrás**, que es el lado en contra. La zona es esa mecha, con
interior la punta y exterior el borde del cuerpo.

Si el ID anterior iba **al revés**, su extremo no queda detrás sino delante —es
el ancla de éste— y no sirve de nivel al que volver: ahí no hay PUL y manda el
APUL de la sección siguiente.

**La punta se lleva toda la vida de aquel ID.** El UL de un ID vivo mide sólo su
vela del extremo (más la de margen), pero cuando ese ID ha muerto y su zona pasa
a ser el PUL del siguiente, la punta se estira hasta la **mecha más lejana que el
precio alcanzó mientras aquel ID estuvo en pie**: desde el arranque de su pierna
hasta la vela **anterior** a la que lo rompió. Es mecha que el precio ya visitó
con aquel ID vigente y que el borde de una sola vela dejaba fuera. La vela que
rompe queda fuera a propósito: es la que se llevó el nivel por delante, no una
mecha del ID. **El borde del cuerpo no se mueve nunca**, así que el PUL contiene
siempre al UL viejo y nunca es más pequeño que él.

**Nace** con la constitución del ID: sus velas cerraron antes, así que no hay
nada que esperar y no se confirma. Y **no hereda la vela de margen del UL**: esa
regla es la del extremo recién fijado.

**Un ID sin zona en contra es un estado legítimo** y se registra: le pasa a los
primeros ID de cada temporalidad, hasta que aparece el primer PUL de la cadena
(ver la sección siguiente). Esos impulsos se rompen por línea en el lado en
contra, y son los únicos que lo hacen.

> **Ojo con el PUL en la fase 2.1.** Cae DENTRO del rango del ID nuevo —por
> encima de la línea del ancla en un ID alcista—, así que ahí la zona no evita la
> rotura en contra: la **adelanta**. Con la regla de la fase 3.0 ese lado ya no
> se rompe por zona y el PUL sólo se dibuja y se toca.

### APUL (antes penúltimo)

El nivel en contra cuando no hay PUL, y son **tres casos**.

**1. El ID anterior iba al revés y su extremo es el ancla de éste: se hereda su
zona.** Ese extremo no queda detrás, así que el nivel que vale es el que aquel
ID llevaba en su propio lado en contra. Se hereda tal cual: las mismas velas, los
mismos dos precios y la misma ventana de mecha, leídos desde este lado —lo que
era la punta pasa a ser el borde exterior, y al revés—. La cadena puede venir de varios ID atrás; si llega hasta
uno que tampoco tenía zona, éste se rompe por línea. Por eso los ID sin zona en
contra son siempre los del **arranque** de la temporalidad: en cuanto un ID
continúa a otro nace el primer PUL y a partir de ahí ya no vuelve a faltar.

**2. El ID anterior iba al revés pero su extremo quedó por detrás del ancla: es
su UL.** Aquel ID no murió de un giro sino por **rotura a favor** —el precio
siguió en su sentido— y el giro lo trajo después una **constitución abortada**,
así que el ancla de éste se fijó **más allá** de aquel extremo. Entonces sí queda
un nivel detrás, y es el suyo: la zona es su UL, la vela de su extremo con la
punta estirada a toda su vida, exactamente igual que un PUL. Se llama APUL
porque aquel ID iba al revés, y por eso su mecha apunta al lado de la rotura en
contra: el precio que vuelve encuentra antes el **borde del cuerpo** y la punta
es lo que hay que cruzar.

**3. Constitución abortada con el ID anterior en el mismo sentido: se busca en el
retroceso.** Ahí el ID anterior va en el mismo sentido que éste, pero **no por
continuación**: en medio había un ID
contrario que iba a constituirse y una vela lo mató antes de que naciera. Ese ID
que no llegó a existir es el que tenía que dar el nivel, así que se va a buscarlo
donde estaba: se corre la máquina de ID **dentro del retroceso del ID anterior**
—de su extremo a la vela que lo rompió, mismas velas y mismos modos— y se toma el
UL del **último ID interior contrario**, con su punta estirada a la vida de aquel
ID interior. Es una corrida sobre velas ya cerradas y anteriores a la
constitución, así que no mira al futuro; y no anida: la máquina interior no
vuelve a buscar otro APUL. Si dentro de aquel retroceso no llegó a nacer ningún
ID contrario, no hay APUL y el ID se queda con su PUL; el detector lo cuenta en
`apul_sin_id_interior`.

**Sustituye al PUL, no lo acompaña**: un ID lleva una zona en contra y sólo una.

> **Ojo, igual que con el PUL.** El APUL también cae DENTRO del rango del ID
> nuevo, así que **adelanta** la rotura en contra en vez de evitarla. Desde la
> fase 3.0 ese lado se rompe por el ancla y esto ya no mata a nadie.

### Interior y exterior

Significan lo mismo en las tres zonas: el borde **interior** es el que un precio
que sale del rango encuentra primero, y el **exterior** el que tiene que cruzar
para dejar la zona atrás. En un ID alcista el UL se recorre hacia arriba (cuerpo
→ mecha) y la zona en contra hacia abajo, porque la rotura a favor sube y la
rotura en contra baja.

Cuál es cuál lo decide **hacia dónde miraba la mecha**, que es el sentido del ID
que la fijó:

- apunta en el sentido de este ID —el PUL, y el APUL heredado de un giro—: el
  precio que vuelve en contra encuentra antes la **punta**;
- apunta al contrario —el APUL del extremo del ID contrario anterior y el de un
  ID interior del retroceso—: encuentra antes el **borde del cuerpo**, y la punta
  es lo que hay que cruzar.

Por eso el PUL es el UL viejo con los bordes cambiados de papel: los mismos
precios, recorridos al revés. Romper es cerrar más allá del borde **exterior**.

## Casos límite y qué se decidió

| Caso | Decisión | Recuento |
|---|---|---|
| UL de altura cero | Zona degenerada, no se descarta | se cuenta en el informe |
| ID sin zona en contra | Estado legítimo; consultarlo lanza `LookaheadError` | los del arranque de cada temporalidad |
| Zona en contra de altura cero | Zona degenerada, igual que en el UL | se cuenta en el informe |
| PUL (el ID anterior iba igual) | El UL viejo, con la punta de toda su vida | la continuación de tendencia |
| APUL heredado (el anterior iba al revés y su extremo es el ancla) | La zona en contra de aquel ID, tal cual | cada giro de tendencia |
| APUL del extremo contrario (el anterior iba al revés y su extremo quedó detrás del ancla) | El UL de aquel ID, con la punta de toda su vida | ~11 % de los giros |
| APUL del retroceso (nació tras una abortada, el anterior iba igual) | El UL del último ID interior contrario | poco frecuente |
| Le tocaba APUL del retroceso y no había ID contrario dentro | Se queda con su PUL, y se cuenta | `apul_sin_id_interior` |
| Doji en la vela de una zona | No puede ocurrir: el doji no fija ningún extremo | 0 |

**Por qué la zona en contra no se confirma.** Sus velas cerraron antes de que el
ID naciera —son las de un ID que ya había muerto, o las de uno que se quedó
dentro de un retroceso—, así que el borde contra el que se juzga al ID es el
mismo desde su primera vela hasta la última. La confirmación era una condición
del OB, que colgaba de la vela del ancla; aquí no tiene equivalente.

**La zona en contra puede quedar entera por detrás de la línea del ancla.** El
ancla sale de la vela donde arranca la pierna y la zona en contra de la vela del
extremo de otro ID, que son dos velas distintas: entre la línea y el borde
interior de la zona puede haber un hueco. Un cierre que caiga ahí no rompe —no ha
atravesado la zona— aunque tampoco esté dentro de ella.

## Garantía anti-lookahead

Vive en la propia zona y en el `ZoneBook`. La fase 2.1 no lee por ahí —necesita
las zonas barra a barra dentro de la máquina de estados y usa su propio
`ZoneBreakLevels`, con la misma garantía— pero las cuatro vías siguen vivas para
quien mida o dibuje:

1. consultar una zona antes de `ts_nacimiento_zona` → `LookaheadError`, y la zona
   en contra
   de un ID que no lo tiene, también, con un mensaje propio: «no existe» no es lo
   mismo que «no lo he calculado»;
2. leer el borde exterior de un UL extendido antes de que cierre la vela de
   margen → `LookaheadError`;
3. pedir cualquiera de las dos con la frontera de barras por detrás de su vela.

Los tres tienen test que los provoca a propósito. Además, un test estructural
comprueba sobre el histórico entero que `ts_outer_known <= ts_birth` y
`ts_defining <= ts_birth` para todas las zonas.

### El extremo se mueve, el UL no (fase 2.1, §3.2)

Un ID vigente que se salva por el lado a favor **estira su extremo** hasta el
cuerpo de la vela que lo salvó. **El UL se queda donde estaba:** lo fija la vela
del extremo con la que el ID se constituyó y no se remarca en cada rechazo, así
que un ID que se estiró dos veces sigue teniendo un solo UL —el primero— y ése es
el que va a `zonas.csv`, el que se dibuja y el que decide su rotura a favor.

Fue un ajuste posterior a la primera versión de la fase 2.1, que sí remarcaba la
zona. Remarcándola, cada rechazo alejaba el borde exterior contra el que se
juzgaba al ID y el ID podía ir subiendo escalón a escalón sin morirse; con la
zona quieta el mismo borde juzga todas las velas de su vida y cada rechazo lo
deja más cerca de romperlo.

Dos consecuencias que se ven en el dibujo:

- **El borde interior del UL deja de coincidir con la línea del extremo** a
  partir de la primera extensión. La línea sigue yendo **en escalera** —eso no ha
  cambiado, y pintarla recta desde la constitución enseñaría un precio al que el
  mercado todavía no había llegado— así que en un ID estirado la línea va por
  delante de su zona.
- **El rectángulo del UL es uno y va entero de la constitución al fin del ID.**
  No hay tramos ni escalones de zona que contar.

## Señales de zona (sólo dibujo)

Tres marcas sobre las zonas ya detectadas, en el Diario y en H4 —las dos únicas
temporalidades con ID y por tanto con zonas—. **No son señales operativas:** el
proyecto sigue sin entradas, sin stops y sin targets, y ningún módulo del motor
las lee. Existen para auditar el punto 1 de la lista de la fase 3, *cuándo el
precio toca una zona y en qué se distingue tocar de atravesar*, sin tener que
abrir un CSV.

| Marca | Cuándo sale |
|---|---|
| `TOQUE_PUL` (pentágono) | el precio **venía de fuera** y el rango de la vela corta la **zona en contra** —el PUL o el APUL—, bordes incluidos y cierre donde cierre. Se mide en la **vela fina**, no al cierre de la del ID |
| `RECHAZO_UL` (hexagrama) | el precio **venía de fuera**, el rango corta la zona UL y el cierre **no** pasa de su borde exterior |
| `ROTURA_UL` (rombo-estrella) | el cierre queda más allá del borde exterior del UL |

Detalles que se decidieron y no se esconden:

- **Tocar es cosa de mechas**, como en la sección D: basta con que `[low, high]`
  corte la zona. **Romper es cerrar más allá del borde exterior**, con la misma
  desigualdad estricta que la rotura de la fase 2.1 —cerrar justo en el borde es
  cerrar dentro— así que rechazo y rotura se excluyen: la vela que rompe no
  rechaza.
- **El toque del PUL no espera al cierre de la vela grande.** Tocar es un asunto
  de mechas: en cuanto el precio entra en la zona ya está tocada, y esperar
  cuatro horas a que cierre la vela de H4 sería fechar la señal tarde —y, si
  algún día hay entradas detrás, tarde de verdad—. Pero la vela del ID sigue
  mandando, porque es la que dice si el precio había **salido** de la zona. Las
  dos cosas: el toque se clasifica en la temporalidad del ID —una señal por vela
  y sólo si el cierre anterior estaba fuera— y luego se **re-fecha** en la vela
  de la serie más corta de la corrida (M15 en el reparto por defecto) en la que
  el precio entró, con los números de esa vela, que son los que se conocen en
  ese instante. La marca cae *dentro* de la vela grande, en el minuto en que
  tocó, y un paseo de quince minutos por encima del borde no inventa una visita
  nueva. El rechazo y la rotura del UL están definidos por dónde **cierra** la
  vela, así que ésos se quedan enteros en la temporalidad del ID. El explorador
  lo sabe —cada señal viaja con la temporalidad en la que se midió— y el replay
  descubre el toque al cerrar su vela de M15, no la de H4.
- **La señal se cobra desde fuera, en las dos zonas.** La vela que fija el UL cierra en su borde
  interior —ese borde *es* su cuerpo—, así que el precio nace **dentro** de la
  zona. Si la vela siguiente la toca no la está rechazando: sigue metida ahí y
  nunca llegó desde fuera. Lo mismo en el PUL: una vela que **abre dentro** de la
  zona no la está tocando, ya estaba. Para que un `TOQUE_PUL` o un `RECHAZO_UL`
  cuenten, el cierre anterior tiene que estar fuera de la zona **y por el lado
  por el que el precio la busca**: el UL se busca hacia donde va el ID y el PUL
  hacia el lado contrario, así que en un ID alcista se llega al UL desde abajo y
  al PUL desde arriba. Cerrar dentro de la zona desarma la señal hasta que el
  precio vuelva a salir, y volver desde el otro lado del borde exterior tampoco
  la arma: eso es un nivel ya roto, no un rechazo. Sólo se mira un cierre
  anterior al tramo, el de la vela del nacimiento de la zona, que ya está en el
  pasado.
- **No hay «rotura del PUL».** Atravesar el PUL es la rotura en contra que el
  detector ya marca; duplicarla aquí sería contar dos veces lo mismo. Una vela
  que se lleva el PUL por delante sale como `TOQUE_PUL`, con el marcador plantado
  en el borde que cruzó.
- **`ROTURA_UL` es geometría, no la regla de rotura.** Con `break_by_zone: true`
  cae exactamente sobre las roturas a favor de los ID **zonificados** —hay test
  que lo comprueba— y con la regla apagada sigue existiendo, porque el UL sigue
  estando dibujado aunque no decida nada. Los ID de calentamiento mueren igual y
  su rotura sale en los eventos, pero la fase 2.0 no les calcula zonas, así que
  no tienen UL que romper y no dejan señal: 2 en D y 1 en H4 sobre 2018–2025.
  El explorador tampoco los dibuja.
- **Sin lookahead.** Cada señal se busca desde la vela **siguiente** al
  nacimiento de su zona —igual que los contactos de la sección D— y hasta la
  vela que mata al ID, incluida: es justo la que puede llevar la rotura.
- **Se apagan.** Capa propia en el explorador, con su entrada en la leyenda y su
  recuento en el texto de estado. Obedecen el selector de ID visibles; el filtro
  de zonas no, porque son marcas puntuales y no rectángulos que tapen el precio.

Viven en `domain/structure/zone_signals.py` (la geometría, pura) y en
`application/structure/zone_signals.py` (el recorrido de la corrida). Un test de
capas comprueba que **ningún** otro módulo de `domain/` ni de `application/` las
importa: encenderlas no puede mover un impulso, ni una zona, ni una rotura.

## Configuración

```yaml
zones:
  enabled: false   # por defecto
```

No hay más parámetros y es a propósito: las dos zonas se derivan de velas que el
detector ya registra, sin holguras, umbrales ni ventanas que ajustar. **No existe
una configuración que produzca zonas distintas**, sólo zonas o ninguna zona, y
por eso este bloque queda fuera de `config_hash`. Eso es lo que mantiene
comparables las corridas archivadas de la fase 1 con las de ahora.

## Cómo se corre

```bash
# Fase 2.0 completa: informe §7, tabla de zonas, explorador y capturas del §8.
.venv/bin/chronos structure zonas -o now/fase20

# Sin capturas (Kaleido abre un navegador headless por imagen).
.venv/bin/chronos structure zonas --sin-capturas -o now/fase20

# `detect` obedece el YAML sin discutir: con `zones.enabled: false` no emite nada.
.venv/bin/chronos structure detect
```

El comando `zonas` enciende las zonas para su propia corrida —correrlo con ellas
apagadas no produciría nada— y lo dice. Con `--respetar-config` obedece el YAML.
Antes de escribir nada imprime la comprobación de que encender las zonas no ha
movido ni un impulso, y aborta si la ha movido.

## Salidas

| Fichero | Qué lleva |
|---|---|
| `reporte_zonas.txt` | Las siete secciones del §7, por temporalidad y por año |
| `zonas.csv` | Una fila por zona con las columnas del §4 más auditoría |
| `roturas_y_zonas.csv` | Una fila por rotura con la zona que le tocaba (§7.7) |
| `explorador_zonas.html` | Las capas «Zonas UL», «PUL» y «APUL», activables por separado |
| `LEEME.txt` + PNG | Los cinco lotes de capturas del §8 |

## Estado

**Pendiente de auditoría visual del propietario.** No se recomienda nada ni se
interpreta ningún resultado: el informe presenta números e imágenes. La fase 2.1
ya les ha dado poder sobre los impulsos, así que auditar lo que se dibuja aquí
sigue siendo el paso que valida todo lo que viene detrás.
