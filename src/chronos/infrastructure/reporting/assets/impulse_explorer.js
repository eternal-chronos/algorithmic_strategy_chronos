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
   *   · Limpio  — el ID actual en la temporalidad que se está mirando, y nada
   *               más. Fuera se quedan las capas que no deciden nada sobre el
   *               ID vigente: limbo, contactos, 50 % y extremo contrario.
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
      accumulation: true
    },
    normal: {
      visible: "pair",
      limbo: true, marks: true, contacts: false, mid: false, wrong: true,
      accumulation: true
    },
    all: {
      visible: "all",
      limbo: true, marks: true, contacts: true, mid: true, wrong: true,
      accumulation: true
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
    /* La ACUMULACIÓN: desde la vela en que el ID cumple la firma del propietario
     * —dos toques arriba y dos abajo sin romper— hasta que muere. Encendida en
     * los tres niveles; la capa está APARCADA y el payload no trae ninguna
     * hasta que el propietario la vuelva a pedir. */
    accumulation: true,
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
    zoom: { x: null, y: null },
    /* Simulador de entradas (I.1). `sim` es la caja plantada a mano —lado,
     * entrada, stop, objetivo y el tramo que ocupa— y `arming` el lado que
     * espera el clic que la planta. Es DIBUJO: no sale del explorador, no lo lee
     * nadie y el motor no se entera de que existe. */
    sim: null,
    arming: null,
    /* El R:R con el que se planta y al que vuelve el objetivo cuando se mueve el
     * stop. `null` es «a mano»: lo dejó ahí un arrastre del objetivo. */
    ratio: 2,
    /* Recuadros marcados a mano (I.3). Cada uno es un rectángulo —`kind`,
     * `from`, `to`, `low`, `high`— que planta el PROPIETARIO para señalar lo
     * que ve y todavía no tiene regla. No salen del explorador y el motor no
     * los ve. `arming === "rect:A"` es el botón de ese nombre esperando el clic
     * que planta el siguiente. */
    rects: [],
    /* Líneas trazadas a mano (I.4). Cada una es un segmento —`kind`, `from`,
     * `to` y el precio de cada extremo, `left` y `right`— que planta el
     * PROPIETARIO para señalar lo que quiere explicar: un nivel, un tramo, de
     * dónde a dónde mira. Cada una lleva el nombre de la TEMPORALIDAD que se
     * está marcando con ella. Tampoco salen del explorador ni las ve el motor.
     * `arming === "line:H4"` es el botón de esa temporalidad esperando el clic. */
    lines: [],
    /* Fibonacci trazados a mano (I.5). Cada uno son las DOS anclas que puso el
     * propietario —`from`/`zero` la del 0 y `to`/`hundred` la del 100— y de
     * ellas salen los porcentajes: el 0 y el 100 son los extremos, y los
     * retrocesos —el 50, el 70,5 de la GOLD ZONE, el 72 y el 79— caen entre
     * medias contando DESDE EL 0. Tampoco es capa del motor: nadie ha medido
     * ese retroceso, no sale del explorador y no hay ninguna regla detrás. */
    fibs: [],
    /* El 0 ya clavado mientras se espera el clic del 100. Un Fibonacci son dos
     * clics y entre uno y otro no hay nada que dibujar todavía. */
    fibDraft: null,
    /* Cuenta simulada (I.2). El capital puesto, cómo se mide el riesgo de la
     * SIGUIENTE operación y las que se llevan apuntadas. Cada una guarda los
     * dólares que se jugó y el capital que había puesto al apuntarla: lo ya
     * cobrado no cambia al tocar la casilla, sólo lo que venga detrás. Lo
     * apunta el propietario a mano: el motor no ve ninguna de estas
     * operaciones. */
    account: {
      initial: 50,
      mode: "percent",   // percent | cash
      risk: 2,
      trades: [],
      copied: null       // resultado del último «Copiar», para poder decirlo
    }
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

  function keeps(allowed, id) { return allowed === null || allowed[id] === true; }

  /* Primer minuto que el ID llega a pintar: el nivel más antiguo de los dos ya
   * existía en el precio antes de la constitución (tramo punteado de B.1). */
  function drawnFrom(impulse) {
    return Math.min(impulse.x0, impulse.xa, impulse.xe);
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
        var levels = [
          { name: "ancla", value: impulse.a, defined: impulse.xa },
          { name: "extremo", value: impulse.e, defined: impulse.xe }
        ];
        levels.forEach(function (level) {
          level.from = impulse.x0;
          push(bucket.live, impulse.x0, clip(impulse.x1, edges), level.value,
            head + "<br>" + level.name + " " + price(level.value) +
            "<br>fija la vela de " + stamp(level.defined));
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

  /* Nivel del 50 % de cada ID: la línea punteada que el propietario traza a mano
   * en mitad del rango (F.3). Visual y opcional; no entra en ningún cálculo. */
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
        var level = (impulse.a + impulse.e) / 2;
        var caption = "50 % del ID " + timeframe + " nº " + impulse.id + "<br>" + price(level);
        x.push(iso(impulse.x0), iso(clip(impulse.x1, edges)), null);
        y.push(level, level, null);
        text.push(caption, caption, "");
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

  /* --- La acumulación ---------------------------------------------------------
   *
   * La firma del propietario: el precio toca la parte alta del ID, toca la
   * baja, y repite —dos toques en cada límite sin romper ninguno—. Cuando se
   * cumple, el ID está ACUMULANDO entre su ancla y su extremo y, como dice el
   * mentor, dentro de una acumulación las estructuras «las puede pasar por la
   * piedra»: lo que vale es el rango de extremo a extremo.
   *
   * Viene fechada del motor: `x` es la vela del toque que completa la firma y
   * desde ahí hasta que el ID muere se sombrea el rango. Antes de esa vela no
   * se sabía, y por eso el sombreado no empieza en la constitución.
   *
   * La capa está APARCADA: el motor no manda ninguna hasta que el propietario
   * la vuelva a pedir, y sin ninguna la casilla se esconde.
   */
  function hasAccumulations() { return DATA.hasAccumulations === true; }

  function accumulationsOf(timeframe) { return impulsesOf(timeframe).accumulations || []; }

  function accumulationTraces(range) {
    if (blindfolded() || !state.accumulation || !hasAccumulations()) { return []; }
    var edges = window_(range);
    var traces = [];
    overlays().forEach(function (timeframe) {
      if (!isVisible(timeframe)) { return; }
      var allowed = visibleIds(timeframe, edges);
      var bucket = shape();
      accumulationsOf(timeframe).forEach(function (item) {
        if (item.x1 < edges.lo || item.x > edges.hi) { return; }
        if (pending(item.x, timeframe, edges)) { return; }
        if (!keeps(allowed, item.id)) { return; }
        var a = iso(item.x), b = iso(clip(item.x1, edges));
        var caption = "ACUMULACIÓN · ID " + timeframe + " nº " + item.id + " · " + item.d +
          "<br>desde la vela de " + stamp(item.x) + ": ahí se cumple la firma —" +
          "dos toques arriba y dos abajo sin romper—" +
          "<br>rango: " + price(item.lo) + " → " + price(item.hi) +
          "<br>toques en toda la vida del ID: " + item.up + " arriba, " + item.dn + " abajo" +
          "<br>dentro de una acumulación vale el rango de extremo a extremo, no la " +
          "estructura de dentro<br>ES DIBUJO: no decide nada";
        bucket.x.push(a, b, b, a, a, null);
        bucket.y.push(item.lo, item.lo, item.hi, item.hi, item.lo, null);
        bucket.text.push(caption, caption, caption, caption, caption, "");
      });
      if (!bucket.x.length) { return; }
      traces.push({
        type: "scatter", mode: "lines",
        name: "Acumulación " + label(timeframe) + (timeframe === primary() ? "" : " (contexto)"),
        x: bucket.x, y: bucket.y, text: bucket.text,
        hoverinfo: "text", hoverlabel: { align: "left" }, connectgaps: false,
        fill: "toself", fillcolor: rgba(COLORS.accumulation, 0.2),
        fillpattern: { shape: "x", fgcolor: COLORS.accumulation, bgcolor: rgba(COLORS.accumulation, 0.08), size: 8 },
        line: { color: COLORS.accumulation, width: 1, dash: "longdash" }
      });
    });
    return traces;
  }

  function shape() { return { x: [], y: [], text: [] }; }

  /* El color de la paleta llega como `#rrggbb`; el relleno necesita alfa. */
  function rgba(hex, alpha) {
    var value = String(hex).replace("#", "");
    if (value.length !== 6) { return hex; }
    var r = parseInt(value.slice(0, 2), 16);
    var g = parseInt(value.slice(2, 4), 16);
    var b = parseInt(value.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
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
    traces.push(breakTrace(breaks, "favor", "ROTURA_A_FAVOR", "triangle-up", COLORS.ink));
    traces.push(breakTrace(breaks, "contra", "ROTURA_EN_CONTRA", "x", COLORS.muted));
    return traces.filter(Boolean);
  }

  function breakTrace(breaks, kind, name, symbol, color) {
    var items = breaks.filter(function (item) { return item.k === kind; });
    if (!items.length) { return null; }
    return {
      type: "scatter", mode: "markers",
      name: name + " " + label(primary()),
      x: items.map(function (item) { return iso(item.x); }),
      y: items.map(function (item) { return item.y; }),
      text: items.map(function (item) {
        return name + " (" + primary() + ")<br>" + stamp(item.x) +
          "<br>ID roto: " + item.id + " (" + item.d + ")" +
          "<br>cierre " + price(item.y) + " más allá de " + price(item.lvl) +
          "<br>pierna en curso: " + item.next;
      }),
      hoverinfo: "text", hoverlabel: { align: "left" },
      marker: { symbol: symbol, size: 10, color: color, line: { color: COLORS.surface, width: 1 } }
    };
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
    if (x - box.left < MARGIN.l) {
      var plot = plotBox(box);
      if (y <= plot.top + plot.height) { return "y"; }
    }
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

  /* --- Simulador de entradas (I.1) ------------------------------------------
   *
   * Dos botones —Largo y Corto— y la caja de siempre: el rectángulo del OBJETIVO
   * pegado por encima de la entrada y el del RIESGO por debajo, al revés en
   * corto. El botón ARMA y el clic siguiente sobre el gráfico PLANTA la caja con
   * la entrada en el precio y el minuto de ese clic. Después se arrastra: el
   * borde de fuera de cada caja mueve el objetivo o el stop, la línea de la
   * entrada mueve el conjunto entero, los bordes de los lados alargan el tramo y
   * el interior lo desplaza todo.
   *
   * ES DIBUJO A MANO Y NADA MÁS. No hay orden, ni ejecución, ni resultado: la
   * caja no lee una sola vela, nadie comprueba si el precio llegó al objetivo o
   * al stop y el motor no se entera de que existe. Sirve para medir a ojo
   * —cuántos pips de riesgo, qué R:R— encima de lo que el explorador ya pinta.
   * El proyecto sigue sin entradas.
   *
   * El arrastre está escrito a mano, con el mismo gesto que la escala de los
   * ejes (G.3), en vez de con las formas editables de Plotly: `edits.shapePosition`
   * las vuelve arrastrables TODAS, y los rectángulos del limbo ocupan la pantalla
   * entera, así que se quedarían con el pan del gráfico.
   */
  var SIM_SIDES = [
    {
      id: "long", label: "Largo",
      title: "Arma la caja de un LARGO: objetivo por encima de la entrada, riesgo\n" +
        "por debajo. El clic siguiente sobre el gráfico la planta ahí. Es dibujo:\n" +
        "no abre nada."
    },
    {
      id: "short", label: "Corto",
      title: "Arma la caja de un CORTO: objetivo por debajo de la entrada, riesgo\n" +
        "por encima. El clic siguiente sobre el gráfico la planta ahí. Es dibujo:\n" +
        "no abre nada."
    }
  ];

  /* Un pip es la última cifra que se enseña del precio: con cuatro decimales,
   * 0,0001. La distancia se mide con esa unidad y no en porcentaje porque es la
   * que se usa al hablar de un stop. */
  var PIP = Math.pow(10, -DECIMALS);

  /* R:R de salida y los que ofrece el control. Con uno puesto, el objetivo es
   * SIEMPRE el riesgo por ese número: mover el stop arrastra el objetivo con él.
   * Arrastrar el objetivo suelta el candado y deja el ratio «a mano», igual que
   * tocar una casilla suelta el nivel de ruido: el control no puede decir 1:3 si
   * lo que hay dibujado es otra cosa. */
  var SIM_RATIOS = [1, 2, 3, 4];

  var GRAB = 9;          // píxeles de tolerancia para agarrar un borde

  /* Dónde empiezan las tres formas de la caja dentro de `layout.shapes`. Se fija
   * al dibujar y es lo que permite mover sólo esas tres durante el arrastre. */
  var simIndex = null;

  var simDrag = null;

  var SIM_CURSORS = {
    stop: "ns-resize", target: "ns-resize", entry: "ns-resize",
    from: "ew-resize", to: "ew-resize", body: "move"
  };

  function sideLabel(id) {
    var side = SIM_SIDES.filter(function (item) { return item.id === id; })[0];
    return side ? side.label : id;
  }

  function round_(value) { return Number(value.toFixed(DECIMALS)); }

  function pips(distance) { return Math.round(Math.abs(distance) / PIP); }

  function decimal(value) { return value.toFixed(1).replace(".", ","); }

  function simRisk() { return Math.abs(state.sim.entry - state.sim.stop); }

  function simReward() { return Math.abs(state.sim.target - state.sim.entry); }

  function simRatio() {
    var risk = simRisk();
    return risk > 0 ? simReward() / risk : 0;
  }

  function simBox(name, x0, x1, from, to, colour, text, position) {
    return {
      type: "rect", name: name, xref: "x", yref: "y",
      x0: x0, x1: x1, y0: from, y1: to,
      fillcolor: rgba(colour, 0.22), line: { color: rgba(colour, 0.55), width: 1 },
      layer: "above",
      label: { text: text, textposition: position, font: { size: 11, color: colour } }
    };
  }

  /* Las tres formas, siempre en este orden: objetivo, riesgo y la línea de la
   * entrada. El orden es el que usa el arrastre para saber qué está moviendo. */
  function simShapes() {
    var sim = state.sim;
    if (!sim) { return []; }
    var long_ = sim.side === "long";
    var x0 = iso(sim.from), x1 = iso(sim.to);
    // Cada rectángulo dice también lo que se juega con el capital puesto (I.2):
    // el dinero es la razón de dibujar la caja y tenerlo que buscar arriba, en
    // la barra, mientras se arrastra abajo es no verlo.
    var stake = simStake();
    return [
      simBox("sim-objetivo", x0, x1, sim.entry, sim.target, COLORS.bullish,
        "objetivo " + pips(simReward()) + " pips · " + signedMoney(stake.reward),
        long_ ? "top left" : "bottom left"),
      simBox("sim-riesgo", x0, x1, sim.entry, sim.stop, COLORS.bearish,
        "riesgo " + pips(simRisk()) + " pips · " + signedMoney(-stake.risk),
        long_ ? "bottom left" : "top left"),
      {
        type: "line", name: "sim-entrada", xref: "x", yref: "y",
        x0: x0, x1: x1, y0: sim.entry, y1: sim.entry,
        line: { color: COLORS.ink, width: 1.2 }, layer: "above",
        label: {
          text: sideLabel(sim.side).toUpperCase() + " · R:R 1:" + decimal(simRatio()),
          textposition: "end", font: { size: 11, color: COLORS.ink }
        }
      }
    ];
  }

  function armSim(side) {
    state.arming = state.arming === side ? null : side;
    draw();
  }

  /* El botón que está armado, cuando es de los del simulador: `arming` lo
   * comparten la caja simulada, los recuadros a mano (I.3), las líneas a mano
   * (I.4) y el Fibonacci (I.5) —no se puede estar esperando dos clics a la
   * vez— y los controles de cada uno sólo pueden hablar del suyo. */
  function simArming() {
    return armedRect() || armedLine() || armedFib() ? null : state.arming;
  }

  function clearSim() {
    state.sim = null;
    if (simArming()) { state.arming = null; }
    draw();
  }

  /* La caja de salida: el riesgo es un 5 % de lo que se ve de alto y el objetivo
   * el doble, sobre un cuarto de la ventana de ancho. Es un punto de partida
   * para arrastrar, no una propuesta: el motor no ha dicho nada de este precio
   * ni de esta distancia. */
  function plantSim(side, point) {
    var y = viewRange("y"), x = viewRange("x");
    if (!y || !x) { return; }
    var risk = (y[1] - y[0]) * 0.05;
    var dir = side === "long" ? 1 : -1;
    state.sim = {
      side: side,
      entry: round_(point.price),
      stop: round_(point.price - dir * risk),
      target: round_(point.price + (state.ratio || 2) * dir * risk),
      from: point.minute,
      to: point.minute + Math.max(1, Math.round((x[1] - x[0]) * 0.25))
    };
    state.arming = null;
    draw();
  }

  /* El objetivo a la distancia que manda el ratio, contada desde la entrada y
   * en riesgos. Sin ratio puesto no se toca: lo que hay dibujado lo puso una
   * mano. */
  function applyRatio() {
    var sim = state.sim;
    if (!sim || !state.ratio) { return; }
    var dir = sim.side === "long" ? 1 : -1;
    sim.target = round_(sim.entry + dir * simRisk() * state.ratio);
  }

  function setRatio(value) {
    state.ratio = value;
    applyRatio();
    normalizeSim();
    draw();
  }

  /* La caja no puede darse la vuelta: en largo el stop va por debajo de la
   * entrada y el objetivo por encima, y al revés en corto. Un arrastre que cruce
   * la entrada se queda a un pip, que es lo que impide un R:R negativo o
   * infinito. */
  function normalizeSim() {
    var sim = state.sim;
    // Sin caja no hay nada que enderezar: el ratio se puede fijar antes de
    // plantar —y así se planta la siguiente—, y eso no puede romper el dibujo.
    if (!sim) { return; }
    var dir = sim.side === "long" ? 1 : -1;
    if (dir * (sim.entry - sim.stop) < PIP) {
      sim.stop = round_(sim.entry - dir * PIP);
    }
    if (dir * (sim.target - sim.entry) < PIP) {
      sim.target = round_(sim.entry + dir * PIP);
    }
    if (sim.to <= sim.from) {
      if (simDrag && simDrag.part === "from") { sim.from = sim.to - span(state.chart); }
      else { sim.to = sim.from + span(state.chart); }
    }
  }

  // --- El gráfico en píxeles -------------------------------------------------

  function chartBox() {
    var chart = document.getElementById("chart");
    if (!chart || typeof chart.getBoundingClientRect !== "function") { return null; }
    return chart.getBoundingClientRect();
  }

  /* El rectángulo de dibujo dentro del div, en píxeles.
   *
   * Tiene que ser el que Plotly ha calculado, no el que se le pidió: `MARGIN` es
   * una PETICIÓN y Plotly la ensancha por su cuenta —la leyenda horizontal de
   * arriba empuja el margen superior, y las etiquetas largas del eje de precios
   * el de la izquierda—. Con el margen pedido, precio y píxel se desplazan unos
   * cuantos píxeles y los tiradores de la caja dejan de estar donde se ve la
   * línea: agarrar el stop se vuelve cuestión de suerte. `_size` es lo que Plotly
   * resolvió; `MARGIN` sólo se usa mientras no haya figura de la que leerlo. */
  function plotBox(box) {
    var chart = document.getElementById("chart");
    var size = chart && chart._fullLayout && chart._fullLayout._size;
    var axis = chart && chart._fullLayout && chart._fullLayout.yaxis;
    // El precio ocupa todo el alto de la figura; se lee igualmente el `domain`
    // resuelto por Plotly para que precio y píxel no se separen nunca.
    var domain = (axis && axis.domain) || [0, 1];
    var share = domain[1] - domain[0];
    if (size && size.w > 0 && size.h > 0) {
      return {
        left: box.left + size.l,
        top: box.top + size.t + size.h * (1 - domain[1]),
        width: size.w,
        height: size.h * share
      };
    }
    var height = box.height - MARGIN.t - MARGIN.b;
    return {
      left: box.left + MARGIN.l,
      top: box.top + MARGIN.t + height * (1 - domain[1]),
      width: box.width - MARGIN.l - MARGIN.r,
      height: height * share
    };
  }

  /* Lo que se está viendo, en datos. Manda el eje ya resuelto por Plotly; si
   * todavía no hay figura de la que leerlo, se cae al tramo recortado, que es lo
   * que ese eje va a autoescalar. Los dos ejes son lineales —el de fechas
   * también, en minutos—, así que pasar de píxel a dato es una regla de tres. */
  function viewRange(key) {
    var pair = currentRange(key);
    if (pair) { return pair; }
    var cut = slice(bounds());
    var b = bars();
    if (cut.end <= cut.start) { return null; }
    if (key === "x") { return [b.t[cut.start], b.t[cut.end - 1] + span(state.chart)]; }
    var lo = Infinity, hi = -Infinity;
    for (var i = cut.start; i < cut.end; i += 1) {
      if (b.l[i] < lo) { lo = b.l[i]; }
      if (b.h[i] > hi) { hi = b.h[i]; }
    }
    return hi > lo ? [lo, hi] : null;
  }

  /* `loose` deja pasar los puntos de fuera del área de dibujo: al arrastrar, el
   * ratón se sale del gráfico y el gesto no puede morirse ahí. */
  function dataAt(box, clientX, clientY, loose) {
    var x = viewRange("x"), y = viewRange("y"), plot = plotBox(box);
    if (!x || !y || plot.width <= 0 || plot.height <= 0) { return null; }
    var fx = (clientX - plot.left) / plot.width;
    var fy = (clientY - plot.top) / plot.height;
    if (isNaN(fx) || isNaN(fy)) { return null; }
    if (!loose && (fx < 0 || fx > 1 || fy < 0 || fy > 1)) { return null; }
    return {
      minute: Math.round(x[0] + fx * (x[1] - x[0])),
      price: y[1] - fy * (y[1] - y[0])
    };
  }

  function pixelAt(box, minute, value) {
    var x = viewRange("x"), y = viewRange("y"), plot = plotBox(box);
    if (!x || !y || plot.width <= 0 || plot.height <= 0) { return null; }
    return {
      x: plot.left + (minute - x[0]) / (x[1] - x[0]) * plot.width,
      y: plot.top + (y[1] - value) / (y[1] - y[0]) * plot.height
    };
  }

  // --- Plantar y arrastrar ---------------------------------------------------

  function clickChart(event) {
    if (!state.arming) { return; }
    var box = chartBox();
    if (!box || axisAt(box, event.clientX, event.clientY)) { return; }
    var point = dataAt(box, event.clientX, event.clientY);
    if (!point) { return; }
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
    var armed = armedRect();
    if (armed) { plantRect(armed, point); return; }
    var traced = armedLine();
    if (traced) { plantLine(traced, point); return; }
    if (armedFib()) { plantFib(point); return; }
    plantSim(state.arming, point);
  }

  /* Qué parte de la caja hay bajo el ratón. Las tres líneas ganan al interior:
   * con la caja estrecha, todo el rectángulo cae dentro de la tolerancia y lo
   * que se quiere agarrar entonces es el nivel más cercano. */
  function simHandleAt(box, cx, cy) {
    var sim = state.sim;
    if (!sim) { return null; }
    var left = pixelAt(box, sim.from, sim.entry);
    var right = pixelAt(box, sim.to, sim.entry);
    var stop = pixelAt(box, sim.from, sim.stop);
    var target = pixelAt(box, sim.from, sim.target);
    if (!left || !right || !stop || !target) { return null; }
    if (cx < left.x - GRAB || cx > right.x + GRAB) { return null; }
    var top = Math.min(stop.y, target.y), bottom = Math.max(stop.y, target.y);
    if (cy < top - GRAB || cy > bottom + GRAB) { return null; }
    var near = [["stop", stop.y], ["target", target.y], ["entry", left.y]]
      .filter(function (level) { return Math.abs(cy - level[1]) <= GRAB; })
      .sort(function (a, b) { return Math.abs(cy - a[1]) - Math.abs(cy - b[1]); })[0];
    if (near) { return near[0]; }
    if (Math.abs(cx - left.x) <= GRAB) { return "from"; }
    if (Math.abs(cx - right.x) <= GRAB) { return "to"; }
    return "body";
  }

  function startSimDrag(event) {
    // El gesto de los ejes se registra antes y corta la propagación cuando es
    // suyo, pero eso no impide que este oyente del mismo div se ejecute.
    if (axisDrag || state.arming || !state.sim) { return; }
    var box = chartBox();
    if (!box) { return; }
    var part = simHandleAt(box, event.clientX, event.clientY);
    if (!part) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    simDrag = {
      part: part, origin: origin,
      base: {
        entry: state.sim.entry, stop: state.sim.stop, target: state.sim.target,
        from: state.sim.from, to: state.sim.to
      }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveSimDrag(event) {
    if (!simDrag) { hoverSim(event); return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applySimDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applySimDrag(point) {
    var sim = state.sim, base = simDrag.base, part = simDrag.part;
    var dy = point.price - simDrag.origin.price;
    var dx = point.minute - simDrag.origin.minute;
    if (part === "stop") {
      sim.stop = round_(point.price);
      // Con un R:R puesto, mover el stop es mover el objetivo: el ratio es lo
      // que se ha fijado y la distancia al objetivo, su consecuencia.
      applyRatio();
    } else if (part === "target") {
      sim.target = round_(point.price);
      // Lo que se arrastra manda: a partir de aquí el ratio es el que se vea.
      state.ratio = null;
    } else if (part === "from") {
      sim.from = point.minute;
    } else if (part === "to") {
      sim.to = point.minute;
    } else {
      // La línea de la entrada y el interior mueven la caja ENTERA: las
      // distancias al stop y al objetivo son lo que se acaba de decidir y
      // recolocar la entrada no puede cambiarlas por su cuenta.
      sim.entry = round_(base.entry + dy);
      sim.stop = round_(base.stop + dy);
      sim.target = round_(base.target + dy);
      if (part === "body") { sim.from = base.from + dx; sim.to = base.to + dx; }
    }
    normalizeSim();
    redrawSim();
  }

  /* Durante el arrastre se mueven sólo las tres formas, no la figura entera: con
   * ocho años de velas embebidas, rehacerla en cada píxel del gesto se nota. Al
   * soltar se redibuja de verdad, que es cuando se ponen al día las notas. */
  function redrawSim() {
    var chart = document.getElementById("chart");
    if (simIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    simShapes().forEach(function (shape, position) {
      var key = "shapes[" + (simIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
      update[key + ".label.text"] = shape.label.text;
    });
    Plotly.relayout(chart, update);
  }

  function endSimDrag() {
    if (!simDrag) { return; }
    simDrag = null;
    draw();
  }

  /* Sin cursor no se ve que la caja se puede agarrar: el borde de un rectángulo
   * translúcido no dice por sí solo que sea un tirador. */
  function hoverSim(event) {
    var chart = document.getElementById("chart");
    if (!chart || !chart.style || state.arming) { return; }
    if (!state.sim && !state.rects.length && !state.lines.length && !state.fibs.length) {
      return;
    }
    var box = chartBox();
    var part = box && state.sim ? simHandleAt(box, event.clientX, event.clientY) : null;
    if (part) { chart.style.cursor = SIM_CURSORS[part] || ""; return; }
    var hit = box && state.rects.length
      ? rectHandleAt(box, event.clientX, event.clientY)
      : null;
    if (hit) { chart.style.cursor = RECT_CURSORS[hit.part] || ""; return; }
    var stroke = box && state.lines.length
      ? lineHandleAt(box, event.clientX, event.clientY)
      : null;
    if (stroke) { chart.style.cursor = LINE_CURSORS[stroke.part] || ""; return; }
    var rule = box && state.fibs.length
      ? fibHandleAt(box, event.clientX, event.clientY)
      : null;
    chart.style.cursor = rule ? (FIB_CURSORS[rule.part] || "") : "";
  }

  /* Qué caja hay puesta. Un rectángulo de colores sobre el precio se lee como
   * una operación: el estado tiene que decir que no lo es. */
  function simCaption() {
    if (simArming()) {
      return "SIMULADOR ARMADO (" + sideLabel(simArming()).toLowerCase() +
        "): pulsa sobre el gráfico para plantar la entrada, o Escape para dejarlo";
    }
    if (!state.sim) { return null; }
    var sim = state.sim;
    return "simulación " + sideLabel(sim.side).toUpperCase() + " · entrada " +
      price(sim.entry) + " · stop " + price(sim.stop) + " (" + pips(simRisk()) +
      " pips) · objetivo " + price(sim.target) + " (" + pips(simReward()) +
      " pips) · R:R 1:" + decimal(simRatio()) +
      (state.ratio
        ? " (fijo: mover el stop mueve el objetivo)"
        : " (a mano: lo dejó ahí un arrastre del objetivo)") +
      " · un pip es la última cifra del precio · ES DIBUJO A MANO: no hay orden, " +
      "ni ejecución, ni resultado; nadie mira si el precio llegó y el motor no la ve";
  }

  function buildSimButtons() {
    var container = document.getElementById("sim-buttons");
    if (!container) { return; }
    SIM_SIDES.forEach(function (side) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = side.label;
      button.dataset.side = side.id;
      button.title = side.title;
      button.addEventListener("click", function () { armSim(side.id); });
      container.appendChild(button);
    });
  }

  function buildRatioButtons() {
    var container = document.getElementById("sim-ratio");
    if (!container) { return; }
    SIM_RATIOS.forEach(function (value) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = "1:" + value;
      button.dataset.ratio = String(value);
      button.title = "Fija el objetivo a " + value + " veces el riesgo. Con esto puesto, " +
        "mover el stop mueve el objetivo. Arrastrar el objetivo suelta el candado.";
      button.addEventListener("click", function () { setRatio(value); });
      container.appendChild(button);
    });
  }

  function bindSim() {
    var chart = document.getElementById("chart");
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("click", clickChart, true);
    chart.addEventListener("mousedown", startSimDrag, true);
    document.addEventListener("mousemove", moveSimDrag);
    document.addEventListener("mouseup", endSimDrag);
  }

  /* --- Los recuadros a mano (I.3) -------------------------------------------
   *
   * Tres rectángulos —A, B y C— que planta el PROPIETARIO encima del gráfico
   * para señalar lo que ve y todavía no tiene regla. No son zonas del motor —el
   * motor no calcula ninguna—: no las ve nadie más que quien las dibuja.
   * Existen para poder mandar una captura señalando lo que no hay regla que
   * detecte. Los nombres son letras a propósito: lo que significan lo irá
   * dictando el propietario sobre el dibujo.
   *
   * Cada nombre lleva SU COLOR y todos van punteados: ninguna capa calculada
   * usa esos tres tonos, así que lo que se vea con ellos lo ha puesto una mano,
   * y cuál de los tres es se lee sin abrir la leyenda.
   *
   * Se marcan VARIOS de cada nombre: en un mismo gráfico hay el de H4 y el de
   * H1, y enseñarlos de uno en uno no dice lo que hay que decir. El gesto es el
   * mismo que el de la caja simulada —el botón ARMA, el clic PLANTA y después
   * se arrastra por los bordes o por dentro—, y por eso comparten `arming`: no
   * se puede estar esperando dos clics a la vez.
   */
  var RECT_KINDS = ["A", "B", "C"].map(function (name) {
    return {
      id: name, label: "Recuadro " + name,
      title: "Arma el recuadro " + name + ": el clic siguiente sobre el gráfico lo\n" +
        "planta ahí. Escape desarma. Lo dibujas tú: el motor no lo ve."
    };
  });

  /* `arming` es UNO y lo comparten los tres botones y la caja simulada, así que
   * el nombre del recuadro viaja dentro del propio valor. */
  var RECT_ARM = "rect:";

  function armedRect() {
    var arming = state.arming;
    if (!arming || String(arming).indexOf(RECT_ARM) !== 0) { return null; }
    return String(arming).slice(RECT_ARM.length);
  }

  function rectColor(kind) { return COLORS.rects[kind]; }

  var RECT_CURSORS = {
    high: "ns-resize", low: "ns-resize",
    from: "ew-resize", to: "ew-resize", body: "move"
  };

  /* Dónde empiezan los recuadros dentro de `layout.shapes`, igual que la caja
   * simulada: es lo que permite mover sólo el que se arrastra en vez de rehacer
   * la figura entera en cada píxel del gesto. */
  var rectIndex = null;

  var rectDrag = null;

  /* Se numeran POR NOMBRE: el segundo A es «A 2» aunque entre los dos se haya
   * plantado un B, porque lo que se cuenta al mirarlos es cuántos hay de cada
   * cosa. */
  function rectShapes() {
    var seen = {};
    return state.rects.map(function (rect, position) {
      var color = rectColor(rect.kind);
      seen[rect.kind] = (seen[rect.kind] || 0) + 1;
      return {
        type: "rect", name: "rect-" + position, xref: "x", yref: "y",
        x0: iso(rect.from), x1: iso(rect.to), y0: rect.low, y1: rect.high,
        fillcolor: rgba(color, 0.14),
        line: { color: color, width: 1.4, dash: "dot" },
        layer: "above",
        label: {
          text: rect.kind + " " + seen[rect.kind] + " (a mano)",
          textposition: "top left",
          font: { size: 11, color: color }
        }
      };
    });
  }

  function armRect(kind) {
    state.arming = armedRect() === kind ? null : RECT_ARM + kind;
    draw();
  }

  /* El recuadro de salida: alto un 4 % de lo que se ve y ancho un octavo de la
   * ventana, centrado en el precio del clic. Es un punto de partida para
   * arrastrar, no una propuesta: el motor no ha dicho nada de este precio. */
  function plantRect(kind, point) {
    var y = viewRange("y"), x = viewRange("x");
    if (!y || !x) { return; }
    var half = (y[1] - y[0]) * 0.02;
    var width = Math.max(span(state.chart), Math.round((x[1] - x[0]) * 0.125));
    state.rects.push({
      kind: kind,
      from: point.minute,
      to: point.minute + width,
      low: round_(point.price - half),
      high: round_(point.price + half)
    });
    state.arming = null;
    draw();
  }

  function undoRect() {
    if (!state.rects.length) { return; }
    state.rects.pop();
    draw();
  }

  function clearRects() {
    state.rects = [];
    if (armedRect()) { state.arming = null; }
    draw();
  }

  /* Un recuadro no puede darse la vuelta: el arrastre que cruza el borde
   * contrario se queda a un pip —o a una vela, en horizontal—, que es lo que
   * impide un rectángulo de altura cero o del revés. */
  function normalizeRect(rect) {
    if (rect.high - rect.low < PIP) {
      if (rectDrag && rectDrag.part === "low") { rect.low = round_(rect.high - PIP); }
      else { rect.high = round_(rect.low + PIP); }
    }
    if (rect.to <= rect.from) {
      if (rectDrag && rectDrag.part === "from") { rect.from = rect.to - span(state.chart); }
      else { rect.to = rect.from + span(state.chart); }
    }
  }

  /* Qué parte de un recuadro hay bajo el ratón. Los bordes ganan al interior:
   * con el recuadro estrecho, todo él cae dentro de la tolerancia y lo que se
   * quiere agarrar entonces es el borde más cercano. */
  function rectPartAt(box, rect, cx, cy) {
    var corner = pixelAt(box, rect.from, rect.high);
    var opposite = pixelAt(box, rect.to, rect.low);
    if (!corner || !opposite) { return null; }
    if (cx < corner.x - GRAB || cx > opposite.x + GRAB) { return null; }
    if (cy < corner.y - GRAB || cy > opposite.y + GRAB) { return null; }
    if (Math.abs(cy - corner.y) <= GRAB) { return "high"; }
    if (Math.abs(cy - opposite.y) <= GRAB) { return "low"; }
    if (Math.abs(cx - corner.x) <= GRAB) { return "from"; }
    if (Math.abs(cx - opposite.x) <= GRAB) { return "to"; }
    return "body";
  }

  /* El último plantado es el que está encima, así que se busca del final al
   * principio: con dos recuadros solapados se agarra el que se ve. */
  function rectHandleAt(box, cx, cy) {
    for (var index = state.rects.length - 1; index >= 0; index -= 1) {
      var part = rectPartAt(box, state.rects[index], cx, cy);
      if (part) { return { index: index, part: part }; }
    }
    return null;
  }

  function startRectDrag(event) {
    // La caja simulada se registra antes y tiene preferencia: si ella ha
    // agarrado el gesto, aquí no hay nada que hacer.
    if (simDrag || axisDrag || state.arming || !state.rects.length) { return; }
    var box = chartBox();
    if (!box) { return; }
    var hit = rectHandleAt(box, event.clientX, event.clientY);
    if (!hit) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    var rect = state.rects[hit.index];
    rectDrag = {
      index: hit.index, part: hit.part, origin: origin,
      base: { from: rect.from, to: rect.to, low: rect.low, high: rect.high }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveRectDrag(event) {
    if (simDrag || !rectDrag) { return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applyRectDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyRectDrag(point) {
    var rect = state.rects[rectDrag.index], base = rectDrag.base, part = rectDrag.part;
    if (part === "high") {
      rect.high = round_(point.price);
    } else if (part === "low") {
      rect.low = round_(point.price);
    } else if (part === "from") {
      rect.from = point.minute;
    } else if (part === "to") {
      rect.to = point.minute;
    } else {
      // Por dentro se mueve el recuadro ENTERO: su tamaño es lo que se acaba de
      // decidir y recolocarlo no puede cambiarlo por su cuenta.
      var dy = point.price - rectDrag.origin.price;
      var dx = point.minute - rectDrag.origin.minute;
      rect.low = round_(base.low + dy);
      rect.high = round_(base.high + dy);
      rect.from = base.from + dx;
      rect.to = base.to + dx;
    }
    normalizeRect(rect);
    redrawRects();
  }

  /* Igual que la caja simulada: durante el arrastre se mueven sólo las formas
   * de los recuadros, no la figura entera. */
  function redrawRects() {
    var chart = document.getElementById("chart");
    if (rectIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    rectShapes().forEach(function (shape, position) {
      var key = "shapes[" + (rectIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
    });
    Plotly.relayout(chart, update);
  }

  function endRectDrag() {
    if (!rectDrag) { return; }
    rectDrag = null;
    draw();
  }

  /* Cuántos hay de cada nombre, en el orden de los botones y sin nombrar los
   * que no se han puesto. */
  function rectCounts() {
    var counts = {};
    state.rects.forEach(function (rect) {
      counts[rect.kind] = (counts[rect.kind] || 0) + 1;
    });
    return RECT_KINDS.filter(function (kind) {
      return counts[kind.id];
    }).map(function (kind) {
      return counts[kind.id] + " de " + kind.id;
    }).join(" · ");
  }

  /* Qué recuadros hay puestos. Un rectángulo encima del precio se lee como algo
   * que el motor ha encontrado: el estado tiene que decir que lo ha puesto una
   * mano y que detrás no hay ninguna regla. */
  function rectCaption() {
    var armed = armedRect();
    if (armed) {
      return "RECUADRO DE " + armed + " ARMADO: pulsa sobre el gráfico para " +
        "plantarlo, o Escape para dejarlo";
    }
    if (!state.rects.length) { return null; }
    return "recuadros marcados a mano: " + state.rects.length +
      " (" + rectCounts() + ")" +
      " · los dibuja el propietario para señalar lo que ve: NO los ha " +
      "detectado el motor, no hay regla detrás y no salen de la pantalla";
  }

  function buildRectButtons() {
    var container = document.getElementById("rect-buttons");
    if (!container) { return; }
    RECT_KINDS.forEach(function (kind) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = kind.label;
      button.dataset.kind = kind.id;
      button.title = kind.title;
      button.addEventListener("click", function () { armRect(kind.id); });
      container.appendChild(button);
    });
  }

  function bindRects() {
    var chart = document.getElementById("chart");
    document.getElementById("rect-undo").addEventListener("click", undoRect);
    document.getElementById("rect-clear").addEventListener("click", clearRects);
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startRectDrag, true);
    document.addEventListener("mousemove", moveRectDrag);
    document.addEventListener("mouseup", endRectDrag);
  }

  /* --- Las líneas a mano (I.4) ----------------------------------------------
   *
   * Tres LÍNEAS que traza el PROPIETARIO encima del gráfico para señalar lo que
   * todavía no tiene regla: por dónde pasa un nivel, qué dos puntos une, de
   * dónde a dónde mira. No son capa del motor —como los recuadros de I.3—: no
   * las ha calculado nadie, el motor no se entera de que existen y no salen de
   * la pantalla. Existen para poder mandar una captura señalando lo que se
   * quiere explicar.
   *
   * El NOMBRE de cada una es la TEMPORALIDAD que se está marcando con ella —el
   * nivel que se ve en H4, el de H1, el de M15—, que es lo que se quiere decir
   * al señalarla: el color es sólo para distinguirlas. Los tonos son los tres
   * de la mano, los mismos que los recuadros, para que una línea no se confunda
   * con nada que haya calculado el motor. Lo que las separa de los recuadros es
   * el TRAZO: continuo la línea, punteado el recuadro.
   *
   * Se plantan HORIZONTALES —marcar un nivel es lo que más se hace— y se
   * inclinan arrastrando un extremo: los dos se mueven en precio y en tiempo, y
   * por dentro se desplaza entera. El gesto es el de los recuadros y comparten
   * `arming` con ellos y con la caja simulada.
   */
  var LINE_KINDS = [
    { id: "H4", label: "H4", name: "línea de H4" },
    { id: "H1", label: "H1", name: "línea de H1" },
    { id: "M15", label: "M15", name: "línea de M15" }
  ];

  var LINE_ARM = "line:";

  function armedLine() {
    var arming = state.arming;
    if (!arming || String(arming).indexOf(LINE_ARM) !== 0) { return null; }
    return String(arming).slice(LINE_ARM.length);
  }

  function lineColor(kind) { return COLORS.lines[kind]; }

  function lineName(kind) {
    var found = LINE_KINDS.filter(function (item) { return item.id === kind; })[0];
    return found ? found.name : kind;
  }

  var LINE_CURSORS = { left: "ew-resize", right: "ew-resize", body: "move" };

  /* Dónde empiezan las líneas dentro de `layout.shapes`, igual que los
   * recuadros: es lo que permite mover sólo la que se arrastra. */
  var lineIndex = null;

  var lineDrag = null;

  /* Se numeran POR TEMPORALIDAD: la segunda de H4 es «H4 2» aunque entre las
   * dos se haya trazado una de H1. */
  function lineShapes() {
    var seen = {};
    return state.lines.map(function (line, position) {
      var color = lineColor(line.kind);
      seen[line.kind] = (seen[line.kind] || 0) + 1;
      return {
        type: "line", name: "line-" + position, xref: "x", yref: "y",
        x0: iso(line.from), x1: iso(line.to), y0: line.left, y1: line.right,
        line: { color: color, width: 2.2 },
        layer: "above",
        label: {
          text: lineName(line.kind) + " " + seen[line.kind] + " (a mano)",
          textposition: "top left",
          font: { size: 11, color: color }
        }
      };
    });
  }

  function armLine(kind) {
    state.arming = armedLine() === kind ? null : LINE_ARM + kind;
    draw();
  }

  /* La línea de salida: HORIZONTAL al precio del clic y de media ventana de
   * ancho, centrada en él. Es un punto de partida para arrastrar, no una
   * propuesta: el motor no ha dicho nada de ese precio. */
  function plantLine(kind, point) {
    var x = viewRange("x");
    if (!x) { return; }
    var half = Math.max(span(state.chart), Math.round((x[1] - x[0]) * 0.25));
    var level = round_(point.price);
    state.lines.push({
      kind: kind,
      from: point.minute - half,
      to: point.minute + half,
      left: level,
      right: level
    });
    state.arming = null;
    draw();
  }

  function undoLine() {
    if (!state.lines.length) { return; }
    state.lines.pop();
    draw();
  }

  function clearLines() {
    state.lines = [];
    if (armedLine()) { state.arming = null; }
    draw();
  }

  /* Una línea no puede darse la vuelta: el arrastre que cruza el otro extremo se
   * queda a una vela, que es lo que impide un segmento de ancho cero o del
   * revés. En vertical no hay nada que impedir: horizontal es justo como nace. */
  function normalizeLine(line) {
    if (line.to <= line.from) {
      if (lineDrag && lineDrag.part === "left") {
        line.from = line.to - span(state.chart);
      } else {
        line.to = line.from + span(state.chart);
      }
    }
  }

  /* Distancia del ratón al trazo, en píxeles: su proyección sobre el segmento,
   * recortada a los extremos. Sin esto una línea inclinada sólo se dejaría
   * agarrar en el punto donde coincide con el ratón en horizontal. */
  function strokeDistance(cx, cy, a, b) {
    var dx = b.x - a.x, dy = b.y - a.y;
    var length = dx * dx + dy * dy;
    var t = length ? ((cx - a.x) * dx + (cy - a.y) * dy) / length : 0;
    t = Math.max(0, Math.min(1, t));
    var x = a.x + t * dx, y = a.y + t * dy;
    return Math.sqrt((cx - x) * (cx - x) + (cy - y) * (cy - y));
  }

  function nearPixel(cx, cy, point) {
    return Math.abs(cx - point.x) <= GRAB && Math.abs(cy - point.y) <= GRAB;
  }

  /* Qué parte de una línea hay bajo el ratón. Los extremos ganan al trazo: son
   * lo que la inclina, y con la línea corta todo ella cae dentro de la
   * tolerancia de los dos. */
  function linePartAt(box, line, cx, cy) {
    var a = pixelAt(box, line.from, line.left);
    var b = pixelAt(box, line.to, line.right);
    if (!a || !b) { return null; }
    if (nearPixel(cx, cy, a)) { return "left"; }
    if (nearPixel(cx, cy, b)) { return "right"; }
    return strokeDistance(cx, cy, a, b) <= GRAB ? "body" : null;
  }

  /* La última trazada está encima, así que se busca del final al principio. */
  function lineHandleAt(box, cx, cy) {
    for (var index = state.lines.length - 1; index >= 0; index -= 1) {
      var part = linePartAt(box, state.lines[index], cx, cy);
      if (part) { return { index: index, part: part }; }
    }
    return null;
  }

  function startLineDrag(event) {
    // La caja simulada y los recuadros se registran antes y tienen preferencia:
    // si uno de ellos ha agarrado el gesto, aquí no hay nada que hacer.
    if (simDrag || rectDrag || axisDrag || state.arming || !state.lines.length) { return; }
    var box = chartBox();
    if (!box) { return; }
    var hit = lineHandleAt(box, event.clientX, event.clientY);
    if (!hit) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    var line = state.lines[hit.index];
    lineDrag = {
      index: hit.index, part: hit.part, origin: origin,
      base: { from: line.from, to: line.to, left: line.left, right: line.right }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveLineDrag(event) {
    if (simDrag || rectDrag || !lineDrag) { return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applyLineDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyLineDrag(point) {
    var line = state.lines[lineDrag.index], base = lineDrag.base, part = lineDrag.part;
    if (part === "left") {
      line.from = point.minute;
      line.left = round_(point.price);
    } else if (part === "right") {
      line.to = point.minute;
      line.right = round_(point.price);
    } else {
      // Por dentro se mueve la línea ENTERA: su inclinación es lo que se acaba
      // de decidir y recolocarla no puede cambiarla por su cuenta.
      var dy = point.price - lineDrag.origin.price;
      var dx = point.minute - lineDrag.origin.minute;
      line.from = base.from + dx;
      line.to = base.to + dx;
      line.left = round_(base.left + dy);
      line.right = round_(base.right + dy);
    }
    normalizeLine(line);
    redrawLines();
  }

  /* Igual que los recuadros: durante el arrastre se mueven sólo las formas de
   * las líneas, no la figura entera. */
  function redrawLines() {
    var chart = document.getElementById("chart");
    if (lineIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    lineShapes().forEach(function (shape, position) {
      var key = "shapes[" + (lineIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
    });
    Plotly.relayout(chart, update);
  }

  function endLineDrag() {
    if (!lineDrag) { return; }
    lineDrag = null;
    draw();
  }

  /* Cuántas hay de cada temporalidad, en el orden de los botones y sin nombrar
   * las que no se han trazado. */
  function lineCounts() {
    var counts = {};
    state.lines.forEach(function (line) {
      counts[line.kind] = (counts[line.kind] || 0) + 1;
    });
    return LINE_KINDS.filter(function (kind) {
      return counts[kind.id];
    }).map(function (kind) {
      return counts[kind.id] + " " + kind.label;
    }).join(" · ");
  }

  /* Qué líneas hay trazadas. Un trazo sobre el precio se lee como un nivel que
   * ha encontrado alguien: el estado tiene que decir que lo ha puesto una mano y
   * que detrás no hay ninguna regla. */
  function lineCaption() {
    var armed = armedLine();
    if (armed) {
      return lineName(armed).toUpperCase() + " ARMADA: pulsa sobre el gráfico " +
        "para plantarla, o Escape para dejarla";
    }
    if (!state.lines.length) { return null; }
    return "líneas marcadas a mano: " + state.lines.length +
      " (" + lineCounts() + ")" +
      " · las traza el propietario para señalar lo que quiere explicar: NO las " +
      "ha dibujado el motor, no hay ninguna regla detrás y no salen de la pantalla";
  }

  function buildLineButtons() {
    var container = document.getElementById("line-buttons");
    if (!container) { return; }
    LINE_KINDS.forEach(function (kind) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = kind.label;
      button.dataset.kind = kind.id;
      button.title = "Arma la " + kind.name + ": el clic siguiente sobre el\n" +
        "gráfico la planta ahí, horizontal al precio pulsado. Escape desarma.\n" +
        "El nombre dice la temporalidad que estás marcando; la dibujas tú y el\n" +
        "motor no la ve.";
      button.addEventListener("click", function () { armLine(kind.id); });
      container.appendChild(button);
    });
  }

  function bindLines() {
    var chart = document.getElementById("chart");
    document.getElementById("line-undo").addEventListener("click", undoLine);
    document.getElementById("line-clear").addEventListener("click", clearLines);
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startLineDrag, true);
    document.addEventListener("mousemove", moveLineDrag);
    document.addEventListener("mouseup", endLineDrag);
  }

  /* --- El Fibonacci a mano (I.5) --------------------------------------------
   *
   * Una REGLA para medir retrocesos que planta el PROPIETARIO. Se traza con DOS
   * CLICS: el primero clava el 0 y el segundo el 100, así que la dirección la
   * decide él —el 0 arriba en un retroceso bajista, abajo en uno alcista— y no
   * hay que darle la vuelta a nada después.
   *
   * Los porcentajes que se dibujan vienen en el payload: hoy son el 0, el 50,
   * el 70,5, el 72, el 79 y el 100, y se cuentan SIEMPRE desde el 0 hacia el
   * 100, que es lo que hace que el 79 esté cerca del 100 y no al revés. El
   * tramo se estira un poco a la derecha del ancla del 100: un retroceso se
   * mira hacia adelante.
   *
   * NO ES CAPA DEL MOTOR. Nadie ha medido ese retroceso, no hay ninguna regla
   * detrás, no sale de la pantalla y el motor no se entera de que existe. Va en
   * GRIS y no en un color porque no marca nada: es una regla, y lo que la
   * identifica son sus porcentajes escritos al lado. La única excepción es la
   * GOLD ZONE —el 70,5, que también viene en el payload—: va en AMARILLO y con
   * su nombre, porque es el nivel que el propietario quiere ver de un vistazo.
   *
   * Después se arrastra, como los recuadros y las líneas: cada ancla se mueve
   * sola y por dentro se desplaza el conjunto entero. Escape suelta el botón, y
   * también el 0 ya clavado cuando falta el segundo clic.
   */
  var FIB_ARM = "fib";

  function armedFib() { return state.arming === FIB_ARM; }

  function fibLevels() { return DATA.meta.fibLevels || []; }

  /* El nivel de la gold zone, o `null` si el payload no lo declara. */
  function fibGold() {
    var level = DATA.meta.fibGold;
    return level === undefined ? null : level;
  }

  function isGold(level) { return fibGold() !== null && level === fibGold(); }

  function fibColor() { return COLORS.fib; }

  /* La gold zone lleva su color; sin él en la paleta, el del resto. */
  function fibLevelColor(level) {
    return isGold(level) ? (COLORS.fibGold || fibColor()) : fibColor();
  }

  /* El porcentaje escrito como se lee: «70,5 %», no «70.5 %». */
  function fibLabel(level) {
    return String(level).replace(".", ",") + " %";
  }

  /* Cuánto se estira el dibujo más allá del ancla del 100, en partes del tramo
   * medido. Es dibujo: los niveles siguen siendo los mismos, sólo se ven venir. */
  var FIB_AHEAD = 0.4;

  var FIB_CURSORS = { zero: "ns-resize", hundred: "ns-resize", body: "move" };

  /* Dónde empiezan los Fibonacci dentro de `layout.shapes`, igual que las
   * líneas: es lo que permite mover sólo el que se arrastra. */
  var fibIndex = null;

  var fibDrag = null;

  /* El precio de un porcentaje, contado desde el 0 hacia el 100. */
  function fibPrice(fib, level) {
    return fib.zero + (fib.hundred - fib.zero) * level / 100;
  }

  /* De dónde a dónde se pinta: del ancla más a la izquierda a la más a la
   * derecha, más el tramo de adelanto. */
  function fibSpan(fib) {
    var from = Math.min(fib.from, fib.to);
    var to = Math.max(fib.from, fib.to);
    return { from: from, to: to + Math.round((to - from) * FIB_AHEAD) };
  }

  function fibShapes() {
    var shapes = [];
    state.fibs.forEach(function (fib, position) {
      var edges = fibSpan(fib);
      fibLevels().forEach(function (level) {
        var value = round_(fibPrice(fib, level));
        var edge = level === 0 || level === 100;
        var gold = isGold(level);
        shapes.push({
          type: "line", name: "fib-" + position + "-" + level, xref: "x", yref: "y",
          x0: iso(edges.from), x1: iso(edges.to), y0: value, y1: value,
          line: {
            color: fibLevelColor(level),
            width: edge || gold ? 2 : 1.2,
            dash: edge ? "solid" : "dash"
          },
          layer: "above",
          label: {
            text: (level === 0 ? "Fib " + (position + 1) + " (a mano) · " : "") +
              (gold ? "GOLD ZONE " : "") + fibLabel(level) + " · " + price(value),
            textposition: "top left",
            font: { size: 11, color: fibLevelColor(level) }
          }
        });
      });
    });
    // El 0 ya clavado mientras se espera el clic del 100: sin esto, el primer
    // clic no deja rastro y parece que no ha pasado nada.
    var draft = state.fibDraft;
    if (draft) {
      var half = Math.max(span(state.chart), Math.round(draft.width));
      shapes.push({
        type: "line", name: "fib-draft", xref: "x", yref: "y",
        x0: iso(draft.minute - half), x1: iso(draft.minute + half),
        y0: draft.price, y1: draft.price,
        line: { color: fibColor(), width: 2, dash: "dot" },
        layer: "above",
        label: {
          text: "0 % · " + price(draft.price) + " · pulsa dónde va el 100",
          textposition: "top left",
          font: { size: 11, color: fibColor() }
        }
      });
    }
    return shapes;
  }

  function armFib() {
    state.arming = armedFib() ? null : FIB_ARM;
    state.fibDraft = null;
    draw();
  }

  /* Los dos clics. El primero deja el 0 clavado y el botón SIGUE armado: hasta
   * que no se dice dónde va el 100 no hay retroceso que medir. */
  function plantFib(point) {
    var x = viewRange("x");
    if (!x) { return; }
    if (!state.fibDraft) {
      state.fibDraft = {
        minute: point.minute,
        price: round_(point.price),
        width: Math.round((x[1] - x[0]) * 0.12)
      };
      draw();
      return;
    }
    var draft = state.fibDraft;
    state.fibs.push({
      from: draft.minute,
      to: point.minute,
      zero: draft.price,
      hundred: round_(point.price)
    });
    state.fibDraft = null;
    state.arming = null;
    normalizeFib(state.fibs[state.fibs.length - 1]);
    draw();
  }

  function undoFib() {
    if (state.fibDraft) { state.fibDraft = null; draw(); return; }
    if (!state.fibs.length) { return; }
    state.fibs.pop();
    draw();
  }

  function clearFibs() {
    state.fibs = [];
    state.fibDraft = null;
    if (armedFib()) { state.arming = null; }
    draw();
  }

  /* Un Fibonacci sin recorrido no mide nada: todos los niveles caerían en el
   * mismo precio. El ancla que no se está moviendo se queda quieta y la otra se
   * separa un pip, que es lo mínimo que el gráfico distingue. */
  function normalizeFib(fib) {
    if (Math.abs(fib.hundred - fib.zero) >= PIP) { return; }
    if (fibDrag && fibDrag.part === "zero") {
      fib.zero = round_(fib.hundred - PIP);
    } else {
      fib.hundred = round_(fib.zero + PIP);
    }
  }

  /* Qué parte hay bajo el ratón. Las dos anclas ganan a los niveles: son lo que
   * define el retroceso, y con el tramo corto todo cae dentro de la tolerancia. */
  function fibPartAt(box, fib, cx, cy) {
    var zero = pixelAt(box, fib.from, fib.zero);
    var hundred = pixelAt(box, fib.to, fib.hundred);
    if (!zero || !hundred) { return null; }
    if (nearPixel(cx, cy, zero)) { return "zero"; }
    if (nearPixel(cx, cy, hundred)) { return "hundred"; }
    var edges = fibSpan(fib);
    var hit = fibLevels().filter(function (level) {
      var a = pixelAt(box, edges.from, fibPrice(fib, level));
      var b = pixelAt(box, edges.to, fibPrice(fib, level));
      return a && b && strokeDistance(cx, cy, a, b) <= GRAB;
    });
    return hit.length ? "body" : null;
  }

  /* El último trazado está encima, así que se busca del final al principio. */
  function fibHandleAt(box, cx, cy) {
    for (var index = state.fibs.length - 1; index >= 0; index -= 1) {
      var part = fibPartAt(box, state.fibs[index], cx, cy);
      if (part) { return { index: index, part: part }; }
    }
    return null;
  }

  function startFibDrag(event) {
    // La caja simulada, los recuadros y las líneas se registran antes y tienen
    // preferencia: si uno de ellos ha agarrado el gesto, aquí no hay nada que hacer.
    if (simDrag || rectDrag || lineDrag || axisDrag) { return; }
    if (state.arming || !state.fibs.length) { return; }
    var box = chartBox();
    if (!box) { return; }
    var hit = fibHandleAt(box, event.clientX, event.clientY);
    if (!hit) { return; }
    var origin = dataAt(box, event.clientX, event.clientY, true);
    if (!origin) { return; }
    var fib = state.fibs[hit.index];
    fibDrag = {
      index: hit.index, part: hit.part, origin: origin,
      base: { from: fib.from, to: fib.to, zero: fib.zero, hundred: fib.hundred }
    };
    if (event.preventDefault) { event.preventDefault(); }
    if (event.stopPropagation) { event.stopPropagation(); }
  }

  function moveFibDrag(event) {
    if (simDrag || rectDrag || lineDrag || !fibDrag) { return; }
    var box = chartBox();
    var point = box && dataAt(box, event.clientX, event.clientY, true);
    if (!point) { return; }
    applyFibDrag(point);
    if (event.preventDefault) { event.preventDefault(); }
  }

  function applyFibDrag(point) {
    var fib = state.fibs[fibDrag.index], base = fibDrag.base, part = fibDrag.part;
    if (part === "zero") {
      fib.from = point.minute;
      fib.zero = round_(point.price);
    } else if (part === "hundred") {
      fib.to = point.minute;
      fib.hundred = round_(point.price);
    } else {
      // Por dentro se mueve el conjunto ENTERO: el retroceso medido es lo que se
      // acaba de decidir y recolocarlo no puede cambiarlo por su cuenta.
      var dy = point.price - fibDrag.origin.price;
      var dx = point.minute - fibDrag.origin.minute;
      fib.from = base.from + dx;
      fib.to = base.to + dx;
      fib.zero = round_(base.zero + dy);
      fib.hundred = round_(base.hundred + dy);
    }
    normalizeFib(fib);
    redrawFibs();
  }

  /* Igual que las líneas: durante el arrastre se mueven sólo las formas del
   * Fibonacci, no la figura entera. */
  function redrawFibs() {
    var chart = document.getElementById("chart");
    if (fibIndex === null || !chart || typeof Plotly === "undefined" || !Plotly.relayout) {
      draw();
      return;
    }
    var update = {};
    fibShapes().forEach(function (shape, position) {
      var key = "shapes[" + (fibIndex + position) + "]";
      update[key + ".x0"] = shape.x0;
      update[key + ".x1"] = shape.x1;
      update[key + ".y0"] = shape.y0;
      update[key + ".y1"] = shape.y1;
      update[key + ".label.text"] = shape.label.text;
    });
    Plotly.relayout(chart, update);
  }

  function endFibDrag() {
    if (!fibDrag) { return; }
    fibDrag = null;
    draw();
  }

  /* Qué Fibonacci hay trazados. Cinco rayas con porcentajes se leen como una
   * medida que ha hecho alguien: el estado tiene que decir que la ha puesto una
   * mano y que detrás no hay ninguna regla. */
  function fibCaption() {
    if (state.fibDraft) {
      return "FIBONACCI a medias: el 0 está en " + price(state.fibDraft.price) +
        " · pulsa sobre el gráfico dónde va el 100, o Escape para dejarlo";
    }
    if (armedFib()) {
      return "FIBONACCI ARMADO: el primer clic clava el 0 y el segundo el 100, " +
        "o Escape para dejarlo";
    }
    if (!state.fibs.length) { return null; }
    return "Fibonacci trazados a mano: " + state.fibs.length +
      " (niveles " + fibLevels().map(fibLabel).join(", ") + ", contados del 0 al 100" +
      (fibGold() !== null ? "; el " + fibLabel(fibGold()) + " es la GOLD ZONE, en amarillo" : "") +
      ")" +
      " · los mide el propietario para explicar un retroceso: NO los ha " +
      "calculado el motor, no hay ninguna regla detrás y no salen de la pantalla";
  }

  function buildFibButtons() {
    var container = document.getElementById("fib-buttons");
    if (!container) { return; }
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Fibonacci";
    button.dataset.kind = FIB_ARM;
    button.title = "Arma el Fibonacci: el primer clic sobre el gráfico clava el 0\n" +
      "y el segundo el 100, así que la dirección la eliges tú. Se dibujan los\n" +
      "niveles " + fibLevels().join(", ") + " %, contados del 0 al 100. Escape\n" +
      "desarma. Lo mides tú: el motor no lo ve.";
    button.addEventListener("click", armFib);
    container.appendChild(button);
  }

  function bindFibs() {
    var chart = document.getElementById("chart");
    document.getElementById("fib-undo").addEventListener("click", undoFib);
    document.getElementById("fib-clear").addEventListener("click", clearFibs);
    if (!chart || !chart.addEventListener || !document.addEventListener) { return; }
    chart.addEventListener("mousedown", startFibDrag, true);
    document.addEventListener("mousemove", moveFibDrag);
    document.addEventListener("mouseup", endFibDrag);
  }

  /* --- Cuenta simulada (I.2) -------------------------------------------------
   *
   * Un capital de partida, un riesgo por operación y tres botones: la caja que
   * hay dibujada se apunta como GANADA, PERDIDA o en BREAK-EVEN y el saldo se
   * mueve. Lo que se cobra es el R:R de esa caja: ganar suma el riesgo por el
   * ratio, perder resta el riesgo entero y el break-even no mueve nada.
   *
   * SIGUE SIENDO DIBUJO. Nadie mira las velas: quien decide si esa entrada ganó
   * o perdió es el propietario, mirando el gráfico. El motor no ve estas
   * operaciones, no hay orden, no hay ejecución y esto no es dinero.
   *
   * Cada operación guarda los DÓLARES que se jugó y su múltiplo de riesgo
   * (+ratio, -1, 0). Lo cobrado queda cobrado: cambiar el capital o el riesgo
   * de la barra sólo cambia la apuesta de las operaciones que vengan detrás, no
   * lo que ganaron o perdieron las ya apuntadas. La curva arranca del capital
   * que había puesto al apuntar la primera; hasta entonces, del de la casilla.
   *
   * El riesgo NO compone: el porcentaje es del capital escrito en la casilla,
   * no del saldo vivo. Es lo que se quiere para juzgar una racha —si el tamaño
   * crece con el saldo, una buena seguida tapa lo que venga detrás—, y para
   * subir la apuesta se sube el capital a mano, sin mover nada de lo anterior.
   */
  var ACCOUNT_RESULTS = [
    {
      id: "win", label: "Ganada",
      title: "Apunta la caja dibujada como GANADA: suma el riesgo por el R:R de\n" +
        "la caja. Quita la caja del gráfico; «Deshacer» la devuelve."
    },
    {
      id: "loss", label: "Perdida",
      title: "Apunta la caja dibujada como PERDIDA: resta el riesgo entero.\n" +
        "Quita la caja del gráfico; «Deshacer» la devuelve."
    },
    {
      id: "be", label: "BE",
      title: "Break-even: la operación se apunta y no mueve el saldo. Cuenta en el\n" +
        "número de operaciones, pero no en el porcentaje de acierto."
    }
  ];

  var RESULT_NAMES = { win: "GANADA", loss: "PERDIDA", be: "BREAK-EVEN" };

  /* Cómo se mide el riesgo de la siguiente operación: un porcentaje del saldo
   * —que sube y baja con él, que es lo que compone— o una cantidad fija. */
  var RISK_MODES = [
    { id: "percent", label: "% del capital" },
    { id: "cash", label: "$ fijos" }
  ];

  function round2(value) { return Math.round(value * 100) / 100; }

  function money(value) {
    return round2(value).toLocaleString(
      "es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }
    ) + " $";
  }

  function signedMoney(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + money(Math.abs(round2(value)));
  }

  function pct(value) {
    return value.toLocaleString(
      "es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 }
    ) + " %";
  }

  function signedPct(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + pct(Math.abs(value));
  }

  function signedR(value) {
    return (value > 0 ? "+" : value < 0 ? "-" : "") + decimal(Math.abs(value)) + " R";
  }

  function plural(count, one, many) {
    return count.toLocaleString("es-ES") + " " + (count === 1 ? one : many);
  }

  /* Lo que se arriesga en la SIGUIENTE operación. El porcentaje es del capital
   * que hay escrito en la casilla, no del saldo vivo: el propietario pone 50 $ y
   * el 19 % son 9,50 $ hasta que cambie la casilla. Para que la apuesta suba
   * con la cuenta hay que subir el capital a mano, y eso sólo cambia lo que
   * viene: las operaciones ya apuntadas guardan la suya. */
  function riskFor() {
    var account = state.account;
    if (account.mode === "cash") { return round2(account.risk); }
    return round2(account.initial * account.risk / 100);
  }

  /* De dónde arranca la curva: del capital que había puesto al apuntar la
   * primera operación. Sin ninguna, de la casilla. Así tocar la casilla con
   * operaciones apuntadas no desplaza el saldo: sólo cambia la siguiente apuesta. */
  function accountStart() {
    var trades = state.account.trades;
    return trades.length ? trades[0].initial : state.account.initial;
  }

  /* La curva, sumando lo que cada operación movió con los dólares que se jugó
   * ENTONCES. Cada fila lleva esa apuesta, lo que movió y el saldo que dejó. */
  function accountRows() {
    var balance = accountStart();
    return state.account.trades.map(function (trade) {
      var delta = round2(trade.risk * trade.r);
      balance = round2(balance + delta);
      return { trade: trade, risk: trade.risk, delta: delta, balance: balance };
    });
  }

  function accountStats() {
    var rows = accountRows();
    var initial = accountStart();
    var balance = rows.length ? rows[rows.length - 1].balance : initial;
    var counts = { win: 0, loss: 0, be: 0 };
    var r = 0;
    var peak = initial;
    var drawdown = 0;
    var drawdownPct = 0;
    rows.forEach(function (row) {
      counts[row.trade.result] += 1;
      r += row.trade.r;
      if (row.balance > peak) { peak = row.balance; }
      var fall = peak - row.balance;
      if (fall > drawdown) {
        drawdown = fall;
        drawdownPct = peak > 0 ? fall / peak * 100 : 0;
      }
    });
    var decided = counts.win + counts.loss;
    return {
      rows: rows, initial: initial, balance: balance, counts: counts,
      trades: rows.length, r: round2(r),
      net: round2(balance - initial),
      netPct: initial > 0 ? (balance - initial) / initial * 100 : 0,
      hit: decided ? counts.win / decided * 100 : null,
      drawdown: round2(drawdown), drawdownPct: drawdownPct,
      risk: riskFor()
    };
  }

  /* Lo que la caja dibujada se juega AHORA: lo que resta si toca el stop y lo
   * que suma si llega al objetivo. Es lo que va escrito dentro de cada
   * rectángulo, para no tener que mirar arriba mientras se dibuja abajo. */
  function simStake() {
    var risk = riskFor();
    return { risk: risk, reward: round2(risk * simRatio()) };
  }

  function recordTrade(result) {
    var sim = state.sim;
    if (!sim) { return; }
    var ratio = Number(simRatio().toFixed(2));
    state.account.copied = null;
    state.account.trades.push({
      result: result,
      r: result === "win" ? ratio : (result === "loss" ? -1 : 0),
      ratio: ratio,
      // Los dólares que se juega y el capital puesto AHORA: se quedan con la
      // operación. Cambiar la barra después no toca lo ya cobrado.
      risk: riskFor(),
      initial: state.account.initial,
      chart: state.chart,
      at: iso(sim.from),
      box: {
        side: sim.side, entry: sim.entry, stop: sim.stop, target: sim.target,
        from: sim.from, to: sim.to
      }
    });
    // La caja se va: ya está cobrada, y dejarla puesta invita a apuntarla dos
    // veces. La siguiente entrada se planta como la primera.
    state.sim = null;
    state.arming = null;
    draw();
  }

  /* Deshacer devuelve el saldo Y la caja: el error que se deshace casi siempre
   * es haber pulsado el botón que no era, y replantar el dibujo a mano para
   * volver a cobrarlo bien sería perder la medida. */
  function undoTrade() {
    var last = state.account.trades.pop();
    if (!last) { return; }
    state.account.copied = null;
    state.sim = {
      side: last.box.side, entry: last.box.entry, stop: last.box.stop,
      target: last.box.target, from: last.box.from, to: last.box.to
    };
    state.arming = null;
    draw();
  }

  function resetAccount() {
    state.account.trades = [];
    state.account.copied = null;
    draw();
  }

  function setInitial(value) {
    var amount = parseFloat(value);
    // Un capital vacío o negativo no se acepta: `syncControls` repone el que
    // había, así que el control nunca se queda diciendo algo que no está puesto.
    if (isNaN(amount) || amount <= 0) { draw(); return; }
    state.account.initial = round2(amount);
    state.account.copied = null;
    draw();
  }

  function setRiskMode(value) {
    if (value !== "percent" && value !== "cash") { return; }
    state.account.mode = value;
    state.account.risk = value === "percent"
      ? Math.min(100, state.account.risk)
      : state.account.risk;
    state.account.copied = null;
    draw();
  }

  function setRisk(value) {
    var amount = parseFloat(value);
    if (isNaN(amount) || amount <= 0) { draw(); return; }
    if (state.account.mode === "percent" && amount > 100) { draw(); return; }
    state.account.risk = round2(amount);
    state.account.copied = null;
    draw();
  }

  function pad(text, width) {
    var out = String(text);
    while (out.length < width) { out += " "; }
    return out;
  }

  function riskLabel() {
    return state.account.mode === "percent"
      ? pct(state.account.risk) + " de " + money(state.account.initial) + " puestos"
      : money(state.account.risk) + " fijos";
  }

  /* El historial en texto plano, para pegarlo fuera del explorador. Cada fila
   * lleva los dólares que se jugó entonces: la configuración de arriba es la de
   * la siguiente operación, no la de todas. */
  function accountText() {
    var stats = accountStats();
    var lines = [
      "CUENTA SIMULADA · la apunta el propietario a mano sobre cajas dibujadas: " +
        "el motor no ve estas operaciones y no hay orden ninguna",
      "partía de " + money(stats.initial) + " · riesgo " + riskLabel() +
        " = " + money(stats.risk) + " en la siguiente operación",
      "capital " + money(stats.balance) + " · " + signedMoney(stats.net) +
        " (" + signedPct(stats.netPct) + ") · " + signedR(stats.r) +
        " · " + plural(stats.trades, "operación", "operaciones") +
        " (" + stats.counts.win + " ganadas, " + stats.counts.loss + " perdidas, " +
        stats.counts.be + " en break-even) · acierto " +
        (stats.hit === null ? "sin decidir" : pct(stats.hit)) +
        " · caída máxima " + money(stats.drawdown) + " (" + pct(stats.drawdownPct) + ")"
    ];
    if (!stats.trades) {
      lines.push("");
      lines.push("sin operaciones apuntadas");
      return lines.join("\n");
    }
    lines.push("");
    lines.push(
      pad("nº", 4) + pad("entrada (UTC)", 21) + pad("gráfico", 9) + pad("lado", 7) +
      pad("precio", 9) + pad("stop", 9) + pad("objetivo", 10) + pad("R:R", 8) +
      pad("resultado", 12) + pad("R", 7) + pad("riesgo", 10) + pad("mueve", 12) + "capital"
    );
    stats.rows.forEach(function (row, index) {
      var trade = row.trade;
      lines.push(
        pad(index + 1, 4) + pad(trade.at.slice(0, 16), 21) + pad(label(trade.chart), 9) +
        pad(sideLabel(trade.box.side).toUpperCase(), 7) +
        pad(price(trade.box.entry), 9) + pad(price(trade.box.stop), 9) +
        pad(price(trade.box.target), 10) + pad("1:" + decimal(trade.ratio), 8) +
        pad(RESULT_NAMES[trade.result], 12) + pad(signedR(trade.r).replace(" R", ""), 7) +
        pad(money(row.risk), 10) + pad(signedMoney(row.delta), 12) + money(row.balance)
      );
    });
    return lines.join("\n");
  }

  /* Copiar es de la barra, no del gráfico: el portapapeles puede no estar —un
   * navegador viejo, un fichero abierto sin permiso— y el estado tiene que
   * decirlo en vez de dejar creer que el historial ya está guardado fuera. */
  function copyAccount() {
    var text = accountText();
    var stats = accountStats();
    var done = false;
    try {
      var clipboard = typeof navigator !== "undefined" && navigator.clipboard;
      if (clipboard && clipboard.writeText) {
        var promise = clipboard.writeText(text);
        // El permiso se resuelve después: si lo niegan, el estado tiene que
        // desdecirse en vez de dejar creer que el historial ya está fuera.
        if (promise && promise.catch) {
          promise.catch(function () {
            state.account.copied = { ok: false, trades: stats.trades };
            draw();
          });
        }
        done = true;
      } else {
        done = legacyCopy(text);
      }
    } catch (error) {
      done = false;
    }
    state.account.copied = { ok: done, trades: stats.trades };
    draw();
  }

  function legacyCopy(text) {
    if (!document.body || !document.execCommand) { return false; }
    var area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    if (area.select) { area.select(); }
    var done = document.execCommand("copy");
    if (document.body.removeChild) { document.body.removeChild(area); }
    return done === true;
  }

  /* Lo corto, para la barra: el saldo tiene que verse sin leer nada más. */
  function accountSummary() {
    var stats = accountStats();
    return money(stats.balance) + " · riesgo " + money(stats.risk) + " · " +
      plural(stats.trades, "operación", "operaciones") +
      (stats.trades ? " · " + signedR(stats.r) : "");
  }

  /* Lo largo, para el estado de abajo. Sólo aparece cuando hay algo que contar
   * —una caja dibujada o alguna operación apuntada—: con la cuenta intacta no
   * hay nada que declarar. */
  function accountCaption() {
    var stats = accountStats();
    if (!stats.trades && !state.sim) { return null; }
    var text = "CUENTA SIMULADA: capital " + money(stats.balance) +
      " (partía de " + money(stats.initial) + ") · " + signedMoney(stats.net) +
      " (" + signedPct(stats.netPct) + ") · riesgo " + riskLabel() + " = " +
      money(stats.risk) + " en la siguiente operación";
    if (stats.trades) {
      text += " · " + plural(stats.trades, "operación apuntada", "operaciones apuntadas") +
        " (" + stats.counts.win + " ganadas, " + stats.counts.loss + " perdidas, " +
        stats.counts.be + " en break-even) · acierto " +
        (stats.hit === null ? "sin decidir" : pct(stats.hit)) +
        " (el break-even no cuenta) · " + signedR(stats.r) +
        " · caída máxima " + money(stats.drawdown) + " (" + pct(stats.drawdownPct) + ")";
    }
    if (state.sim) {
      var stake = simStake();
      text += " · la caja dibujada se juega " + money(stake.risk) + " para ganar " +
        money(stake.reward);
    }
    if (stats.balance <= 0) {
      text += " · CUENTA A CERO: no queda capital que arriesgar y las operaciones " +
        "siguientes no mueven nada";
    }
    if (state.account.copied) {
      text += state.account.copied.ok
        ? " · historial copiado al portapapeles (" +
          plural(state.account.copied.trades, "operación", "operaciones") + ")"
        : " · NO SE HA PODIDO COPIAR: este navegador no deja escribir en el portapapeles";
    }
    return text + " · NO ES DINERO: los resultados los apunta el propietario a mano " +
      "mirando el gráfico; nadie comprueba si el precio llegó y el motor no ve nada de esto";
  }

  function buildAccountButtons() {
    var container = document.getElementById("account-buttons");
    if (!container) { return; }
    ACCOUNT_RESULTS.forEach(function (result) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = result.label;
      button.dataset.result = result.id;
      button.title = result.title;
      button.addEventListener("click", function () { recordTrade(result.id); });
      container.appendChild(button);
    });
  }

  function buildRiskModes() {
    var select = document.getElementById("account-mode");
    if (!select) { return; }
    RISK_MODES.forEach(function (mode) {
      var option = document.createElement("option");
      option.value = mode.id;
      option.textContent = mode.label;
      select.appendChild(option);
    });
    select.value = state.account.mode;
  }

  function bindAccount() {
    document.getElementById("account-initial").addEventListener("change", function (event) {
      setInitial(event.target.value);
    });
    document.getElementById("account-risk").addEventListener("change", function (event) {
      setRisk(event.target.value);
    });
    document.getElementById("account-mode").addEventListener("change", function (event) {
      setRiskMode(event.target.value);
    });
    document.getElementById("account-undo").addEventListener("click", undoTrade);
    document.getElementById("account-reset").addEventListener("click", resetAccount);
    document.getElementById("account-copy").addEventListener("click", copyAccount);
  }

  function syncAccount() {
    var stats = accountStats();
    document.getElementById("account-initial").value = String(state.account.initial);
    document.getElementById("account-risk").value = String(state.account.risk);
    document.getElementById("account-mode").value = state.account.mode;
    document.getElementById("account-summary").textContent = accountSummary();
    // Sin caja dibujada no hay nada que cobrar: lo que se apunta es SIEMPRE una
    // caja concreta, con su R:R y su fecha, no un resultado suelto.
    document.querySelectorAll("#account-buttons button").forEach(function (button) {
      button.disabled = !state.sim;
    });
    document.getElementById("account-undo").disabled = !stats.trades;
    document.getElementById("account-reset").disabled = !stats.trades;
    document.getElementById("account-copy").disabled = !stats.trades;
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

  /* Las formas del limbo y, detrás, las tres de la caja simulada. Se apunta
   * dónde empiezan: es lo que permite mover sólo esas tres mientras se arrastra
   * en vez de rehacer la figura entera. */
  function simShapes_(shapes) {
    var box = simShapes();
    simIndex = box.length ? shapes.length : null;
    return shapes.concat(box);
  }

  /* Y encima de todo, los recuadros a mano (I.3): los planta el propietario y
   * tienen que verse sobre lo que dibuja el motor. Se apunta dónde empiezan por
   * lo mismo que la caja simulada: para poder arrastrar uno sin rehacer la
   * figura entera. */
  function rectShapes_(shapes) {
    var boxes = rectShapes();
    rectIndex = boxes.length ? shapes.length : null;
    return shapes.concat(boxes);
  }

  /* Y por encima de los recuadros, las líneas a mano (I.4): son lo último que
   * se planta y lo que se está señalando. Se apunta dónde empiezan por lo mismo
   * que los recuadros: para arrastrar una sin rehacer la figura entera. */
  function lineShapes_(shapes) {
    var strokes = lineShapes();
    lineIndex = strokes.length ? shapes.length : null;
    return shapes.concat(strokes);
  }

  /* Y encima de las líneas, el Fibonacci a mano (I.5): es la regla con la que
   * se está midiendo, así que no puede quedar debajo de lo que mide. Se apunta
   * dónde empieza por lo mismo que las líneas. */
  function fibShapes_(shapes) {
    var rules = fibShapes();
    fibIndex = rules.length ? shapes.length : null;
    return shapes.concat(rules);
  }

  function layout(range) {
    var x = xRange(range);
    var figure = {
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
      shapes: fibShapes_(lineShapes_(rectShapes_(simShapes_(limboShapes(range))))),
      xaxis: {
        type: "date", gridcolor: COLORS.grid, rangeslider: { visible: false },
        // El rango va siempre con su `autorange`: si se diera uno sin apagar el
        // otro, Plotly reescalaría el eje y el encuadre no aguantaría el paso.
        range: x, autorange: x ? false : true,
        anchor: "y",
        title: { text: "UTC", font: { size: 11, color: COLORS.muted } }
      },
      yaxis: {
        gridcolor: COLORS.grid, tickformat: "." + DECIMALS + "f", fixedrange: false,
        domain: [0, 1],
        // Sin esto el eje de precios se rehace en cada paso y el gráfico "salta"
        // en vertical: con encuadre manual manda lo que fijó el propietario.
        range: state.zoom.y || undefined,
        autorange: state.zoom.y ? false : true
      }
    };
    return figure;
  }

  function draw() {
    // El encuadre manual sigue al reloj ANTES de recortar: la ventana de datos
    // se calcula sobre el tramo que va a quedar a la vista, no sobre el anterior.
    if (state.replay && state.zoom.x) { state.zoom.x = followX(state.zoom.x, now_()); }
    var range = bounds();
    var cut = slice(range);
    var traces = priceTraces(cut)
      // La acumulación es un relleno: va detrás de todo lo que es línea o marca.
      .concat(accumulationTraces(range))
      .concat(impulseTraces(range))
      .concat(midTraces(range))
      .concat(markerTraces(range))
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
   * control. Sólo se nombran las que la corrida podría dibujar: decir
   * «acumulación apagada» en un explorador sin acumulaciones haría buscar una
   * capa que no existe. */
  var NOISE_NAMES = [
    ["limbo", "limbo", function () { return true; }],
    ["marks", "constituciones y roturas", function () { return true; }],
    ["contacts", "contactos", function () { return true; }],
    ["mid", "nivel 50 %", function () { return true; }],
    ["wrong", "extremo de color contrario", function () { return true; }],
    ["accumulation", "acumulación", hasAccumulations]
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
    // La caja simulada es lo único que dibuja el propietario, así que se declara
    // siempre: también con la venda puesta, donde es justo el gesto de decir
    // dónde habrías entrado antes de revelar.
    var simulada = simCaption();
    // Y la cuenta, cuando hay algo que contar: un saldo que se mueve sin decir
    // de dónde sale se lee como un resultado del motor.
    var cuenta = accountCaption();
    // Y los recuadros a mano, por lo mismo: son lo otro que dibuja el
    // propietario y en la ciega marcar dónde se ve una zona es justo el gesto
    // de la prueba.
    var recuadros = rectCaption();
    // Y las líneas, por lo mismo que los recuadros.
    var lineas = lineCaption();
    // Y el Fibonacci, por lo mismo: es una medida, y una medida sin dueño se lee
    // como que la ha hecho el motor.
    var fibonacci = fibCaption();
    if (blindfolded()) {
      return "AUDITORÍA CIEGA · semilla " + state.seed + " · " + label(state.chart) + " · " +
        range.from + " → " + range.to + " · " + visible.toLocaleString("es-ES") +
        " velas. Marca tus impulsos y pulsa Revelar. Sorteada dentro de " +
        state.scope.from + " → " + state.scope.to + "." +
        (simulada ? " · " + simulada : "") +
        (recuadros ? " · " + recuadros : "") +
        (lineas ? " · " + lineas : "") +
        (fibonacci ? " · " + fibonacci : "") +
        (cuenta ? " · " + cuenta : "");
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
    if (hasAccumulations() && state.accumulation) {
      var acumulando = [];
      overlays().filter(isVisible).forEach(function (timeframe) {
        var allowedAcc = visibleIds(timeframe, edges);
        accumulationsOf(timeframe).forEach(function (item) {
          if (item.x1 < edges.lo || item.x > edges.hi) { return; }
          if (pending(item.x, timeframe, edges) || !keeps(allowedAcc, item.id)) { return; }
          acumulando.push("ID " + timeframe + " nº " + item.id + " desde " + stamp(item.x) +
            " (" + price(item.lo) + " → " + price(item.hi) + ")");
        });
      });
      text += " · ACUMULACIÓN (dos toques arriba y dos abajo sin romper): " +
        (acumulando.length ? acumulando.join("; ") : "ninguna a la vista") +
        " · el sombreado empieza en el toque que completa la firma, no en la constitución";
    } else if (hasAccumulations()) {
      text += " · la acumulación no se está dibujando (capa apagada)";
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
    if (simulada) { text += " · " + simulada; }
    if (recuadros) { text += " · " + recuadros; }
    if (lineas) { text += " · " + lineas; }
    if (fibonacci) { text += " · " + fibonacci; }
    if (cuenta) { text += " · " + cuenta; }
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

  /* Atajos de temporalidad: una tecla por gráfico. Si la corrida no trae ese
   * gráfico —un histórico H1 no da para M15 ni para M5— la tecla no hace nada. */
  var CHART_KEYS = { "d": "D", "4": "H4", "1": "H1", "m": "M15", "5": "M5" };
  var CHART_SHORTCUTS = {};
  Object.keys(CHART_KEYS).forEach(function (key) { CHART_SHORTCUTS[CHART_KEYS[key]] = key; });

  /* Cambia el gráfico activo. Conserva el reloj canónico del replay, no el que
   * se lee en este gráfico: si vienes de pasar por el diario, lo que allí no
   * cabía sigue estando aquí. */
  function selectChart(chart) {
    if (DATA.charts.indexOf(chart) < 0) { return; }
    var at = state.replay ? state.at : null;
    state.chart = chart;
    if (at !== null) { alignCursor(at); }
    buildImpulseLayers();
    draw();
  }

  function buildChartButtons() {
    var container = document.getElementById("tf-buttons");
    DATA.charts.forEach(function (chart) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = label(chart);
      button.dataset.tf = chart;
      button.title = "Dibuja el impulso de " + DATA.layout[chart].map(label).join(" y ") +
        ". Atajo de teclado: " + (CHART_SHORTCUTS[chart] || "sin atajo");
      button.addEventListener("click", function () { selectChart(chart); });
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

  /* Lo que la corrida NO trae no se puede encender, lo pida quien lo pida: ni el
   * estado de salida ni un nivel de ruido. Se aplica en un solo sitio y después
   * de cada preset, porque el preset no sabe qué llevaba el payload. */
  function enforceAvailability() {
    if (!hasAccumulations()) { state.accumulation = false; }
  }

  /* La acumulación está aparcada: sin ninguna en la corrida no hay nada que
   * pintar, y una casilla que no puede dibujar nada sólo hace dudar de si está
   * fallando. */
  function buildAnalysisLayers() {
    if (hasAccumulations()) { return; }
    hide("accumulation-layer");
    hide("analysis-layers");
  }

  function hide(id) {
    var element = document.getElementById(id);
    if (element && element.style) { element.style.display = "none"; }
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

  /* Las casillas de impulso cambian con el gráfico —una por temporalidad que se
   * dibuja en él— y se reconstruyen en cada cambio de temporalidad. */
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

    document.querySelectorAll("#sim-buttons button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.side === state.arming));
    });
    document.getElementById("sim-clear").disabled = !state.sim && !simArming();
    document.querySelectorAll("#sim-ratio button").forEach(function (button) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.ratio) === state.ratio));
    });
    // Armado, el gráfico deja de ser sólo para mirar: el cursor lo dice.
    var canvas = document.getElementById("chart");
    if (canvas && canvas.style) { canvas.style.cursor = state.arming ? "crosshair" : ""; }

    // I.3 — los recuadros a mano: cada botón dice si está esperando el clic y
    // se pinta del color de su recuadro, y sin recuadros puestos no hay nada
    // que quitar.
    document.querySelectorAll("#rect-buttons button").forEach(function (button) {
      var kind = button.dataset.kind;
      var pressed = armedRect() === kind;
      button.setAttribute("aria-pressed", String(pressed));
      button.style.color = pressed ? COLORS.surface : rectColor(kind);
      button.style.background = pressed ? rectColor(kind) : "";
      button.style.borderColor = pressed ? rectColor(kind) : "";
    });
    document.getElementById("rect-undo").disabled = !state.rects.length;
    document.getElementById("rect-clear").disabled = !state.rects.length;

    // I.4 — las líneas a mano: lo mismo, cada botón con el color de su línea.
    document.querySelectorAll("#line-buttons button").forEach(function (button) {
      var kind = button.dataset.kind;
      var pressed = armedLine() === kind;
      button.setAttribute("aria-pressed", String(pressed));
      button.style.color = pressed ? COLORS.surface : lineColor(kind);
      button.style.background = pressed ? lineColor(kind) : "";
      button.style.borderColor = pressed ? lineColor(kind) : "";
    });
    document.getElementById("line-undo").disabled = !state.lines.length;
    document.getElementById("line-clear").disabled = !state.lines.length;

    // I.5 — el Fibonacci: un solo botón, y «Quitar último» sirve también para
    // soltar el 0 que está esperando su segundo clic.
    document.querySelectorAll("#fib-buttons button").forEach(function (button) {
      var pressed = armedFib();
      button.setAttribute("aria-pressed", String(pressed));
      button.style.color = pressed ? COLORS.surface : fibColor();
      button.style.background = pressed ? fibColor() : "";
      button.style.borderColor = pressed ? fibColor() : "";
    });
    document.getElementById("fib-undo").disabled =
      !state.fibs.length && !state.fibDraft;
    document.getElementById("fib-clear").disabled =
      !state.fibs.length && !state.fibDraft;

    if (hasAccumulations()) {
      document.getElementById("layer-accumulation").checked = state.accumulation;
    }
    syncAccount();
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
      ["layer-accumulation", "accumulation"]
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
    document.getElementById("sim-clear").addEventListener("click", clearSim);
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

  /* ◀ ▶ también con las flechas del teclado (B.3), la barra espaciadora para
   * arrancar y parar el replay, y una tecla por temporalidad (d/4/1/m) para
   * saltar de gráfico. Se ignoran mientras el foco está en un campo de texto:
   * ahí las teclas escriben y robarlas haría imposible poner una fecha o una
   * semilla.
   *
   * En replay las flechas dan pasos en vez de mover la ventana: es el mismo
   * gesto —avanzar y retroceder en el tiempo— a la escala de lo que se mira. */
  function bindArrowKeys() {
    if (!document.addEventListener) { return; }
    document.addEventListener("keydown", function (event) {
      var arrow = event.key === "ArrowLeft" || event.key === "ArrowRight";
      var space = event.key === " " || event.key === "Spacebar";
      // Escape desarma el simulador de entradas: un botón que se queda esperando
      // un clic tiene que poder soltarse sin plantar nada.
      var escape = event.key === "Escape" || event.key === "Esc";
      // Con Ctrl/Alt/Meta la tecla es del navegador (Ctrl+D marca la página):
      // ahí no hay atajo de temporalidad.
      var modified = event.ctrlKey || event.altKey || event.metaKey;
      var chart = modified ? null : CHART_KEYS[String(event.key).toLowerCase()];
      if (!arrow && !space && !chart && !escape) { return; }
      var focused = document.activeElement;
      var tag = focused && focused.tagName ? focused.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
      // La barra espaciadora sobre un botón lo pulsa: ahí no se roba.
      if (space && (tag === "BUTTON" || !state.replay)) { return; }
      if (escape) {
        // Y el 0 del Fibonacci que espera su segundo clic: a medias no mide nada
        // y tiene que poder soltarse sin plantar el 100 en cualquier sitio.
        if (state.arming || state.fibDraft) {
          state.arming = null;
          state.fibDraft = null;
          draw();
        }
        return;
      }
      if (event.preventDefault) { event.preventDefault(); }
      if (chart) { selectChart(chart); return; }
      if (space) { toggleReplay(); return; }
      var back = event.key === "ArrowLeft" ? -1 : 1;
      if (state.replay) { stepReplay(back); } else { step(back); }
    });
  }

  buildChartButtons();
  buildModeButtons();
  buildAnalysisLayers();
  buildNoiseButtons();
  // El nivel de salida se aplica aquí, con el payload ya leído: así lo que no
  // trae la corrida se queda apagado aunque el preset lo encienda.
  setNoise(state.noise);
  buildVisibleButtons();
  buildPresetButtons();
  buildImpulseLayers();
  buildSimButtons();
  buildRectButtons();
  buildLineButtons();
  buildFibButtons();
  buildRatioButtons();
  buildAccountButtons();
  buildRiskModes();
  bindControls();
  bindAxisScaling();
  bindSim();
  bindRects();
  bindLines();
  bindFibs();
  bindAccount();
  draw();
})();
