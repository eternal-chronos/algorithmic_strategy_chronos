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
 'zone-layers', 'layer-zones-ul', 'layer-zones-ob', 'layer-zones-context',
 'signal-layers', 'layer-signals',
 'break-layers', 'avoided-layer', 'layer-avoided', 'steps-layer', 'layer-steps',
 'blind-seed', 'blind-start', 'blind-reveal', 'blind-exit',
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

function furthest(traces, layout) {
  const points = [];
  traces.forEach(function (trace) {
    if (!engineLayer(trace.name)) { return; }
    (trace.x || []).forEach(function (value) { if (value) { points.push(value); } });
  });
  (layout.shapes || []).forEach(function (shape) { points.push(shape.x1); });
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
          // Fase 2.0: una zona que existe va rellena; una candidata sin
          // confirmar, sólo con el contorno. La diferencia se comprueba aquí.
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
      shapes: (layout.shapes || []).length,
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
      'layer-zones-ul', 'layer-zones-ob', 'layer-zones-context', 'layer-signals',
      'layer-avoided', 'layer-steps']
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

// Fase 2.0 — las dos capas de zonas se encienden y se apagan por separado. El
// gráfico con contexto lleva dos temporalidades, así que también comprueba que
// las zonas de la superior se dibujan.
const conZonas = tabs.filter(function (tab) {
  return JSON.parse(elements['explorer-data'].textContent).layout[tab.dataset.tf].length > 1;
})[0] || tabs[0];
conZonas.fire('click');
presets[0].fire('click');
steps.push(snapshot('zonas-por-defecto'));
elements['layer-zones-ob'].fire('change', { target: { checked: false } });
steps.push(snapshot('zonas-solo-ul'));
elements['layer-zones-ul'].fire('change', { target: { checked: false } });
elements['layer-zones-ob'].fire('change', { target: { checked: true } });
steps.push(snapshot('zonas-solo-ob'));
elements['layer-zones-ul'].fire('change', { target: { checked: false } });
elements['layer-zones-ob'].fire('change', { target: { checked: false } });
steps.push(snapshot('zonas-apagadas'));
elements['layer-zones-ul'].fire('change', { target: { checked: true } });
elements['layer-zones-ob'].fire('change', { target: { checked: true } });
// H.1 — las zonas de la temporalidad de contexto se apagan solas: son dos
// rectángulos del tamaño de la pantalla y tapan el precio.
elements['layer-zones-context'].fire('change', { target: { checked: false } });
steps.push(snapshot('zonas-sin-contexto'));
elements['layer-zones-context'].fire('change', { target: { checked: true } });
// El filtro de ID visibles NO toca las zonas: UL y OB son siempre los del ID
// actual, así que los tres pasos tienen que salir iguales.
const visiblesZonas = elements['visible-buttons'].children;
visiblesZonas.forEach(function (button) {
  button.fire('click');
  steps.push(snapshot('zonas-ids-' + button.dataset.visible));
});
// Se devuelve el filtro a su valor de salida: los pasos de más abajo lo dan por
// supuesto y este bloque no debe cambiar el estado con el que se encuentran.
visiblesZonas.filter(function (button) { return button.dataset.visible === 'pair'; })
  .forEach(function (button) { button.fire('click'); });

// Fase 2.1: la capa de roturas evitadas, encendida y apagada sobre las mismas
// velas. Con `break_by_zone: false` no hay ninguna y los dos pasos salen iguales.
// Se dibujan todos los ID: con el filtro «actual + anterior» las evitadas de los
// impulsos antiguos quedan fuera y el paso no comprobaría la capa sino el filtro.
// Y se vuelve a encender el impulso principal, que el paso `sin-principal` dejó
// apagado: la capa de evitadas es sólo del principal, igual que los marcadores.
visiblesZonas.filter(function (button) { return button.dataset.visible === 'all'; })
  .forEach(function (button) { button.fire('click'); });
const capaPrincipal = elements['impulse-layers'].children[0].children[0];
capaPrincipal.fire('change', { target: { checked: true } });
steps.push(snapshot('evitadas-por-defecto'));
elements['layer-avoided'].fire('change', { target: { checked: false } });
steps.push(snapshot('evitadas-apagadas'));
elements['layer-avoided'].fire('change', { target: { checked: true } });

// Señales de zona: el toque del OB y el rechazo/rotura del UL. Se encienden y se
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
visiblesZonas.filter(function (button) { return button.dataset.visible === 'pair'; })
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
