// Layout coordinates are feet: X runs east, Z runs south, Y is elevation.
function buildHouse(data) {
  const house = new THREE.Group();
  const wall = new THREE.MeshStandardMaterial({ color: 0xece6da, roughness: 0.85 });
  const floor = new THREE.MeshStandardMaterial({ color: 0xa99780, roughness: 0.9 });
  const roof = new THREE.MeshStandardMaterial({ color: 0x62716d, roughness: 0.85 });
  const frame = new THREE.MeshStandardMaterial({ color: 0x344a49, roughness: 0.55 });
  const glass = new THREE.MeshStandardMaterial({
    color: 0x93cfe0, transparent: true, opacity: 0.35, roughness: 0.15, depthWrite: false,
  });
  function box(name, x, y, z, width, height, depth, material) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
    mesh.name = name;
    mesh.position.set(x, y, z);
    house.add(mesh);
    return mesh;
  }
  function span(name, part, bottom, top, thickness, material, lo = part.lo, hi = part.hi) {
    const horizontal = part.axis === 'h';
    return box(name,
      horizontal ? (lo + hi) / 2 : part.fixed,
      part.floor * data.floor_to_floor_ft + (bottom + top) / 2,
      horizontal ? part.fixed : (lo + hi) / 2,
      horizontal ? hi - lo : thickness, top - bottom,
      horizontal ? thickness : hi - lo, material);
  }
  for (const part of data.walls) {
    span('Wall', part, part.bottom, part.top, data.wall_thickness_ft, wall);
  }
  for (const part of data.windows) {
    const trim = Math.min(0.12, (part.hi - part.lo) / 4);
    span('Window glass', part, part.bottom, part.top, 0.06, glass);
    span('Window sill', part, part.bottom, part.bottom + trim, 0.22, frame);
    span('Window header', part, part.top - trim, part.top, 0.22, frame);
    span('Window frame', part, part.bottom, part.top, 0.22, frame, part.lo, part.lo + trim);
    span('Window frame', part, part.bottom, part.top, 0.22, frame, part.hi - trim, part.hi);
    const middle = (part.lo + part.hi) / 2;
    span('Window mullion', part, part.bottom, part.top, 0.16, frame, middle - trim / 2, middle + trim / 2);
  }
  for (const room of data.floor_slabs) {
    const base = room.floor * data.floor_to_floor_ft;
    box('Floor ' + room.name, room.x + room.width / 2, base - 0.15,
      room.y + room.length / 2, room.width, 0.3, room.length, floor);
    box('Roof ' + room.name, room.x + room.width / 2, base + data.wall_height_ft + 0.25,
      room.y + room.length / 2, room.width, 0.5, room.length, roof);
  }
  return house;
}
