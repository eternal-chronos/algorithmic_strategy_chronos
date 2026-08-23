/* Explorador del impulso dominante.
 *
 * El estado visible es mínimo —gráfico, vista, ventana de fechas y capas— y la
 * figura se reconstruye entera en cada cambio con Plotly.react. Es más barato de
 * razonar que llevar la cuenta de índices de traza.
 *
 * Cada gráfico dibuja su impulso y el de la temporalidad superior que le toque
 * (el reparto viene en DATA.layout). El primero de la lista es el principal:
 * línea continua, sombreado de limbo y marcadores. Los demás son contexto y van
 * en trazo discontinuo, sin marcadores, para que no compitan con lo que se está
 * auditando.
 *
 * La ventana de fechas recorta los datos en vez de limitarse a mover el eje: los
 * dos ejes se autoescalan al tramo y el dibujo no arrastra ocho años de velas en
 * cada paso. Las marcas de tiempo viajan como minutos desde la época en UTC, así
 * que filtrar por fecha es comparar enteros.
 *
 * TODO LO DE ESTE FICHERO ES DIBUJO. Ni el filtro de ID visibles, ni la
 * navegación, ni el tramo punteado tocan un solo cálculo: el payload llega ya
 * calculado por el detector y aquí sólo se elige qué parte de él se pinta.
 *
 * El replay (G.1) es la excepción a la que hay que mirar con lupa. Reproduce la
 * historia paso a paso desde una fecha y en cada paso dibuja SÓLO lo que el
 * motor podía saber a esa hora. Como las velas van etiquetadas al inicio del
 * intervalo, saber eso exige el cierre y no la etiqueta: la vela de `t` cierra
 * en `t + span(tf)`, así que un impulso diario constituido el lunes no aparece
 * sobre el gráfico de H4 hasta que el lunes termina. Todo lo que en modo normal
 * se filtra por el borde de la ventana, aquí se filtra además por ese reloj
 * (`knownUntil`). Sigue sin calcularse nada: se retrasa lo que se enseña.
 */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("explorer-data").textContent);
  var COLORS = DATA.colors;
  var DECIMALS = DATA.meta.decimals;
  var SESSION_TZ = DATA.meta.sessionTimezone;
  // El nombre con el que se escribe: `Etc/GMT+4` es el UTC-4 y se lee al revés.
  var SESSION_TZ_LABEL = DATA.meta.sessionTimezoneLabel || SESSION_TZ;
  var DAY = 1440;

  var VISIBLE_MODES = [
    { id: "current", label: "Actual" },
    { id: "pair", label: "Actual + anterior" },
    { id: "all", label: "Todos" }
  ];

  /* --- Ruido (H.1) ----------------------------------------------------------
   *
   * Con todas las capas encendidas el gráfico llega a llevar diez marcas
   * distintas sobre las mismas veinte velas y deja de poder leerse: se ve QUE
   * pasan cosas, no CUÁLES. Estos tres niveles son un preset de las casillas que
   * ya existen —ni una capa nueva, ni un cálculo nuevo— y se aplican de golpe:
   *
   *   · Limpio  — el ID actual y sus zonas en la temporalidad que se está
   *               mirando, y nada más. Fuera se quedan las capas que no deciden
   *               nada sobre el ID vigente: limbo, contactos, 50 %, extremo
   *               contrario, zonas del contexto, roturas evitadas y escalera.
   *   · Normal  — lo que el explorador enseñaba hasta ahora.
   *   · Todo    — todo, incluidos los contactos.
   *
   * Tocar una casilla suelta deja el nivel «a mano» y ningún botón pulsado: el
   * control dice lo que hay puesto, no lo que se pulsó la última vez.
   */
  var NOISE_LEVELS = [
    { id: "clean", label: "Limpio" },
    { id: "normal", label: "Normal" },
    { id: "all", label: "Todo" }
  ];

  var NOISE = {
    clean: {
      visible: "current",
      limbo: false, marks: true, contacts: false, mid: false, wrong: false,
      zonesUl: true, zonesOb: true, zonesContext: false, signals: true,
      avoided: false, steps: false
    },
    normal: {
      visible: "pair",
      limbo: true, marks: true, contacts: false, mid: false, wrong: true,
      zonesUl: true, zonesOb: true, zonesContext: true, signals: true,
      avoided: true, steps: true
    },
    all: {
      visible: "all",
      limbo: true, marks: true, contacts: true, mid: true, wrong: true,
      zonesUl: true, zonesOb: true, zonesContext: true, signals: true,
      avoided: true, steps: true
    }
  };

  var PRESETS = [
    { id: "all", label: "Todo", days: null },
    { id: "5y", label: "5 años", days: 1825 },
    { id: "2y", label: "2 años", days: 730 },
    { id: "1y", label: "1 año", days: 365 },
    { id: "6m", label: "6 meses", days: 182 },
    { id: "3m", label: "3 meses", days: 91 },
    { id: "1m", label: "1 mes", days: 30 },
    { id: "1w", label: "1 semana", days: 7 },
  ];

  var state = {
    /* Nivel de ruido vigente, o `null` si las capas se han tocado a mano. El
     * valor de salida es «Limpio» y se aplica al arrancar: lo que se abre por
     * defecto es el gráfico legible, no el que lo lleva todo encendido. */
    noise: "clean",
    chart: DATA.charts[0],
    mode: DATA.meta.legStartMode,   // LEG_START_MODE que se está mirando (R-36)
    wrong: true,       // marcar los extremos de color contrario (R-36)
    view: "candles",
    preset: "3m",
    from: null,
    to: null,
    hidden: {},        // temporalidades de impulso apagadas a mano
    visible: "pair",   // ID que se dibujan: current | pair | all (B.2)
    limbo: true,
    marks: true,
    contacts: false,   // capa de contactos: apagada por defecto (F.2)
    mid: false,        // nivel del 50 % de cada ID (F.3)
    /* Fase 2.0. Encendidas de salida cuando la corrida trae zonas: auditarlas es
     * justo para lo que se abre este fichero. Si no las trae, las casillas ni
     * siquiera se enseñan. */
    zonesUl: true,
    zonesOb: true,
    /* Las zonas de la temporalidad de CONTEXTO. Son dos rectángulos del tamaño
     * de la pantalla —el diario sobre H4— y no deciden nada de lo que se está
     * auditando en este gráfico: en el nivel «Limpio» se van. */
    zonesContext: true,
    /* Señales de zona: toque del OB, rechazo del UL y rotura del UL. Nace
     * encendida en los tres niveles de ruido —es lo que el propietario ha pedido
     * ver— y sólo existe donde existen las zonas: Diario y H4. */
    signals: true,
    /* Fase 2.1. Las roturas evitadas son lo primero que hay que auditar de la
     * regla nueva, así que la capa nace encendida cuando la corrida trae alguna.
     * Con `break_by_zone: false` no hay ninguna y la casilla ni se enseña. */
    avoided: true,
    /* Fase 2.1, §3.2. Los saltos de la escalera del extremo. Nace encendida: sin
     * ella la línea del extremo cambia de precio a mitad del ID sin decir por
     * qué, y eso se lee como un error de dibujo. */
    steps: true,
    blind: false,      // auditoría ciega en curso (F.1)
    revealed: false,
    seed: null,
    scope: null,       // rango dentro del que se sortean las ventanas ciegas
    /* Replay (G.1). `cursor` es el índice de la última vela CERRADA del gráfico
     * actual; `sub`, cuántas velas de la temporalidad inferior lleva formadas la
     * siguiente. Con `sub > 0` hay una vela a medio hacer en el borde derecho
     * que el motor todavía no ha visto. */
    replay: false,
    cursor: 0,
    sub: 0,
    /* El reloj, en minutos: hasta dónde ha visto el mercado el replay. Es el
     * estado canónico y no depende de la temporalidad que se esté mirando; el
     * par (`cursor`, `sub`) es la lectura de ese reloj en el gráfico actual.
     * Guardarlo aparte es lo que permite ir y volver entre temporalidades sin
     * perder resolución: el diario no puede enseñar la hora y cuarto que llevas
     * corrida, pero el reloj no la olvida. */
    at: 0,
    forming: true,     // armar la vela en curso con la temporalidad inferior
    playing: false,
    speed: 700,        // milisegundos entre pasos
    window: 180,       // velas a la vista durante el replay
    resume: null,      // periodo al que se vuelve al salir del replay
    /* Encuadre manual (G.2). `Plotly.react` vuelve a decidir los ejes en cada
     * dibujo, así que el zoom que el usuario deja con la rueda o arrastrando se
     * perdía en cada paso del replay. Aquí se guarda lo último que fijó a mano
     * —x en minutos, y en precio— y se le vuelve a imponer al gráfico: mientras
     * haya encuadre manual la escala la manda el usuario y el replay se limita a
     * desplazar la ventana para que el presente siga a la vista. Se suelta con
     * «Ajustar» o con doble clic sobre el gráfico. */
    zoom: { x: null, y: null }
  };

  var timer = null;    // temporizador de la reproducción automática

  /* Auditoría ciega (F.1). El sorteo va con semilla y la semilla se enseña: una
   * ventana "al azar" que no se puede volver a abrir no sirve para discutirla
   * después con nadie. mulberry32: pequeño, determinista y suficiente para
   * elegir un día. */
  function rng(seed) {
    var value = seed >>> 0;
    return function () {
      value = (value + 0x6D2B79F5) >>> 0;
      var t = Math.imul(value ^ (value >>> 15), 1 | value);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // --- Tiempo ---------------------------------------------------------------

  function toDate(minute) { return new Date(minute * 60000); }
  function iso(minute) { return toDate(minute).toISOString().replace("T", " ").slice(0, 19); }
  function dayOf(minute) { return toDate(minute).toISOString().slice(0, 10); }
  function dayStart(text) { return Math.floor(Date.parse(text + "T00:00:00Z") / 60000); }
  function dayEnd(text) { return dayStart(text) + DAY - 1; }
  function shiftDays(text, delta) { return dayOf(dayStart(text) + delta * DAY); }
  function spanDays(from, to) { return Math.max(1, Math.round((dayStart(to) - dayStart(from)) / DAY)); }

  var sessionFormat = new Intl.DateTimeFormat("sv-SE", {
    timeZone: SESSION_TZ, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false
  });

  function stamp(minute) {
    return iso(minute).slice(0, 16) + " UTC · " +
      sessionFormat.format(toDate(minute)).replace(",", "") + " " + SESSION_TZ_LABEL;
  }

  function price(value) { return value.toFixed(DECIMALS); }

  // --- Ventana --------------------------------------------------------------

  function bars() { return DATA.bars[state.chart]; }

  /* Duración de la vela de cada temporalidad, en minutos. Viene medida sobre las
   * velas desde Python: con ancla de sesión el diario no dura siempre lo mismo y
   * deducirla del nombre mentiría. */
  function span(timeframe) { return DATA.spans[timeframe] || DATA.spans[state.chart]; }

  /* El reloj del replay: el minuto en que cerró la última vela del gráfico. Todo
   * lo posterior a esta marca es futuro y no se dibuja. */
  function now_() { return bars().t[state.cursor] + span(state.chart); }

  function bounds() {
    var t = bars().t;
    var first = dayOf(t[0]);
    var last = dayOf(t[t.length - 1]);
    if (state.replay) {
      var start = Math.max(0, state.cursor - state.window + 1);
      // Con encuadre manual el usuario puede haber alejado el zoom más allá de
      // las velas a la vista: si no se ampliara el corte, la mitad izquierda de
      // su pantalla saldría vacía.
      if (state.zoom.x) {
        start = Math.min(start, Math.max(0, lowerBound(t, state.zoom.x[0]) - 1));
      }
      var edge = now_();
      return {
        from: dayOf(t[start]), to: dayOf(edge), first: first, last: last,
        lo: t[start], hi: edge, cut: { start: start, end: state.cursor + 1 }
      };
    }
    if (state.from || state.to) {
      return range_(state.from || first, state.to || last, first, last);
    }
    var preset = PRESETS.filter(function (p) { return p.id === state.preset; })[0] || PRESETS[0];
    if (preset.days === null) { return range_(first, last, first, last); }
    var from = shiftDays(last, -preset.days);
    return range_(from < first ? first : from, last, first, last);
  }

  function range_(from, to, first, last) {
    return {
      from: from, to: to, first: first, last: last,
      lo: dayStart(from), hi: dayEnd(to)
    };
  }

  function window_(range) { return { lo: range.lo, hi: range.hi }; }

  function slice(range) {
    if (range.cut) { return range.cut; }
    var t = bars().t;
    var start = lowerBound(t, range.lo);
    var end = lowerBound(t, range.hi + 1);
    return { start: start, end: end };
  }

  /* Hasta qué minuto se conoce lo que produjo `timeframe`. Fuera del replay es
   * el borde derecho de la ventana y no cambia nada. Dentro, hay que descontar
   * la vela: lo que ocurre en la vela etiquetada en `x` no se sabe hasta que
   * cierra, en `x + span`. */
  function knownUntil(timeframe, edges) {
    return state.replay ? edges.hi - span(timeframe) : edges.hi;
  }

  /* ¿La vela etiquetada en `x` todavía no ha cerrado? Sólo en replay: es el
   * filtro que impide que el dibujo se adelante al motor. */
  function pending(x, timeframe, edges) {
    return state.replay && x > knownUntil(timeframe, edges);
  }

  /* Ninguna línea se estira más allá del presente. */
  function clip(x, edges) { return state.replay && x > edges.hi ? edges.hi : x; }

  function lowerBound(values, target) {
    var low = 0, high = values.length;
    while (low < high) {
      var middle = (low + high) >> 1;
      if (values[middle] < target) { low = middle + 1; } else { high = middle; }
    }
    return low;
  }

  // --- Capas ----------------------------------------------------------------

  function overlays() { return DATA.layout[state.chart]; }
  function primary() { return overlays()[0]; }
  function label(timeframe) { return DATA.labels[timeframe] || timeframe; }

  /* R-36 — los impulsos del modo que se está mirando. Cada modo trae su corrida
   * entera (impulsos, limbo y contactos) ya calculada en Python; cambiar de modo
   * es cambiar de qué corrida se lee, nunca recalcular aquí. Las velas no se
   * tocan: son las mismas en los tres, que es lo que permite superponerlos. */
  function impulsesOf(timeframe) {
    // El modo activo no se duplica en byMode: sus impulsos son DATA.impulses. La
    // elección va por identidad del modo y no por "si falta, tira de la otra":
    // una variante ausente tiene que dar error, no dibujar la corrida de otro
    // modo sin que se note.
    var source = state.mode === DATA.meta.legStartMode
      ? DATA.impulses
      : (DATA.byMode || {})[state.mode];
    return source[timeframe];
  }

  function modes() { return DATA.modes || []; }
  function modeInfo(id) {
    return modes().filter(function (item) { return item.id === id; })[0] || null;
  }

  /* En auditoría ciega y hasta revelar no se dibuja ninguna capa del motor: sólo
   * las velas. Es el punto de la prueba —mirar el gráfico pelado antes de ver lo
   * que marcó el módulo— y por eso el apagón se hace aquí, en un único sitio, y
   * no casilla por casilla. */
  function blindfolded() { return state.blind && !state.revealed; }
  function isVisible(timeframe) {
    return !blindfolded() && !state.hidden[state.chart + ":" + timeframe];
  }

  /* B.2 — qué ID se dibujan. El "actual" es el último constituido en o antes del
   * borde derecho de la ventana, es decir el vigente en la fecha que se está
   * mirando; el "anterior" es el inmediatamente previo por constitución. Se
   * recalcula en cada `draw()`, así que al desplazarse con ◀ ▶ el par cambia
   * solo. Cada temporalidad elige el suyo: en H1 el par de H1 y el par de H4.
   *
   * Es filtro de DIBUJO. Los demás ID siguen en el payload, en los CSV y en los
   * informes: aquí no se agrupa, ni se fusiona, ni se recalcula nada.
   * `null` significa "sin filtro". */
  function visibleIds(timeframe, edges) {
    if (state.visible === "all") { return null; }
    return lastIds(timeframe, edges, state.visible === "pair" ? 2 : 1);
  }

  /* Los `count` últimos ID constituidos en o antes del borde derecho. Vacío si
   * a esa hora todavía no se había constituido ninguno. */
  function lastIds(timeframe, edges, count) {
    var list = impulsesOf(timeframe).list;
    var until = knownUntil(timeframe, edges);
    var last = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].x0 > until) { break; }
      last = i;
    }
    if (last < 0) { return {}; }
    var keep = {};
    for (var j = 0; j < count && last - j >= 0; j++) { keep[list[last - j].id] = true; }
    return keep;
  }

  /* Las zonas van SIEMPRE con el ID actual y sólo con él, mande lo que mande el
   * selector B.2. En cuanto se constituye un ID nuevo, el UL y el OB del
   * anterior desaparecen del gráfico: son las zonas vivas —las que el motor
   * mira para la rotura de la fase 2.1— y arrastrar las de los ID muertos
   * llenaba la pantalla de rectángulos que ya no deciden nada. Los ID viejos
   * siguen en el payload y en los CSV; aquí sólo se elige qué se pinta. */
  function zoneIds(timeframe, edges) { return lastIds(timeframe, edges, 1); }

  function keeps(allowed, id) { return allowed === null || allowed[id] === true; }

  /* Primer minuto que el ID llega a pintar: el nivel más antiguo de los dos ya
   * existía en el precio antes de la constitución (tramo punteado de B.1). En un
   * ID que estiró su extremo, el primer escalón es más antiguo que `xe`, que es
   * el del final. */
  function drawnFrom(impulse) {
    return Math.min(impulse.x0, impulse.xa, firstExtremeAt(impulse));
  }

  /* --- La escalera del extremo (fase 2.1, §3.2) ------------------------------
   *
   * El extremo de un ID vigente SE MUEVE: cada vela que cierra más allá de la
   * línea sin atravesar el UL entero lo estira. `e` y `xe` son los del final, así
   * que dibujar con ellos solos una línea recta desde la constitución enseñaría
   * durante horas un precio al que el mercado todavía no había llegado —el mismo
   * lookahead que el tramo punteado de B.1 evita por el otro lado—. Los ID
   * estirados traen `st`, un par `[minuto, precio]` por tramo, y aquí se
   * convierte en escalones: cada nivel desde que manda hasta que lo sustituye el
   * siguiente.
   *
   * `defined` es la vela que fija el nivel. En el primer escalón es anterior a la
   * constitución (el extremo se fija dentro de la pierna) y por eso lleva tramo
   * punteado; en los demás es la propia vela que estira, así que el nivel y su
   * vela nacen a la vez y no hay nada que puntear. */
  function extremeSteps(impulse, timeframe, edges) {
    if (!impulse.st) {
      return [{
        from: impulse.x0, to: impulse.x1, value: impulse.e, defined: impulse.xe,
        step: 1, steps: 1
      }];
    }
    var steps = [];
    impulse.st.forEach(function (item, position) {
      var next = impulse.st[position + 1];
      steps.push({
        from: position === 0 ? impulse.x0 : item[0],
        to: next ? next[0] : impulse.x1,
        value: item[1],
        defined: item[0],
        step: position + 1,
        steps: impulse.st.length
      });
    });
    // En replay un escalón no existe hasta que cierra la vela que lo estira: lo
    // que se dibuja es lo que el motor sabía, y hasta ese cierre el extremo
    // vigente era el anterior.
    return steps.filter(function (item, position) {
      return position === 0 || !pending(item.defined, timeframe, edges);
    });
  }

  /* La vela que fijó el PRIMER extremo: la del final en un ID que no se estiró y
   * la del extremo con el que nació en uno que sí. */
  function firstExtremeAt(impulse) {
    return impulse.st ? impulse.st[0][0] : impulse.xe;
  }

  // --- Trazas ---------------------------------------------------------------

  function priceTraces(cut) {
    var b = bars();
    var x = b.t.slice(cut.start, cut.end).map(iso);
    var open = b.o.slice(cut.start, cut.end);
    var high = b.h.slice(cut.start, cut.end);
    var low = b.l.slice(cut.start, cut.end);
    var close = b.c.slice(cut.start, cut.end);
    var text = b.t.slice(cut.start, cut.end).map(function (minute, i) {
      return stamp(minute) + "<br>O " + price(open[i]) + " · H " + price(high[i]) +
        "<br>L " + price(low[i]) + " · C " + price(close[i]) +
        "<br>cuerpo [" + price(Math.min(open[i], close[i])) + ", " +
        price(Math.max(open[i], close[i])) + "]";
    });

    var half = formingCandle();

    if (state.view === "line") {
      if (half) { x = x.concat([iso(half.x)]); close = close.concat([half.c]); }
      return [{
        type: "scatter", mode: "lines", name: "Cierres " + label(state.chart),
        x: x, y: close, line: { color: COLORS.ink, width: 1.2 },
        text: text, hoverinfo: "text", hoverlabel: { align: "left" }
      }];
    }
    var traces = [{
      type: "candlestick", name: "Velas " + label(state.chart),
      x: x, open: open, high: high, low: low, close: close,
      increasing: { line: { color: COLORS.bullish, width: 1 }, fillcolor: COLORS.bullish },
      decreasing: { line: { color: COLORS.bearish, width: 1 }, fillcolor: COLORS.bearish },
      text: text, hoverinfo: "text", hoverlabel: { align: "left" }
    }];
    if (half) { traces.push(formingTrace(half)); }
    return traces;
  }

  /* La vela en formación (G.1). Se arma con las velas de la temporalidad
   * inferior que ya han cerrado dentro del intervalo en curso: en H4, con las de
   * H1. No es un cálculo del módulo —el detector no ve una vela hasta que
   * cierra— sino la forma de mirar cómo se va haciendo, y por eso va hueca y en
   * su propia traza: mientras esté ahí, ninguna capa del motor la ha leído.
   */
  function finer() {
    var position = DATA.charts.indexOf(state.chart);
    var next = position < 0 ? null : DATA.charts[position + 1];
    return next && DATA.bars[next] ? next : null;
  }

  /* Las velas inferiores que caen dentro de la vela que se está formando, como
   * [inicio, fin) sobre el array de la temporalidad inferior. */
  function formingRange() {
    var timeframe = finer();
    var t = bars().t;
    if (!timeframe || state.cursor + 1 >= t.length) { return null; }
    var start = t[state.cursor + 1];
    var fine = DATA.bars[timeframe].t;
    var from = lowerBound(fine, start);
    var to = lowerBound(fine, start + span(state.chart));
    return to > from ? { timeframe: timeframe, from: from, to: to, at: start } : null;
  }

  /* Pasos intermedios que quedan dentro de la vela en curso. El último no se
   * ofrece: completar la vela y que el motor reaccione son el mismo instante,
   * así que ese paso cierra la vela en vez de dibujarla entera sin reacción. */
  function subSteps() {
    if (!state.forming) { return 0; }
    var edges = formingRange();
    return edges ? edges.to - edges.from - 1 : 0;
  }

  /* El reloj fino: el minuto exacto hasta el que se ha visto el mercado. Con la
   * vela en curso a medio armar no es el cierre de la última vela cerrada sino
   * el de la última vela inferior formada, y esa diferencia es justo la que hay
   * que conservar al saltar de temporalidad. */
  function clock() {
    var edges = formingRange();
    if (!state.sub || !edges) { return now_(); }
    var fine = DATA.bars[edges.timeframe].t;
    var last = Math.min(edges.from + state.sub, edges.to) - 1;
    return fine[last] + span(edges.timeframe);
  }

  /* La temporalidad más fina embebida por debajo de la que se mira. No es la de
   * los pasos —en el diario se avanza de H4 en H4—, sino la que da resolución al
   * dibujo de la vela en curso. */
  function finest() {
    var charts = DATA.charts;
    for (var i = charts.length - 1; i >= 0; i--) {
      if (DATA.bars[charts[i]] && span(charts[i]) < span(state.chart)) { return charts[i]; }
    }
    return null;
  }

  /* La vela a medio hacer del borde derecho. Llega exactamente hasta el reloj y
   * se arma con la temporalidad más fina que haya, no con la de los pasos: en el
   * diario cada paso es una H4, pero si el reloj lleva hora y cuarto corrida el
   * día tiene que verse con esa hora y cuarto dentro. Es lo que hace que saltar
   * de H1 al diario no parezca un salto atrás de un día entero.
   *
   * Sigue sin ser una vela del motor —por eso se dibuja hueca—: el detector no
   * ve nada hasta que la vela cierra. */
  function formingCandle() {
    if (!state.replay || !state.forming) { return null; }
    var t = bars().t;
    if (state.cursor + 1 >= t.length) { return null; }
    var start = t[state.cursor + 1];
    var timeframe = finest();
    if (!timeframe || state.at <= start) { return null; }
    var fine = DATA.bars[timeframe];
    var from = lowerBound(fine.t, start);
    var end = Math.min(
      lowerBound(fine.t, state.at - span(timeframe) + 1),  // cerradas al reloj
      lowerBound(fine.t, start + span(state.chart))        // y dentro de la vela
    );
    if (end <= from) { return null; }
    var high = -Infinity, low = Infinity;
    for (var i = from; i < end; i++) {
      if (fine.h[i] > high) { high = fine.h[i]; }
      if (fine.l[i] < low) { low = fine.l[i]; }
    }
    // El recuento que se enseña es el de los PASOS, que es lo que el propietario
    // controla con ▶▶; el dibujo va más fino cuando el reloj lo permite.
    var steps = formingRange();
    return {
      x: start, o: fine.o[from], h: high, l: low, c: fine.c[end - 1],
      done: state.sub, total: steps ? steps.to - steps.from : 0,
      timeframe: steps ? steps.timeframe : timeframe,
      until: fine.t[end - 1] + span(timeframe)
    };
  }

  function formingTrace(half) {
    var rising = half.c >= half.o;
    var colour = rising ? COLORS.bullish : COLORS.bearish;
    return {
      type: "candlestick", name: "Vela en formación",
      x: [iso(half.x)], open: [half.o], high: [half.h], low: [half.l], close: [half.c],
      increasing: { line: { color: colour, width: 1.4 }, fillcolor: "rgba(0,0,0,0)" },
      decreasing: { line: { color: colour, width: 1.4 }, fillcolor: "rgba(0,0,0,0)" },
      opacity: 0.85,
      text: ["VELA EN FORMACIÓN · el motor aún no la ha visto cerrar<br>" +
        stamp(half.x) + " → " + label(state.chart) +
        "<br>" + half.done + " de " + half.total + " velas de " + label(half.timeframe) +
        " · precio hasta " + iso(half.until).slice(0, 16) + " UTC" +
        "<br>O " + price(half.o) + " · H " + price(half.h) +
        "<br>L " + price(half.l) + " · C " + price(half.c)],
      hoverinfo: "text", hoverlabel: { align: "left" }
    };
  }

  /* Las dos líneas de cada ID, en dos tramos (B.1):
   *
   *   · punteado y atenuado, desde la vela que DEFINE el nivel hasta la
   *     constitución. El nivel ya estaba en el precio, pero el ID todavía no
   *     existía: ese intervalo es limbo.
   *   · sólido, desde la constitución hasta el fin del ID, y ni un minuto más.
   *
   * Sin esa distinción el dibujo daría a entender que el sistema conocía el
   * nivel antes de tiempo, que es exactamente lo contrario de lo que hace: el
   * extremo no se fija hasta que cierra la vela contraria.
   */
  function impulseTraces(range) {
    var edges = window_(range);
    var traces = [];
    overlays().forEach(function (timeframe, position) {
      if (!isVisible(timeframe)) { return; }
      var own = position === 0;
      var allowed = visibleIds(timeframe, edges);
      var buckets = {
        alcista: { live: segment(), pending: segment() },
        bajista: { live: segment(), pending: segment() }
      };

      impulsesOf(timeframe).list.forEach(function (impulse) {
        if (impulse.x1 < edges.lo || drawnFrom(impulse) > edges.hi) { return; }
        // En replay el ID no existe hasta que cierra la vela que lo constituye:
        // ni siquiera su tramo de limbo, que sólo se conoce mirando hacia atrás
        // desde la constitución.
        if (pending(impulse.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, impulse.id)) { return; }
        var bucket = buckets[impulse.d];
        var head = "ID " + timeframe + " nº " + impulse.id + " · " + impulse.d +
          "<br>constituido " + stamp(impulse.x0);
        var levels = [{
          name: "ancla", from: impulse.x0, to: impulse.x1,
          value: impulse.a, defined: impulse.xa, step: 1, steps: 1
        }].concat(extremeSteps(impulse, timeframe, edges).map(function (item) {
          return {
            name: "extremo", from: item.from, to: item.to, value: item.value,
            defined: item.defined, step: item.step, steps: item.steps
          };
        }));
        levels.forEach(function (level) {
          var escalera = level.steps > 1
            ? "<br>ESCALÓN " + level.step + " de " + level.steps +
              ": el extremo se estiró estando el ID ya vigente (§3.2)"
            : "";
          push(bucket.live, level.from, clip(level.to, edges), level.value,
            head + "<br>" + level.name + " " + price(level.value) +
            "<br>fija la vela de " + stamp(level.defined) + escalera);
          if (level.defined < level.from) {
            push(bucket.pending, level.defined, level.from, level.value,
              "ANTES DE LA CONSTITUCIÓN · el nivel ya estaba en el precio, el ID no" +
              "<br>futuro " + level.name + " del ID " + timeframe + " nº " + impulse.id +
              " en " + price(level.value) +
              "<br>lo fija la vela de " + stamp(level.defined) +
              "<br>el ID no nace hasta " + stamp(impulse.x0));
          }
        });
      });

      ["alcista", "bajista"].forEach(function (direction) {
        var colour = direction === "alcista" ? COLORS.bullish : COLORS.bearish;
        var suffix = own ? "" : " (contexto)";
        if (buckets[direction].live.x.length) {
          traces.push(lineTrace(
            "ID " + label(timeframe) + " " + direction + suffix,
            buckets[direction].live,
            { color: colour, width: own ? 1.6 : 2.6, dash: own ? "solid" : "dot" },
            own ? 1 : 0.75
          ));
        }
        if (buckets[direction].pending.x.length) {
          traces.push(lineTrace(
            "Nivel previo a la constitución · " + label(timeframe) + " " + direction + suffix,
            buckets[direction].pending,
            { color: colour, width: own ? 1.2 : 1.6, dash: "dot" },
            0.35
          ));
        }
      });
    });
    return traces;
  }

  function segment() { return { x: [], y: [], text: [] }; }

  function push(bucket, from, to, value, text) {
    bucket.x.push(iso(from), iso(to), null);
    bucket.y.push(value, value, null);
    bucket.text.push(text, text, "");
  }

  function lineTrace(name, bucket, line, opacity) {
    return {
      type: "scatter", mode: "lines", name: name,
      x: bucket.x, y: bucket.y, text: bucket.text,
      hoverinfo: "text", hoverlabel: { align: "left" }, connectgaps: false,
      line: line, opacity: opacity
    };
  }

  /* Los SALTOS de la escalera del extremo (fase 2.1, §3.2).
   *
   * La escalera ya está en la línea del ID, pero un escalón se puede confundir
   * con un ID nuevo si no se dice cuál es. Esta capa marca el salto: el tramo
   * vertical que une el nivel viejo con el nuevo, sobre la vela que lo estiró, y
   * un triángulo en el nivel nuevo apuntando hacia donde se fue. Sin ella, el
   * dibujo enseña la escalera pero no dice que es una.
   *
   * Va sobre TODAS las temporalidades visibles, no sólo la principal: la línea
   * del ID de contexto también es una escalera y su salto también engaña.
   */
  function hasSteps() { return DATA.hasSteps === true; }

  function stepTraces(range) {
    if (blindfolded() || !state.steps || !hasSteps()) { return []; }
    var edges = window_(range);
    var traces = [];
    overlays().forEach(function (timeframe, position) {
      if (!isVisible(timeframe)) { return; }
      var own = position === 0;
      var allowed = visibleIds(timeframe, edges);
      var jumps = { x: [], y: [] };
      var marks = { x: [], y: [], text: [], colour: [], symbol: [] };

      impulsesOf(timeframe).list.forEach(function (impulse) {
        if (!impulse.st) { return; }
        if (impulse.x1 < edges.lo || drawnFrom(impulse) > edges.hi) { return; }
        if (pending(impulse.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, impulse.id)) { return; }
        var steps = extremeSteps(impulse, timeframe, edges);
        steps.forEach(function (item, index) {
          if (!index) { return; }
          if (item.defined < edges.lo || item.defined > edges.hi) { return; }
          var before = steps[index - 1].value;
          jumps.x.push(iso(item.defined), iso(item.defined), null);
          jumps.y.push(before, item.value, null);
          marks.x.push(iso(item.defined));
          marks.y.push(item.value);
          marks.colour.push(impulse.d === "alcista" ? COLORS.bullish : COLORS.bearish);
          marks.symbol.push(impulse.d === "alcista" ? "triangle-up" : "triangle-down");
          marks.text.push(
            "EXTREMO ESTIRADO (§3.2) · ID " + timeframe + " nº " + impulse.id +
            " · " + impulse.d +
            "<br>escalón " + item.step + " de " + item.steps + ": de " + price(before) +
            " a " + price(item.value) +
            "<br>lo estira la vela de " + stamp(item.defined) +
            "<br>hasta aquí la línea del extremo estaba en " + price(before) +
            "<br>el ID no muere: la vela cerró más allá de la línea sin atravesar el UL entero"
          );
        });
      });

      if (!marks.x.length) { return; }
      var name = "Extremo estirado · " + label(timeframe) + (own ? "" : " (contexto)");
      traces.push({
        type: "scatter", mode: "lines", name: name,
        x: jumps.x, y: jumps.y, hoverinfo: "skip", showlegend: false,
        connectgaps: false, opacity: own ? 1 : 0.6,
        line: { color: COLORS.ink, width: 1.6, dash: "dot" }
      });
      traces.push({
        type: "scatter", mode: "markers", name: name,
        x: marks.x, y: marks.y, text: marks.text,
        hoverinfo: "text", hoverlabel: { align: "left" },
        opacity: own ? 1 : 0.6,
        marker: {
          symbol: marks.symbol, size: 11, color: marks.colour,
          line: { color: COLORS.ink, width: 1.4 }
        }
      });
    });
    return traces;
  }

  /* Nivel del 50 % de cada ID: la línea punteada que el propietario traza a mano
   * en mitad del rango (F.3). Visual y opcional; no entra en ningún cálculo.
   *
   * También va en escalera: el 50 % se mide contra el extremo, y el extremo se
   * mueve. Una recta aquí volvería a meter por la puerta de atrás el nivel que
   * la línea del ID ya no adelanta. */
  function midTraces(range) {
    if (!state.mid || blindfolded()) { return []; }
    var edges = window_(range);
    var x = [], y = [], text = [];
    overlays().forEach(function (timeframe) {
      if (!isVisible(timeframe)) { return; }
      var allowed = visibleIds(timeframe, edges);
      impulsesOf(timeframe).list.forEach(function (impulse) {
        if (impulse.x1 < edges.lo || impulse.x0 > knownUntil(timeframe, edges)) { return; }
        if (!keeps(allowed, impulse.id)) { return; }
        extremeSteps(impulse, timeframe, edges).forEach(function (item) {
          var level = (impulse.a + item.value) / 2;
          var caption = "50 % del ID " + timeframe + " nº " + impulse.id + "<br>" + price(level) +
            (item.steps > 1 ? "<br>escalón " + item.step + " de " + item.steps : "");
          x.push(iso(item.from), iso(clip(item.to, edges)), null);
          y.push(level, level, null);
          text.push(caption, caption, "");
        });
      });
    });
    if (!x.length) { return []; }
    return [{
      type: "scatter", mode: "lines", name: "Nivel 50 %",
      x: x, y: y, text: text, hoverinfo: "text", connectgaps: false,
      line: { color: COLORS.muted, width: 1, dash: "dot" }
    }];
  }

  /* Capa R-36: dónde cae el extremo cuando lo fija una vela del color contrario
   * al impulso. El marcador se pone sobre la vela que lo fijó —no sobre la
   * constitución— porque es esa vela la que incumple la regla. El dato viene
   * marcado desde el detector (`extreme_on_counter_bar`); aquí no se compara
   * ningún color. */
  function wrongExtremeTraces(range) {
    if (!state.wrong || blindfolded()) { return []; }
    var edges = window_(range);
    var items = [];
    overlays().forEach(function (timeframe) {
      if (!isVisible(timeframe)) { return; }
      var allowed = visibleIds(timeframe, edges);
      impulsesOf(timeframe).list.forEach(function (impulse) {
        if (!impulse.w) { return; }
        if (impulse.xe < edges.lo || impulse.xe > edges.hi) { return; }
        // La marca cae sobre la vela del extremo, pero no se sabe que el extremo
        // es ése hasta la constitución: en replay manda la constitución.
        if (pending(impulse.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, impulse.id)) { return; }
        items.push({ tf: timeframe, impulse: impulse });
      });
    });
    if (!items.length) { return []; }
    return [{
      type: "scatter", mode: "markers", name: "Extremo de color contrario (R-36)",
      x: items.map(function (item) { return iso(item.impulse.xe); }),
      y: items.map(function (item) { return item.impulse.e; }),
      text: items.map(function (item) {
        var impulse = item.impulse;
        return "EXTREMO SOBRE VELA DEL COLOR CONTRARIO<br>ID " + item.tf + " nº " +
          impulse.id + " · " + impulse.d +
          "<br>extremo " + price(impulse.e) + " fijado por una vela " + impulse.ec +
          "<br>" + stamp(impulse.xe) +
          "<br>en un impulso " + impulse.d + " debería fijarlo una vela " +
          (impulse.d === "alcista" ? "verde" : "roja");
      }),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: {
        symbol: "x-thin", size: 13, color: COLORS.ink,
        line: { color: COLORS.ink, width: 2.4 }
      }
    }];
  }

  /* --- Fase 2.0: zonas UL y OB ---------------------------------------------
   *
   * DIBUJO Y NADA MÁS. Las zonas llegan calculadas desde Python y no intervienen
   * en la detección de impulsos ni en la regla de rotura, que en esta fase sigue
   * siendo por línea.
   *
   * Cada zona se pinta en dos tramos, con la misma convención que las líneas del
   * ID en B.1: relleno sólido desde que la zona NACE hasta que muere el ID —que
   * es exactamente cuando existe— y un contorno atenuado desde la vela que la
   * define hasta ese nacimiento. Sin esa distinción el dibujo diría que la zona
   * estaba ahí antes de tiempo, que es justo lo contrario de lo que ocurre: el
   * UL no existe hasta la constitución y el OB, hasta que se confirma.
   *
   * Las zonas viajan sólo con el modo activo de R-36: se calcularon sobre sus
   * impulsos y superponerlas a las de otro modo enseñaría zonas de impulsos que
   * en ese modo no existen. Al cambiar de modo, las capas se quedan vacías.
   */
  function hasZones() { return DATA.hasZones === true; }

  function zonesAvailable() {
    return hasZones() && state.mode === DATA.meta.legStartMode;
  }

  function zonesOf(timeframe) {
    if (!zonesAvailable()) { return []; }
    return impulsesOf(timeframe).zones || [];
  }

  function candidatesOf(timeframe) {
    if (!zonesAvailable()) { return []; }
    return impulsesOf(timeframe).obCandidates || [];
  }

  function wantsZone(kind) {
    return kind === "UL" ? state.zonesUl : state.zonesOb;
  }

  /* Las zonas de la temporalidad de CONTEXTO son dos rectángulos que ocupan la
   * pantalla entera —el UL y el OB del diario sobre un gráfico de H4— y tapan el
   * precio que se está auditando. Se pueden apagar solas, y «Limpio» las apaga. */
  function drawsZonesOf(position) {
    return position === 0 || state.zonesContext;
  }

  /* Un polígono por zona dentro de una sola traza por (temporalidad, tipo,
   * dirección): con `fill: toself` y separadores nulos, Plotly los dibuja todos
   * sin multiplicar las trazas por cada ID de la ventana. */
  function zoneTraces(range) {
    if (blindfolded() || !zonesAvailable()) { return []; }
    if (!state.zonesUl && !state.zonesOb) { return []; }
    var edges = window_(range);
    var traces = [];

    overlays().forEach(function (timeframe, position) {
      if (!isVisible(timeframe) || !drawsZonesOf(position)) { return; }
      var own = position === 0;
      var allowed = zoneIds(timeframe, edges);
      var buckets = {};

      zonesOf(timeframe).forEach(function (zone) {
        if (!wantsZone(zone.k)) { return; }
        if (zone.x1 < edges.lo || zone.xd > edges.hi) { return; }
        // La zona no existe hasta que cierra la vela que la hace nacer.
        if (pending(zone.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, zone.id)) { return; }
        var key = zone.k + ":" + zone.d;
        if (!buckets[key]) { buckets[key] = { live: shape(), before: shape(), zone: zone }; }
        pushZone(buckets[key].live, zone.x0, clip(zone.x1, edges), zone, timeframe, false);
        if (zone.xd < zone.x0) {
          pushZone(buckets[key].before, zone.xd, zone.x0, zone, timeframe, true);
        }
      });

      Object.keys(buckets).forEach(function (key) {
        var kind = key.split(":")[0];
        var direction = key.split(":")[1];
        var colour = direction === "alcista" ? COLORS.bullish : COLORS.bearish;
        var name = (kind === "UL" ? "Zona UL " : "Zona OB ") + label(timeframe) +
          " " + direction + (own ? "" : " (contexto)");
        if (buckets[key].live.x.length) {
          traces.push(zoneTrace(name, buckets[key].live, colour, kind, own, false));
        }
        if (buckets[key].before.x.length) {
          traces.push(zoneTrace(
            "Antes de existir · " + name, buckets[key].before, colour, kind, own, true
          ));
        }
      });
    });
    return traces;
  }

  function shape() { return { x: [], y: [], text: [] }; }

  /* Un rectángulo cerrado, en el orden que espera `fill: toself`, más el nulo
   * que lo separa del siguiente. */
  function pushZone(bucket, from, to, zone, timeframe, before) {
    var a = iso(from), b = iso(to);
    var caption = zoneCaption(zone, timeframe, before);
    // Una zona plana no tiene rectángulo: es una línea, y se dibuja como tal.
    var lo = zone.lo, hi = zone.hi;
    bucket.x.push(a, b, b, a, a, null);
    bucket.y.push(lo, lo, hi, hi, lo, null);
    bucket.text.push(caption, caption, caption, caption, caption, "");
  }

  function zoneCaption(zone, timeframe, before) {
    var head = before
      ? "ANTES DE EXISTIR · la vela ya definía la zona, el ID no estaba constituido<br>"
      : "";
    var body = "Zona " + zone.k + " del ID " + timeframe + " nº " + zone.id +
      " · " + zone.d +
      "<br>interior " + price(zone.i) + " · exterior " + price(zone.o) +
      "<br>altura " + price(zone.hi - zone.lo) +
      "<br>la define la vela de " + stamp(zone.xd);
    if (zone.k === "UL") {
      body += zone.ext
        ? "<br>extendida a la vela siguiente"
        : "<br>sin extender";
      if (zone.flat) { body += "<br>ALTURA CERO: la vela no dejó mecha"; }
      // §3.2: el extremo se estira con cada rechazo, el UL NO. Se marca una vez,
      // al constituirse el ID, y es el mismo hasta que el ID muere.
      body += "<br>el UL no se remarca: es el de la constitución del ID";
    } else if (zone.xc !== null && zone.xc !== undefined) {
      body += "<br>confirmado por la vela de " + stamp(zone.xc);
    }
    body += "<br>la zona existe desde " + stamp(zone.x0);
    return head + body;
  }

  function zoneTrace(name, bucket, colour, kind, own, before) {
    return {
      type: "scatter", mode: "lines", name: name,
      x: bucket.x, y: bucket.y, text: bucket.text,
      hoverinfo: "text", hoverlabel: { align: "left" }, connectgaps: false,
      fill: before ? "none" : "toself",
      fillcolor: before ? undefined : rgba(colour, kind === "UL" ? 0.30 : 0.16),
      opacity: before ? 0.35 : (own ? 1 : 0.7),
      line: {
        color: colour,
        width: before ? 1 : (own ? 1.2 : 1),
        dash: before ? "dot" : "solid"
      }
    };
  }

  /* El color de la paleta llega como `#rrggbb`; el relleno necesita alfa. */
  function rgba(hex, alpha) {
    var value = String(hex).replace("#", "");
    if (value.length !== 6) { return hex; }
    var r = parseInt(value.slice(0, 2), 16);
    var g = parseInt(value.slice(2, 4), 16);
    var b = parseInt(value.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /* La vela del ancla de un ID que murió sin OB confirmado. La zona NO existe
   * —por eso va sin relleno y con el borde punteado— pero el propietario tiene
   * que ver dónde estaba la candidata para juzgar la regla (§8). */
  function candidateTraces(range) {
    if (blindfolded() || !state.zonesOb || !zonesAvailable()) { return []; }
    var edges = window_(range);
    var bucket = shape();

    overlays().forEach(function (timeframe, position) {
      if (!isVisible(timeframe) || !drawsZonesOf(position)) { return; }
      var allowed = zoneIds(timeframe, edges);
      candidatesOf(timeframe).forEach(function (item) {
        if (item.x1 < edges.lo || item.xd > edges.hi) { return; }
        if (pending(item.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, item.id)) { return; }
        var caption = "OB SIN CONFIRMAR · esta zona NO existe<br>ID " + timeframe +
          " nº " + item.id + " · " + item.d +
          "<br>vela del ancla " + stamp(item.xd) +
          "<br>de " + price(item.lo) + " a " + price(item.hi) +
          "<br>ninguna vela del color del impulso superó su mecha antes de morir el ID" +
          "<br>en la fase 2.1 este ID se rompería por línea";
        var a = iso(Math.max(item.xd, edges.lo)), b = iso(clip(item.x1, edges));
        bucket.x.push(a, b, b, a, a, null);
        bucket.y.push(item.lo, item.lo, item.hi, item.hi, item.lo, null);
        bucket.text.push(caption, caption, caption, caption, caption, "");
      });
    });
    if (!bucket.x.length) { return []; }
    return [{
      type: "scatter", mode: "lines", name: "OB sin confirmar",
      x: bucket.x, y: bucket.y, text: bucket.text,
      hoverinfo: "text", hoverlabel: { align: "left" }, connectgaps: false,
      fill: "none", opacity: 0.8,
      line: { color: COLORS.muted, width: 1.4, dash: "dot" }
    }];
  }

  /* Marcador sobre la vela que confirma cada OB (§8). Va sobre esa vela y no
   * sobre la del ancla: es la que hace nacer la zona. */
  function confirmationTraces(range) {
    if (blindfolded() || !state.zonesOb || !zonesAvailable()) { return []; }
    var edges = window_(range);
    var items = [];

    overlays().forEach(function (timeframe, position) {
      if (!isVisible(timeframe) || !drawsZonesOf(position)) { return; }
      var allowed = zoneIds(timeframe, edges);
      zonesOf(timeframe).forEach(function (zone) {
        if (zone.k !== "OB" || zone.xc === null || zone.xc === undefined) { return; }
        if (zone.xc < edges.lo || zone.xc > edges.hi) { return; }
        // Se sabe que esa vela confirmó, pero la zona no aparece hasta nacer.
        if (pending(zone.x0, timeframe, edges)) { return; }
        if (!keeps(allowed, zone.id)) { return; }
        items.push({ tf: timeframe, zone: zone });
      });
    });
    if (!items.length) { return []; }
    return [{
      type: "scatter", mode: "markers", name: "Confirmación del OB",
      x: items.map(function (item) { return iso(item.zone.xc); }),
      y: items.map(function (item) { return item.zone.i; }),
      text: items.map(function (item) {
        var zone = item.zone;
        return "CONFIRMA EL OB del ID " + item.tf + " nº " + zone.id +
          " (" + zone.d + ")<br>" + stamp(zone.xc) +
          "<br>una vela " + (zone.d === "alcista" ? "verde" : "roja") +
          " superó la mecha de la vela del ancla" +
          "<br>la zona OB existe desde " + stamp(zone.x0);
      }),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: {
        symbol: "star", size: 11, color: COLORS.ink,
        line: { color: COLORS.surface, width: 1 }
      }
    }];
  }

  /* Capa "Contactos" (F.2): dónde tocó el precio los límites del ID sin salirse.
   * Símbolos distintos para el toque de mecha y para la rotura fallida. */
  function contactTraces(range) {
    if (!state.contacts || blindfolded() || !isVisible(primary())) { return []; }
    var edges = window_(range);
    var allowed = visibleIds(primary(), edges);
    var source = impulsesOf(primary()).contacts || [];
    var visible = source.filter(function (item) {
      return item.x >= edges.lo && item.x <= edges.hi &&
        !pending(item.x, primary(), edges) && keeps(allowed, item.id);
    });
    return [
      contactTrace(visible, "mecha", "TOQUE_MECHA", "circle-open"),
      contactTrace(visible, "fallida", "ROTURA_FALLIDA", "square-open")
    ].filter(Boolean);
  }

  function contactTrace(contacts, kind, name, symbol) {
    var items = contacts.filter(function (item) { return item.k === kind; });
    if (!items.length) { return null; }
    return {
      type: "scatter", mode: "markers", name: name + " " + label(primary()),
      x: items.map(function (item) { return iso(item.x); }),
      y: items.map(function (item) { return item.y; }),
      text: items.map(function (item) {
        return name + " (" + primary() + ")<br>" + stamp(item.x) +
          "<br>ID " + item.id + " · límite " + item.s + " en " + price(item.y) +
          "<br>cierre de la vela " + price(item.c);
      }),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: { symbol: symbol, size: 9, color: COLORS.ink, line: { width: 1.2 } }
    };
  }

  /* Marcadores sólo del impulso principal: los de la temporalidad superior
   * caerían sobre velas que en este gráfico no son las suyas y sólo añadirían
   * ruido a la auditoría. */
  function markerTraces(range) {
    if (!state.marks || !isVisible(primary())) { return []; }
    var edges = window_(range);
    var allowed = visibleIds(primary(), edges);
    var source = impulsesOf(primary());
    var traces = [];

    // Los marcadores pertenecen a un ID: si su ID no se dibuja, el marcador
    // suelto sólo sería ruido. El filtro de B.2 los acompaña.
    var constitutions = source.constitutions.filter(function (item) {
      return item.x >= edges.lo && item.x <= edges.hi &&
        !pending(item.x, primary(), edges) && keeps(allowed, item.id);
    });
    if (constitutions.length) {
      traces.push({
        type: "scatter", mode: "markers", name: "Constitución " + label(primary()),
        x: constitutions.map(function (item) { return iso(item.x); }),
        y: constitutions.map(function (item) { return item.y; }),
        text: constitutions.map(function (item) {
          return "CONSTITUCIÓN " + primary() + " nº " + item.id + " (" + item.d + ")<br>" +
            stamp(item.x) + "<br>ancla " + price(item.a) + " · extremo " + price(item.e) +
            "<br>A1 " + (item.a1 === null ? "n/d" : price(item.a1)) + " · A2 " + price(item.a2) +
            "<br>cuerpo de la vela contraria " + price(item.body) +
            "<br>limbo previo: " + item.limbo + " barras";
        }),
        hoverinfo: "text", hoverlabel: { align: "left" },
        marker: {
          symbol: "diamond", size: 9,
          color: constitutions.map(function (item) {
            return item.d === "alcista" ? COLORS.bullish : COLORS.bearish;
          }),
          line: { color: COLORS.surface, width: 1 }
        }
      });
    }

    var breaks = source.breaks.filter(function (item) {
      return item.x >= edges.lo && item.x <= edges.hi &&
        !pending(item.x, primary(), edges) && keeps(allowed, item.id);
    });
    /* Fase 2.1: morir atravesando una zona y morir por línea son dos cosas
     * distintas y se distinguen en el dibujo. El símbolo relleno es el de
     * siempre —así la fase 1 se sigue leyendo igual, porque ahí todo es línea— y
     * el hueco marca la excepción: no había zona en ese lado. */
    traces.push(breakTrace(breaks, "favor", "linea", "ROTURA_A_FAVOR", "triangle-up", COLORS.ink));
    traces.push(breakTrace(breaks, "favor", "zona", "ROTURA_A_FAVOR", "triangle-up-open", COLORS.ink));
    traces.push(breakTrace(breaks, "contra", "linea", "ROTURA_EN_CONTRA", "x", COLORS.muted));
    traces.push(breakTrace(breaks, "contra", "zona", "ROTURA_EN_CONTRA", "x-open", COLORS.muted));
    return traces.filter(Boolean);
  }

  /* `source` separa las roturas por el nivel que las produjo: `linea` es la
   * regla de la fase 1 y `zona` (UL u OB) la de la 2.1. Con `break_by_zone`
   * apagado todas caen en `linea` y las dos trazas nuevas salen vacías. */
  function breakTrace(breaks, kind, source, name, symbol, color) {
    var items = breaks.filter(function (item) {
      return item.k === kind && (item.src === "linea") === (source === "linea");
    });
    if (!items.length) { return null; }
    var suffix = DATA.meta.breakByZone
      ? " · " + (source === "linea" ? "por línea" : "por zona")
      : "";
    return {
      type: "scatter", mode: "markers",
      name: name + " " + label(primary()) + suffix,
      x: items.map(function (item) { return iso(item.x); }),
      y: items.map(function (item) { return item.y; }),
      text: items.map(function (item) {
        var how = item.src === "linea"
          ? (DATA.meta.breakByZone
            ? "<br>por LÍNEA: ese lado no tenía zona (el OB nunca se confirmó)"
            : "")
          : "<br>por ZONA " + item.src + ": la atravesó entera" +
            "<br>línea del ID en " + price(item.ln);
        return name + " (" + primary() + ")<br>" + stamp(item.x) +
          "<br>ID roto: " + item.id + " (" + item.d + ")" +
          "<br>cierre " + price(item.y) + " más allá de " + price(item.lvl) + how +
          "<br>pierna en curso: " + item.next;
      }),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: { symbol: symbol, size: 10, color: color, line: { color: COLORS.surface, width: 1 } }
    };
  }

  /* --- Fase 2.1: las roturas evitadas ---------------------------------------
   *
   * Velas que bajo la regla antigua habrían matado el ID y con la nueva no:
   * cerraron más allá de la línea sin atravesar la zona entera. Es la capa que
   * el propietario audita primero, así que el dibujo lleva las dos cosas que
   * hacen falta para juzgarla sin abrir ningún CSV: el segmento que va del
   * cierre a la línea que cruzó —eso es exactamente lo que la regla antigua
   * contaba como rotura— y el marcador sobre el cierre. */

  function hasAvoided() { return DATA.hasAvoided === true; }

  function avoidedOf(timeframe) {
    return impulsesOf(timeframe).avoided || [];
  }

  function avoidedTraces(range) {
    if (blindfolded() || !state.avoided || !hasAvoided()) { return []; }
    if (!isVisible(primary())) { return []; }
    var edges = window_(range);
    var allowed = visibleIds(primary(), edges);
    var items = avoidedOf(primary()).filter(function (item) {
      return item.x >= edges.lo && item.x <= edges.hi &&
        !pending(item.x, primary(), edges) && keeps(allowed, item.id);
    });
    if (!items.length) { return []; }

    // Un solo trazo con separadores nulos: una traza por vela salvada
    // multiplicaría por cien las trazas de una ventana larga.
    var sx = [];
    var sy = [];
    items.forEach(function (item) {
      sx.push(iso(item.x), iso(item.x), null);
      sy.push(item.ln, item.y, null);
    });

    return [
      {
        type: "scatter", mode: "lines", name: "Rotura evitada · tramo",
        x: sx, y: sy, hoverinfo: "skip", showlegend: false,
        line: { color: COLORS.ink, width: 2.4 }
      },
      {
        type: "scatter", mode: "markers",
        name: "ROTURA EVITADA " + label(primary()),
        x: items.map(function (item) { return iso(item.x); }),
        y: items.map(function (item) { return item.y; }),
        text: items.map(function (item) {
          return "ROTURA EVITADA (" + (item.k === "favor"
            ? "ROTURA_A_FAVOR" : "ROTURA_EN_CONTRA") + ")<br>" + stamp(item.x) +
            "<br>ID " + item.id + " (" + item.d + ")" +
            "<br>cierre " + price(item.y) + " más allá de la línea " + price(item.ln) +
            "<br>zona " + item.z + " [" + price(item.zi) + ", " + price(item.zo) + "]" +
            "<br>le faltó " + price(Math.abs(item.zo - item.y)) + " para atravesarla" +
            (item.ext ? "<br>aquí el ID extiende su extremo" : "");
        }),
        hoverinfo: "text", hoverlabel: { align: "left" },
        marker: {
          symbol: "circle-x", size: 12, color: COLORS.ink,
          line: { color: COLORS.surface, width: 1 }
        }
      }
    ];
  }

  /* --- Señales de zona (visual) ---------------------------------------------
   *
   * Las tres marcas que el propietario quiere ver sobre el Diario y H4 mientras
   * audita el toque de una zona: el precio TOCA un OB, LLEGA a un UL y lo
   * RECHAZA, o ROMPE un UL.
   *
   * DIBUJO Y NADA MÁS, como el resto del fichero. Vienen calculadas de Python
   * sobre las zonas de la fase 2.0 y no producen entradas, ni stops, ni
   * targets: el proyecto sigue sin señales operativas. Aquí no se decide cuál
   * cuenta ni se agrupa nada; se elige qué se pinta.
   *
   * Van con el impulso PRINCIPAL del gráfico, igual que los demás marcadores: la
   * señal de un ID de H4 sobre velas de H1 caería en un sitio que en ese gráfico
   * no es el suyo. Y obedecen el filtro de ID visibles (B.2), no el de las
   * zonas: son marcas puntuales, no rectángulos que tapen el precio, y el globo
   * lleva los dos bordes de su zona, así que una señal de un ID cuyo rectángulo
   * ya no se dibuja se sigue pudiendo juzgar. Con el filtro en «ID actual» se
   * ven las del ID vigente y su zona a la vez.
   */

  function hasSignals() { return DATA.hasSignals === true; }

  /* Se calcularon sobre las zonas del modo activo de R-36, así que desaparecen
   * al cambiar de modo por la misma razón que las zonas. */
  function signalsOf(timeframe) {
    if (!zonesAvailable()) { return []; }
    return impulsesOf(timeframe).signals || [];
  }

  /* Un símbolo por tipo, ninguno repetido de las capas que ya existían, y el
   * color del ID al que pertenece la señal. */
  var SIGNAL_STYLE = {
    TOQUE_OB: { symbol: "pentagon", size: 12, title: "TOQUE DE OB" },
    RECHAZO_UL: { symbol: "hexagram", size: 12, title: "RECHAZO DEL UL" },
    ROTURA_UL: { symbol: "star-diamond", size: 13, title: "ROTURA DEL UL" }
  };
  var SIGNAL_KINDS = ["TOQUE_OB", "RECHAZO_UL", "ROTURA_UL"];

  function visibleSignals(edges) {
    if (blindfolded() || !state.signals || !hasSignals()) { return []; }
    if (!zonesAvailable() || !isVisible(primary())) { return []; }
    var allowed = visibleIds(primary(), edges);
    return signalsOf(primary()).filter(function (item) {
      return item.x >= edges.lo && item.x <= edges.hi &&
        !pending(item.x, primary(), edges) && keeps(allowed, item.id);
    });
  }

  function signalTraces(range) {
    var visible = visibleSignals(window_(range));
    if (!visible.length) { return []; }
    return SIGNAL_KINDS.map(function (kind) {
      return signalTrace(visible, kind);
    }).filter(Boolean);
  }

  function signalTrace(signals, kind) {
    var items = signals.filter(function (item) { return item.k === kind; });
    if (!items.length) { return null; }
    var style = SIGNAL_STYLE[kind];
    return {
      type: "scatter", mode: "markers",
      name: kind + " " + label(primary()),
      x: items.map(function (item) { return iso(item.x); }),
      y: items.map(function (item) { return item.y; }),
      text: items.map(signalCaption),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: {
        symbol: style.symbol, size: style.size,
        color: items.map(function (item) {
          return item.d === "alcista" ? COLORS.bullish : COLORS.bearish;
        }),
        line: { color: COLORS.surface, width: 1.2 }
      }
    };
  }

  function signalCaption(item) {
    var style = SIGNAL_STYLE[item.k];
    var text = style.title + " (" + primary() + ")<br>" + stamp(item.x) +
      "<br>ID nº " + item.id + " (" + item.d + ") · zona " + item.z +
      "<br>señal nº " + item.n + " de este tipo en este ID";
    if (item.k === "ROTURA_UL") {
      text += "<br>cierre " + price(item.c) + " más allá del borde exterior " +
        price(item.lvl);
    } else {
      text += "<br>borde interior " + price(item.lvl) +
        " · la mecha llegó a " + price(item.r) +
        "<br>cierre " + price(item.c) +
        (item["in"] ? " DENTRO de la zona" : " fuera de la zona");
      if (item.k === "RECHAZO_UL") {
        text += "<br>no pasó del borde exterior: el UL aguanta";
      }
    }
    return text + "<br>SÓLO DIBUJO: no abre ni cierra ninguna operación";
  }

  /* El sombreado es el del impulso principal: dos capas de limbo superpuestas
   * no se leen, y el limbo que importa al auditar un gráfico es el suyo. */
  function limboShapes(range) {
    if (!state.limbo || blindfolded() || !isVisible(primary())) { return []; }
    var edges = window_(range);
    return impulsesOf(primary()).limbo.filter(function (region) {
      return region[1] >= edges.lo && region[0] <= edges.hi &&
        !pending(region[0], primary(), edges);
    }).map(function (region) {
      return {
        type: "rect", xref: "x", yref: "paper",
        x0: iso(region[0]), x1: iso(clip(region[1], edges)), y0: 0, y1: 1,
        fillcolor: COLORS.limbo, opacity: 0.16, line: { width: 0 }, layer: "below"
      };
    });
  }

  // --- Encuadre manual (G.2) -------------------------------------------------

  /* Plotly devuelve las marcas del eje de fechas como texto sin zona (las mismas
   * cadenas que se le dieron, que son UTC) o como milisegundos. Se vuelven a
   * minutos para poder compararlas con la ventana. */
  function toMinute(value) {
    if (value === null || value === undefined) { return null; }
    if (typeof value === "number") { return Math.round(value / 60000); }
    var text = String(value).trim().replace(" ", "T");
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(text)) { text += "Z"; }
    var ms = Date.parse(text);
    return isNaN(ms) ? null : Math.round(ms / 60000);
  }

  function axisRange(event, axis) {
    var pair = event[axis + ".range"];
    var lo = event[axis + ".range[0]"];
    var hi = event[axis + ".range[1]"];
    if (lo === undefined && pair) { lo = pair[0]; hi = pair[1]; }
    return [lo, hi];
  }

  /* Lo que el usuario acaba de hacer con la rueda, arrastrando o con el doble
   * clic. Sólo se guarda el encuadre; el gráfico ya está pintado como él quiere
   * y volver a dibujarlo aquí pelearía con su gesto. El doble clic (autorange)
   * es la salida: suelta el encuadre y devuelve el mando al replay. */
  function captureZoom(event) {
    if (!event) { return; }
    var released = false;
    ["xaxis", "yaxis"].forEach(function (axis) {
      var key = axis === "xaxis" ? "x" : "y";
      if (event[axis + ".autorange"]) { state.zoom[key] = null; released = true; return; }
      var pair = axisRange(event, axis);
      var lo = key === "x" ? toMinute(pair[0]) : Number(pair[0]);
      var hi = key === "x" ? toMinute(pair[1]) : Number(pair[1]);
      if (lo === null || hi === null || isNaN(lo) || isNaN(hi) || hi <= lo) { return; }
      state.zoom[key] = [lo, hi];
    });
    if (released) { draw(); }
  }

  function releaseZoom() {
    if (!state.zoom.x && !state.zoom.y) { return; }
    state.zoom = { x: null, y: null };
    draw();
  }

  /* --- Escalar arrastrando sobre los ejes (G.3) -----------------------------
   *
   * Como en cualquier gráfico de trading: se aprieta sobre los PRECIOS y se
   * arrastra para comprimir o estirar la vertical, y se aprieta sobre las
   * FECHAS para abrir o cerrar el gráfico de lado. Plotly, sobre el eje, hace
   * pan y no escala, así que el gesto se implementa aquí: se lee el rango
   * vigente, se multiplica por un factor y se le devuelve con `relayout`. El
   * `plotly_relayout` que eso emite lo recoge `captureZoom`, de modo que el
   * encuadre así tomado aguanta los pasos del replay igual que el de la rueda.
   *
   * Sigue sin calcularse nada del motor: esto es encuadre, como el zoom. */
  var MARGIN = { l: 66, r: 18, t: 16, b: 44 };

  var axisDrag = null;

  /* Sobre qué eje cae el punto: la banda de la izquierda es la de los precios y
   * la de abajo la de las fechas. Fuera de las dos, no es este gesto. */
  function axisAt(box, x, y) {
    if (x - box.left < MARGIN.l) { return "y"; }
    if (box.top + box.height - y < MARGIN.b) { return "x"; }
    return null;
  }

  /* El rango que se está viendo. Manda el encuadre manual si lo hay; si no, se
   * lee del gráfico ya resuelto por Plotly. Sin ninguno de los dos no hay nada
   * que escalar y el gesto se deja pasar. */
  function currentRange(key) {
    if (state.zoom[key]) { return state.zoom[key].slice(); }
    var chart = document.getElementById("chart");
    var full = chart && (chart._fullLayout || chart.layout);
    var axis = full && full[key === "x" ? "xaxis" : "yaxis"];
    if (!axis || !axis.range) { return null; }
    var lo = key === "x" ? toMinute(axis.range[0]) : Number(axis.range[0]);
    var hi = key === "x" ? toMinute(axis.range[1]) : Number(axis.range[1]);
    if (lo === null || hi === null || isNaN(lo) || isNaN(hi) || hi <= lo) { return null; }
    return [lo, hi];
  }

  /* El rango multiplicado por `factor` alrededor de `anchor`: 0,5 es el centro y
   * 1 el extremo alto. */
  function scaledRange(pair, factor, anchor) {
    var width = pair[1] - pair[0];
    var pivot = pair[0] + width * anchor;
    var next = width * factor;
    return [pivot - next * anchor, pivot + next * (1 - anchor)];
  }

  function startAxisDrag(event) {
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.getBoundingClientRect !== "function") { return; }
    var box = chart.getBoundingClientRect();
    var key = axisAt(box, event.clientX, event.clientY);
    if (!key) { return; }
    var pair = currentRange(key);
    if (!pair) { return; }
    axisDrag = { key: key, from: key === "y" ? event.clientY : event.clientX, base: pair };
    // En captura y cortando la propagación: si el evento llegara a las capas de
    // arrastre de Plotly, su pan y esta escala pelearían por el mismo gesto.
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  /* 150 px de arrastre duplican o parten en dos lo que se ve. El eje de precios
   * escala alrededor del centro; el de fechas ancla en el borde derecho, que es
   * donde está la última vela: abrir y cerrar el gráfico de lado no puede mover
   * el presente de sitio. */
  function moveAxisDrag(event) {
    if (!axisDrag) { return; }
    var delta = axisDrag.key === "y"
      ? event.clientY - axisDrag.from
      : axisDrag.from - event.clientX;
    var pair = scaledRange(
      axisDrag.base, Math.exp(delta / 150), axisDrag.key === "y" ? 0.5 : 1
    );
    applyAxisRange(axisDrag.key, pair);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyAxisRange(key, pair) {
    var chart = document.getElementById("chart");
    if (!chart || typeof Plotly === "undefined" || !Plotly.relayout) { return; }
    var name = key === "x" ? "xaxis" : "yaxis";
    var update = {};
    update[name + ".range"] = key === "x"
      ? [iso(Math.round(pair[0])), iso(Math.round(pair[1]))]
      : pair;
    update[name + ".autorange"] = false;
    Plotly.relayout(chart, update);
  }

  function endAxisDrag() { axisDrag = null; }

  function bindAxisScaling() {
    var chart = document.getElementById("chart");
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startAxisDrag, true);
    document.addEventListener("mousemove", moveAxisDrag);
    document.addEventListener("mouseup", endAxisDrag);
  }

  /* Cambiar de tramo de historia —preset, fechas, ventana ciega, arrancar o
   * salir del replay— es pedir otro sitio, no otro zoom: ahí el encuadre manual
   * estorba. Alternar capas o dar un paso del replay no lo tocan. */
  function dropZoom() { state.zoom = { x: null, y: null }; }

  /* Desplaza el encuadre del usuario lo justo para que el presente siga dentro,
   * conservando su anchura: el nivel de zoom es suyo, la posición la manda el
   * reloj. Mientras el borde derecho quepa, no se mueve nada. */
  function followX(view, present) {
    var air = span(state.chart);
    var edge = present + 2 * air;        // la vela en formación, más un respiro
    var width = view[1] - view[0];
    if (edge > view[1]) { return [Math.round(edge - width), Math.round(edge)]; }
    if (present < view[0]) {
      return [Math.round(present - width / 2), Math.round(present + width / 2)];
    }
    return view;
  }

  function xRange(range) {
    if (state.zoom.x) { return [iso(state.zoom.x[0]), iso(state.zoom.x[1])]; }
    // En replay el eje se fija a mano y deja aire a la derecha: si se
    // autoescalara, la última vela quedaría pegada al borde y el gráfico daría
    // un salto en cada paso.
    return state.replay
      ? [iso(range.lo), iso(range.hi + 8 * span(state.chart))]
      : undefined;
  }

  /* Se engancha una sola vez, después del primer dibujo: `Plotly.react` conserva
   * los oyentes del div. El `on` lo pone Plotly al montar el gráfico, así que si
   * todavía no está se reintenta en cuanto el navegador respire; sin esto, el
   * enganche dependería de que el propietario tocara otro control. */
  var zoomBound = false;
  var zoomTries = 0;

  function bindZoom() {
    if (zoomBound) { return; }
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.on !== "function") {
      // Acotado: si el gráfico no aparece, se deja de insistir en vez de dejar
      // un temporizador dando vueltas para siempre.
      if (typeof setTimeout === "function" && zoomTries < 20) {
        zoomTries += 1;
        setTimeout(bindZoom, 50);
      }
      return;
    }
    zoomBound = true;
    chart.on("plotly_relayout", captureZoom);
  }

  // --- Figura ---------------------------------------------------------------

  function layout(range) {
    var x = xRange(range);
    return {
      height: 720,
      margin: MARGIN,
      paper_bgcolor: COLORS.surface,
      plot_bgcolor: COLORS.surface,
      font: { family: COLORS.font, size: 12, color: COLORS.ink },
      hovermode: "closest",
      // Varias marcas cuelgan del mismo borde de zona y caen unas encima de
      // otras: el globo tiene que poder abrirse sin clavar el ratón en el píxel
      // exacto. `namelength: -1` evita que Plotly recorte el nombre de la capa.
      hoverdistance: 16,
      hoverlabel: { font: { size: 12 }, namelength: -1 },
      dragmode: "pan",
      showlegend: true,
      legend: { orientation: "h", y: 1.04, x: 0, font: { size: 11 } },
      shapes: limboShapes(range),
      xaxis: {
        type: "date", gridcolor: COLORS.grid, rangeslider: { visible: false },
        // El rango va siempre con su `autorange`: si se diera uno sin apagar el
        // otro, Plotly reescalaría el eje y el encuadre no aguantaría el paso.
        range: x, autorange: x ? false : true,
        title: { text: "UTC", font: { size: 11, color: COLORS.muted } }
      },
      yaxis: {
        gridcolor: COLORS.grid, tickformat: "." + DECIMALS + "f", fixedrange: false,
        // Sin esto el eje de precios se rehace en cada paso y el gráfico "salta"
        // en vertical: con encuadre manual manda lo que fijó el propietario.
        range: state.zoom.y || undefined,
        autorange: state.zoom.y ? false : true
      }
    };
  }

  function draw() {
    // El encuadre manual sigue al reloj ANTES de recortar: la ventana de datos
    // se calcula sobre el tramo que va a quedar a la vista, no sobre el anterior.
    if (state.replay && state.zoom.x) { state.zoom.x = followX(state.zoom.x, now_()); }
    var range = bounds();
    var cut = slice(range);
    // Las zonas van justo detrás de las velas: son áreas, y encima de las
    // líneas del ID taparían lo que se está auditando.
    var traces = priceTraces(cut)
      .concat(zoneTraces(range))
      .concat(candidateTraces(range))
      .concat(impulseTraces(range))
      .concat(stepTraces(range))
      .concat(midTraces(range))
      .concat(markerTraces(range))
      .concat(avoidedTraces(range))
      .concat(signalTraces(range))
      .concat(confirmationTraces(range))
      .concat(contactTraces(range))
      .concat(wrongExtremeTraces(range));

    Plotly.react("chart", traces, layout(range), {
      responsive: true, scrollZoom: true, displaylogo: false,
      // Sin herramientas de dibujo: en auditoría ciega no se puede marcar el
      // gráfico antes de revelar, que es justo lo que invalidaría la prueba.
      modeBarButtonsToRemove: [
        "select2d", "lasso2d", "drawline", "drawopenpath", "drawclosedpath",
        "drawcircle", "drawrect", "eraseshape"
      ]
    });
    bindZoom();
    syncControls(range);
    document.getElementById("notes").textContent = notes(range, cut);
  }

  /* Las capas que el nivel de ruido puede apagar, con el nombre que llevan en el
   * control. Sólo se nombran las que la corrida podría dibujar: decir «escalera
   * apagada» en un explorador sin extensiones haría buscar una capa que no
   * existe. */
  var NOISE_NAMES = [
    ["limbo", "limbo", function () { return true; }],
    ["marks", "constituciones y roturas", function () { return true; }],
    ["contacts", "contactos", function () { return true; }],
    ["mid", "nivel 50 %", function () { return true; }],
    ["wrong", "extremo de color contrario", function () { return true; }],
    ["zonesContext", "zonas del contexto", function () {
      return zonesAvailable() && overlays().length > 1;
    }],
    ["signals", "señales de zona", hasSignals],
    ["avoided", "roturas evitadas", hasAvoided],
    ["steps", "escalera del extremo", hasSteps]
  ];

  /* Qué nivel de ruido está puesto y qué se está dejando fuera por él. Un
   * gráfico con menos marcas TIENE que decir cuáles se ha callado: si no, la
   * ausencia de una capa se lee como que ahí no pasó nada, que es la forma más
   * silenciosa de auditar mal. */
  function noiseCaption() {
    var level = NOISE_LEVELS.filter(function (item) { return item.id === state.noise; })[0];
    var off = NOISE_NAMES.filter(function (item) {
      return item[2]() && !state[item[0]];
    }).map(function (item) { return item[1]; });
    return "RUIDO: " + (level ? level.label : "a mano") +
      (off.length
        ? " · capas apagadas: " + off.join(", ") + " (siguen en los datos y en los informes)"
        : " · todas las capas encendidas");
  }

  function notes(range, cut) {
    var b = bars();
    var visible = cut.end - cut.start;
    var edges = window_(range);
    if (blindfolded()) {
      return "AUDITORÍA CIEGA · semilla " + state.seed + " · " + label(state.chart) + " · " +
        range.from + " → " + range.to + " · " + visible.toLocaleString("es-ES") +
        " velas. Marca tus impulsos y pulsa Revelar. Sorteada dentro de " +
        state.scope.from + " → " + state.scope.to + ".";
    }
    var dibujados = overlays().filter(isVisible).map(function (timeframe) {
      var allowed = visibleIds(timeframe, edges);
      var enVentana = impulsesOf(timeframe).list.filter(function (impulse) {
        return impulse.x1 >= edges.lo && drawnFrom(impulse) <= edges.hi;
      });
      var n = enVentana.filter(function (impulse) { return keeps(allowed, impulse.id); }).length;
      var total = n === enVentana.length ? "" : " de " + enVentana.length.toLocaleString("es-ES");
      return n.toLocaleString("es-ES") + total + " de " + label(timeframe);
    });

    var text = label(state.chart) + " · " + range.from + " → " + range.to + " · " +
      visible.toLocaleString("es-ES") + " velas en la ventana";
    if (state.replay) {
      var half = formingCandle();
      text = "REPLAY · " + label(state.chart) + " · reloj " +
        iso(state.at).slice(0, 16) + " UTC · última vela cerrada " +
        stamp(b.t[state.cursor]) + " (nº " + (state.cursor + 1).toLocaleString("es-ES") +
        " de " + b.t.length.toLocaleString("es-ES") + ")" +
        (half
          ? (half.done
            ? " · vela en formación con " + half.done + " de " + half.total + " velas de " +
              label(half.timeframe)
            // Puede haber vela a medio armar sin que haya cerrado ninguna vela
            // del paso: es lo que pasa al llegar al diario desde H1.
            : " · vela en formación, todavía sin ninguna vela de " +
              label(half.timeframe) + " cerrada")
          : "") +
        " · sólo se dibuja lo que el motor sabía a esa hora";
    }
    if (state.zoom.x || state.zoom.y) {
      text += " · encuadre manual: el zoom se mantiene entre pasos (Ajustar para soltarlo)";
    }
    text += " · " + noiseCaption();
    if (dibujados.length) { text += " · impulsos dibujados: " + dibujados.join(", "); }
    if (state.visible !== "all") {
      text += " · filtro de dibujo «" +
        (state.visible === "current" ? "ID actual" : "ID actual + anterior") +
        "»: los demás siguen en los datos y en los informes";
    }
    if (hasZones() && !zonesAvailable()) {
      text += " · las zonas de la fase 2.0 se calcularon sobre " +
        DATA.meta.legStartMode + " y no se dibujan en otro modo: serían zonas de " +
        "impulsos que en este modo no existen";
    } else if (zonesAvailable() && (state.zonesUl || state.zonesOb)) {
      var conZona = 0;
      overlays().forEach(function (timeframe, position) {
        if (!isVisible(timeframe) || !drawsZonesOf(position)) { return; }
        var allowed = zoneIds(timeframe, edges);
        conZona += zonesOf(timeframe).filter(function (zone) {
          return wantsZone(zone.k) && zone.x1 >= edges.lo && zone.xd <= edges.hi &&
            keeps(allowed, zone.id);
        }).length;
      });
      text += " · zonas dibujadas: " + conZona.toLocaleString("es-ES") +
        " (sólo las del ID actual: al constituirse uno nuevo, las del anterior se van)" +
        (state.zonesContext || overlays().length < 2
          ? ""
          : " · zonas del contexto (" + label(overlays()[1]) + ") APAGADAS") +
        (DATA.meta.breakByZone
          ? " (fase 2.1: éstas SÍ deciden la rotura)"
          : " (fase 2.0: sólo se dibujan, no rompen nada)");
    }
    if (hasSignals() && state.signals && zonesAvailable()) {
      var senales = visibleSignals(edges);
      var porTipo = SIGNAL_KINDS.map(function (kind) {
        return senales.filter(function (item) { return item.k === kind; }).length +
          " " + kind;
      }).join(", ");
      // Una capa de señales que no dice cuántas hay se lee como que ahí no pasó
      // nada; y una que no dice que es sólo dibujo se lee como una entrada.
      text += " · señales de zona a la vista: " + senales.length.toLocaleString("es-ES") +
        " (" + porTipo + ") de " + label(primary()) +
        " · SON DIBUJO: no abren ni cierran nada, no hay entradas en el proyecto";
    }
    if (hasAvoided() && state.avoided && isVisible(primary())) {
      var allowedAvoided = visibleIds(primary(), edges);
      var evitadas = avoidedOf(primary()).filter(function (item) {
        return item.x >= edges.lo && item.x <= edges.hi && keeps(allowedAvoided, item.id);
      }).length;
      text += " · roturas evitadas a la vista: " + evitadas.toLocaleString("es-ES") +
        " (velas que con la regla antigua habrían matado el ID)";
    }
    if (hasSteps()) {
      var escalones = 0;
      var estirados = 0;
      overlays().filter(isVisible).forEach(function (timeframe) {
        var allowedSteps = visibleIds(timeframe, edges);
        impulsesOf(timeframe).list.forEach(function (impulse) {
          if (!impulse.st) { return; }
          if (impulse.x1 < edges.lo || drawnFrom(impulse) > edges.hi) { return; }
          if (pending(impulse.x0, timeframe, edges)) { return; }
          if (!keeps(allowedSteps, impulse.id)) { return; }
          estirados += 1;
          escalones += extremeSteps(impulse, timeframe, edges).length - 1;
        });
      });
      text += " · extremo estirado (§3.2): " + estirados.toLocaleString("es-ES") +
        " ID a la vista con " + escalones.toLocaleString("es-ES") + " saltos. " +
        "La línea del extremo va EN ESCALERA: cada tramo es el nivel que estaba " +
        "puesto en ese momento, no el del final" +
        (state.steps ? "" : " (capa de saltos APAGADA: los escalones se dibujan igual)");
    }
    var info = modeInfo(state.mode);
    if (info) {
      text += " · LEG_START_MODE = " + info.name + " (hash " + info.hash + "): " +
        info.impulses.toLocaleString("es-ES") + " impulsos en total, " +
        info.wrong.toLocaleString("es-ES") + " con el extremo sobre vela de color contrario";
    }
    if (!visible) {
      // Un gráfico vacío no puede quedarse callado: o el mercado estaba cerrado,
      // o las velas de esta temporalidad no llegan hasta aquí, y son dos cosas
      // muy distintas. La segunda es la que se lleva pasando por la primera.
      text += ". No hay velas de " + label(state.chart) + " en este tramo: " +
        (b.t.length
          ? "el mercado estaba cerrado, o las embebidas empiezan en " + stamp(b.t[0]) +
            (b.truncated ? " porque el resto lo recortó max_explorer_bars" : "")
          : "el histórico no llega") + ".";
    }
    if (b.truncated) {
      text += ". Aviso: de las " + b.total.toLocaleString("es-ES") + " velas de " +
        label(state.chart) + " sólo se han embebido las últimas " +
        b.t.length.toLocaleString("es-ES") + " (max_explorer_bars).";
    }
    if (state.blind && state.revealed) {
      text += " · revelado de la ventana ciega con semilla " + state.seed;
    }
    return text;
  }

  // --- Auditoría ciega (F.1) -------------------------------------------------

  /* Anchura de la ventana que se sortea: la del preset elegido. Con "Todo" se
   * usa un mes, que es lo que se puede auditar de una sentada. */
  function blindWidth() {
    var preset = PRESETS.filter(function (p) { return p.id === state.preset; })[0];
    return preset && preset.days ? preset.days : 30;
  }

  function startBlind(seed) {
    // La ciega y el replay son dos pruebas distintas sobre la misma ventana: al
    // empezar una se sale de la otra en vez de dejar controles muertos.
    resetReplay();
    dropZoom();
    var range = bounds();
    if (!state.blind) { state.scope = { from: range.from, to: range.to }; }
    state.seed = seed;
    state.blind = true;
    state.revealed = false;

    var width = Math.min(blindWidth(), spanDays(state.scope.from, state.scope.to));
    var room = Math.max(0, spanDays(state.scope.from, state.scope.to) - width);
    var offset = Math.floor(rng(state.seed)() * (room + 1));
    state.from = shiftDays(state.scope.from, offset);
    state.to = shiftDays(state.from, width - 1);
    if (state.to > state.scope.to) { state.to = state.scope.to; }
    draw();
  }

  function resetBlind() {
    if (!state.blind) { return; }
    dropZoom();
    state.from = state.scope.from;
    state.to = state.scope.to;
    state.blind = false;
    state.revealed = false;
    state.scope = null;
  }

  function exitBlind() {
    if (!state.blind) { return; }
    resetBlind();
    draw();
  }

  function seedInput() { return document.getElementById("blind-seed"); }

  /* Si el propietario escribe una semilla, manda la suya y se reabre la misma
   * ventana. Si no, se sortea una nueva cada vez y se enseña: el campo muestra
   * siempre la semilla de la ventana que se está viendo, así que sin esta
   * distinción "Otra ventana" repetiría la anterior para siempre. */
  function chosenSeed() {
    var typed = parseInt(seedInput().value, 10);
    if (state.seedTyped && !isNaN(typed)) {
      state.seedTyped = false;
      return typed;
    }
    return Math.floor(Math.random() * 1000000);
  }

  // --- Replay (G.1) -----------------------------------------------------------

  /* Arranca en la fecha elegida con el cursor en la última vela ANTERIOR a ese
   * día: el primer paso descubre la primera vela de la fecha, que es lo que se
   * quiere auditar, y no la enseña ya hecha. */
  function startReplay(day) {
    var t = bars().t;
    var index = lowerBound(t, dayStart(day)) - 1;
    if (index < 0) { index = 0; }
    if (index > t.length - 1) { index = t.length - 1; }
    if (!state.replay) {
      state.resume = { from: state.from, to: state.to, preset: state.preset };
    }
    resetBlind();
    pauseReplay();
    dropZoom();
    state.replay = true;
    state.cursor = index;
    state.sub = 0;
    state.at = now_();
    draw();
  }

  function resetReplay() {
    if (!state.replay) { return; }
    pauseReplay();
    dropZoom();
    state.replay = false;
    state.from = state.resume.from;
    state.to = state.resume.to;
    state.preset = state.resume.preset;
    state.resume = null;
    state.sub = 0;
  }

  function exitReplay() {
    if (!state.replay) { return; }
    resetReplay();
    draw();
  }

  /* Al cambiar de temporalidad en mitad del replay el reloj no se mueve: se
   * busca la última vela de la nueva que ya hubiera cerrado a esa misma hora. Si
   * no se hiciera, el índice del cursor —que es de otro array— señalaría a una
   * fecha cualquiera.
   *
   * Y lo que va corrido de la vela en curso se conserva igual: si en H4 llevas
   * dos velas dentro del día, el diario tiene que enseñar su vela a medio armar
   * con esas dos horas dentro. Sin esto, saltar de temporalidad devolvía el
   * gráfico al último cierre —el día anterior— y parecía que el replay se
   * reiniciaba. El reloj no puede ir a más resolución que la temporalidad
   * inferior de la nueva: lo que no completa una de sus velas se queda fuera. */
  function alignCursor(at) {
    var t = bars().t;
    var index = lowerBound(t, at - span(state.chart) + 1) - 1;
    state.cursor = Math.min(Math.max(index, 0), t.length - 1);
    state.sub = 0;
    if (!state.forming) { return; }
    var edges = formingRange();
    if (!edges) { return; }
    var fine = DATA.bars[edges.timeframe].t;
    // Velas inferiores cerradas a esa hora que caen dentro de la que se forma.
    var formed = lowerBound(fine, at - span(edges.timeframe) + 1) - edges.from;
    state.sub = Math.max(0, Math.min(formed, edges.to - edges.from - 1));
  }

  /* Un paso: o se forma un trozo más de la vela en curso, o la vela cierra y el
   * motor reacciona. Nunca las dos cosas a la vez. Devuelve si se movió algo. */
  function stepReplay(direction) {
    var t = bars().t;
    if (direction > 0) {
      if (state.sub < subSteps()) { state.sub += 1; }
      else if (state.cursor + 1 < t.length) { state.cursor += 1; state.sub = 0; }
      else { return false; }
    } else if (state.sub > 0) {
      state.sub -= 1;
    } else if (state.cursor > 0) {
      state.cursor -= 1;
      state.sub = 0;
    } else {
      return false;
    }
    state.at = clock();
    draw();
    return true;
  }

  function playReplay() {
    if (!state.replay || state.playing) { return; }
    state.playing = true;
    schedule();
    draw();
  }

  /* Encadenada con `setTimeout` y no con `setInterval`: si un paso tarda más que
   * el intervalo —ventanas grandes, muchas capas— los pasos no se apilan. */
  function schedule() {
    timer = setTimeout(function () {
      timer = null;
      if (!state.playing) { return; }
      if (!stepReplay(1)) { pauseReplay(); draw(); }
      else { schedule(); }
    }, state.speed);
  }

  function pauseReplay() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
    state.playing = false;
  }

  // --- Controles ------------------------------------------------------------

  function buildChartButtons() {
    var container = document.getElementById("tf-buttons");
    DATA.charts.forEach(function (chart) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label(chart);
      button.dataset.tf = chart;
      button.title = "Dibuja el impulso de " + DATA.layout[chart].map(label).join(" y ");
      button.addEventListener("click", function () {
        // El reloj canónico, no el que se lee en este gráfico: si vienes de
        // pasar por el diario, lo que allí no cabía sigue estando aquí.
        var at = state.replay ? state.at : null;
        state.chart = chart;
        if (at !== null) { alignCursor(at); }
        buildImpulseLayers();
        draw();
      });
      container.appendChild(button);
    });
  }

  /* R-36 — un botón por modo. Sólo aparecen si la corrida embebió los tres: con
   * uno solo no hay nada que comparar y el control sobraría. */
  function buildModeButtons() {
    var container = document.getElementById("mode-buttons");
    if (modes().length < 2) {
      var group = document.getElementById("mode-group");
      if (group && group.style) { group.style.display = "none"; }
      return;
    }
    modes().forEach(function (mode) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = mode.label;
      button.dataset.mode = mode.id;
      button.title = mode.name + " · " + mode.impulses.toLocaleString("es-ES") +
        " impulsos · " + mode.wrong.toLocaleString("es-ES") +
        " con el extremo sobre vela de color contrario · hash " + mode.hash;
      button.addEventListener("click", function () {
        state.mode = mode.id;
        draw();
      });
      container.appendChild(button);
    });
  }

  /* Lo que la corrida NO trae no se puede encender, lo pida quien lo pida: ni el
   * estado de salida ni un nivel de ruido. Se aplica en un solo sitio y después
   * de cada preset, porque el preset no sabe qué llevaba el payload. */
  function enforceAvailability() {
    if (!hasZones()) { state.zonesUl = state.zonesOb = false; }
    if (!hasSignals()) { state.signals = false; }
    if (!hasSteps()) { state.steps = false; }
    if (!hasAvoided()) { state.avoided = false; }
  }

  /* Fase 2.0. Sin zonas en el payload las casillas no se enseñan: una capa que
   * no puede dibujar nada sólo hace dudar de si está fallando. */
  function buildZoneLayers() {
    if (hasZones()) { return; }
    hide("zone-layers");
  }

  /* Mismo criterio para las señales: sin zonas no hay ni una, y una casilla que
   * no puede dibujar nada sólo hace dudar de si está fallando. */
  function buildSignalLayers() {
    if (hasSignals()) { return; }
    hide("signal-layers");
  }

  /* Fase 2.1, mismo criterio: con `break_by_zone: false` no hay ni una rotura
   * evitada, y una casilla que no puede dibujar nada sólo hace dudar. */
  function buildBreakLayers() {
    // Las dos casillas se apagan por separado: puede haber roturas evitadas en
    // contra —que no mueven el extremo— sin una sola extensión, y al revés.
    if (!hasSteps()) { hide("steps-layer"); }
    if (!hasAvoided()) { hide("avoided-layer"); }
    if (hasAvoided() || hasSteps()) { return; }
    hide("break-layers");
  }

  function hide(id) {
    var element = document.getElementById(id);
    if (element && element.style) { element.style.display = "none"; }
  }

  /* H.1 — un botón por nivel de ruido. Sólo mueve casillas que ya existían: lo
   * que el nivel decide es cuántas cosas se dibujan a la vez, nunca qué se
   * calcula. */
  function setNoise(id) {
    var preset = NOISE[id];
    if (!preset) { return; }
    Object.keys(preset).forEach(function (key) { state[key] = preset[key]; });
    state.noise = id;
    enforceAvailability();
  }

  /* Cualquier casilla tocada a mano deja el nivel sin dueño: a partir de ahí lo
   * que se ve es la mezcla del propietario y ningún botón puede decir que es
   * suya. */
  function handTuned() { state.noise = null; }

  function buildNoiseButtons() {
    var container = document.getElementById("noise-buttons");
    if (!container) { return; }
    NOISE_LEVELS.forEach(function (level) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = level.label;
      button.dataset.noise = level.id;
      button.title = "Preset de capas: enciende y apaga las que ya existen. " +
        "No cambia la detección ni los informes.";
      button.addEventListener("click", function () {
        setNoise(level.id);
        draw();
      });
      container.appendChild(button);
    });
  }

  function buildVisibleButtons() {
    var container = document.getElementById("visible-buttons");
    VISIBLE_MODES.forEach(function (mode) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = mode.label;
      button.dataset.visible = mode.id;
      button.title = "Filtro de dibujo: no cambia la detección ni los informes";
      button.addEventListener("click", function () {
        state.visible = mode.id;
        handTuned();
        draw();
      });
      container.appendChild(button);
    });
  }

  function buildPresetButtons() {
    var container = document.getElementById("preset-buttons");
    PRESETS.forEach(function (preset) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.label;
      button.dataset.preset = preset.id;
      button.addEventListener("click", function () {
        state.preset = preset.id;
        state.from = state.to = null;
        dropZoom();
        draw();
      });
      container.appendChild(button);
    });
  }

  /* Las casillas de impulso cambian con el gráfico: en H4 son H4 y Diario, en
   * M15 sólo H1. Se reconstruyen en cada cambio de temporalidad. */
  function buildImpulseLayers() {
    var container = document.getElementById("impulse-layers");
    container.innerHTML = "";
    overlays().forEach(function (timeframe, position) {
      var key = state.chart + ":" + timeframe;
      var id = "layer-id-" + timeframe;
      var wrapper = document.createElement("label");
      wrapper.className = "toggle";
      var box = document.createElement("input");
      box.type = "checkbox";
      box.id = id;
      box.checked = !state.hidden[key];
      box.addEventListener("change", function (event) {
        state.hidden[key] = !event.target.checked;
        draw();
      });
      wrapper.appendChild(box);
      wrapper.appendChild(
        document.createTextNode(
          "ID " + label(timeframe) + (position === 0 ? " (principal)" : " (contexto)")
        )
      );
      container.appendChild(wrapper);
    });
  }

  /* Avanza o retrocede la ventana actual una anchura completa. Los tramos van
   * pegados y sin solapar: el "hasta" de uno es el día anterior al "desde" del
   * siguiente, así ninguna vela se audita dos veces. */
  function step(direction) {
    var range = bounds();
    var width = spanDays(range.from, range.to) + 1;
    var from = shiftDays(range.from, direction * width);
    var to = shiftDays(range.to, direction * width);
    if (from < range.first) { from = range.first; to = shiftDays(from, width - 1); }
    if (to > range.last) { to = range.last; from = shiftDays(to, -(width - 1)); }
    state.from = from < range.first ? range.first : from;
    state.to = to > range.last ? range.last : to;
    dropZoom();
    draw();
  }

  function syncControls(range) {
    var usingPreset = !state.from && !state.to;
    document.querySelectorAll("#tf-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.tf === state.chart));
    });
    document.querySelectorAll("#view-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
    });
    document.querySelectorAll("#preset-buttons button").forEach(function (button) {
      button.setAttribute(
        "aria-pressed", String(usingPreset && button.dataset.preset === state.preset)
      );
      // Durante el replay la ventana la manda el cursor: los controles de
      // periodo se apagan en vez de mentir sobre lo que se está viendo.
      button.disabled = state.replay;
    });
    document.querySelectorAll("#visible-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.visible === state.visible));
    });
    document.querySelectorAll("#noise-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.noise === state.noise));
    });
    // El preset mueve el estado sin tocar el DOM, así que las casillas se
    // sincronizan siempre: si no, un nivel apagaría una capa dejando su casilla
    // marcada y el control mentiría sobre lo que se está viendo.
    [
      ["layer-limbo", "limbo"],
      ["layer-marks", "marks"],
      ["layer-contacts", "contacts"],
      ["layer-mid", "mid"],
      ["layer-wrong", "wrong"]
    ].forEach(function (pair) {
      document.getElementById(pair[0]).checked = state[pair[1]];
    });
    document.querySelectorAll("#mode-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
    });
    var from = document.getElementById("from");
    var to = document.getElementById("to");
    from.min = to.min = range.first;
    from.max = to.max = range.last;
    from.value = range.from;
    to.value = range.to;

    // Sólo hay algo que soltar si el encuadre está tomado a mano.
    document.getElementById("zoom-reset").disabled = !state.zoom.x && !state.zoom.y;

    // Fase 2.0: las zonas sólo existen para el modo con el que se calcularon.
    if (hasZones()) {
      ["layer-zones-ul", "layer-zones-ob", "layer-zones-context"].forEach(function (id) {
        document.getElementById(id).disabled = !zonesAvailable();
      });
      document.getElementById("layer-zones-ul").checked = state.zonesUl;
      document.getElementById("layer-zones-ob").checked = state.zonesOb;
      document.getElementById("layer-zones-context").checked = state.zonesContext;
    }
    if (hasSignals()) {
      // Se calcularon sobre las zonas del modo activo: en otro modo no hay nada
      // que encender, igual que con las zonas.
      document.getElementById("layer-signals").disabled = !zonesAvailable();
      document.getElementById("layer-signals").checked = state.signals;
    }
    if (hasAvoided()) {
      document.getElementById("layer-avoided").checked = state.avoided;
    }
    if (hasSteps()) {
      document.getElementById("layer-steps").checked = state.steps;
    }
    seedInput().value = state.seed === null ? "" : String(state.seed);
    document.getElementById("blind-reveal").disabled = !blindfolded();
    document.getElementById("blind-exit").disabled = !state.blind;
    document.getElementById("blind-start").textContent =
      state.blind ? "Otra ventana" : "Empezar";

    syncReplay(range);
  }

  function syncReplay(range) {
    var group = document.getElementById("replay-group");
    if (group) { group.className = state.replay ? "group on" : "group"; }

    var day = document.getElementById("replay-date");
    day.min = range.first;
    day.max = range.last;
    if (state.replay) { day.value = dayOf(bars().t[state.cursor]); }
    else if (!day.value) { day.value = range.to; }

    ["replay-step", "replay-back", "replay-play", "replay-exit"].forEach(function (id) {
      document.getElementById(id).disabled = !state.replay;
    });
    document.getElementById("replay-start").textContent =
      state.replay ? "Reiniciar" : "Empezar";
    document.getElementById("replay-play").textContent = state.playing ? "⏸" : "▶";
    document.getElementById("replay-forming").checked = state.forming;
    document.getElementById("replay-window").value = String(state.window);
    ["from", "to", "prev", "next"].forEach(function (id) {
      document.getElementById(id).disabled = state.replay;
    });
  }

  function bindControls() {
    document.querySelectorAll("#view-buttons button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.view = button.dataset.view;
        draw();
      });
    });
    document.getElementById("zoom-reset").addEventListener("click", releaseZoom);
    document.getElementById("prev").addEventListener("click", function () { step(-1); });
    document.getElementById("next").addEventListener("click", function () { step(1); });
    ["from", "to"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", function (event) {
        var value = event.target.value;
        if (!value) { return; }
        state[id] = value;
        var range = bounds();
        if (range.from > range.to) { state[id === "from" ? "to" : "from"] = value; }
        dropZoom();
        draw();
      });
    });
    [
      ["layer-limbo", "limbo"],
      ["layer-marks", "marks"],
      ["layer-contacts", "contacts"],
      ["layer-mid", "mid"],
      ["layer-wrong", "wrong"],
      ["layer-zones-ul", "zonesUl"],
      ["layer-zones-ob", "zonesOb"],
      ["layer-zones-context", "zonesContext"],
      ["layer-signals", "signals"],
      ["layer-avoided", "avoided"],
      ["layer-steps", "steps"]
    ].forEach(function (pair) {
      document.getElementById(pair[0]).addEventListener("change", function (event) {
        state[pair[1]] = event.target.checked;
        handTuned();
        draw();
      });
    });
    seedInput().addEventListener("change", function () { state.seedTyped = true; });
    document.getElementById("blind-start").addEventListener("click", function () {
      startBlind(chosenSeed());
    });
    document.getElementById("blind-reveal").addEventListener("click", function () {
      if (!state.blind) { return; }
      state.revealed = true;
      draw();
    });
    document.getElementById("blind-exit").addEventListener("click", exitBlind);
    bindReplay();
    bindArrowKeys();
  }

  function bindReplay() {
    document.getElementById("replay-start").addEventListener("click", function () {
      var day = document.getElementById("replay-date").value;
      startReplay(day || bounds().last);
    });
    document.getElementById("replay-step").addEventListener("click", function () {
      if (state.replay) { stepReplay(1); }
    });
    document.getElementById("replay-back").addEventListener("click", function () {
      if (state.replay) { stepReplay(-1); }
    });
    document.getElementById("replay-play").addEventListener("click", toggleReplay);
    document.getElementById("replay-exit").addEventListener("click", exitReplay);
    document.getElementById("replay-forming").addEventListener("change", function (event) {
      state.forming = event.target.checked;
      // Sin vela en formación el reloj vuelve al último cierre: es lo que se está
      // enseñando, y el reloj no puede prometer más de lo que se ve.
      if (!state.forming) { state.sub = 0; state.at = now_(); }
      draw();
    });
    document.getElementById("replay-speed").addEventListener("change", function (event) {
      var speed = parseInt(event.target.value, 10);
      if (!isNaN(speed) && speed > 0) { state.speed = speed; }
    });
    document.getElementById("replay-window").addEventListener("change", function (event) {
      var count = parseInt(event.target.value, 10);
      if (isNaN(count) || count < 2) { return; }
      state.window = count;
      dropZoom();
      draw();
    });
  }

  function toggleReplay() {
    if (!state.replay) { return; }
    if (state.playing) { pauseReplay(); draw(); } else { playReplay(); }
  }

  /* ◀ ▶ también con las flechas del teclado (B.3), y la barra espaciadora para
   * arrancar y parar el replay. Se ignoran mientras el foco está en un campo de
   * texto: ahí las flechas mueven el cursor y robarlas haría imposible escribir
   * una fecha o una semilla.
   *
   * En replay las flechas dan pasos en vez de mover la ventana: es el mismo
   * gesto —avanzar y retroceder en el tiempo— a la escala de lo que se mira. */
  function bindArrowKeys() {
    if (!document.addEventListener) { return; }
    document.addEventListener("keydown", function (event) {
      var arrow = event.key === "ArrowLeft" || event.key === "ArrowRight";
      var space = event.key === " " || event.key === "Spacebar";
      if (!arrow && !space) { return; }
      var focused = document.activeElement;
      var tag = focused && focused.tagName ? focused.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
      // La barra espaciadora sobre un botón lo pulsa: ahí no se roba.
      if (space && (tag === "BUTTON" || !state.replay)) { return; }
      if (event.preventDefault) { event.preventDefault(); }
      if (space) { toggleReplay(); return; }
      var back = event.key === "ArrowLeft" ? -1 : 1;
      if (state.replay) { stepReplay(back); } else { step(back); }
    });
  }

  buildChartButtons();
  buildModeButtons();
  buildZoneLayers();
  buildSignalLayers();
  buildBreakLayers();
  buildNoiseButtons();
  // El nivel de salida se aplica aquí, con el payload ya leído: así lo que no
  // trae la corrida se queda apagado aunque el preset lo encienda.
  setNoise(state.noise);
  buildVisibleButtons();
  buildPresetButtons();
  buildImpulseLayers();
  bindControls();
  bindAxisScaling();
  draw();
})();
