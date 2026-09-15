// First-person navigation in feet. Door gaps remain passable; walls and windows block walking.
function createWalkSpace(data, rooms, radius = 0.3) {
  function canStand(x, z, floor) {
    if (!rooms.some(r => r.floor === floor && x >= r.x && x <= r.x + r.width && z >= r.y && z <= r.y + r.length)) return false;
    return ![...data.walls, ...data.windows].some(w => {
      if (w.floor !== floor || w.bottom >= 6 || w.top <= 0.2) return false;
      const half = data.wall_thickness_ft / 2 + radius;
      return w.axis === 'h'
        ? Math.abs(z - w.fixed) < half && x > w.lo - radius && x < w.hi + radius
        : Math.abs(x - w.fixed) < half && z > w.lo - radius && z < w.hi + radius;
    });
  }
  function move(position, dx, dz, floor) {
    // Small substeps stop a delayed frame from jumping through a wall.
    const steps = Math.max(1, Math.ceil(Math.hypot(dx, dz) / 0.1));
    for (let i = 0; i < steps; i++) {
      if (canStand(position.x + dx / steps, position.z, floor)) position.x += dx / steps;
      if (canStand(position.x, position.z + dz / steps, floor)) position.z += dz / steps;
    }
  }
  function spawn(room) {
    for (const fx of [0.5, 0.25, 0.75]) {
      for (const fz of [0.5, 0.25, 0.75]) {
        const point = { x: room.x + room.width * fx, z: room.y + room.length * fz };
        if (canStand(point.x, point.z, room.floor)) return point;
      }
    }
    return null;
  }
  return { canStand, move, spawn };
}

function joystickAxes(dx, dy, radius, deadZone = 0.12) {
  const distance = Math.hypot(dx, dy);
  if (!distance || radius <= 0) return { x: 0, y: 0, knobX: 0, knobY: 0 };
  const reach = Math.min(distance, radius);
  const strength = Math.max(0, (reach / radius - deadZone) / (1 - deadZone));
  return { x: dx / distance * strength, y: dy / distance * strength,
    knobX: dx / distance * reach, knobY: dy / distance * reach };
}

function createWalkTour({ camera, controls, canvas, wrap, data, rooms, setStatus }) {
  const button = document.getElementById('walk-tour');
  const panel = document.getElementById('walk-panel');
  const roomSelect = document.getElementById('walk-room');
  const roomName = document.getElementById('walk-current-room');
  const walkMeasurements = document.getElementById('walk-measurements');
  const measurementToggle = document.getElementById('toggle-measurements');
  const optionsToggle = document.getElementById('walk-options-toggle');
  const optionsPanel = document.getElementById('walk-options');
  function setOptions(open) {
    optionsPanel.hidden = !open;
    optionsToggle.setAttribute('aria-expanded', String(open));
  }
  const space = createWalkSpace(data, rooms);
  const keys = new Set();
  const sticks = new Map();
  const moveButtons = new Map();
  let active = false, saved, floor = 0, yaw = 0, pitch = 0, drag = null;
  const settings = ['roof', 'labels', 'measurements', 'wireframe'].map(name => document.getElementById('toggle-' + name));
  const destinations = rooms.filter(room => space.spawn(room));
  for (const room of destinations) {
    const option = document.createElement('option');
    option.value = room.id;
    option.textContent = room.name;
    roomSelect.append(option);
  }
  canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', '3D home. In walk mode, use W A S D to move and arrow keys to look.');
  const look = () => camera.rotation.set(pitch, yaw, 0, 'YXZ');
  function resetStick(stick) {
    const pointer = stick.pointer;
    stick.pointer = null;
    stick.x = stick.y = 0;
    stick.knob.style.transform = '';
    stick.el.classList.remove('is-active');
    if (pointer !== null && stick.el.hasPointerCapture(pointer)) stick.el.releasePointerCapture(pointer);
  }
  function clearInput() {
    keys.clear(); sticks.forEach(resetStick); drag = null;
    moveButtons.forEach(button => { button.pointers.clear(); button.keyboard = false; button.el.classList.remove('is-active'); });
  }
  function jump(room) {
    const point = space.spawn(room);
    if (!point) return;
    floor = room.floor;
    camera.position.set(point.x, floor * data.floor_to_floor_ft + 5.5, point.z);
    // Face a doorway when entering a room.
    const door = room.doors?.[0];
    if (door) {
      const horizontal = door.wall === 'north' || door.wall === 'south';
      const x = horizontal ? room.x + door.position * room.width : room.x + (door.wall === 'east' ? room.width : 0);
      const z = horizontal ? room.y + (door.wall === 'south' ? room.length : 0) : room.y + door.position * room.length;
      yaw = Math.atan2(-(x - point.x), -(z - point.z));
    } else yaw = 0;
    pitch = 0;
    roomSelect.value = room.id;
    roomName.textContent = room.name;
    roomName.title = room.name;
    setOptions(false);
    clearInput();
    look();
    canvas.focus({ preventScroll: true });
  }
  function enter() {
    if (active || !destinations.length) return;
    controls.update();
    saved = { position: camera.position.clone(), quaternion: camera.quaternion.clone(),
      target: controls.target.clone(), fov: camera.fov, near: camera.near,
      settings: settings.map(el => ({ checked: el.checked, disabled: el.disabled })) };
    active = true;
    controls.enabled = false;
    camera.fov = 70;
    camera.near = 0.05;
    camera.updateProjectionMatrix();
    settings.forEach(el => {
      el.checked = el.id === 'toggle-roof';
      el.dispatchEvent(new Event('change'));
      el.disabled = true;
    });
    panel.hidden = false;
    wrap.classList.add('is-walking');
    button.textContent = 'Exit walk';
    button.setAttribute('aria-pressed', 'true');
    jump(destinations.find(r => r.doors?.some(d => d.connects_to === 'outside')) || destinations[0]);
    setStatus('Walk inside · Hold arrows: move · Right stick: look · Esc to exit', false);
  }
  function exit() {
    if (!active) return;
    active = false;
    clearInput();
    panel.hidden = true;
    setOptions(false);
    wrap.classList.remove('is-walking');
    button.textContent = 'Walk inside';
    button.setAttribute('aria-pressed', 'false');
    settings.forEach((el, i) => {
      el.checked = saved.settings[i].checked;
      el.disabled = saved.settings[i].disabled;
      el.dispatchEvent(new Event('change'));
    });
    camera.position.copy(saved.position);
    camera.quaternion.copy(saved.quaternion);
    camera.fov = saved.fov;
    camera.near = saved.near;
    camera.updateProjectionMatrix();
    controls.target.copy(saved.target);
    controls.enabled = true;
    controls.update();
    setStatus('Drag to orbit', false);
  }
  button.disabled = !destinations.length || wrap.hidden;
  button.addEventListener('click', () => active ? exit() : enter());
  document.getElementById('walk-exit').addEventListener('click', exit);
  optionsToggle.addEventListener('click', () => setOptions(optionsPanel.hidden));
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.walk-info')) setOptions(false);
  });
  walkMeasurements.addEventListener('change', () => {
    if (!active) return;
    measurementToggle.checked = walkMeasurements.checked;
    measurementToggle.dispatchEvent(new Event('change'));
  });
  measurementToggle.addEventListener('change', () => {
    walkMeasurements.checked = measurementToggle.checked;
  });
  roomSelect.addEventListener('change', () => jump(destinations.find(r => r.id === roomSelect.value)));
  for (const id of ['top-view', 'reset-view', 'view-plan', 'print-plan']) {
    document.getElementById(id).addEventListener('click', exit, true);
  }
  document.getElementById('ai-composer').addEventListener('submit', exit, true);
  window.addEventListener('beforeprint', exit);
  window.addEventListener('blur', clearInput);
  document.addEventListener('visibilitychange', clearInput);
  document.addEventListener('focusin', event => { if (event.target !== canvas) clearInput(); });
  document.addEventListener('keydown', event => {
    if (!active) return;
    if (event.key === 'Escape') {
      if (!optionsPanel.hidden) { setOptions(false); optionsToggle.focus(); }
      else { exit(); button.focus(); }
      return;
    }
    if (document.activeElement !== canvas && !document.activeElement?.matches('[data-stick], [data-move]')) return;
    if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.code)) {
      event.preventDefault();
      keys.add(event.code);
    }
  });
  document.addEventListener('keyup', event => keys.delete(event.code));
  canvas.addEventListener('pointerdown', event => {
    if (!active || event.button !== 0 || drag) return;
    canvas.focus({ preventScroll: true });
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', event => {
    if (!active || drag?.id !== event.pointerId) return;
    yaw -= (event.clientX - drag.x) * 0.004;
    pitch = Math.max(-1.3, Math.min(1.3, pitch - (event.clientY - drag.y) * 0.004));
    drag.x = event.clientX; drag.y = event.clientY;
    look();
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    canvas.addEventListener(name, () => { drag = null; });
  }
  panel.querySelectorAll('[data-move]').forEach(el => {
    const button = { el, pointers: new Set(), keyboard: false };
    moveButtons.set(el.dataset.move, button);
    const sync = () => el.classList.toggle('is-active', button.keyboard || button.pointers.size > 0);
    el.addEventListener('pointerdown', event => {
      if (!active || event.button !== 0) return;
      event.preventDefault();
      button.pointers.add(event.pointerId);
      el.setPointerCapture(event.pointerId);
      sync();
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      el.addEventListener(name, event => { button.pointers.delete(event.pointerId); sync(); });
    }
    el.addEventListener('keydown', event => {
      if (!active || ![' ', 'Enter'].includes(event.key)) return;
      event.preventDefault(); button.keyboard = true; sync();
    });
    el.addEventListener('keyup', event => {
      if (![' ', 'Enter'].includes(event.key)) return;
      event.preventDefault(); button.keyboard = false; sync();
    });
    el.addEventListener('blur', () => { button.keyboard = false; sync(); });
  });
  panel.querySelectorAll('[data-stick]').forEach(el => {
    const stick = { el, knob: el.querySelector('.walk-stick-knob'), pointer: null, x: 0, y: 0 };
    sticks.set(el.dataset.stick, stick);
    function position(event) {
      const rect = el.getBoundingClientRect();
      const axes = joystickAxes(event.clientX - rect.left - rect.width / 2,
        event.clientY - rect.top - rect.height / 2, (rect.width - stick.knob.offsetWidth) / 2 - 5);
      stick.x = axes.x; stick.y = axes.y;
      stick.knob.style.transform = `translate(${axes.knobX}px, ${axes.knobY}px)`;
    }
    el.addEventListener('pointerdown', event => {
      if (!active || event.button !== 0 || stick.pointer !== null) return;
      event.preventDefault();
      stick.pointer = event.pointerId;
      el.setPointerCapture(event.pointerId);
      el.classList.add('is-active');
      position(event);
    });
    el.addEventListener('pointermove', event => {
      if (stick.pointer !== event.pointerId) return;
      position(event);
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      el.addEventListener(name, event => { if (stick.pointer === event.pointerId) resetStick(stick); });
    }
  });
  function update(dt) {
    if (!active) return;
    dt = Math.min(dt, 0.05);
    const down = name => keys.has(name) || Boolean(moveButtons.get(name)?.keyboard || moveButtons.get(name)?.pointers.size);
    const lookStick = sticks.get('look');
    yaw += ((down('ArrowLeft') ? 1 : 0) - (down('ArrowRight') ? 1 : 0) - lookStick.x) * dt * 1.6;
    pitch = Math.max(-1.3, Math.min(1.3, pitch + ((down('ArrowUp') ? 1 : 0) - (down('ArrowDown') ? 1 : 0) - lookStick.y) * dt));
    look();
    const forward = (down('KeyW') ? 1 : 0) - (down('KeyS') ? 1 : 0);
    const side = (down('KeyD') ? 1 : 0) - (down('KeyA') ? 1 : 0);
    const speed = 4 * dt / Math.max(1, Math.hypot(forward, side));
    space.move(camera.position, (-Math.sin(yaw) * forward + Math.cos(yaw) * side) * speed,
      (-Math.cos(yaw) * forward - Math.sin(yaw) * side) * speed, floor);
    const room = rooms.find(r => r.floor === floor && camera.position.x >= r.x && camera.position.x <= r.x + r.width && camera.position.z >= r.y && camera.position.z <= r.y + r.length);
    if (room) { roomName.textContent = room.name; roomName.title = room.name; roomSelect.value = room.id; }
  }
  return { update, exit };
}
