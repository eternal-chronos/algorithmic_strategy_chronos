# La estrategia real, contada por el propietario

Lo que sigue es la estrategia **tal como se opera a mano**, dictada por el
propietario el 2026-09-09. No es lo que hace el motor hoy: es el destino. Se
guarda literal —con sus matices y sus "muchas veces no tiene por qué ser
estrictamente así"— porque la distancia entre este documento y el código es
exactamente lo que falta por construir.

Contexto de por qué se escribe: el motor, tal como está en `setup2`, da un
**27,5% de aciertos** sobre 1058 operaciones de 2018 a 2025 con 1:3 fijo, y el
punto de equilibrio de 1:3 es el 25%. El propietario lleva **más de un año
operando esto a mano perdiendo muy poco**. Toda esa brecha son reglas que no
están escritas. Éstas son.

## 1. Cuántas operaciones se buscan

**Una a tres por semana, y hay semanas sin ninguna.** No es un sistema de
frecuencia: es de selección. Cualquier regla que multiplique el número de
operaciones va en dirección contraria a la estrategia, aunque mejore la R total
del backtest.

## 2. El Diario manda la dirección, y no se va nunca en su contra

Con un **ID alcista en el Diario**, todo lo que pase hacia abajo dentro de ese
ID —antes de llegar a su PUL o a su APUL— es **retroceso**, no cambio de
tendencia. Mientras el Diario diga alcista, en las temporalidades menores se
buscan **sólo entradas alcistas**. Nunca contra la tendencia del Diario.

El propietario lo dice sabiendo el precio que tiene: *"repito, esto hace que sea
más difícil buscar entradas, pero las que se encuentran son más de calidad"*.

## 3. La cadena: Diario → H4 → H1 → M15

El precio va de PUL a UL y de UL a PUL. Lo que se busca es que **llegue a un PUL
o a un APUL** y entrar ahí **a favor de la tendencia**.

La forma canónica, con el Diario alcista:

1. el precio llega al **PUL (o APUL) del Diario**;
2. en **H4** se buscan ID **alcistas** —a favor de la tendencia del Diario— y que
   testeen **su propio PUL**;
3. en **H1** se busca la **confirmación**;
4. en **M15** se **refina** la entrada.

Cada escalón es la misma pregunta en más detalle: llegar a la zona en contra de
un ID que va a favor, y entrar ahí.

## 4. El RSI de 21 como soporte y resistencia

Se usa el **RSI de 21 periodos** con tres líneas: **55 arriba, 50 medio, 45
abajo**. No se lee como sobrecompra/sobreventa sino como **volumen** y como
**nivel**:

- por encima de **55**: mucho volumen alcista;
- por debajo de **45**: mucho volumen bajista;
- la **zona del 50** actúa como soporte o resistencia: si el precio **viene de
  arriba y entra en la zona del 50**, ésta hace de **soporte** y lo que se espera
  es un movimiento alcista.

Dónde funciona: **muy bien en H4 y en el Diario**. En **H1 es más débil**, porque
hay más volatilidad.

## 5. Lo que no es estricto

*"Muchas veces no tiene por qué ser estrictamente así."* Dos casos que el
propietario da por buenos y que la forma canónica del punto 3 no recoge:

- **Mechas de agotamiento en H4.** El precio llega al PUL del Diario, en H4 no
  hay un ID alcista que testee su PUL sino **mechas de agotamiento**, y en H1 se
  forma **algún patrón**. Eso también es entrada.
- **El PUL roto que no llega a APUL.** El Diario venía de un ID bajista y lo
  rompe; en H4 hay un ID bajista que **no se rompe del todo** pero sí **rompe su
  PUL bajista**. Aunque ese PUL no se convierta en APUL, **si el precio rechaza
  ahí, se usa igual**.

## 6. Lo que queda fuera de este documento

El propietario avisa de que hay **factores humanos** difíciles de explicar que
también intervienen. No se inventan aquí. Lo que este documento recoge es lo
falsable; el resto se irá sacando operación a operación.

## 7. Qué falta en el motor, medido contra este documento

Contrastando lo de arriba con `application/entries/trades.py` a fecha de hoy:

| Regla de este documento | Estado en el motor |
|---|---|
| El Diario manda la dirección | **No existe.** La fase 3.1 dice literalmente "el Diario se queda fuera"; en la 3.0 sólo veta. |
| Sólo a favor de la tendencia del Diario | **No existe.** El régimen `HACIA_ZONA` busca contra el ID de H4 en 533 de las 1058 operaciones. |
| El Diario tiene que estar en su PUL/APUL | **No existe.** La cadena arranca en H4. |
| ID de H4 a favor que testea su PUL | Parcial: es la forma `ZONA`, pero sin exigir que el ID de H4 vaya a favor del Diario. |
| RSI(21) con 45/50/55 | **No existe** en el motor. La función `rsi` de Wilder sí está en `domain/indicators.py`. |
| Mechas de agotamiento en H4 | **No existe.** Los únicos patrones son OB y FVG, y sólo en M15. |
| PUL roto que no llega a APUL, con rechazo | **No existe.** |
| 1 a 3 operaciones por semana | El motor hace **132 al año**, unas 2,5 por semana, sin filtro de calidad. |

## 8. La predicción, y lo que salió al medirla

Los datos decían que la esperanza del motor **no está en la selección sino en la
cola**: 1:4 mejora, break-even, parciales y trailing empeoran, y ningún corte que
el motor sabe medir sube los aciertos por encima del 31%. Este documento aporta
tres discriminadores nuevos, así que se midieron los tres, uno a uno, el
2026-09-09. Cada filtro decide **cuándo se mira**; ninguno cambia dónde se entra
ni dónde va el stop, que siguen siendo los del motor.

### Lo que salió, a 1:3

| Capa | n | Aciertos | R media | ops/semana |
|---|---|---|---|---|
| 0 · base | 1058 | 27,5% | +0,132 | 2,54 |
| 1 · dirección del Diario | 388 | 27,6% | +0,133 | 0,93 |
| 2 · + ID de H4 a favor del Diario | 227 | 27,3% | +0,108 | 0,54 |
| 3 · + Diario ya en su PUL/APUL | 137 | 27,7% | +0,135 | 0,33 |
| 3 + RSI H4 a favor del 50 | 106 | 25,5% | +0,052 | 0,25 |
| 3 + RSI + suelo de stop de 1 $ | 106 | 28,3% | +0,186 | 0,25 |

Del RSI se probaron cuatro lecturas sobre la capa 2: a favor del 50 (27,3%),
banda 45/55 (27,0%), **dentro** de 45-55 (20,8%, la peor de todo el estudio) y el
RSI del Diario a favor del 50 (27,6%).

**Los aciertos no se mueven.** Las operaciones caen de 1058 a 106 —un factor de
diez, hasta el ritmo de 1 a 3 por semana que pide la sección 1— y la proporción
de acierto se queda clavada entre el 25% y el 28%. Es la firma de un filtro sin
poder discriminante: recorta la muestra sin cambiar su composición.

### Por qué: el R:R manda, no la señal

Las mismas capas, cambiando sólo el objetivo:

| R:R | equilibrio | base | capa 1 | capa 2 | capa 3 |
|---|---|---|---|---|---|
| 1:1 | 50,0% | 51,8% | 51,0% | 48,9% | 49,6% |
| 1:1,5 | 40,0% | 43,1% | 43,1% | 40,4% | 42,9% |
| 1:2 | 33,3% | 36,2% | 34,6% | 34,2% | 34,8% |
| 1:3 | 25,0% | 27,5% | 27,6% | 27,3% | 27,7% |

El porcentaje de acierto lo fija el objetivo, no el sitio de la entrada: siempre
entre 1,5 y 3 puntos por encima del punto de equilibrio, con filtros y sin ellos.
Eso es un paseo aleatorio con una prima diminuta. Si el sitio de entrada
capturase algo, filtrar subiría los aciertos a R:R fijo. No sube ni un punto.

### La conclusión

**El problema no está en el contexto, está en la geometría de la entrada.** Los
tres filtros de este documento dicen cuándo mirar, y mirar mejor no arregla un
sitio de entrada que no captura nada. Y el sitio no se ha elegido nunca: está
declarado provisional en el propio `trades.py`, sección 5 —*"pon el stop loss
donde mejor lo veas y luego iremos afinando"*—, y ese "luego" no ha llegado.

Lo que queda sin probar de este documento, y hay que probar antes de dar ningún
veredicto sobre la estrategia real:

- las **mechas de agotamiento en H4** (sección 5), que no están implementadas;
- el **PUL roto que no llega a APUL con rechazo** (sección 5), tampoco;
- y sobre todo **dónde pone el propietario la entrada y el stop**, que es lo
  único que ninguna variante ha tocado.

El camino para eso no es más backtest sobre el mismo histórico: son operaciones
reales marcadas por el propietario, con su entrada, su stop y su salida, para
comparar geometría contra geometría.
