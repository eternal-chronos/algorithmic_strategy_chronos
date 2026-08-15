# Módulo 3 — ENTRADAS (fase 3.0)

Primera fase que **decide operar**, y por tanto la primera que produce un
resultado. Todo lo anterior era estructura.

> **Nada de aquí recomienda un parámetro ni interpreta si la estrategia es buena
> o mala.** Se presentan números e imágenes; decide el propietario.

```bash
# Se monta sobre la fase 2.1: el comando enciende `break_by_zone` y las zonas
# para su propia corrida.
chronos structure entradas --config config/impulse.yaml
```

---

## 1. La cascada

**H4 es el motor.** El día a día se busca ahí. El Diario es contexto: confirma y
permite alargar, pero **nunca dispara**. H1 confirma. M15 afina.

### 1.1 Contexto diario (opcional)

Si el precio en Diario toca una zona (OB o UL) de su ID diario vigente, queda
**contexto diario activo** en la dirección de ese ID.

- **Tocar** = el `high`/`low` de la vela diaria alcanza cualquier punto de la
  zona. Basta el contacto; no hace falta cerrar dentro.
- Se lee al **cierre** de la vela diaria que tocó. Antes de ese cierre el
  contacto no se sabe.
- El contexto vive **mientras vive el ID diario que lo produjo**. No es una
  ventana inventada: es la única frontera que el enunciado nombra ("su ID diario
  vigente").
- **En conflicto manda H4.** El conflicto se registra en la señal y **no la
  descarta**: el desglose del §5.1 sólo puede confirmar o desmentir la hipótesis
  del propietario si las operaciones en conflicto están ahí.

### 1.2 Señal en H4

El precio toca una zona (OB o UL) del ID vigente de H4 y la zona queda **en
observación**. Dos desenlaces:

| Desenlace | Qué pasa | Dirección |
|---|---|---|
| **Respeto** | el precio reacciona en la zona sin romperla | a favor del ID de H4 |
| **Rotura y retesteo** | rompe la zona y **vuelve a testearla**. Sólo el UL | a favor de la rotura |

**Invalidación:** si la zona se rompe y no hay retesteo, la observación muere. En
el OB la rotura invalida **siempre**.

**La vela que rompe cierra primero.** Cuando se rompe una zona UL, el retesteo
sólo lo valida **otra vela de la temporalidad del ID, posterior** a la que
rompió. Un toque de la zona en una temporalidad menor dentro de esa misma vela
**no** vale, y la regla es la misma en Diario, H4 y H1. No es una comodidad: la
vela que atraviesa la zona entera pasa por ella obligatoriamente, así que sin
esta regla toda rotura vendría con retesteo gratis y la invalidación de arriba no
descartaría nunca nada. Está escrita una sola vez, en
`first_retest_after_break` (`domain/entries/zone_timeline.py`).

> Sobre la población medida no cambia ni una operación: la búsqueda del retesteo
> ya arrancaba en la vela siguiente a la de la rotura y sobre velas de la
> temporalidad del ID. Lo que cambia es que ahora la regla tiene nombre, sitio y
> test propio, y que la serie que se recorre se deriva de `impulse.timeframe` en
> vez de recibirse de fuera.

Dos detalles del código que no son evidentes y que sostienen la corrección:

- **El UL se mueve.** Con la rotura por zona un ID vigente extiende su extremo, y
  cada extensión cambia su UL. La observación usa el UL **vigente en cada barra**
  (`LastZoneTimeline`), reconstruido desde la traza de extensiones que el detector
  registra. Usar el último sería lookahead justo en las velas que más importan:
  las que extienden el extremo son las mismas que tocan la zona.
- **La rotura de la zona es la muerte del ID.** Con `BREAK_BY_ZONE = true`, cerrar
  más allá del borde exterior del UL *es* la rotura a favor. Así que la rotura de
  la observación no se recalcula: se lee de cómo murió el impulso. Y la zona que
  se retestea es la vigente al cierre **anterior** al de la rotura, que es el
  borde contra el que se juzgó esa vela.

### 1.3 Confirmación en H1

Con la zona en observación y **el precio dentro de ella**, se busca en H1, a favor
de la dirección esperada:

1. se constituye un **ID de H1** en esa dirección;
2. se forma un **OB de H1** en esa dirección (nace al confirmarse);
3. aparece un **rechazo** (§2).

Se toma la **primera** que aparece: es la única que el propietario podría haber
operado. Las tres valen por igual y no se ordenan por calidad.

### 1.4 Entrada

- **En H1**: la zona de H1, es decir el **OB** del ID de H1 vigente en la
  confirmación, siempre que vaya en la dirección buscada y ya haya nacido.
- **En M15**: un **OB suelto**, sin exigir ID de M15. Una vela de color contrario
  a la dirección buscada que después es **superada, mecha incluida**, por una vela
  del color de la dirección. La zona es esa vela entera.

**Las dos variantes se implementan y se miden por separado.**

---

## 2. ⚠️ Rechazos: tres definiciones, **ninguna adoptada**

El propietario ha delegado la formalización y no ha elegido. Las tres se
implementan, las tres se marcan y el motor no toma ninguna por defecto.

| Definición | Regla | Parámetros |
|---|---|---|
| `R1_mecha_en_zona_cierre_fuera` | la mecha entra en la zona y el cuerpo cierra fuera, a favor de la dirección | ninguno |
| `R2_mecha_dominante` | la mecha contra el movimiento supera el percentil P de la proporción mecha/cuerpo de H1, **sobre sesiones anteriores** | `RECHAZO_PERCENTIL` ∈ {60, 75, 90} |
| `R3_cierre_en_extremo` | el cierre queda en el tercio favorable del rango de la vela | ninguno |

- **El doji no rechaza en ninguna.** §2.2 lo declara neutro en todo el módulo, y
  excluirlo también de R1 —que podría evaluarse sobre él— es lo que permite
  comparar las tres sobre la misma población.
- **La confirmación usa la UNIÓN de las tres.** No es una definición adoptada: es
  el filtro más laxo, y las tres son subconjuntos suyos, así que el desglose del
  §5.8 puede recortar hacia cualquiera sin volver a recorrer H1. Elegir una habría
  decidido lo que el enunciado delega.
- **El umbral de R2 sólo mira sesiones anteriores**, y la sesión es la del corte
  del proyecto (`NY_18:00`), no el día natural de UTC.

---

## 3. Stop y objetivo — versión 1, PRE-REGISTRADA

**Stop:** al otro lado de la zona de entrada, exactamente en su **borde exterior**,
sin holgura añadida. Dos variantes medidas por separado: `STOP_ZONE = "h1"` y
`STOP_ZONE = "m15"`.

**Objetivo: 1 : 3,3 R fijo, siempre.** Sin parciales, sin trailing, sin
break-even. Punto de equilibrio bruto: **23,3 %** de aciertos.

> Ésta es la **versión 1 del stop**, pre-registrada antes de ver ningún resultado.
> Cualquier cambio posterior será la versión 2 y contará como configuración
> medida, no como corrección.

El informe da la distribución del 1R en **USD, en ATR y en % del precio**, por año
y por variante, con el recuento de los 1R menores que una horquilla entera. Es el
control de sanidad que dice si la fórmula aterriza en una banda operable.

---

## 4. ⚠️ Ejecución realista, y el fichero de ask que no existe

- Entrada en el **open de la barra M1 siguiente** a la decisión.
- Regla intra-barra **conservadora**: si el stop y el objetivo se tocan en la
  misma barra, **gana el stop**. Se marca aparte (`stop_misma_barra`) para poder
  contar cuántas operaciones dependen de esa convención.
- **Sizing de investigación continuo**, sin lote mínimo. Las métricas en R son
  invariantes al riesgo por operación: todos los costes escalan con el lotaje y el
  lotaje con el riesgo.
- El deslizamiento se cobra **como coste** y no desplazando el precio de ejecución,
  para que el bruto valga exactamente −1 R o +3,3 R y la diferencia bruto/neto se
  pueda leer.

> **NO HAY FICHERO DE ASK EN EL PROYECTO.** El §4 pide longs al ask y shorts al
> bid, y sólo está descargado el M1 del lado bid. **El comando se detiene y
> avisa.** Para correr hay que autorizarlo explícitamente
> (`--asumir-bid-en-los-dos-lados` o `entries.allow_missing_ask: true`), y
> entonces se usa el bid para los dos lados y la asunción se declara en portada
> del informe, en el explorador y en la salida de la CLI. **No se fabrica ninguna
> serie de ask.**

**Todos los costes van marcados VERIFICAR** hasta calibrarlos contra Pepperstone
Razor: horquilla, deslizamiento, comisión y swap.

---

## 5. Qué se reporta

Nunca un número agregado sin su desglose. Los ocho del enunciado:

1. con contexto diario / sin contexto diario (y el conflicto, aparte);
2. tipo de zona (OB / UL);
3. desenlace (respeto / rotura y retesteo);
4. entrada en H1 / entrada en M15;
5. stop en H1 / stop en M15;
6. dirección;
7. año;
8. definición de rechazo (las tres).

Todo **en R, neto y bruto**, con expectativa por operación e intervalo de
confianza, win rate, payoff, rachas, coste por operación en R, distribución del
1R en las tres unidades, embudo de señales y frecuencia semanal.

---

## 6. Garantía anti-lookahead (§7)

Cinco excepciones provocadas a propósito, y todas se comprueban en cada corrida:

| Qué se pide antes de tiempo | Qué salta |
|---|---|
| el UL de un ID que aún no se ha constituido | `LookaheadError` |
| el UL de una barra que aún no ha cerrado | `LookaheadError` |
| el OB suelto de M15 mirando una vela que no existe | `LookaheadError` |
| el umbral de R2 más allá de la frontera declarada | `LookaheadError` |
| la cascada sobre una corrida con la rotura por línea | `DomainError` |

Además, **la búsqueda en H1 va en una única pasada cronológica**: las
observaciones se recogen primero y después se recorre H1 hacia adelante avanzando
la frontera barra a barra. Recorrer H1 por observación habría hecho saltar la
frontera hacia atrás y la garantía habría quedado en un adorno.

---

## 7. Casos límite encontrados

Los diez están escritos en la sección 6 del informe y en `EDGE_CASES`
(`infrastructure/reporting/entry_report.py`). Los tres que más decidieron el
diseño:

- **El UL se mueve mientras la zona está en observación** → línea temporal del UL.
- **La zona que se retestea no es la que se ve después de romperse** → se usa la
  vigente al cierre anterior al de la rotura.
- **Entrada en H1 con stop de M15 es imposible sin lookahead** → se miden las tres
  combinaciones que existen y la cuarta se declara imposible.

---

## 8. Salidas

| Fichero | Qué es |
|---|---|
| `now/fase30/reporte_entradas.txt` | el informe entero, con los stops **antes** que los resultados |
| `now/fase30/evidencia_entradas.txt` | el esperado al lado del obtenido, caso por caso |
| `now/fase30/operaciones.csv` | una fila por operación, con todas las columnas del §5 |
| `now/fase30/descartadas.csv` | una fila por señal descartada y su guardarraíl |
| `now/fase30/explorador_entradas.html` | cada operación navegable sobre las cuatro temporalidades, **M15 incluida** |
| `now/fase30/*.png` | 20 ganadoras, 20 perdedoras, 10 por guardarraíl y los cinco arquetipos |
| `now/fase30/LEEME.txt` | índice de la carpeta y los avisos |

> **Nota para el propietario:** revisa los stops **antes** de mirar los
> resultados. Si ves primero qué operaciones ganaron, tu juicio sobre dónde va el
> stop queda contaminado. El informe está ordenado para eso.

La pestaña de **M15** es donde se afinan las entradas y ya se puede seleccionar:
la corrida de esta fase le pasa al explorador los cuatro gráficos del reparto y
no sólo los tres que llevan detector. Sobre M15 se dibuja el impulso de **H1**
—M15 no tiene estructura propia, igual que en las fases 2.0 y 2.1— y encima las
cuatro capas de la fase 3. Dos consecuencias que conviene saber:

- con `max_explorer_bars = 60.000`, M15 entra recortada a las velas más
  recientes (~1,7 años) y el explorador lo dice en sus notas;
- en el replay, la vela en formación de H1 pasa a armarse con M15, que antes en
  esta fase no estaba y dejaba a H1 sin temporalidad inferior.

### 8.1 El replay, en el explorador

Misma nota, llevada al gráfico. El replay del explorador reproduce la historia
paso a paso y con la fase 3 dentro **no adelanta el final**:

| Cuándo | Qué se dibuja |
|---|---|
| el precio toca la zona de H4 | ○ azul: **señal en curso**. El globo no dice en qué acabará |
| confirma en H1 | ◇ azul en la vela de la confirmación |
| llega la entrada | ◆ ámbar: **operación abierta**, con su stop y su objetivo. El globo da la decisión entera y el flotante al precio de ese momento |
| el precio toca el stop o el objetivo | ★ o ✕ y el desenlace, en la vela en que ocurre |

Las tres reglas que sostienen esto:

- El reloj de la fase 3 es el **fino** (`state.at`), no el cierre de la vela del
  gráfico: la entrada es el open de una M1 concreta, y con la vela en formación
  armada el mercado ya ha pasado por ella. Con el otro reloj, en el diario una
  entrada se retrasaría un día entero.
- El desenlace se decide con `settled()`: `xx <= reloj`. El texto de estado
  cuenta las ganadas **sólo entre las ya cerradas**, porque esconderlo en el
  gráfico y contarlo debajo no escondería nada.
- El flotante en R es aritmética sobre el precio que ya está en pantalla
  —`(precio − entrada) / 1R`, con el signo del lado—, no una regla: el motor no
  publica un flotante por minuto y pedírselo haría el fichero inmanejable.

«Parar en eventos» detiene la reproducción en el paso que abre o cierra una
operación y anuncia cuál en el estado: con ▶ puesto, una entrada se ve y se
pierde en el mismo segundo.

**«Sólo lo reciente»** (apagada de salida) es un filtro de dibujo sobre las
cuatro capas de la fase 3: deja lo que sigue **vivo** —operaciones abiertas y
señales en observación— y la marca de lo **último que pasó**, que desaparece en
cuanto entra o cierra otra. Con los rechazos encendidos quedan los de las
señales todavía vivas. Manda igual fuera del replay, medido contra el borde
derecho de la ventana. No toca la detección ni los informes, y el estado dice
cuántas marcas está escondiendo: los recuentos del resumen siguen siendo los de
la ventana entera.
