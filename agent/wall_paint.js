function createWallPainter({ camera, canvas, house, furnitureGroup, wrap, version, colors }) {
  const toggle = document.getElementById('paint-toggle');
  const panel = document.getElementById('paint-panel');
  const label = document.getElementById('paint-wall-label');
  const status = document.getElementById('paint-status');
  const picker = document.getElementById('paint-color');
  const hexInput = document.getElementById('paint-color-hex');
  const reset = document.getElementById('paint-reset');
  const undo = document.getElementById('paint-undo');
  const swatches = [...panel.querySelectorAll('[data-paint-color]')];
  const surfaces = house.userData.paintSurfaces;
  const raycaster = new THREE.Raycaster();
  const sessionId = location.pathname.match(/\/sessions\/([^/]+)\/assets\//)?.[1];
  const history = [];
  let selected = null, down = null, saving = false;
  const defaultColor = '#ece6da';
  function sync() {
    picker.disabled = hexInput.disabled = reset.disabled = !selected || saving;
    swatches.forEach(button => {
      button.disabled = !selected || saving;
      button.setAttribute('aria-pressed', String(!!selected && button.dataset.paintColor === picker.value));
    });
    undo.disabled = !history.length || saving;
    panel.setAttribute('aria-busy', String(saving));
  }
  function select(key) {
    if (selected) {
      selected.material.emissive.set(0x000000);
      if (!saving) selected.material.color.set(colors[selected.key] || defaultColor);
    }
    selected = surfaces.get(key) || null;
    if (selected) {
      selected.material.emissive.set(0x17242a);
      picker.value = hexInput.value = '#' + selected.material.color.getHexString().toUpperCase();
      label.textContent = selected.label;
      status.textContent = 'Choose a color for this side of the wall.';
    } else {
      label.textContent = 'Select a wall';
      status.textContent = 'Click an interior wall. Drag to look around.';
    }
    sync();
  }
  function open(active) {
    panel.hidden = !active;
    toggle.setAttribute('aria-pressed', String(active));
    wrap.classList.toggle('is-painting', active);
    if (!active) select(null);
  }
  async function save(surface, color, isUndo = false) {
    if (!surface || saving) return;
    const previous = colors[surface.key] || null;
    surface.material.color.set(color || defaultColor);
    picker.value = hexInput.value = (color || defaultColor).toUpperCase();
    saving = true; sync();
    status.textContent = 'Saving paint…';
    try {
      if (!sessionId) throw new Error('Open this project in ConstructIQ to save wall colors.');
      const response = await fetch(`/sessions/${encodeURIComponent(sessionId)}/wall-paint`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_version: version, room_id: surface.roomId, wall: surface.wall, color }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not save paint.');
      if (color) colors[surface.key] = color; else delete colors[surface.key];
      if (isUndo) history.pop();
      else { history.push({ key: surface.key, color: previous }); if (history.length > 20) history.shift(); }
      status.textContent = 'Saved to project';
    } catch (error) {
      surface.material.color.set(previous || defaultColor);
      if (selected === surface) picker.value = hexInput.value = (previous || defaultColor).toUpperCase();
      status.textContent = error.message + ' Previous color restored.';
    } finally { saving = false; sync(); }
  }
  toggle.disabled = false;
  toggle.addEventListener('click', () => open(panel.hidden));
  document.getElementById('paint-close').addEventListener('click', () => open(false));
  for (const id of ['view-plan', 'print-plan']) document.getElementById(id).addEventListener('click', () => open(false));
  picker.addEventListener('input', () => {
    if (selected && !saving) { selected.material.color.set(picker.value); hexInput.value = picker.value.toUpperCase(); sync(); }
  });
  picker.addEventListener('change', () => save(selected, picker.value));
  const hexPattern = /^#[0-9A-Fa-f]{6}$/;
  hexInput.addEventListener('input', () => {
    if (selected && !saving && hexPattern.test(hexInput.value)) {
      selected.material.color.set(hexInput.value);
      picker.value = hexInput.value;
      sync();
    }
  });
  function commitHexInput() {
    if (!selected || saving) return;
    if (hexPattern.test(hexInput.value)) save(selected, hexInput.value.toUpperCase());
    else hexInput.value = picker.value.toUpperCase(); // invalid text: revert to the last applied color
  }
  hexInput.addEventListener('change', commitHexInput);
  hexInput.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); commitHexInput(); } });
  swatches.forEach(button => button.addEventListener('click', () => save(selected, button.dataset.paintColor)));
  reset.addEventListener('click', () => save(selected, null));
  undo.addEventListener('click', () => {
    const last = history[history.length - 1];
    if (last) { select(last.key); save(selected, last.color, true); }
  });
  canvas.addEventListener('pointerdown', event => {
    if (!panel.hidden && event.button === 0) down = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
  });
  canvas.addEventListener('pointermove', event => {
    if (down?.id === event.pointerId && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) down.moved = true;
  });
  canvas.addEventListener('pointercancel', () => { down = null; });
  canvas.addEventListener('pointerup', event => {
    const start = down; down = null;
    if (panel.hidden || saving || !start || start.id !== event.pointerId || start.moved) return;
    const rect = canvas.getBoundingClientRect();
    raycaster.setFromCamera(new THREE.Vector2(
      (event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
    // Include occluders: clicking furniture/glass must not paint a wall behind it.
    const meshes = [];
    for (const root of [house, furnitureGroup]) root.traverseVisible(obj => { if (obj.isMesh) meshes.push(obj); });
    const hit = raycaster.intersectObjects(meshes, false)[0];
    select(hit?.object.userData.paintFaces?.[hit.face?.materialIndex]);
  });
  sync();
}
