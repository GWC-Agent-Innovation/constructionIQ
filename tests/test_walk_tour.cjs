const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../agent/walk_tour.js'), 'utf8'), context);
const rooms = [{ id: 'room', x: 0, y: 0, width: 20, length: 20, floor: 0 }];
const wall = (lo, hi, bottom = 0, top = 9) => ({ axis: 'v', fixed: 10, lo, hi, bottom, top, floor: 0 });
const space = (walls, windows = []) => context.createWalkSpace({ walls, windows, wall_thickness_ft: 0.5 }, rooms);

test('joystick center and dead zone produce no movement', () => {
  for (const [x, y] of [[0, 0], [2, 2]]) {
    const axes = context.joystickAxes(x, y, 40);
    assert.equal(Math.hypot(axes.x, axes.y), 0);
  }
});

test('joystick strength is proportional and clamped, even on diagonals', () => {
  const half = context.joystickAxes(20, 0, 40);
  assert.ok(half.x > 0 && half.x < 0.5);
  const full = context.joystickAxes(100, -100, 40);
  assert.ok(Math.abs(Math.hypot(full.x, full.y) - 1) < 1e-10);
  assert.ok(Math.abs(Math.hypot(full.knobX, full.knobY) - 40) < 1e-10);
  assert.ok(full.x > 0 && full.y < 0);
});

test('walking cannot tunnel through a wall, including a long frame', () => {
  const navigation = space([wall(0, 20)]);
  const point = { x: 5, z: 5 };
  navigation.move(point, 12, 0, 0);
  assert.ok(point.x < 9.5);
  assert.ok(navigation.canStand(point.x, point.z, 0));
});

test('door gaps are passable below their header', () => {
  const navigation = space([wall(0, 8), wall(11, 20), wall(8, 11, 7)]);
  const point = { x: 5, z: 9.5 };
  navigation.move(point, 10, 0, 0);
  assert.ok(Math.abs(point.x - 15) < 0.01);
});

test('windows block walking even if there is a wall opening', () => {
  const navigation = space([wall(0, 8), wall(11, 20)], [wall(8, 11, 3, 7)]);
  assert.equal(navigation.canStand(10, 9.5, 0), false);
});

test('movement slides along walls and stays inside the home', () => {
  const navigation = space([wall(0, 20)]);
  const point = { x: 9, z: 5 };
  navigation.move(point, 4, 4, 0);
  assert.ok(point.x < 9.5);
  assert.ok(point.z > 8.9);
  assert.equal(navigation.canStand(-1, 5, 0), false);
  assert.equal(navigation.canStand(5, 21, 0), false);
  assert.equal(navigation.canStand(5, 5, 1), false);
});

test('room spawn avoids a blocked center', () => {
  const navigation = space([wall(0, 20)]);
  const point = navigation.spawn(rooms[0]);
  assert.ok(point);
  assert.ok(navigation.canStand(point.x, point.z, 0));
});
