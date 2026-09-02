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
 'layer-frame',
 'signal-layers', 'layer-signals',
 'cascade-layers', 'layer-cascade',
 'break-layers', 'avoided-layer', 'layer-avoided', 'steps-layer', 'layer-steps',
 'blind-seed', 'blind-start', 'blind-reveal', 'blind-exit',
 'sim-group', 'sim-buttons', 'sim-ratio', 'sim-clear',
 'rect-group', 'rect-buttons', 'rect-undo', 'rect-clear',
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
    if (selector === '#sim-ratio button') { return elements['sim-ratio'].children; }
    if (selector === '#rect-buttons button') { return elements['rect-buttons'].children; }
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

// I.3 — los recuadros de PUL, UL y APUL los planta el propietario a mano, igual
// que la caja simulada: tampoco son capa del motor.
function rectShape(shape) {
  return String(shape.name || '').indexOf('rect-') === 0;
}

function handDrawn(shape) {
  return simShape(shape) || rectShape(shape);
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
          // Fase 2.0: el tramo en que la zona es del ID va relleno y el que va
          // desde su vela definitoria, sólo con el contorno. Se comprueba aquí.
          fill: trace.fill || null,
          fillcolor: trace.fillcolor || null,
          captions: trace.text && trace.text.length <= 200 ? trace.text.slice() : null,
          xs: trace.mode === 'markers' && (trace.x || []).length <= 200
            ? trace.x.slice()
            : null,
          // Fase 2.1 (§3.2): la línea del extremo va en ESCALERA, así que «cuántos
          // puntos tiene» no dice nada. Para comprobar QUÉ nivel se dibuja en CADA
          // tramo hace falta el trazo entero: cada segmento es (x0, x1, precio).
          segments: trace.mode === 'lines' && (trace.x || []).length <= 3000
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
    simRatio: (elements['sim-ratio'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.ratio || null,
    simCursor: elements['chart'].style.cursor || '',
    // I.3 — los recuadros a mano: qué botón espera el clic y si hay algo que quitar.
    rectArmed: (elements['rect-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.kind || null,
    rectUndoDisabled: elements['rect-undo'].disabled === true,
    rectClearDisabled: elements['rect-clear'].disabled === true,
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
      'layer-frame', 'layer-signals',
      'layer-cascade', 'layer-avoided', 'layer-steps']
      .reduce(function (state, id) {
        state[id] = elements[id].checked === true;
        return state;
      }, {}),
  };
}

global.window = global;
eval(fs.readFileSync(scriptPath, 'utf8'));

const payloadMeta = JSON.parse(elements['explorer-data'].textContent).meta;
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

// Atajos de temporalidad: d/4/1/m saltan de gráfico, salvo con el foco en un
// campo de texto o con una tecla modificadora (Ctrl+D es del navegador).
pressKey('4');
steps.push(snapshot('teclado-tf-h4'));
pressKey('m');
steps.push(snapshot('teclado-tf-m15'));
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

// El MARCO del ID: el recuadro de la constitución a la muerte y del ancla al
// extremo. Se enciende y se apaga sobre las mismas velas. El gráfico con
// contexto lleva dos temporalidades, así que también comprueba que el marco de
// la superior se dibuja, y con su color.
const conMarco = tabs.filter(function (tab) {
  return JSON.parse(elements['explorer-data'].textContent).layout[tab.dataset.tf].length > 1;
})[0] || tabs[0];
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

// Cada gráfico lleva su marco y el de la temporalidad que le toca: el del Diario
// sobre H4, el de H4 sobre H1 y el de H1 sobre M15. Se retrata cada gráfico con
// el periodo completo para poder comprobarlo.
tabs.forEach(function (tab) {
  tab.fire('click');
  steps.push(snapshot('marco-de-' + tab.dataset.tf));
});
// El gráfico vuelve a ser el que los pasos siguientes dan por supuesto.
conMarco.fire('click');

// Fase 2.1: la capa de roturas evitadas, encendida y apagada sobre las mismas
// velas. Con `break_by_zone: false` no hay ninguna y los dos pasos salen iguales.
// Se dibujan todos los ID: con el filtro «actual + anterior» las evitadas de los
// impulsos antiguos quedan fuera y el paso no comprobaría la capa sino el filtro.
// Y se vuelve a encender el impulso principal, que el paso `sin-principal` dejó
// apagado: la capa de evitadas es sólo del principal, igual que los marcadores.
visiblesMarco.filter(function (button) { return button.dataset.visible === 'all'; })
  .forEach(function (button) { button.fire('click'); });
const capaPrincipal = elements['impulse-layers'].children[0].children[0];
capaPrincipal.fire('change', { target: { checked: true } });

// Constituciones, roturas y el reloj de arena de las constituciones abortadas
// comparten capa: se apaga y se enciende para que el paso recorra las tres. Va
// aquí porque necesita las tres cosas que este bloque acaba de dejar puestas: el
// impulso principal encendido (los marcadores son suyos), todos los ID y el
// histórico entero a la vista. Las constituciones abortadas son raras y con una
// ventana corta no cae ninguna.
steps.push(snapshot('marcas-por-defecto'));
elements['layer-marks'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-marcas'));
elements['layer-marks'].fire('change', { target: { checked: true } });
steps.push(snapshot('evitadas-por-defecto'));
elements['layer-avoided'].fire('change', { target: { checked: false } });
steps.push(snapshot('evitadas-apagadas'));
elements['layer-avoided'].fire('change', { target: { checked: true } });

// Señales de zona: el toque del PUL y el rechazo/rotura del UL. Se encienden y se
// apagan sobre las mismas velas; sin zonas en el payload los dos pasos salen
// iguales, que es lo que comprueba el test de la corrida sin zonas.
steps.push(snapshot('senales-por-defecto'));
elements['layer-signals'].fire('change', { target: { checked: false } });
steps.push(snapshot('senales-apagadas'));
elements['layer-signals'].fire('change', { target: { checked: true } });

// Fase 2.1 (§3.2): la escalera del extremo. Los saltos son una capa propia que se
// apaga; los ESCALONES no, porque no son una capa sino la línea del ID dibujada
// como fue. Los dos pasos siguientes lo separan.
steps.push(snapshot('escalera-por-defecto'));
elements['layer-steps'].fire('change', { target: { checked: false } });
steps.push(snapshot('escalera-sin-saltos'));
elements['layer-steps'].fire('change', { target: { checked: true } });
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

// La cascada H4 -> H1, con el Diario de veto. Se recorren los cuatro gráficos
// porque cada paso se dibuja en el suyo: el tramo diario en el Diario, el toque
// de H4 en H4 y la confirmación en H1, con su caja si es el PUL de H1. Encendida
// y apagada sobre las mismas velas; sin cascada en
// el payload los dos pasos salen iguales, que es lo que comprueba el test de la
// corrida sin ella. Va con el periodo completo y todos los ID a la vista, que es
// como lo deja el bloque de arriba.
tabs.forEach(function (tab) {
  tab.fire('click');
  // La capa cuelga del impulso principal del gráfico, y algún paso de más arriba
  // pudo apagarlo: se encienden todas antes de mirar, que es lo que se quiere
  // probar aquí.
  elements['impulse-layers'].children.forEach(function (wrapper) {
    wrapper.children[0].fire('change', { target: { checked: true } });
  });
  steps.push(snapshot('cascada-' + tab.dataset.tf));
  elements['layer-cascade'].fire('change', { target: { checked: false } });
  steps.push(snapshot('cascada-apagada-' + tab.dataset.tf));
  elements['layer-cascade'].fire('change', { target: { checked: true } });
});
tabs[0].fire('click');

// R-36 — alternar los tres LEG_START_MODE sobre las mismas velas, y apagar la
// capa que marca los extremos de color contrario.
elements['mode-buttons'].children.forEach(function (button) {
  button.fire('click');
  steps.push(snapshot('modo-' + button.dataset.mode));
});
// De vuelta al modo de la corrida: es el único que lleva zonas, y los pasos del
// replay tienen que poder comprobar que tampoco se adelantan.
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

// El mismo cálculo que hace el explorador: MARGIN sobre el div de 1200 x 720.
function pixelOf(minute, price) {
  const width = 1200 - 66 - 18;
  const height = 720 - 16 - 44;
  return {
    x: 66 + (minute - simX[0]) / (simX[1] - simX[0]) * width,
    y: 16 + (simY[1] - price) / (simY[1] - simY[0]) * height,
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

function cajaSimulada() {
  const shapes = plotCalls[plotCalls.length - 1].sim || [];
  const busca = function (name) {
    return shapes.filter(function (shape) { return shape.name === name; })[0];
  };
  const linea = busca('sim-entrada');
  if (!linea) { return null; }
  return {
    entry: linea.y0,
    target: busca('sim-objetivo').y1,
    stop: busca('sim-riesgo').y1,
    from: minuteOf(linea.x0),
    to: minuteOf(linea.x1),
  };
}

function asaDeLaCaja(price) {
  const caja = cajaSimulada();
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

// El borde de fuera de la caja roja mueve el stop y nada más.
const asaStop = asaDeLaCaja('stop');
arrastrarCaja(asaStop, { x: asaStop.x, y: asaStop.y + 40 });
steps.push(snapshot('sim-stop-arrastrado'));

// La línea de la entrada mueve el conjunto entero: las distancias no cambian.
const asaEntrada = asaDeLaCaja('entry');
arrastrarCaja(asaEntrada, { x: asaEntrada.x, y: asaEntrada.y - 25 });
steps.push(snapshot('sim-entrada-arrastrada'));

// El R:R fijo: 1:3 recoloca el objetivo sin tocar el stop, y con el candado
// puesto mover el stop arrastra el objetivo con él.
const ratios = elements['sim-ratio'].children;

function fijarRatio(value) {
  ratios.filter(function (button) { return button.dataset.ratio === value; })
    .forEach(function (button) { button.fire('click'); });
}

fijarRatio('3');
steps.push(snapshot('sim-ratio-1-3'));

const asaStopConCandado = asaDeLaCaja('stop');
arrastrarCaja(asaStopConCandado, { x: asaStopConCandado.x, y: asaStopConCandado.y - 20 });
steps.push(snapshot('sim-stop-con-candado'));

// Arrastrar el objetivo suelta el candado: manda lo que se ve.
const asaObjetivo = asaDeLaCaja('target');
arrastrarCaja(asaObjetivo, { x: asaObjetivo.x, y: asaObjetivo.y + 30 });
steps.push(snapshot('sim-objetivo-a-mano'));

// En corto el objetivo va por debajo de la entrada y el riesgo por encima.
fijarRatio('4');
armar('short');
clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
steps.push(snapshot('sim-corto'));

elements['sim-clear'].fire('click');
steps.push(snapshot('sim-quitado'));


// I.2 — la cuenta simulada: el capital, el riesgo y los tres botones que apuntan
// la caja dibujada. El encuadre y el ratio son los mismos que usa el bloque de
// arriba, así que el R:R de cada caja es 1:2 exacto y las cifras se pueden
// comprobar a mano: 50 $ al 2 % son 1,00 $ de riesgo y 2,00 $ de objetivo.
const resultados = elements['account-buttons'].children;

function apuntar(result) {
  resultados.filter(function (button) { return button.dataset.result === result; })
    .forEach(function (button) { button.fire('click'); });
}

function plantarCaja(side) {
  armar(side);
  clicGrafico(pixelOf(minutoSimulado, entradaSimulada));
}

// Fijar un R:R sin caja plantada deja el ratio puesto para la siguiente y no
// puede romper nada: es el gesto natural antes de dibujar.
fijarRatio('2');
steps.push(snapshot('ratio-sin-caja'));
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

// I.3 — los recuadros a mano: tres botones —PUL, UL y APUL— que arman, un clic
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
armarRect('PUL');
steps.push(snapshot('rect-armado'));
// Escape suelta el botón sin plantar nada, igual que en el simulador.
pressKey('Escape');
steps.push(snapshot('rect-desarmado'));

armarRect('PUL');
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
armarRect('UL');
clicGrafico(pixelOf(minutoSimulado, medioSimulado));
steps.push(snapshot('rect-segundo'));

// Y se numeran POR NOMBRE: el segundo PUL es «PUL 2» aunque entre medias haya un UL.
armarRect('PUL');
clicGrafico(pixelOf(minutoSimulado, (simY[0] + medioSimulado) / 2));
steps.push(snapshot('rect-tercero'));

elements['rect-undo'].fire('click');
steps.push(snapshot('rect-deshecho'));
elements['rect-clear'].fire('click');
steps.push(snapshot('rect-limpio'));

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
