# Módulo 2 — ZONAS UL Y OB (fase 2.0: sólo detección)

Cada impulso dominante lleva asociadas dos zonas de precio, calculadas sobre
velas de **su misma temporalidad** (D, H4, H1; en M15 no, porque no lleva
detector). Esta fase las **detecta y las dibuja**. No hace nada más.

En la fase 2.1 estas zonas pasarán a decidir la vida y la muerte de los
impulsos, así que su detección tiene que estar auditada y confirmada por el
propietario **antes** de darles ese poder. Ése es todo el motivo de que esta
fase exista por separado.

## Lo que esta fase NO hace

- No toca la lógica de detección del impulso dominante.
- No cambia la regla de rotura del ID: sigue siendo **por línea**.
- No implementa FVG.
- No genera señales, entradas, stops ni targets.
- No optimiza ni busca parámetros.

**Línea base preservada y verificada:** `config_hash = 8e51cd9140c8`,
D 401 / H4 1.914 / H1 7.231 detectados, 392 / 1.910 / 7.224 publicados. Con las
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
mecha. Con la regla de la fase 2.1 se comportaría igual que la línea de la fase
1. Se cuentan aparte en el informe.

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
esos impulsos se romperán por línea. Es la cifra que manda de esta fase:
**5,4 % en D, 5,0 % en H4 y 4,4 % en H1**.

### Interior y exterior

Significan lo mismo en las dos zonas: el borde **interior** es el que un precio
que sale del rango encuentra primero, y el **exterior** el que tiene que cruzar
para dejar la zona atrás. En un ID alcista el UL se recorre hacia arriba (cuerpo
→ mecha) y el OB hacia abajo (`high` → `low`), porque la rotura a favor sube y la
rotura en contra baja. Es la lectura que necesita la fase 2.1.

## Casos límite y qué se decidió

| Caso | Decisión | Recuento |
|---|---|---|
| UL de altura cero | Zona degenerada, no se descarta | 1 / 4 / 27 (D/H4/H1) |
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

Vive en la propia zona y en el `ZoneBook`, que es la puerta por la que leerá la
fase 2.1:

1. consultar una zona antes de `ts_nacimiento_zona` → `LookaheadError`;
2. consultar un OB antes de su `ts_confirmacion` → `LookaheadError` (y uno que
   nunca se confirmó, también, con un mensaje propio: «no existe» no es lo mismo
   que «no lo he calculado»);
3. leer el borde exterior de un UL extendido antes de que cierre la vela de
   margen → `LookaheadError`.

Los tres tienen test que los provoca a propósito. Además, un test estructural
comprueba sobre el histórico entero que `ts_outer_known <= ts_birth` y
`ts_defining <= ts_birth` para todas las zonas.

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
interpreta ningún resultado: el informe presenta números e imágenes y la decisión
de darles poder sobre los impulsos —la fase 2.1— es suya.
