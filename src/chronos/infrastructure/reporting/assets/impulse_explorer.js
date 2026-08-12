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
  var DAY = 1440;

  var VISIBLE_MODES = [
    { id: "current", label: "Actual" },
    { id: "pair", label: "Actual + anterior" },
    { id: "all", label: "Todos" }
  ];

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
      sessionFormat.format(toDate(minute)).replace(",", "") + " " + SESSION_TZ;
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
    var list = impulsesOf(timeframe).list;
    var until = knownUntil(timeframe, edges);
    var last = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].x0 > until) { break; }
      last = i;
    }
    if (last < 0) { return {}; }
    var keep = {};
    keep[list[last].id] = true;
    if (state.visible === "pair" && last > 0) { keep[list[last - 1].id] = true; }
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

  function formingCandle() {
    if (!state.replay || !state.sub) { return null; }
    var edges = formingRange();
    if (!edges) { return null; }
    var fine = DATA.bars[edges.timeframe];
    var count = Math.min(state.sub, edges.to - edges.from);
    var high = -Infinity, low = Infinity;
    for (var i = edges.from; i < edges.from + count; i++) {
      if (fine.h[i] > high) { high = fine.h[i]; }
      if (fine.l[i] < low) { low = fine.l[i]; }
    }
    return {
      x: edges.at, o: fine.o[edges.from], h: high, l: low,
      c: fine.c[edges.from + count - 1],
      done: count, total: edges.to - edges.from, timeframe: edges.timeframe
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
        [["ancla", impulse.a, impulse.xa], ["extremo", impulse.e, impulse.xe]].forEach(
          function (level) {
            var name = level[0], value = level[1], defined = level[2];
            push(bucket.live, impulse.x0, clip(impulse.x1, edges), value,
              head + "<br>" + name + " " + price(value) +
              "<br>fija la vela de " + stamp(defined));
            if (defined < impulse.x0) {
              push(bucket.pending, defined, impulse.x0, value,
                "ANTES DE LA CONSTITUCIÓN · el nivel ya estaba en el precio, el ID no" +
                "<br>futuro " + name + " del ID " + timeframe + " nº " + impulse.id +
                " en " + price(value) +
                "<br>lo fija la vela de " + stamp(defined) +
                "<br>el ID no nace hasta " + stamp(impulse.x0));
            }
          }
        );
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
      type: "scatter", mode: "markers", name: name + " " + label(primary()),
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
      margin: { l: 66, r: 18, t: 16, b: 44 },
      paper_bgcolor: COLORS.surface,
      plot_bgcolor: COLORS.surface,
      font: { family: COLORS.font, size: 12, color: COLORS.ink },
      hovermode: "closest",
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
    var traces = priceTraces(cut)
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
      text = "REPLAY · " + label(state.chart) + " · última vela cerrada " +
        stamp(b.t[state.cursor]) + " (nº " + (state.cursor + 1).toLocaleString("es-ES") +
        " de " + b.t.length.toLocaleString("es-ES") + ")" +
        (half
          ? " · vela en formación con " + half.done + " de " + half.total + " velas de " +
            label(half.timeframe)
          : "") +
        " · sólo se dibuja lo que el motor sabía a esa hora";
    }
    if (state.zoom.x || state.zoom.y) {
      text += " · encuadre manual: el zoom se mantiene entre pasos (Ajustar para soltarlo)";
    }
    if (dibujados.length) { text += " · impulsos dibujados: " + dibujados.join(", "); }
    if (state.visible !== "all") {
      text += " · filtro de dibujo «" +
        (state.visible === "current" ? "ID actual" : "ID actual + anterior") +
        "»: los demás siguen en los datos y en los informes";
    }
    var info = modeInfo(state.mode);
    if (info) {
      text += " · LEG_START_MODE = " + info.name + " (hash " + info.hash + "): " +
        info.impulses.toLocaleString("es-ES") + " impulsos en total, " +
        info.wrong.toLocaleString("es-ES") + " con el extremo sobre vela de color contrario";
    }
    if (!visible) {
      text += ". No hay velas en este tramo: el mercado estaba cerrado o el histórico no llega.";
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
   * fecha cualquiera. */
  function alignCursor(at) {
    var t = bars().t;
    var index = lowerBound(t, at - span(state.chart) + 1) - 1;
    state.cursor = Math.min(Math.max(index, 0), t.length - 1);
    state.sub = 0;
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
        var at = state.replay ? now_() : null;
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
      ["layer-wrong", "wrong"]
    ].forEach(function (pair) {
      document.getElementById(pair[0]).addEventListener("change", function (event) {
        state[pair[1]] = event.target.checked;
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
      if (!state.forming) { state.sub = 0; }
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
  buildVisibleButtons();
  buildPresetButtons();
  buildImpulseLayers();
  bindControls();
  draw();
})();
