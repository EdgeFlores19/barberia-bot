// Pruebas del folio copiable del chat web (static/index.html) con un DOM falso.
// Se corren con `node --test` (las lanza tests/test_front_folio.py; Node ya viene en el CI).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Texto {
  constructor(t) { this.tag = '#text'; this._t = t; }
  get textContent() { return this._t; }
}
class Nodo {
  constructor(tag) {
    this.tag = tag; this.children = []; this._texto = ''; this.className = ''; this.hidden = false;
    this.style = {}; this.attrs = {}; this.htmlAsignado = []; this.parent = null; this.onclick = null;
    this.value = ''; this.scrollTop = 0; this.scrollHeight = 0;
    this.classList = { add: (c) => { this.className += ' ' + c; } };
  }
  get textContent() { return this._texto + this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this.children = []; this._texto = String(v); }
  set innerHTML(v) { this.htmlAsignado.push(v); this.children = []; this._texto = ''; }
  appendChild(n) { n.parent = this; this.children.push(n); return n; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  focus() {}
  select() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  querySelectorAll() { return []; }
}

function cargar({ clipboard = 'ok', execOk = true } = {}) {
  const html = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'index.html'), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const porId = {}, copiados = [], comandos = [], timers = [];
  const document = {
    getElementById: (id) => (porId[id] ||= new Nodo(id)),
    createElement: (tag) => new Nodo(tag),
    createTextNode: (t) => new Texto(t),
    querySelectorAll: () => [],
    body: new Nodo('body'),
    execCommand: (c) => { comandos.push(c); return execOk; },
  };
  const navigator = clipboard === 'ok'
    ? { clipboard: { writeText: async (t) => { copiados.push(t); } } }
    : clipboard === 'denegado'
      ? { clipboard: { writeText: async () => { throw new Error('denegado'); } } }
      : {};                                                    // contexto no seguro: sin clipboard
  const ctx = vm.createContext({
    document, navigator, console, Date, Set, Promise,
    fetch: () => new Promise(() => {}),                        // /api/negocio nunca responde
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
  });
  vm.runInContext(script, ctx);
  return { ctx, document, porId, copiados, comandos, timers };
}

const botones = (burbuja) => burbuja.children.filter((c) => c.tag === 'button');
const ultimaBurbuja = (app) => {
  const filas = app.porId.chat.children;
  return filas[filas.length - 1].children[0];
};

test('partirFolios separa el texto del folio', () => {
  const { ctx } = cargar();
  const partes = ctx.partirFolios('Tu folio es AMB-7K4Z. Puedes cancelar.');
  // [...x]: los arrays del contexto aislado de vm tienen otro prototipo y deepEqual estricto los rechaza.
  assert.deepEqual([...partes.map((p) => p.folio ?? p.texto)], ['Tu folio es ', 'AMB-7K4Z', '. Puedes cancelar.']);
});

test('detecta varios folios en el mismo mensaje', () => {
  const { ctx } = cargar();
  const folios = ctx.partirFolios('De AMB-2222 a AMB-3333').filter((p) => p.folio).map((p) => p.folio);
  assert.deepEqual([...folios], ['AMB-2222', 'AMB-3333']);
});

test('el folio de ejemplo de los mensajes de ayuda no es copiable', () => {
  const { ctx } = cargar();
  assert.equal(ctx.partirFolios('Viene en la confirmación, tipo AMB-7K4Q.').some((p) => p.folio), false);
});

test('no confunde palabras ni códigos con caracteres fuera del alfabeto', () => {
  const { ctx } = cargar();
  for (const t of ['CITA-1234', 'AMB-0000', 'AMB-1I0O', 'amb-7k4z', 'AMB7K4Z', 'XAMB-7K4Z1']) {
    assert.equal(ctx.partirFolios(t).some((p) => p.folio), false, t);
  }
});

test('una burbuja del bot pinta el folio como botón y deja el texto igual', () => {
  const app = cargar();
  app.ctx.burbuja('Tu folio es AMB-7K4Z. Puedes cancelar.', 'bot');
  const b = ultimaBurbuja(app);
  assert.equal(botones(b).length, 1);
  assert.equal(botones(b)[0].textContent, 'AMB-7K4Z');
  assert.equal(botones(b)[0].attrs['aria-label'], 'Copiar folio AMB-7K4Z');
  assert.ok(b.textContent.startsWith('Tu folio es AMB-7K4Z. Puedes cancelar.'));
});

test('lo que escribe el cliente no se convierte en botón', () => {
  const app = cargar();
  app.ctx.burbuja('cancelar AMB-7K4Z', 'mio');
  assert.equal(botones(ultimaBurbuja(app)).length, 0);
});

test('tocar el folio lo copia y avisa', async () => {
  const app = cargar();
  app.ctx.burbuja('Tu folio es AMB-7K4Z.', 'bot');
  await botones(ultimaBurbuja(app))[0].onclick();
  assert.deepEqual(app.copiados, ['AMB-7K4Z']);
  assert.equal(app.porId.nota.textContent, 'Folio copiado');
  assert.equal(app.porId.nota.hidden, false);
  app.timers.at(-1).fn();                                      // pasa el tiempo del aviso
  assert.equal(app.porId.nota.hidden, true);
});

test('si el portapapeles se niega, usa el plan B', async () => {
  const app = cargar({ clipboard: 'denegado' });
  app.ctx.burbuja('Tu folio es AMB-7K4Z.', 'bot');
  await botones(ultimaBurbuja(app))[0].onclick();
  assert.deepEqual(app.comandos, ['copy']);
  assert.equal(app.porId.nota.textContent, 'Folio copiado');
});

test('en un contexto sin portapapeles también copia con el plan B', async () => {
  const app = cargar({ clipboard: 'ninguno' });
  app.ctx.burbuja('Tu folio es AMB-7K4Z.', 'bot');
  await botones(ultimaBurbuja(app))[0].onclick();
  assert.deepEqual(app.comandos, ['copy']);
});

test('si nada funciona, lo dice en vez de fingir que copió', async () => {
  const app = cargar({ clipboard: 'denegado', execOk: false });
  app.ctx.burbuja('Tu folio es AMB-7K4Z.', 'bot');
  await botones(ultimaBurbuja(app))[0].onclick();
  assert.match(app.porId.nota.textContent, /No se pudo copiar/);
});

test('el HTML dentro de un mensaje no se interpreta', () => {
  const app = cargar();
  const malo = '<img src=x onerror=alert(1)> AMB-7K4Z';
  app.ctx.burbuja(malo, 'bot');
  assert.ok(ultimaBurbuja(app).textContent.startsWith(malo));
  assert.deepEqual(app.porId.chat.htmlAsignado, []);          // nunca se asignó innerHTML
});
