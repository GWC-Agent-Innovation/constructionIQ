const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../agent', name), 'utf8');
const geometry = vm.createContext({});
vm.runInContext(source('three_house.js'), geometry);

test('shared wall sides belong to different rooms, including across openings', () => {
  const rooms = [
    { id: 'left', name: 'Left', floor: 0, x: 0, y: 0, width: 10, length: 20 },
    { id: 'right', name: 'Right', floor: 0, x: 10, y: 0, width: 10, length: 20 },
  ];
  for (const [lo, hi, bottom] of [[0, 8, 0], [8, 11, 7], [11, 20, 0]]) {
    const [wall] = geometry.splitPaintWall({ axis: 'v', fixed: 10, lo, hi, floor: 0, bottom, top: 9 }, rooms);
    assert.equal(wall.faces[0].key, 'right:west');
    assert.equal(wall.faces[1].key, 'left:east');
  }
});

test('a continuous wall is split at room boundaries without painting neighboring rooms', () => {
  const rooms = [
    { id: 'a', name: 'A', floor: 0, x: 0, y: 0, width: 10, length: 10 },
    { id: 'b', name: 'B', floor: 0, x: 10, y: 0, width: 10, length: 10 },
    { id: 'upstairs', name: 'Upstairs', floor: 1, x: 0, y: 0, width: 20, length: 10 },
  ];
  const parts = geometry.splitPaintWall({ axis: 'h', fixed: 0, lo: 0, hi: 20, floor: 0 }, rooms);
  assert.equal(parts.length, 2);
  assert.equal(parts[0].faces[4].key, 'a:north');
  assert.equal(parts[1].faces[4].key, 'b:north');
  assert.equal(parts[0].faces[5], undefined); // Exterior face remains independent.
  assert.equal(parts.reduce((sum, p) => sum + p.hi - p.lo, 0), 20);
});

function harness(fail = false) {
  class Element {
    constructor() { this.events = {}; this.hidden = true; this.dataset = {}; this.value = '#ece6da'; this.classList = { toggle() {} }; }
    addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
    async emit(name, args = {}) { for (const fn of this.events[name] || []) await fn(args); }
    setAttribute() {}
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }
    querySelectorAll() { return [swatch]; }
  }
  const swatch = new Element(); swatch.dataset.paintColor = '#a9b8a3';
  const ids = Object.fromEntries(['paint-toggle', 'paint-panel', 'paint-wall-label', 'paint-status', 'paint-color', 'paint-color-hex', 'paint-reset', 'paint-undo', 'paint-close', 'view-plan', 'print-plan'].map(id => [id, new Element()]));
  const material = { color: { value: 'ece6da', set(color) { this.value = color.replace('#', ''); }, getHexString() { return this.value; } }, emissive: { set() {} } };
  const surface = { key: 'bed:north', roomId: 'bed', wall: 'north', label: 'Bedroom · North wall', material };
  const house = { userData: { paintSurfaces: new Map([[surface.key, surface]]) }, traverseVisible() {} };
  const hit = { object: { userData: { paintFaces: { 4: surface.key } } }, face: { materialIndex: 4 } };
  const calls = [], colors = {}, canvas = new Element();
  const context = vm.createContext({
    document: { getElementById: id => ids[id] }, location: { pathname: '/sessions/test/assets/test_3d.html' },
    THREE: { Vector2: class {}, Raycaster: class { setFromCamera() {} intersectObjects() { return [hit]; } } },
    fetch: async (url, args) => { calls.push(JSON.parse(args.body)); return { ok: !fail, json: async () => ({ detail: 'Offline' }) }; },
  });
  vm.runInContext(source('wall_paint.js'), context);
  context.createWallPainter({ camera: {}, canvas, house, furnitureGroup: { traverseVisible() {} }, wrap: new Element(), version: 9, colors });
  async function clickWall(drag = false) {
    await ids['paint-toggle'].emit('click');
    await canvas.emit('pointerdown', { button: 0, pointerId: 1, clientX: 50, clientY: 50 });
    if (drag) await canvas.emit('pointermove', { pointerId: 1, clientX: 80, clientY: 50 });
    await canvas.emit('pointerup', { pointerId: 1, clientX: drag ? 80 : 50, clientY: 50 });
  }
  return { ids, swatch, material, calls, colors, clickWall };
}

test('click selects a wall, saves a swatch and undo restores the default', async () => {
  const h = harness(); await h.clickWall();
  assert.equal(h.ids['paint-wall-label'].textContent, 'Bedroom · North wall');
  assert.equal(h.swatch.disabled, false);
  await h.swatch.emit('click');
  assert.equal(h.colors['bed:north'], '#a9b8a3');
  assert.equal(h.calls[0].layout_version, 9);
  await h.ids['paint-undo'].emit('click');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls[1].color, null);
  assert.equal(h.material.color.value, 'ece6da');
});

test('dragging to look does not select or paint a wall', async () => {
  const h = harness(); await h.clickWall(true);
  assert.equal(h.swatch.disabled, true);
  assert.equal(h.calls.length, 0);
});

test('failed save restores the old color and reports failure', async () => {
  const h = harness(true); await h.clickWall(); await h.swatch.emit('click');
  assert.equal(h.material.color.value, 'ece6da');
  assert.equal(h.colors['bed:north'], undefined);
  assert.match(h.ids['paint-status'].textContent, /Previous color restored/);
});

test('custom hex input previews and saves a valid wall color', async () => {
  const h = harness(); await h.clickWall();
  const input = h.ids['paint-color-hex'];
  input.value = '#123abc';
  await input.emit('input');
  assert.equal(h.material.color.value, '123abc');
  assert.equal(h.calls.length, 0);
  await input.emit('change');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls[0].color, '#123ABC');
  assert.equal(h.colors['bed:north'], '#123ABC');
});

test('invalid hex input does not save paint', async () => {
  const h = harness(); await h.clickWall();
  h.ids['paint-color-hex'].value = 'invalid';
  await h.ids['paint-color-hex'].emit('change');
  assert.equal(h.calls.length, 0);
  assert.equal(h.ids['paint-color-hex'].value, '#ECE6DA');
});
