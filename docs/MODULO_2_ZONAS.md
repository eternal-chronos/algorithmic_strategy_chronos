# Módulo 2 — ZONAS UL Y OB (fase 2.0: sólo detección)

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

## Las dos zonas

### UL (último)

El extremo del ID, sobre la vela que fija `precio_extremo` (la que el detector
registra en `ts_extreme`).

- **ID alcista** — del **techo del cuerpo** `max(open, close)` hacia arriba hasta
  la **punta de la mecha superior** `high`.
- **ID bajista** — de la **base del cuerpo** `min(open, close)` hacia abajo hasta
  la **punta de la mecha inferior** `low`.

El borde interior coincide exactamente con la línea del ID. La zona ocupa sólo
el tramo de mecha: **nunca cubre el cuerpo**.

**Extensión.** Si la vela inmediatamente posterior tiene la mecha más extrema en
la misma dirección, la zona se estira hasta ella. **Una sola vela de margen.** El
borde interior no se mueve. La comparación es estricta: un empate no extiende.

**Nace** al constituirse el ID. Durante el limbo no existe ninguna zona.

**Altura cero.** Si la vela del extremo cerró en su propio máximo (o mínimo), no
hay mecha y la zona mide cero. **Decisión: se conserva como zona degenerada**,
con los dos bordes en el mismo precio, que es exactamente la línea del ID.
Descartarla diría que el ID no tiene UL, y sí lo tiene: lo que no tiene es
mecha. Con la regla de la fase 2.1 se comporta igual que la línea de la fase 1,
y eso es lo correcto: no se trata aparte, se cuenta. Se cuentan aparte en el
informe.

### OB (order block)

La vela donde arranca la pierna, la que fija `precio_ancla` (`ts_anchor`).

- **Bordes:** la vela **entera**, de `low` a `high`. A diferencia del UL, **sí**
  cubre el cuerpo.
- **Confirmación:** no existe hasta que una vela posterior **del color del
  impulso** la supera incluyendo mecha —verde con `high` por encima del `high` del
  OB en un ID alcista; roja con `low` por debajo del `low` en uno bajista—. El
  doji no confirma: §2.2 lo declara neutro en todo el módulo. Con varias
  candidatas manda la primera cronológicamente. La búsqueda termina donde muere
  el ID.
- **Nace** cuando se cumplen las dos cosas: `max(constitución, confirmación)`.

**Un ID sin OB confirmado es un estado legítimo** y se registra. En la fase 2.1
esos impulsos se rompen por línea, y son los **únicos** que lo hacen. Es la cifra
que manda de esta fase: **5,4 % en D y 5,0 % en H4** (4,4 % en H1 cuando H1
llevaba detector).

### Interior y exterior

Significan lo mismo en las dos zonas: el borde **interior** es el que un precio
que sale del rango encuentra primero, y el **exterior** el que tiene que cruzar
para dejar la zona atrás. En un ID alcista el UL se recorre hacia arriba (cuerpo
→ mecha) y el OB hacia abajo (`high` → `low`), porque la rotura a favor sube y la
rotura en contra baja. Es la lectura que usa la fase 2.1: romper es cerrar más
allá del borde **exterior**.

## Casos límite y qué se decidió

| Caso | Decisión | Recuento |
|---|---|---|
| UL de altura cero | Zona degenerada, no se descarta | 1 / 4 (D/H4; 27 en H1 cuando lo llevaba) |
| ID que muere sin OB | Estado legítimo; consultarlo lanza `LookaheadError` | 21 / 95 / 316 |
| La vela que confirma es la que constituye | **Imposible por construcción** | 0 |
| Varias velas podrían confirmar | La primera cronológicamente | — |
| Doji en posición de OB | OB válido, pero con ancla A1 no puede ocurrir | 0 |

**Por qué el caso 7 es imposible.** La vela que constituye un ID es, por
definición, la primera **contraria** a la pierna, y la dirección del ID es la de
la pierna: en un ID alcista constituye una vela roja. La confirmación del OB
exige una vela **del color del impulso**, verde en un ID alcista. Las dos
condiciones se excluyen. No se ha inventado una lectura alternativa: se
implementó la regla literal y se cuenta el resultado. Hay un test que fija la
imposibilidad para que salte si alguien cambia la regla.

**Por qué el caso 8 sale cero.** Con el `ANCHOR_MODE = A1` del proyecto el ancla
sale de la última vela **contraria** previa a la pierna, y `is_counter_to` no
considera contrario a un doji: la vela del OB nunca puede serlo. Con `A2` sí
ocurre, porque el ancla es la primera vela de la pierna y los dojis no cortan la
racha. El día sintético construye las dos lecturas sobre las mismas velas.

## Garantía anti-lookahead

Vive en la propia zona y en el `ZoneBook`. La fase 2.1 no lee por ahí —necesita
las zonas barra a barra dentro de la máquina de estados y usa su propio
`ZoneBreakLevels`, con la misma garantía— pero las cuatro vías siguen vivas para
quien mida o dibuje:

1. consultar una zona antes de `ts_nacimiento_zona` → `LookaheadError`;
2. consultar un OB antes de su `ts_confirmacion` → `LookaheadError` (y uno que
   nunca se confirmó, también, con un mensaje propio: «no existe» no es lo mismo
   que «no lo he calculado»);
3. leer el borde exterior de un UL extendido antes de que cierre la vela de
   margen → `LookaheadError`.

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
| `TOQUE_OB` (pentágono) | el precio **venía de fuera** y el rango de la vela corta la zona OB, bordes incluidos y cierre donde cierre. Se mide en la **vela fina**, no al cierre de la del ID |
| `RECHAZO_UL` (hexagrama) | el precio **venía de fuera**, el rango corta la zona UL y el cierre **no** pasa de su borde exterior |
| `ROTURA_UL` (rombo-estrella) | el cierre queda más allá del borde exterior del UL |

Detalles que se decidieron y no se esconden:

- **Tocar es cosa de mechas**, como en la sección D: basta con que `[low, high]`
  corte la zona. **Romper es cerrar más allá del borde exterior**, con la misma
  desigualdad estricta que la rotura de la fase 2.1 —cerrar justo en el borde es
  cerrar dentro— así que rechazo y rotura se excluyen: la vela que rompe no
  rechaza.
- **El toque del OB no espera al cierre de la vela grande.** Tocar es un asunto
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
  nunca llegó desde fuera. Lo mismo en el OB: una vela que **abre dentro** de la
  zona no la está tocando, ya estaba. Para que un `TOQUE_OB` o un `RECHAZO_UL`
  cuenten, el cierre anterior tiene que estar fuera de la zona **y por el lado
  por el que el precio la busca**: el UL se busca hacia donde va el ID y el OB
  hacia el lado contrario, así que en un ID alcista se llega al UL desde abajo y
  al OB desde arriba. Cerrar dentro de la zona desarma la señal hasta que el
  precio vuelva a salir, y volver desde el otro lado del borde exterior tampoco
  la arma: eso es un nivel ya roto, no un rechazo. Sólo se mira un cierre
  anterior al tramo, el de la vela del nacimiento de la zona, que ya está en el
  pasado.
- **No hay «rotura del OB».** Atravesar el OB es la rotura en contra que el
  detector ya marca; duplicarla aquí sería contar dos veces lo mismo. Una vela
  que se lleva el OB por delante sale como `TOQUE_OB`, con el marcador plantado
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
| `explorador_zonas.html` | Las capas «Zonas UL» y «OB», activables por separado |
| `LEEME.txt` + PNG | Los cinco lotes de capturas del §8 |

## Estado

**Pendiente de auditoría visual del propietario.** No se recomienda nada ni se
interpreta ningún resultado: el informe presenta números e imágenes. La fase 2.1
ya les ha dado poder sobre los impulsos, así que auditar lo que se dibuja aquí
sigue siendo el paso que valida todo lo que viene detrás.
