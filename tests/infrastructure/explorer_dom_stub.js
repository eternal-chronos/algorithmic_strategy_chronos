/* Arranca impulse_explorer.js contra un DOM mínimo y simula los controles.
 *
 * No sustituye a mirar el explorador en un navegador, pero sí detecta lo que
 * más suele romperse: identificadores que no existen, campos mal nombrados en
 * el payload y excepciones dentro del ciclo de render. Además comprueba que los
 * controles exigidos por §5.3 —velas/líneas, el reparto de temporalidades y la
 * navegación por fechas— cambian la figura de verdad.
 *
 * Uso: node explorer_dom_stub.js <impulse_explorer.js> <payload.json>
 */
const fs = require('fs');

const [, , scriptPath, payloadPath] = process.argv;

const plotCalls = [];
const missing = [];
const elements = {};

function makeElement(id) {
  return {
    id: id,
    dataset: {},
    checked: true,
    value: '',
    min: '',
    max: '',
    title: '',
    className: '',
    textContent: '',
    children: [],
    attributes: {},
    listeners: {},
    style: {},
    set innerHTML(value) { if (value === '') { this.children = []; } },
    get innerHTML() { return ''; },
    addEventListener(type, handler) {
      (this.listeners[type] = this.listeners[type] || []).push(handler);
    },
    // Los eventos de Plotly se enganchan al div con `on`, no con addEventListener.
    on(type, handler) { this.addEventListener(type, handler); },
    // El gesto de escalar sobre los ejes necesita saber dónde está el gráfico.
    getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 720 }; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name] ?? null; },
    appendChild(child) { this.children.push(child); return child; },
    fire(type, event) {
      (this.listeners[type] || []).forEach(function (handler) { handler(event || {}); });
    },
  };
}

function declare(id) {
  elements[id] = makeElement(id);
  return elements[id];
}

// Elementos que la plantilla HTML declara de verdad.
['tf-buttons', 'view-buttons', 'preset-buttons', 'visible-buttons', 'mode-buttons',
 'mode-group', 'impulse-layers', 'chart', 'zoom-reset', 'noise-buttons',
 'prev', 'next',
 'from', 'to', 'layer-limbo', 'layer-marks', 'layer-contacts', 'layer-mid', 'layer-wrong',
 'layer-frame', 'layer-sessions', 'layer-patterns',
 'blind-seed', 'blind-start', 'blind-reveal', 'blind-exit',
 'sim-group', 'sim-buttons', 'sim-rr', 'sim-clear',
 'rect-group', 'rect-buttons', 'rect-undo', 'rect-clear',
 'line-group', 'line-buttons', 'line-undo', 'line-clear',
 'fib-group', 'fib-buttons', 'fib-undo', 'fib-clear',
 'account-group', 'account-initial', 'account-mode', 'account-risk',
 'account-buttons', 'account-undo', 'account-reset', 'account-copy', 'account-summary',
 'replay-group', 'replay-date', 'replay-start', 'replay-back', 'replay-step',
 'replay-play', 'replay-exit', 'replay-forming', 'replay-speed', 'replay-window',
 'notes', 'explorer-data'].forEach(declare);

elements['explorer-data'].textContent = fs.readFileSync(payloadPath, 'utf8');

const viewButtons = ['candles', 'line'].map(function (view) {
  const button = makeElement('view-' + view);
  button.dataset.view = view;
  return button;
});

const documentListeners = {};

// I.2 — el portapapeles. `writeText` devuelve algo con `catch`, como la promesa
// de verdad, para que el explorador pueda encadenarlo.
const copiado = [];
// `navigator` es un global de node y no se deja reasignar: hay que redefinirlo.
Object.defineProperty(global, 'navigator', {
  configurable: true,
  writable: true,
  value: {
    clipboard: {
      writeText(text) { copiado.push(text); return { catch() { return null; } }; },
    },
  },
});

global.document = {
  activeElement: null,
  getElementById(id) {
    if (!elements[id]) { missing.push(id); declare(id); }
    return elements[id];
  },
  createElement() { return makeElement('created'); },
  createTextNode(text) { return { text: text }; },
  addEventListener(type, handler) {
    (documentListeners[type] = documentListeners[type] || []).push(handler);
  },
  querySelectorAll(selector) {
    if (selector === '#tf-buttons button') { return elements['tf-buttons'].children; }
    if (selector === '#preset-buttons button') { return elements['preset-buttons'].children; }
    if (selector === '#visible-buttons button') { return elements['visible-buttons'].children; }
    if (selector === '#mode-buttons button') { return elements['mode-buttons'].children; }
    if (selector === '#noise-buttons button') { return elements['noise-buttons'].children; }
    if (selector === '#sim-buttons button') { return elements['sim-buttons'].children; }
    if (selector === '#rect-buttons button') { return elements['rect-buttons'].children; }
    if (selector === '#line-buttons button') { return elements['line-buttons'].children; }
    if (selector === '#fib-buttons button') { return elements['fib-buttons'].children; }
  if (selector === '#account-buttons button') { return elements['account-buttons'].children; }
    if (selector === '#view-buttons button') { return viewButtons; }
    missing.push(selector);
    return [];
  },
};

function fireDocument(type, event) {
  (documentListeners[type] || []).forEach(function (handler) { handler(event); });
}

function pressKey(key, focusedTag) {
  global.document.activeElement = focusedTag ? { tagName: focusedTag } : null;
  (documentListeners['keydown'] || []).forEach(function (handler) {
    handler({ key: key, preventDefault() {} });
  });
  global.document.activeElement = null;
}

/* Lo que dibuja el motor, frente a las velas y a la vela en formación. En el
 * replay ningún punto de estas capas puede caer más allá del reloj. */
function engineLayer(name) {
  return !/^(Velas |Cierres |Vela en formación)/.test(name || '');
}

function simShape(shape) {
  return String(shape.name || '').indexOf('sim-') === 0;
}

// I.3 — los recuadros los planta el propietario a mano, igual que la caja
// simulada: tampoco son capa del motor.
function rectShape(shape) {
  return String(shape.name || '').indexOf('rect-') === 0;
}

// I.4 — las líneas de magenta, cian y oliva las traza el propietario a mano:
// tampoco son capa del motor.
function lineShape(shape) {
  return String(shape.name || '').indexOf('line-') === 0;
}

// I.5 — el Fibonacci gris lo mide el propietario a mano: tampoco es capa del
// motor, ni siquiera el 0 que espera su segundo clic.
function fibShape(shape) {
  return String(shape.name || '').indexOf('fib-') === 0;
}

function handDrawn(shape) {
  return simShape(shape) || rectShape(shape) || lineShape(shape) || fibShape(shape);
}

function furthest(traces, layout) {
  const points = [];
  traces.forEach(function (trace) {
    if (!engineLayer(trace.name)) { return; }
    (trace.x || []).forEach(function (value) { if (value) { points.push(value); } });
  });
  // La caja simulada la dibuja el propietario a mano: no es motor y no puede
  // contar como que el replay se ha adelantado al reloj.
  (layout.shapes || []).forEach(function (shape) {
    if (!handDrawn(shape)) { points.push(shape.x1); }
  });
  return points.length ? points.sort()[points.length - 1] : null;
}

const relayoutCalls = [];

global.Plotly = {
  // Lo que el gesto de escalar sobre los ejes le pide a Plotly. Se reemite como
  // `plotly_relayout`, que es lo que hace Plotly de verdad: así el recorrido
  // comprueba también que el encuadre queda guardado.
  relayout(target, update) {
    relayoutCalls.push(update);
    elements['chart'].fire('plotly_relayout', update);
  },
  react(target, traces, layout) {
    plotCalls.push({
      maxEngineX: furthest(traces, layout),
      target: target,
      traces: traces.map(function (trace) {
        return {
          name: trace.name,
          type: trace.type,
          points: (trace.x && trace.x.length) || 0,
          dash: (trace.line && trace.line.dash) || null,
          // El color del trazo: el marco del ID lo toma de su TEMPORALIDAD y es
          // lo único que dice de quién es cada recuadro cuando hay dos.
          color: (trace.line && trace.line.color) || null,
          fill: trace.fill || null,
          fillcolor: trace.fillcolor || null,
          captions: trace.text && trace.text.length <= 200 ? trace.text.slice() : null,
          // J.1 — las sesiones escriben el nombre en el gráfico (`text`) y
          // cuentan el resto al pasar el ratón (`hovertext`).
          hovers: trace.hovertext && trace.hovertext.length <= 200
            ? trace.hovertext.slice()
            : null,
          textposition: trace.textposition || null,
          xs: trace.mode === 'markers' && (trace.x || []).length <= 200
            ? trace.x.slice()
            : null,
          // El trazo entero, para comprobar QUÉ nivel se dibuja en CADA tramo:
          // cada segmento es (x0, x1, precio).
          segments: /^lines/.test(trace.mode || '') && (trace.x || []).length <= 3000
            ? (trace.x || []).map(function (x, index) { return [x, trace.y[index]]; })
            : null,
        };
      }),
      bars: (traces[0] && traces[0].x && traces[0].x.length) || 0,
      firstBar: traces[0] && traces[0].x && traces[0].x[0],
      lastBar: traces[0] && traces[0].x && traces[0].x[traces[0].x.length - 1],
      hover: traces[0] && traces[0].text && traces[0].text[0],
      shapes: (layout.shapes || []).filter(function (shape) {
        return !handDrawn(shape);
      }).length,
      // I.1 — la caja de la entrada simulada, con lo que dice cada rectángulo.
      sim: (layout.shapes || []).filter(simShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          // La línea de entrada de la caja ACTIVA va más gruesa.
          width: (shape.line && shape.line.width) || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      // I.3 — los recuadros marcados a mano, con el color y el trazo que los
      // distinguen de todo lo que dibuja el motor.
      rect: (layout.shapes || []).filter(rectShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          color: (shape.line && shape.line.color) || null,
          dash: (shape.line && shape.line.dash) || null,
          fillcolor: shape.fillcolor || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      // I.4 — las líneas trazadas a mano, con el color y el trazo que las
      // separan de los recuadros y de todo lo que dibuja el motor.
      line: (layout.shapes || []).filter(lineShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          color: (shape.line && shape.line.color) || null,
          dash: (shape.line && shape.line.dash) || null,
          width: (shape.line && shape.line.width) || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      // I.5 — el Fibonacci trazado a mano: un trazo por nivel, con el
      // porcentaje escrito al lado.
      fib: (layout.shapes || []).filter(fibShape).map(function (shape) {
        return {
          name: shape.name, type: shape.type,
          x0: shape.x0, x1: shape.x1, y0: shape.y0, y1: shape.y1,
          color: (shape.line && shape.line.color) || null,
          dash: (shape.line && shape.line.dash) || null,
          width: (shape.line && shape.line.width) || null,
          label: (shape.label && shape.label.text) || null,
        };
      }),
      // El RSI vive en su propio panel: el reparto de alto entre precio e índice
      // es lo que mantiene los tiradores donde se ve la línea.
      priceDomain: (layout.yaxis && layout.yaxis.domain) || null,
      rsiAxis: layout.yaxis2
        ? {
          domain: layout.yaxis2.domain,
          ticks: layout.yaxis2.tickvals,
          title: layout.yaxis2.title && layout.yaxis2.title.text,
        }
        : null,
      xAnchor: (layout.xaxis && layout.xaxis.anchor) || null,
      yTickFormat: layout.yaxis && layout.yaxis.tickformat,
      xRange: (layout.xaxis && layout.xaxis.range) || null,
      yRange: (layout.yaxis && layout.yaxis.range) || null,
    });
  },
};

function snapshot(label) {
  return {
    label: label,
    chart: (elements['tf-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.tf || null,
    plot: plotCalls[plotCalls.length - 1],
    notes: elements['notes'].textContent,
    from: elements['from'].value,
    to: elements['to'].value,
    layerLabels: elements['impulse-layers'].children.map(function (wrapper) {
      return wrapper.children.map(function (child) { return child.text || ''; }).join('');
    }),
    replayDate: elements['replay-date'].value,
    zoomFree: elements['zoom-reset'].disabled === true,
    replayPlay: elements['replay-play'].textContent,
    lastRelayout: relayoutCalls[relayoutCalls.length - 1] || null,
    replayLocked: elements['from'].disabled === true && elements['next'].disabled === true,
    simArmed: (elements['sim-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.side || null,
    simClearDisabled: elements['sim-clear'].disabled === true,
    simClearTitle: elements['sim-clear'].title,
    // Con dos cajas puestas los botones de armar se apagan, y dicen por qué.
    simButtonsDisabled: elements['sim-buttons'].children.map(function (button) {
      return button.disabled === true;
    }),
    simButtonTitles: elements['sim-buttons'].children.map(function (button) {
      return button.title;
    }),
    // El R:R que el panel dice que hay dibujado: siempre medido de la caja.
    simReadout: elements['sim-rr'].textContent,
    simReadoutSource: elements['sim-rr'].dataset.source || '',
    simReadoutTitle: elements['sim-rr'].title,
    simCursor: elements['chart'].style.cursor || '',
    // I.3 — los recuadros a mano: qué botón espera el clic y si hay algo que quitar.
    rectArmed: (elements['rect-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.kind || null,
    rectUndoDisabled: elements['rect-undo'].disabled === true,
    rectClearDisabled: elements['rect-clear'].disabled === true,
    // I.4 — las líneas a mano: qué botón espera el clic y si hay algo que quitar.
    lineArmed: (elements['line-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.kind || null,
    lineUndoDisabled: elements['line-undo'].disabled === true,
    lineClearDisabled: elements['line-clear'].disabled === true,
    // I.5 — el Fibonacci a mano: si el botón espera un clic y si hay algo que quitar.
    fibArmed: elements['fib-buttons'].children.some(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    }),
    fibUndoDisabled: elements['fib-undo'].disabled === true,
    fibClearDisabled: elements['fib-clear'].disabled === true,
    // I.2 — la cuenta simulada: lo que dice la barra y lo que deja hacer.
    account: {
      summary: elements['account-summary'].textContent,
      initial: elements['account-initial'].value,
      mode: elements['account-mode'].value,
      risk: elements['account-risk'].value,
      resultsDisabled: elements['account-buttons'].children.map(function (button) {
        return button.disabled === true;
      }),
      undoDisabled: elements['account-undo'].disabled === true,
      resetDisabled: elements['account-reset'].disabled === true,
      copyDisabled: elements['account-copy'].disabled === true,
    },
    copiado: copiado.length ? copiado[copiado.length - 1] : null,
    visibleMode: (elements['visible-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.visible || null,
    legStartMode: (elements['mode-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.mode || null,
    // H.1 — el nivel de ruido que está puesto, o null cuando las capas se han
    // tocado a mano y ningún botón puede decir que el estado es suyo.
    noiseLevel: (elements['noise-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.noise || null,
    // Las casillas que el preset mueve sin que nadie las toque: si el estado y
    // el control se separan, el explorador miente sobre lo que se está viendo.
    boxes: ['layer-limbo', 'layer-marks', 'layer-contacts', 'layer-mid', 'layer-wrong',
      'layer-frame', 'layer-sessions', 'layer-patterns']
      .reduce(function (state, id) {
        state[id] = elements[id].checked === true;
        return state;
      }, {}),
  };
}

global.window = global;
eval(fs.readFileSync(scriptPath, 'utf8'));

const payloadData = JSON.parse(elements['explorer-data'].textContent);
const payloadMeta = payloadData.meta;
const steps = [];
const tabs = elements['tf-buttons'].children;
const presets = elements['preset-buttons'].children;

// H.1 — el nivel de ruido. El explorador abre en «Limpio», así que lo primero
// que se retrata es el gráfico tal como sale; después se pasa a «Todo» y se deja
// en «Normal», que es el estado que audita el resto del recorrido.
const noises = elements['noise-buttons'].children;

function setNoise(level) {
  noises.filter(function (button) { return button.dataset.noise === level; })
    .forEach(function (button) { button.fire('click'); });
}

steps.push(snapshot('ruido-de-salida'));
setNoise('all');
steps.push(snapshot('ruido-todo'));
setNoise('normal');
steps.push(snapshot('ruido-normal'));
// Tocar una casilla suelta deja el nivel sin dueño: ningún botón pulsado.
elements['layer-mid'].fire('change', { target: { checked: true } });
steps.push(snapshot('ruido-a-mano'));
elements['layer-mid'].fire('change', { target: { checked: false } });
setNoise('clean');
steps.push(snapshot('ruido-limpio'));
setNoise('normal');

// Un recorrido por cada gráfico, mirando el preset más corto para que la
// ventana quepa en cualquier histórico de prueba.
presets[presets.length - 1].fire('click');
tabs.forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('grafico-' + tab.dataset.tf));
});

// De vuelta al primero: periodo completo, navegación y vista.
tabs[0].fire('click');
presets[0].fire('click');
steps.push(snapshot('todo'));
presets[presets.length - 1].fire('click');
steps.push(snapshot('preset-corto'));
elements['prev'].fire('click');
steps.push(snapshot('ventana-anterior'));
elements['next'].fire('click');
steps.push(snapshot('ventana-siguiente'));

// B.3 — las flechas del teclado mueven la ventana igual que los botones, salvo
// cuando el foco está en un campo de texto.
pressKey('ArrowLeft');
steps.push(snapshot('teclado-izquierda'));
pressKey('ArrowRight');
steps.push(snapshot('teclado-derecha'));
pressKey('ArrowLeft', 'INPUT');
steps.push(snapshot('teclado-en-un-campo'));

// Atajos de temporalidad: d/4/1/m/5 saltan de gráfico, salvo con el foco en un
// campo de texto o con una tecla modificadora (Ctrl+D es del navegador).
pressKey('4');
steps.push(snapshot('teclado-tf-h4'));
pressKey('m');
steps.push(snapshot('teclado-tf-m15'));
pressKey('5');
steps.push(snapshot('teclado-tf-m5'));
pressKey('1');
steps.push(snapshot('teclado-tf-h1'));
pressKey('d', 'INPUT');
steps.push(snapshot('teclado-tf-en-un-campo'));
pressKey('d');
steps.push(snapshot('teclado-tf-diario'));

viewButtons[1].fire('click');
steps.push(snapshot('lineas'));
viewButtons[0].fire('click');

// Capas: apagar el limbo y el impulso principal del gráfico que tenga contexto.
elements['layer-limbo'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-limbo'));
elements['layer-limbo'].fire('change', { target: { checked: true } });

const conContexto = tabs.filter(function (tab) {
  return JSON.parse(elements['explorer-data'].textContent).layout[tab.dataset.tf].length > 1;
});
if (conContexto.length) {
  conContexto[0].fire('click');
  steps.push(snapshot('con-contexto'));
  const layers = elements['impulse-layers'].children;
  layers[0].children[0].fire('change', { target: { checked: false } });
  steps.push(snapshot('sin-principal'));
}

// Capas nuevas: contactos y nivel del 50 % (F.2 y F.3).
tabs[0].fire('click');
presets[presets.length - 1].fire('click');
elements['layer-contacts'].fire('change', { target: { checked: true } });
steps.push(snapshot('con-contactos'));
elements['layer-mid'].fire('change', { target: { checked: true } });
steps.push(snapshot('con-nivel-50'));
elements['layer-contacts'].fire('change', { target: { checked: false } });
elements['layer-mid'].fire('change', { target: { checked: false } });

// J.1 — el alto y el bajo de Asia y de Londres que marca el motor a las 7:58 de
// Nueva York. Se retratan en M15 con la ventana corta, que es donde se leen, y
// apagados en la misma ventana para comprobar que la casilla los quita de
// verdad.
const m15 = tabs.filter(function (tab) { return tab.dataset.tf === 'M15'; })[0] || tabs[0];
m15.fire('click');
presets[presets.length - 1].fire('click');
elements['layer-sessions'].fire('change', { target: { checked: true } });
steps.push(snapshot('con-sesiones'));
elements['layer-sessions'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-sesiones'));
elements['layer-sessions'].fire('change', { target: { checked: true } });
tabs[0].fire('click');

// K.1 — el OB y el FVG que marca el motor dentro del ID. Se retratan en H1 con
// el periodo entero y todos los ID a la vista —es donde se marcan dentro del ID
// de H4, el caso con dos temporalidades—, apagados sobre las mismas velas para
// comprobar que la casilla los quita, en el Diario y en H4 con su propio ID, y
// en M15, donde no se marcan y el estado tiene que decirlo.
const h1Patterns = tabs.filter(function (tab) { return tab.dataset.tf === 'H1'; })[0] || tabs[0];
h1Patterns.fire('click');
presets[0].fire('click');
setNoise('all');
steps.push(snapshot('con-patrones'));
elements['layer-patterns'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-patrones'));
elements['layer-patterns'].fire('change', { target: { checked: true } });
setNoise('clean');
steps.push(snapshot('patrones-id-actual'));
setNoise('all');
tabs.filter(function (tab) { return tab.dataset.tf === 'D'; }).forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('patrones-diario'));
});
tabs.filter(function (tab) { return tab.dataset.tf === 'H4'; }).forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('patrones-h4'));
});
tabs.filter(function (tab) { return tab.dataset.tf === 'M15'; }).forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('patrones-m15'));
});
setNoise('normal');
tabs[0].fire('click');

// El MARCO del ID: el recuadro de la constitución a la muerte y del ancla al
// extremo. Se enciende y se apaga sobre las mismas velas. Si el reparto tiene
// algún gráfico con contexto se retrata ahí, porque así comprueba de paso que el
// marco de la temporalidad superior se dibuja y con su color; el reparto por
// defecto no tiene ninguno —el Diario se dibuja sólo en su gráfico— y entonces
// se retrata H4, que es donde el propietario audita el ID.
const conMarco = tabs.filter(function (tab) {
  return JSON.parse(elements['explorer-data'].textContent).layout[tab.dataset.tf].length > 1;
})[0] || tabs.filter(function (tab) { return tab.dataset.tf === 'H4'; })[0] || tabs[0];
conMarco.fire('click');
presets[0].fire('click');
// El paso `sin-principal` dejó apagado el impulso del gráfico: el marco cuelga
// de las mismas casillas, así que se encienden todas antes de retratarlo.
elements['impulse-layers'].children.forEach(function (wrapper) {
  wrapper.children[0].fire('change', { target: { checked: true } });
});
steps.push(snapshot('marco-por-defecto'));
elements['layer-frame'].fire('change', { target: { checked: false } });
steps.push(snapshot('marco-apagado'));
elements['layer-frame'].fire('change', { target: { checked: true } });
// El marco obedece el selector de ID visibles, igual que las líneas del ID.
const visiblesMarco = elements['visible-buttons'].children;
visiblesMarco.forEach(function (button) {
  button.fire('click');
  steps.push(snapshot('marco-ids-' + button.dataset.visible));
});
// Se devuelve el filtro a su valor de salida: los pasos de más abajo lo dan por
// supuesto y este bloque no debe cambiar el estado con el que se encuentran.
visiblesMarco.filter(function (button) { return button.dataset.visible === 'pair'; })
  .forEach(function (button) { button.fire('click'); });

// Cada gráfico lleva su marco y el de la temporalidad que le toca. Se retrata
// cada gráfico con el periodo completo para poder comprobarlo.
tabs.forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('marco-de-' + tab.dataset.tf));
});
// El gráfico vuelve a ser el que los pasos siguientes dan por supuesto.
conMarco.fire('click');

// Todos los ID a la vista y el impulso principal encendido —el paso
// `sin-principal` lo dejó apagado—: los marcadores son suyos.
visiblesMarco.filter(function (button) { return button.dataset.visible === 'all'; })
  .forEach(function (button) { button.fire('click'); });
const capaPrincipal = elements['impulse-layers'].children[0].children[0];
capaPrincipal.fire('change', { target: { checked: true } });

// Constituciones y roturas comparten capa: se apaga y se enciende para que el
// paso recorra las dos. Va aquí porque necesita lo que este bloque acaba de
// dejar puesto: el impulso principal encendido, todos los ID y el histórico
// entero a la vista.
steps.push(snapshot('marcas-por-defecto'));
elements['layer-marks'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-marcas'));
elements['layer-marks'].fire('change', { target: { checked: true } });

capaPrincipal.fire('change', { target: { checked: false } });
visiblesMarco.filter(function (button) { return button.dataset.visible === 'pair'; })
  .forEach(function (button) { button.fire('click'); });
tabs[0].fire('click');
presets[presets.length - 1].fire('click');

// Auditoría ciega (F.1): sortear con semilla, revelar, repetir y salir.
elements['blind-seed'].value = '4242';
elements['blind-seed'].fire('change', {});
elements['blind-start'].fire('click');
const ciega = snapshot('ciega');
steps.push(ciega);
elements['blind-reveal'].fire('click');
steps.push(snapshot('revelada'));
elements['blind-seed'].value = '4242';
elements['blind-seed'].fire('change', {});
elements['blind-start'].fire('click');
steps.push(snapshot('ciega-misma-semilla'));
elements['blind-exit'].fire('click');
steps.push(snapshot('fuera-de-la-ciega'));

// B.2 — filtro de ID visibles, sobre el periodo completo y en el gráfico con
// contexto: así hay muchos ID en la ventana y los tres modos se distinguen.
// Va al final del recorrido porque deja el periodo en "Todo".
tabs[0].fire('click');
presets[0].fire('click');
const visibles = elements['visible-buttons'].children;
steps.push(snapshot('ids-por-defecto'));
visibles.forEach(function (button) {
  button.fire('click');
  steps.push(snapshot('ids-' + button.dataset.visible));
});

// R-36 — alternar los tres LEG_START_MODE sobre las mismas velas, y apagar la
// capa que marca los extremos de color contrario.
elements['mode-buttons'].children.forEach(function (button) {
  button.fire('click');
  steps.push(snapshot('modo-' + button.dataset.mode));
});
// De vuelta al modo de la corrida, que es el que audita el replay.
elements['mode-buttons'].children
  .filter(function (button) { return button.dataset.mode === payloadMeta.legStartMode; })
  .forEach(function (button) { button.fire('click'); });
elements['layer-wrong'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-marca-r36'));
elements['layer-wrong'].fire('change', { target: { checked: true } });

// G.1 — replay desde una fecha: paso a paso, con la vela en formación armada
// con la temporalidad inferior y sin dibujar nada que el motor no supiera aún.
const payload = JSON.parse(elements['explorer-data'].textContent);
const h4 = tabs.filter(function (tab) { return tab.dataset.tf === 'H4'; })[0] || tabs[0];
h4.fire('click');
// Con todas las capas encendidas: el replay tiene que retrasarlas todas, no
// sólo las líneas de los impulsos.
elements['impulse-layers'].children.forEach(function (wrapper) {
  wrapper.children[0].fire('change', { target: { checked: true } });
});
elements['layer-contacts'].fire('change', { target: { checked: true } });
elements['layer-mid'].fire('change', { target: { checked: true } });
const serie = payload.bars[h4.dataset.tf].t;
const arranque = new Date(serie[Math.floor(serie.length / 2)] * 60000)
  .toISOString().slice(0, 10);
elements['replay-date'].value = arranque;
steps.push(snapshot('antes-del-replay'));
elements['replay-start'].fire('click');
steps.push(snapshot('replay-inicio'));
for (let paso = 1; paso <= 6; paso += 1) {
  elements['replay-step'].fire('click');
  steps.push(snapshot('replay-paso-' + paso));
}
elements['replay-back'].fire('click');
steps.push(snapshot('replay-atras'));
pressKey('ArrowRight');
steps.push(snapshot('replay-teclado'));

// La reproducción automática se enciende y se apaga sin dejar temporizadores
// colgando: si los dejara, este proceso no terminaría.
elements['replay-play'].fire('click');
steps.push(snapshot('replay-reproduciendo'));
elements['replay-play'].fire('click');
steps.push(snapshot('replay-pausado'));

elements['replay-forming'].fire('change', { target: { checked: false } });
steps.push(snapshot('replay-sin-formacion'));
elements['replay-step'].fire('click');
steps.push(snapshot('replay-vela-entera'));
elements['replay-forming'].fire('change', { target: { checked: true } });

// Cambiar de temporalidad no mueve el reloj del replay.
tabs[0].fire('click');
steps.push(snapshot('replay-otra-temporalidad'));
h4.fire('click');
elements['replay-exit'].fire('click');
steps.push(snapshot('replay-fuera'));

// Segunda pasada, sobre el nacimiento de un ID concreto: arranca el día de una
// constitución y avanza vela a vela hasta pasarla. Es el caso que decide si el
// replay sirve: el ID no puede estar dibujado antes de que cierre la vela que
// lo constituye.
const marcas = payload.impulses[h4.dataset.tf].constitutions;
const marca = marcas[Math.floor(marcas.length / 2)];
elements['replay-date'].value = new Date(marca.x * 60000).toISOString().slice(0, 10);
elements['replay-start'].fire('click');
elements['replay-forming'].fire('change', { target: { checked: false } });
for (let paso = 0; paso <= 7; paso += 1) {
  steps.push(snapshot('replay-nacimiento-' + paso));
  elements['replay-step'].fire('click');
}
elements['replay-exit'].fire('click');

// G.1 — el reloj es uno solo para todas las temporalidades: lo que avanzas en H4
// se tiene que ver en el diario como su vela a medio armar, y volver a H4 no
// puede devolverte al principio.
elements['replay-date'].value = arranque;
elements['replay-start'].fire('click');
elements['replay-forming'].fire('change', { target: { checked: true } });
for (let paso = 0; paso < 10; paso += 1) { elements['replay-step'].fire('click'); }
steps.push(snapshot('reloj-h4-avanzado'));
tabs[0].fire('click');
steps.push(snapshot('reloj-en-diario'));
h4.fire('click');
steps.push(snapshot('reloj-de-vuelta-en-h4'));
elements['replay-exit'].fire('click');

// El reloj no se degrada al pasar por una temporalidad de grano grueso: ir de H1
// al diario y volver tiene que devolver el mismo minuto, no el último cierre.
const h1 = tabs.filter(function (tab) { return tab.dataset.tf === 'H1'; })[0] || tabs[0];
h1.fire('click');
elements['replay-date'].value = arranque;
elements['replay-start'].fire('click');
elements['replay-forming'].fire('change', { target: { checked: true } });
for (let paso = 0; paso < 9; paso += 1) { elements['replay-step'].fire('click'); }
steps.push(snapshot('reloj-fino-h1'));
tabs[0].fire('click');
steps.push(snapshot('reloj-fino-en-diario'));
h1.fire('click');
steps.push(snapshot('reloj-fino-de-vuelta'));
elements['replay-exit'].fire('click');
h4.fire('click');

// G.2 — el encuadre hecho a mano tiene que sobrevivir a los pasos: el zoom no se
// rehace en cada dibujo y la ventana sólo se desplaza para seguir al presente.
function minuteOf(text) {
  return Math.round(Date.parse(String(text).replace(' ', 'T') + 'Z') / 60000);
}

function zoomTo(x, y) {
  elements['chart'].fire('plotly_relayout', {
    'xaxis.range[0]': new Date(x[0] * 60000).toISOString().replace('T', ' ').slice(0, 19),
    'xaxis.range[1]': new Date(x[1] * 60000).toISOString().replace('T', ' ').slice(0, 19),
    'yaxis.range[0]': y[0],
    'yaxis.range[1]': y[1],
  });
}

const spanChart = payload.spans[h4.dataset.tf];
elements['replay-date'].value = arranque;
elements['replay-start'].fire('click');
elements['replay-forming'].fire('change', { target: { checked: false } });
// Ventana corta a propósito: así el zoom ancho pide más historia de la que el
// replay recorta por su cuenta.
elements['replay-window'].fire('change', { target: { value: '40' } });
steps.push(snapshot('replay-zoom-sin-zoom'));

const presente = minuteOf(plotCalls[plotCalls.length - 1].lastBar) + spanChart;
const precios = [1.05, 1.35];
zoomTo([presente - 200 * spanChart, presente], precios);
elements['replay-step'].fire('click');
steps.push(snapshot('replay-zoom-ancho-1'));
elements['replay-step'].fire('click');
steps.push(snapshot('replay-zoom-ancho-2'));

// Encuadre estrecho pegado al presente: cada paso lo empuja hacia delante sin
// cambiar la anchura.
const ahora = minuteOf(plotCalls[plotCalls.length - 1].lastBar) + spanChart;
zoomTo([ahora - 5 * spanChart, ahora + spanChart], precios);
elements['replay-step'].fire('click');
steps.push(snapshot('replay-zoom-estrecho-1'));
elements['replay-step'].fire('click');
steps.push(snapshot('replay-zoom-estrecho-2'));
elements['replay-back'].fire('click');
steps.push(snapshot('replay-zoom-atras'));

elements['zoom-reset'].fire('click');
steps.push(snapshot('replay-zoom-suelto'));
elements['replay-exit'].fire('click');

// G.3 — escalar arrastrando sobre los ejes, como en cualquier gráfico de
// trading. La banda de la izquierda son los precios; la de abajo, las fechas.
function arrastrarEje(desde, hasta) {
  elements['chart'].fire('mousedown', {
    clientX: desde[0], clientY: desde[1],
    preventDefault() {}, stopPropagation() {},
  });
  fireDocument('mousemove', {
    clientX: hasta[0], clientY: hasta[1], preventDefault() {},
  });
  fireDocument('mouseup', {});
}

// Con un encuadre ya tomado a mano el rango de partida es conocido, así que lo
// que cambia el gesto se puede medir.
zoomTo([presente - 100 * spanChart, presente], precios);
// Un redibujo para que ese encuadre quede en la figura: `zoomTo` sólo lo
// guarda, porque el gráfico ya está pintado como el usuario acaba de dejarlo.
elements['layer-mid'].fire('change', { target: { checked: true } });
elements['layer-mid'].fire('change', { target: { checked: false } });
steps.push(snapshot('ejes-antes-de-escalar'));
// Sobre los precios y hacia abajo: se ve más rango, las velas se hacen pequeñas.
arrastrarEje([30, 300], [30, 450]);
steps.push(snapshot('eje-precios-arrastrado'));
// Sobre las fechas y hacia la izquierda: entran más velas por el mismo sitio.
arrastrarEje([600, 700], [450, 700]);
steps.push(snapshot('eje-fechas-arrastrado'));
// Y el encuadre así tomado sobrevive al siguiente dibujo, como el de la rueda.
elements['layer-mid'].fire('change', { target: { checked: true } });
steps.push(snapshot('ejes-tras-redibujar'));
elements['layer-mid'].fire('change', { target: { checked: false } });
// Un arrastre en mitad del gráfico no es este gesto y no toca la escala.
const escalados = relayoutCalls.length;
arrastrarEje([600, 300], [500, 400]);
steps.push(snapshot('centro-no-escala'));
if (relayoutCalls.length !== escalados) {
  throw new Error('arrastrar en el centro del gráfico no puede escalar los ejes');
}
elements['zoom-reset'].fire('click');

// Fuera del replay el encuadre manual también manda: encender una capa no puede
// devolver el gráfico a su sitio.
zoomTo([presente - 50 * spanChart, presente], precios);
elements['layer-mid'].fire('change', { target: { checked: true } });
steps.push(snapshot('zoom-fuera-del-replay'));
elements['layer-mid'].fire('change', { target: { checked: false } });
// Y el preset lo suelta: pedir otro tramo de historia es pedir otro sitio.
presets[presets.length - 1].fire('click');
steps.push(snapshot('zoom-suelto-por-el-preset'));

// I.1 — el simulador de entradas: dos botones que arman, un clic que planta la
// caja y arrastres que la mueven. Con el encuadre tomado a mano se sabe qué
// precio hay debajo de cada píxel, así que el recorrido puede comprobar dónde
// cae la caja y cuánto se mueve. Es dibujo del propietario: ninguna de estas
// cosas toca al motor.
tabs[0].fire('click');
presets[0].fire('click');
const spanDiario = payload.spans[tabs[0].dataset.tf];
const simX = [
  minuteOf(plotCalls[plotCalls.length - 1].lastBar) - 200 * spanDiario,
  minuteOf(plotCalls[plotCalls.length - 1].lastBar),
];
const simY = precios;
zoomTo(simX, simY);

// El mismo cálculo que hace el explorador: MARGIN sobre el div de 1200 x 720, y
// de ese alto, la franja que le queda al PRECIO. El RSI se lleva la de abajo
// (`PRICE_DOMAIN` = [0.28, 1] en el explorador), así que sin descontarla precio
// y píxel se separarían y ningún tirador caería donde se ve.
const PRICE_DOMAIN = [0.28, 1];

function pixelOf(minute, price) {
  const width = 1200 - 66 - 18;
  const height = (720 - 16 - 44) * (PRICE_DOMAIN[1] - PRICE_DOMAIN[0]);
  const top = 16 + (720 - 16 - 44) * (1 - PRICE_DOMAIN[1]);
  return {
    x: 66 + (minute - simX[0]) / (simX[1] - simX[0]) * width,
    y: top + (simY[1] - price) / (simY[1] - simY[0]) * height,
  };
}

const simButtons = elements['sim-buttons'].children;

function armar(side) {
  simButtons.filter(function (button) { return button.dataset.side === side; })
    .forEach(function (button) { button.fire('click'); });
}

function clicGrafico(point) {
  elements['chart'].fire('click', {
    clientX: point.x, clientY: point.y,
    preventDefault() {}, stopPropagation() {},
  });
}

function arrastrarCaja(desde, hasta) {
  elements['chart'].fire('mousedown', {
    clientX: desde.x, clientY: desde.y,
    preventDefault() {}, stopPropagation() {},
  });
  fireDocument('mousemove', {
    clientX: hasta.x, clientY: hasta.y, preventDefault() {},
  });
  fireDocument('mouseup', {});
}

// Las formas van numeradas por caja: `sim-1-entrada`, `sim-2-objetivo`...
function cajaSimulada(numero) {
  const shapes = plotCalls[plotCalls.length - 1].sim || [];
  const prefijo = 'sim-' + (numero || 1) + '-';
  const busca = function (name) {
    return shapes.filter(function (shape) { return shape.name === prefijo + name; })[0];
  };
  const linea = busca('entrada');
  if (!linea) { return null; }
  return {
    entry: linea.y0,
    target: busca('objetivo').y1,
    stop: busca('riesgo').y1,
    from: minuteOf(linea.x0),
    to: minuteOf(linea.x1),
  };
}

function asaDeLaCaja(price, numero) {
  const caja = cajaSimulada(numero);
  return pixelOf(Math.round((caja.from + caja.to) / 2), caja[price]);
}

const entradaSimulada = (simY[0] + simY[1]) / 2;
const minutoSimulado = simX[0] + Math.round((simX[1] - simX[0]) * 0.4);

armar('long');
steps.push(snapshot('sim-armado'));
// Escape suelta el botón sin plantar nada.
pressKey('Escape');
steps.push(snapshot('sim-desarmado'));

armar('long');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('sim-largo'));

// El borde de fuera de la caja roja mueve el stop y nada más: el objetivo se
// queda donde estaba y el R:R se vuelve a medir solo.
const asaStop = asaDeLaCaja('stop');
arrastrarCaja(asaStop, { x: asaStop.x, y: asaStop.y + 40 });
steps.push(snapshot('sim-stop-arrastrado'));

// La línea de la entrada mueve el conjunto entero: las distancias no cambian.
const asaEntrada = asaDeLaCaja('entry');
arrastrarCaja(asaEntrada, { x: asaEntrada.x, y: asaEntrada.y - 25 });
steps.push(snapshot('sim-entrada-arrastrada'));

// Arrastrar el objetivo cambia el R:R: manda la distancia que se ve.
const asaObjetivo = asaDeLaCaja('target');
arrastrarCaja(asaObjetivo, { x: asaObjetivo.x, y: asaObjetivo.y + 30 });
steps.push(snapshot('sim-objetivo-a-mano'));

// El R:R del panel se mide MIENTRAS se coloca el objetivo, no al soltarlo: se
// suelta el botón del ratón cuando el número ya dice lo que se buscaba.
const asaEnVuelo = asaDeLaCaja('target');
elements['chart'].fire('mousedown', {
  clientX: asaEnVuelo.x, clientY: asaEnVuelo.y,
  preventDefault() {}, stopPropagation() {},
});
fireDocument('mousemove', {
  clientX: asaEnVuelo.x, clientY: asaEnVuelo.y - 18, preventDefault() {},
});
const rrEnVuelo = elements['sim-rr'].textContent;
fireDocument('mouseup', {});
steps.push(Object.assign(snapshot('sim-objetivo-en-vuelo'), { simReadoutEnVuelo: rrEnVuelo }));

// En corto el objetivo va por debajo de la entrada y el riesgo por encima. Se
// planta con la larga puesta: caben dos, así que es la caja 2 y la activa.
armar('short');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('sim-corto'));

// «Quitar» se lleva UNA caja —la activa, la 2— y la larga se queda y pasa a
// ser la activa. El segundo clic la quita también.
elements['sim-clear'].fire('click');
steps.push(snapshot('sim-quitada-una'));
elements['sim-clear'].fire('click');
steps.push(snapshot('sim-quitado'));


// I.2 — la cuenta simulada: el capital, el riesgo y los tres botones que apuntan
// la caja dibujada. El encuadre es el mismo que usa el bloque de arriba y las
// cajas se plantan y se cobran sin arrastrar nada, así que su R:R es el 1:2 con
// el que nacen y las cifras se pueden comprobar a mano: 50 $ al 2 % son 1,00 $
// de riesgo y 2,00 $ de objetivo.
const resultados = elements['account-buttons'].children;

function apuntar(result) {
  resultados.filter(function (button) { return button.dataset.result === result; })
    .forEach(function (button) { button.fire('click'); });
}

function plantarCaja(side) {
  armar(side);
  clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
}

steps.push(snapshot('cuenta-sin-nada'));
plantarCaja('long');
steps.push(snapshot('cuenta-con-caja'));
apuntar('win');
steps.push(snapshot('cuenta-ganada'));
plantarCaja('long');
apuntar('loss');
steps.push(snapshot('cuenta-perdida'));
plantarCaja('long');
apuntar('be');
steps.push(snapshot('cuenta-break-even'));
elements['account-undo'].fire('click');
steps.push(snapshot('cuenta-deshecha'));
elements['account-copy'].fire('click');
steps.push(snapshot('cuenta-copiada'));

// Cambiar el capital de partida vuelve a contar la curva entera.
elements['account-initial'].fire('change', { target: { value: '100' } });
steps.push(snapshot('cuenta-capital-100'));

// Y el riesgo en dólares fijos, que no compone.
elements['account-mode'].fire('change', { target: { value: 'cash' } });
elements['account-risk'].fire('change', { target: { value: '5' } });
steps.push(snapshot('cuenta-riesgo-fijo'));

// Un riesgo imposible no se acepta y el control repone el que está puesto.
elements['account-risk'].fire('change', { target: { value: '0' } });
steps.push(snapshot('cuenta-riesgo-invalido'));

elements['account-reset'].fire('click');
steps.push(snapshot('cuenta-reiniciada'));
elements['sim-clear'].fire('click');

// Dos cajas a la vez, que es como opera el propietario: dos largas —o dos
// cortas, o una de cada—, cada una con su stop y su objetivo. Con las dos
// puestas los botones de armar se apagan; la activa es la última plantada o
// agarrada, y es la que cobra la cuenta y la que quita «Quitar».
const minutoSegundaCaja = minutoSimulado + Math.round((simX[1] - simX[0]) * 0.3);
plantarCaja('long');
armar('long');
clicGrafico(pixelOf(minutoSegundaCaja, entradaSimulada));
steps.push(snapshot('sim-dos-cajas'));
// Armar con las dos puestas no hace nada: el clic tampoco planta una tercera.
armar('short');
clicGrafico(pixelOf(minutoSegundaCaja, entradaSimulada * 0.9));
steps.push(snapshot('sim-tercera-no-cabe'));
// Agarrar la caja 1 la vuelve la activa.
const asaPrimera = asaDeLaCaja('entry', 1);
arrastrarCaja(asaPrimera, { x: asaPrimera.x, y: asaPrimera.y });
steps.push(snapshot('sim-caja-1-activa'));
// Cobrarla se lleva sólo esa: la 2 pasa a ser la 1 y la activa.
apuntar('win');
steps.push(snapshot('sim-cobrada-la-activa'));
// Deshacer la devuelve como caja 2 y activa.
elements['account-undo'].fire('click');
steps.push(snapshot('sim-devuelta-la-cobrada'));
elements['account-reset'].fire('click');
elements['sim-clear'].fire('click');
elements['sim-clear'].fire('click');
steps.push(snapshot('sim-dos-quitadas'));

// I.3 — los recuadros a mano: dos botones —OB y FVG— que arman, un clic
// que planta el suyo y arrastres que lo mueven. Se marcan varios de cada nombre y
// se quitan de uno en uno o de golpe. Es dibujo del propietario: no lo ha
// detectado el motor y no cuenta como capa.
function botonRect(kind) {
  return elements['rect-buttons'].children.filter(function (button) {
    return button.dataset.kind === kind;
  })[0];
}

function armarRect(kind) {
  botonRect(kind).fire('click');
}

function recuadros() {
  return (plotCalls[plotCalls.length - 1].rect || []).map(function (shape) {
    return {
      name: shape.name,
      high: shape.y1,
      low: shape.y0,
      from: minuteOf(shape.x0),
      to: minuteOf(shape.x1),
    };
  });
}

function asaDelRecuadro(index, borde) {
  const rect = recuadros()[index];
  const minuto = Math.round((rect.from + rect.to) / 2);
  if (borde === 'high') { return pixelOf(minuto, rect.high); }
  if (borde === 'low') { return pixelOf(minuto, rect.low); }
  return pixelOf(minuto, (rect.high + rect.low) / 2);
}

steps.push(snapshot('rect-sin-nada'));
armarRect('OB');
steps.push(snapshot('rect-armado'));
// Escape suelta el botón sin plantar nada, igual que en el simulador.
pressKey('Escape');
steps.push(snapshot('rect-desarmado'));

armarRect('OB');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('rect-plantado'));

// El borde de arriba mueve el techo y nada más.
const asaTecho = asaDelRecuadro(0, 'high');
arrastrarCaja(asaTecho, { x: asaTecho.x, y: asaTecho.y - 20 });
steps.push(snapshot('rect-techo-arrastrado'));

// Por dentro se mueve entero: el alto y el ancho no cambian.
const asaDentro = asaDelRecuadro(0, 'body');
arrastrarCaja(asaDentro, { x: asaDentro.x + 40, y: asaDentro.y + 30 });
steps.push(snapshot('rect-movido'));

// Se marcan varios y de nombres distintos: cada uno con su color.
const medioSimulado = (simY[0] + entradaSimulada) / 2;
armarRect('FVG');
clicGrafico(pixelOf(minutoSimulado, medioSimulado));
steps.push(snapshot('rect-segundo'));

// Y se numeran POR NOMBRE: el segundo OB es «OB 2» aunque entre medias haya un FVG.
armarRect('OB');
clicGrafico(pixelOf(minutoSimulado, (simY[0] + medioSimulado) / 2));
steps.push(snapshot('rect-tercero'));

elements['rect-undo'].fire('click');
steps.push(snapshot('rect-deshecho'));
elements['rect-clear'].fire('click');
steps.push(snapshot('rect-limpio'));

// I.4 — las líneas a mano: tres botones —Diario, H4 y H1— que arman, un clic
// que planta la suya HORIZONTAL al precio pulsado y arrastres que la mueven y
// la inclinan. Es dibujo del propietario: no la ha calculado el motor y no
// cuenta como capa.
function botonLinea(kind) {
  return elements['line-buttons'].children.filter(function (button) {
    return button.dataset.kind === kind;
  })[0];
}

function armarLinea(kind) {
  botonLinea(kind).fire('click');
}

function lineas() {
  return (plotCalls[plotCalls.length - 1].line || []).map(function (shape) {
    return {
      name: shape.name,
      left: shape.y0,
      right: shape.y1,
      from: minuteOf(shape.x0),
      to: minuteOf(shape.x1),
    };
  });
}

function asaDeLinea(index, extremo) {
  const line = lineas()[index];
  if (extremo === 'left') { return pixelOf(line.from, line.left); }
  if (extremo === 'right') { return pixelOf(line.to, line.right); }
  return pixelOf(
    Math.round((line.from + line.to) / 2),
    (line.left + line.right) / 2
  );
}

steps.push(snapshot('linea-sin-nada'));
armarLinea('D');
steps.push(snapshot('linea-armada'));
// Escape suelta el botón sin plantar nada, igual que en los recuadros.
pressKey('Escape');
steps.push(snapshot('linea-desarmada'));

armarLinea('D');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('linea-plantada'));

// El extremo derecho la INCLINA: sube ese lado y el izquierdo se queda.
const asaDerecha = asaDeLinea(0, 'right');
arrastrarCaja(asaDerecha, { x: asaDerecha.x, y: asaDerecha.y - 30 });
steps.push(snapshot('linea-inclinada'));

// Por dentro se mueve entera: la inclinación no cambia.
const asaTrazo = asaDeLinea(0, 'body');
arrastrarCaja(asaTrazo, { x: asaTrazo.x + 40, y: asaTrazo.y + 30 });
steps.push(snapshot('linea-movida'));

// Se trazan varias, una por temporalidad: cada una con su nombre y su color.
const precioAlto = (simY[1] + entradaSimulada) / 2;
armarLinea('H4');
clicGrafico(pixelOf(minutoSimulado, precioAlto));
steps.push(snapshot('linea-segunda'));

// Y se numeran POR NOMBRE: la segunda del Diario es «Diario 2» aunque entre
// medias haya una de H4.
armarLinea('D');
clicGrafico(pixelOf(minutoSimulado, (simY[1] + precioAlto) / 2));
steps.push(snapshot('linea-tercera'));

elements['line-undo'].fire('click');
steps.push(snapshot('linea-deshecha'));
elements['line-clear'].fire('click');
steps.push(snapshot('linea-limpia'));

// I.5 — el Fibonacci a mano: un botón que arma, DOS clics —el 0 y el 100— y los
// cinco niveles dibujados entre ellos. Es la regla del propietario: no la ha
// calculado el motor y no cuenta como capa.
function botonFib() {
  return elements['fib-buttons'].children[0];
}

function fibs() {
  return (plotCalls[plotCalls.length - 1].fib || []).map(function (shape) {
    return {
      name: shape.name,
      price: shape.y0,
      from: minuteOf(shape.x0),
      to: minuteOf(shape.x1),
      dash: shape.dash,
      label: shape.label,
    };
  });
}

function nivelFib(index, level) {
  return fibs().filter(function (shape) {
    return shape.name === 'fib-' + index + '-' + level;
  })[0];
}

steps.push(snapshot('fib-sin-nada'));
botonFib().fire('click');
steps.push(snapshot('fib-armado'));
// Escape suelta el botón sin clavar nada.
pressKey('Escape');
steps.push(snapshot('fib-desarmado'));

// El primer clic clava el 0 y el botón SIGUE armado: hasta el segundo no hay
// retroceso que medir.
const fibCero = simY[0] + (simY[1] - simY[0]) * 0.2;
const fibCien = simY[0] + (simY[1] - simY[0]) * 0.8;
botonFib().fire('click');
clicGrafico(pixelOf(minutoSimulado, fibCero));
steps.push(snapshot('fib-cero-clavado'));

// Escape con el 0 a medias lo suelta sin plantar el 100 en cualquier sitio.
pressKey('Escape');
steps.push(snapshot('fib-cero-soltado'));

botonFib().fire('click');
clicGrafico(pixelOf(minutoSimulado, fibCero));
const minutoFibCien = minutoSimulado + Math.round((simX[1] - simX[0]) * 0.2);
clicGrafico(pixelOf(minutoFibCien, fibCien));
steps.push(snapshot('fib-trazado'));

// El ancla del 100 se mueve sola: los tres retrocesos la siguen.
const asaCien = pixelOf(minutoFibCien, fibCien);
arrastrarCaja(asaCien, { x: asaCien.x, y: asaCien.y - 30 });
steps.push(snapshot('fib-cien-movido'));

// Por dentro se desplaza el conjunto entero y el retroceso medido no cambia.
const fibTrazado = nivelFib(0, 80);
const asaFib = pixelOf(
  Math.round((fibTrazado.from + fibTrazado.to) / 2), fibTrazado.price
);
arrastrarCaja(asaFib, { x: asaFib.x, y: asaFib.y + 25 });
steps.push(snapshot('fib-movido'));

// Se trazan varios y se numeran en el orden en que se plantan.
botonFib().fire('click');
clicGrafico(pixelOf(minutoSimulado, fibCien));
clicGrafico(pixelOf(minutoFibCien, fibCero));
steps.push(snapshot('fib-segundo'));

elements['fib-undo'].fire('click');
steps.push(snapshot('fib-deshecho'));
elements['fib-clear'].fire('click');
steps.push(snapshot('fib-limpio'));

// El RSI no se enciende ni se apaga: está puesto en los cuatro gráficos y sólo
// desaparece con la venda de la auditoría ciega, porque lo calcula el motor.
tabs[1].fire('click');
steps.push(snapshot('rsi-en-h4'));
tabs[0].fire('click');
steps.push(snapshot('rsi-en-diario'));

console.log(JSON.stringify({
  unknownElements: missing,
  chartTabs: tabs.map(function (tab) { return tab.textContent; }),
  chartTitles: tabs.map(function (tab) { return tab.title; }),
  presetLabels: presets.map(function (button) { return button.textContent; }),
  visibleLabels: elements['visible-buttons'].children.map(function (b) { return b.textContent; }),
  modeLabels: elements['mode-buttons'].children.map(function (b) { return b.textContent; }),
  modeTitles: elements['mode-buttons'].children.map(function (b) { return b.title; }),
  totalPlots: plotCalls.length,
  steps: steps,
}));
