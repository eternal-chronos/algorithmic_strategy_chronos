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
 'mode-group', 'impulse-layers',
 'prev', 'next',
 'from', 'to', 'layer-limbo', 'layer-marks', 'layer-contacts', 'layer-mid', 'layer-wrong',
 'blind-seed', 'blind-start', 'blind-reveal', 'blind-exit',
 'notes', 'explorer-data'].forEach(declare);

elements['explorer-data'].textContent = fs.readFileSync(payloadPath, 'utf8');

const viewButtons = ['candles', 'line'].map(function (view) {
  const button = makeElement('view-' + view);
  button.dataset.view = view;
  return button;
});

const documentListeners = {};

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
    if (selector === '#view-buttons button') { return viewButtons; }
    missing.push(selector);
    return [];
  },
};

function pressKey(key, focusedTag) {
  global.document.activeElement = focusedTag ? { tagName: focusedTag } : null;
  (documentListeners['keydown'] || []).forEach(function (handler) {
    handler({ key: key, preventDefault() {} });
  });
  global.document.activeElement = null;
}

global.Plotly = {
  react(target, traces, layout) {
    plotCalls.push({
      target: target,
      traces: traces.map(function (trace) {
        return {
          name: trace.name,
          type: trace.type,
          points: (trace.x && trace.x.length) || 0,
          dash: (trace.line && trace.line.dash) || null,
        };
      }),
      bars: (traces[0] && traces[0].x && traces[0].x.length) || 0,
      firstBar: traces[0] && traces[0].x && traces[0].x[0],
      lastBar: traces[0] && traces[0].x && traces[0].x[traces[0].x.length - 1],
      hover: traces[0] && traces[0].text && traces[0].text[0],
      shapes: (layout.shapes || []).length,
      yTickFormat: layout.yaxis && layout.yaxis.tickformat,
    });
  },
};

function snapshot(label) {
  return {
    label: label,
    plot: plotCalls[plotCalls.length - 1],
    notes: elements['notes'].textContent,
    from: elements['from'].value,
    to: elements['to'].value,
    layerLabels: elements['impulse-layers'].children.map(function (wrapper) {
      return wrapper.children.map(function (child) { return child.text || ''; }).join('');
    }),
    visibleMode: (elements['visible-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.visible || null,
    legStartMode: (elements['mode-buttons'].children.filter(function (button) {
      return button.getAttribute('aria-pressed') === 'true';
    })[0] || {}).dataset?.mode || null,
  };
}

global.window = global;
eval(fs.readFileSync(scriptPath, 'utf8'));

const steps = [];
const tabs = elements['tf-buttons'].children;
const presets = elements['preset-buttons'].children;

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
elements['layer-wrong'].fire('change', { target: { checked: false } });
steps.push(snapshot('sin-marca-r36'));
elements['layer-wrong'].fire('change', { target: { checked: true } });

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
