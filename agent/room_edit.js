function createRoomEditor({ sessionId, layoutVersion }) {
  const toggle = document.getElementById('room-edit-toggle');
  const status = document.getElementById('room-edit-status');
  const panel = document.getElementById('plan-view');
  const svg = panel.querySelector('svg');
  if (!toggle || !svg) return;
  const scale = parseFloat(svg.dataset.scale || '24');
  let version = layoutVersion;
  let active = false, saving = false, drag = null, swapSource = null;

  function setStatus(text, isError) {
    status.textContent = text || '';
    status.classList.toggle('is-error', !!isError);
  }

  function clearSelection() {
    if (swapSource) svg.querySelector(`.room-hit[data-room="${swapSource}"]`)?.classList.remove('is-selected');
    swapSource = null;
  }

  function setActive(value) {
    active = value;
    toggle.setAttribute('aria-pressed', String(active));
    svg.classList.toggle('is-editing', active);
    clearSelection();
    setStatus(active ? 'Drag a room edge to resize it, or click two rooms to swap them.' : '');
  }

  async function postEdit(body) {
    if (saving) return;
    saving = true;
    setStatus('Saving…');
    try {
      const response = await fetch(`/sessions/${encodeURIComponent(sessionId)}/room-edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_version: version, ...body }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = result.detail;
        const message = typeof detail === 'string' ? detail
          : Array.isArray(detail?.issues) ? detail.issues.join(' ') : 'Could not apply that change.';
        throw new Error(message);
      }
      setStatus('Saved — reloading the updated plan…');
      window.location.reload();
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      saving = false;
    }
  }

  toggle.addEventListener('click', () => setActive(!active));

  svg.querySelectorAll('.room-edge').forEach(edge => {
    edge.style.cursor = { north: 'ns-resize', south: 'ns-resize', east: 'ew-resize', west: 'ew-resize' }[edge.dataset.edge];
    edge.addEventListener('pointerdown', event => {
      if (!active || saving) return;
      event.preventDefault();
      event.stopPropagation();
      drag = { room: edge.dataset.room, edge: edge.dataset.edge, startX: event.clientX, startY: event.clientY,
               pointerId: event.pointerId };
      edge.setPointerCapture(event.pointerId);
      setStatus(`Resizing ${edge.dataset.room}…`);
    });
  });
  svg.addEventListener('pointerup', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = (event.clientX - drag.startX) / scale;
    const dy = (event.clientY - drag.startY) / scale;
    const deltaFt = { east: dx, west: -dx, south: dy, north: -dy }[drag.edge];
    const body = { action: 'resize', room_id: drag.room, edge: drag.edge };
    const startedRoom = drag.room;
    drag = null;
    if (Math.abs(deltaFt) < 0.25) {
      setStatus('Drag further to resize that wall.');
      return;
    }
    postEdit({ ...body, delta_ft: Math.round(deltaFt * 10) / 10 });
  });
  svg.addEventListener('pointercancel', () => { drag = null; });

  svg.querySelectorAll('.room-hit').forEach(hit => {
    hit.style.cursor = 'pointer';
    hit.addEventListener('pointerdown', event => {
      if (!active || saving || drag) return;
      event.stopPropagation();
      const roomId = hit.dataset.room;
      if (!swapSource) {
        swapSource = roomId;
        hit.classList.add('is-selected');
        setStatus(`Selected ${roomId}. Click another room to swap with it.`);
        return;
      }
      if (swapSource === roomId) {
        clearSelection();
        setStatus('Selection cleared.');
        return;
      }
      const other = swapSource;
      clearSelection();
      postEdit({ action: 'swap', room_id: roomId, other_room_id: other });
    });
  });

  setActive(false);
}
