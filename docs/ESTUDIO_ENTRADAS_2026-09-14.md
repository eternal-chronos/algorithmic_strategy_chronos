# Estudio de entradas 2018–2025: ¿hay un 70 % de aciertos a 1:3?

Encargo del propietario (2026-09-14): sin implementar nada, recorrer el histórico
buscando cómo serían las mejores entradas a 1:3 con un 70 % de aciertos, con la
regla de contexto dictada:

> Si en H4 y en el Diario no estamos en ninguna zona (UL/PUL/APUL), se buscan
> entradas sólo en H1. Si H4 está en un ID alcista y toca su PUL o APUL, en H1
> sólo se buscan ID alcistas para entrar, hasta que llegue al UL o rompa el ID
> por cualquier lado.

**Respuesta corta: no existe.** Con la estructura tal como la calcula hoy el motor
(ID + UL/PUL/APUL en D, H4 y H1), ninguna geometría de entrada, ningún filtro de
contexto y ningún modelo estadístico lleva el acierto a 1:3 por encima del
~30 % fuera de la muestra. El punto de equilibrio de 1:3 es el 25 %. Todo lo que
se midió está entre el 20 % y el 30 %. El único subconjunto que parecía llegar al
40 % resultó ser un error de look-ahead (sección 6) y al corregirlo volvió al 25 %.

Todo el estudio es bruto salvo donde se dice lo contrario; cuando se aplica coste
es 0,25 $ por vuelta.

## 1. Qué se midió

**Datos.** M1 bid Dukascopy 2018-01 → 2025-12, agregado con la rejilla del
proyecto (sesión NY 17:00). Estructura: la corrida real del motor con
`config/impulse.yaml` (D 278 ID, H4 1.343, H1 4.300).

**Contexto de D y H4, a resolución de H1 y causal.** Para cada vela H1 cerrada:
el ID vivo de D y de H4 (constituido en una vela ya cerrada, no roto), y su
estado `en_zona` según la regla dictada: se enciende cuando una vela H1 toca el
PUL/APUL de ese ID y se apaga cuando toca su UL o el ID muere. El contexto se lee
en la vela H1 **anterior** al toque de H1, nunca en la del toque.

**Candidato de entrada.** Cada vez que el precio, viniendo de fuera, toca el
PUL/APUL de un ID vivo de H1 → entrada a favor de ese ID (3.704 toques en 8 años,
~9 por semana antes de filtrar).

**Geometrías (27 combinaciones).**

| Entrada | Stop |
|---|---|
| `inner`: límite en el borde interior de la zona | `outer0/025/05`: borde exterior + 0 / 0,25 / 0,5 ATR(H1) |
| `mid`: límite en el 50 % de la zona | `anchor0/025`: línea del ancla del ID + 0 / 0,25 ATR |
| `outer`: límite en el borde exterior | `wick`: mecha de la vela H1 que toca + 0,1 ATR (sólo confirmaciones) |
| `conf_h1`: cierre H1 de vuelta fuera de la zona → mercado en la siguiente | |
| `conf_m15`: primer cierre M15 de vuelta fuera de la zona → mercado en la siguiente | |

Suelo de stop de 1,00 $ (el spread del oro). Objetivo 3R. Resolución en M1:
se mide la MFE en R antes de que salte el stop (horizonte 10 días; el 95 % toca
el stop antes), así que la misma operación da el acierto a cualquier R:R.

**Familias alternativas.** (A) toque directo del PUL/APUL del ID de **H4** y del
**Diario** (entrada límite o confirmación con la temporalidad inferior, stop en
borde exterior o ancla), con y sin el Diario a favor. (B) entrada al
**constituirse** un ID de H1 a favor, por estado de H4.

**Métodos de búsqueda del 70 %.**

1. Rejilla completa: 27 geometrías × regla dictada × «nunca contra el Diario».
2. Minería de reglas: 1,53 millones de conjunciones de 1 a 3 condiciones sobre
   32 variables binarizadas (altura de zona, tamaño del stop, retroceso del ID,
   hora cTrader, día, ordinal del toque, RSI 21 por zonas en H1/H4/D, estado y
   tipo de zona de D y H4, tendencia rápida/lenta, confluencia con la zona y con
   el 50 % de la caja superior, cadena, si 3R cabe antes del UL, PUL vs APUL y
   origen del APUL, dirección). Entrenadas en 2018–2021, comprobadas en 2022–2025.
3. Regresión logística L2 sobre las 32 categóricas + 14 numéricas, con
   walk-forward anual (entrenar con los años anteriores, operar el siguiente con
   el decil / quintil más probable).

## 2. La rejilla: el acierto lo fija el R:R, no la entrada

Acierto a 1:3 por geometría, todo el histórico (n = operaciones):

| Entrada | outer0 | outer025 | outer05 | anchor0 | anchor025 |
|---|---|---|---|---|---|
| inner | 25,1 % (3.704) | 26,1 % | 24,9 % | 22,9 % | 21,2 % |
| mid | 24,9 % (2.126) | 26,7 % | 25,6 % | 24,9 % | 22,6 % |
| outer | 27,6 % (1.384) | 27,5 % | 26,3 % | 25,8 % | 23,3 % |
| conf_h1 | 25,3 % (1.910) | 24,1 % | 23,1 % | 21,4 % | 20,8 % |
| conf_m15 | 25,0 % (2.629) | 24,6 % | 24,4 % | 21,3 % | 20,3 % |

A 1:1 todas dan entre el 46 % y el 51 %; a 1:2, entre el 29 % y el 36 %. Es la
firma de un paseo aleatorio: el acierto es siempre el del punto de equilibrio
más una prima de uno a tres puntos, sea cual sea el sitio de entrada.

**La regla dictada no cambia nada.** Aplicada tal cual (con D o H4 en zona, sólo
a favor de ese ID; si ninguno está en zona, libre) deja el 92 % de las
operaciones y mueve el acierto menos de un punto (por ejemplo `inner/outer025`:
26,1 % → 26,1 %; `conf_m15/outer0`: 25,0 % → 25,6 %). Añadir «nunca contra el
Diario» lo deja en 24–27 % con la mitad de operaciones.

Con coste de 0,25 $ por vuelta **todas** las geometrías dan R neta negativa
(−0,05 a −0,25 R por operación).

## 3. Las familias alternativas, igual

| Familia | n | acierto 1:3 |
|---|---|---|
| Toque del PUL/APUL de **H4**, límite interior, stop exterior | 1.060 | 23,8 % |
| … sólo con el Diario a favor | 364 | 24,2 % |
| … Diario a favor + 1er toque + 3R cabe antes del UL | 61 | 24,6 % |
| … con confirmación de cierre H1 | 758 | 23,2 % |
| Toque del PUL/APUL del **Diario**, límite interior, stop exterior | 206 | 27,7 % |
| Constitución de ID de H1 a favor con H4 en zona a favor, stop ancla | 150 | 25,3 % |
| Constitución de ID de H1, H4 libre a favor | 385 | 26,0 % |

El RSI 21 de H4 a favor del 50 no separa nada (H4 con Diario a favor: 19,8 % a
favor vs 29,3 % en contra con stop en el ancla; 24,6 % vs 23,6 % con stop
exterior).

## 4. La minería de reglas: el 70 % existe dentro de la muestra y desaparece fuera

De 1.532.997 reglas evaluadas:

- **476** superan el 60 % en 2018–2021 con al menos 20 operaciones en 2022–2025.
  Su acierto medio en 2022–2025 es **24,1 %**. Una sola pasa del 50 % fuera de
  muestra (y es la del stop con look-ahead de la sección 6). **Ninguna llega al
  70 %.**
- Las mejores en muestra —80 %, 79 %, 79 % con n = 25–34— dan 11 %, 15 % y 13 %
  después. Es exactamente lo que hace el azar con 1,5 millones de intentos.
- Las más estables (n ≥ 40 en las dos mitades) rondan el 46–52 % en ambas:
  `inner/anchor0 & retroceso 0,7–1 & RSI H4 > 55 & H4 con PUL` (52 % / 51 %,
  n = 46 / 55). Con tantas reglas probadas, que unas pocas queden ahí es lo que
  se espera por puro azar; no es una prueba de nada. Sólo servirían como
  hipótesis a comprobar hacia delante, nunca como resultado.

## 5. El modelo: walk-forward anual

Logística sobre todas las variables, entrenando con los años anteriores y
operando el año siguiente sólo el decil más probable:

| Geometría | n 2020–25 | ops/sem | acierto 1:3 | R neta/op |
|---|---|---|---|---|
| `inner/outer025` decil superior | 278 | 0,9 | 26,6 % | −0,01 R |
| `conf_m15/outer0` decil superior | 224 | 0,7 | 26,3 % | −0,04 R |
| `conf_m15/wick` decil superior (**con look-ahead**, ver 6) | 307 | 1,0 | 37,8 % | +0,37 R |

Dentro de la muestra el modelo llega al 50–64 % en su decil superior; fuera, se
queda en el 25–30 %. Lo poco que el modelo aprende y coincide con lo dictado
—H4/D en zona a favor suman, la franja 08–11 cTrader suma, las horas 20–01 y un
Diario que ya devolvió más del 70 % de su ID restan— no basta para mover el
acierto.

## 6. La trampa del stop en la mecha

La geometría `conf_m15/wick` (entrar al primer cierre M15 fuera de la zona, stop
bajo la mecha de la vela H1 que tocó) fue la única que llegó al 38–42 % con
filtros sencillos y estable entre mitades. Pero la mecha de la vela H1 **no se
conoce hasta que la vela cierra**, y la entrada por M15 se hace dentro de ella:
el stop se estaba poniendo debajo de un mínimo que aún no había ocurrido.
Recalculado con la mecha **vista hasta el minuto de la entrada**:

| Filtros | con look-ahead | causal (+0,1 ATR) |
|---|---|---|
| base | 28,4 % | 25,3 % |
| regla dictada + 02–11 cTrader + stop ≤ 0,6 ATR + retroceso ≥ 0,3 | 40,0 % | 28,1 % |
| … + RSI H4 fuera de 45–55 | 42,1 % | 30,2 % |
| … + 1er toque | — | 32,2 % (n = 183; 38 % / 28 % por mitades) |

Queda como aviso para cualquier estudio futuro: **cualquier stop que use la
vela de la temporalidad superior a la de la entrada es sospechoso de look-ahead.**

## 7. Qué acierto sí hay, y a qué R:R

Del mejor subconjunto causal (`conf_m15`, stop bajo la mecha vista + 0,1 ATR,
regla dictada, 02–11 cTrader, stop ≤ 0,6 ATR, retroceso ≥ 0,3, RSI H4 fuera de
la banda; n = 434, ~1 por semana):

| R:R | acierto | equilibrio | esperanza bruta |
|---|---|---|---|
| 1:1 | 55 % | 50 % | +0,11 R |
| 1:2 | 40 % | 33 % | +0,19 R |
| 1:3 | 30 % | 25 % | +0,21 R |

El 70 % sólo aparece a **1:0,5 o menos** (85 % a 1:0,5, que es +0,27 R bruta y
cero con coste). No hay ningún corte de este estudio en que el 70 % y el 1:3
convivan.

## 8. Lo que esto dice y lo que no

**Dice** que el toque de una zona PUL/APUL, tal como el motor la marca hoy, no
es por sí solo un sitio de entrada con ventaja: el precio no rebota en ella más
de lo que rebotaría en cualquier nivel. Ni el contexto de D/H4, ni el RSI, ni la
hora, ni la caja del 50 %, ni la tendencia rápida/lenta lo cambian. Coincide con
lo que ya salió el 2026-09-09 (`ESTRATEGIA_DEL_PROPIETARIO.md`, §8) con otras
entradas: los filtros de contexto no tienen poder discriminante sobre esta
geometría.

**No dice** que la estrategia del propietario no funcione. Dice que lo que la
hace funcionar a mano no está en las variables que el motor calcula. Lo que no
está medido, en orden de sospecha:

1. **Dónde pone él la entrada y el stop de verdad.** Ninguna de las cinco
   entradas y seis stops del estudio es la suya; son las que se pueden derivar de
   la zona. Es la misma conclusión del estudio anterior y sigue sin resolverse.
2. **La confirmación en M15/M5**: aquí «confirmación» es un cierre fuera de la
   zona. Si lo que él mira es un patrón (mecha de agotamiento, giro de
   estructura en M5), no está medido.
3. **La gestión**: parciales, break-even, cierre por hora. Un 70 % a «1:3» puede
   ser en realidad un 70 % de operaciones que no pierden entera la R, no un 70 %
   que llega a 3R.
4. **El descarte humano**: si de 9 toques por semana él opera 1–3, el criterio
   con el que descarta 6 es la estrategia. Eso sólo sale de operaciones reales
   marcadas.

## 9. Recomendación

No implementar entradas sobre esta base: cualquier variante que se elija ahora
saldría al 25–30 % a 1:3 y negativa con spread, como las anteriores.

Lo que sí sirve para avanzar:

- **Marcar 30–50 operaciones reales en el explorador** (entrada, stop, salida,
  y por qué se descartaron las vecinas). Con eso se compara geometría contra
  geometría; sin eso, el siguiente backtest dará lo mismo que éste.
- Si se quiere una cifra con la que operar mientras tanto: el mejor subconjunto
  causal es el de la sección 7 (~1 operación por semana, 30 % a 1:3, +0,21 R
  bruta, ~+0,1 R con spread). Es poco, y no es el 70 %.
- Si en el futuro aparece un 70 % a 1:3 en un backtest, lo primero es buscar el
  look-ahead (sección 6). Un 70 % a 1:3 son +1,8 R por operación; sobre 100
  operaciones al año son +180 R. No es una cifra que un nivel geométrico pueda
  dar por sí solo.

## Reproducibilidad

Scripts del estudio (fuera del repo, en el scratchpad de la sesión):
`01_build_structure.py` (corre el motor y cachea), `02_candidates.py` (toques y
resolución en M1), `03b_search.py` (minería de reglas), `04_model.py` y
`07_walkforward.py` (logística), `06_alt.py` (familias A y B), `08_wick_fix.py`
(stop causal). Todos parten de `config/impulse.yaml` sin tocarlo.
