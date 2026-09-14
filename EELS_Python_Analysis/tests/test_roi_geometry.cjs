const assert = require('node:assert/strict');
const { adjust, handles } = require('../web/static/roi_geometry.js');

const axes = { x: Array.from({ length: 11 }, (_, i) => i), y: Array.from({ length: 21 }, (_, i) => i) };
const roi = { x_min: 2, x_max: 6, y_min: 4, y_max: 12 };
const start = { x: 3, y: 8 };
const point = { x: 4, y: 10 };
assert.deepEqual(adjust(roi, start, point, 'move', axes), { x_min: 3, x_max: 7, y_min: 6, y_max: 14 });
assert.deepEqual(adjust(roi, start, { x: 100, y: -100 }, 'move', axes), { x_min: 6, x_max: 10, y_min: 0, y_max: 8 });

for (const mode of ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']) {
  const expected = { ...roi };
  if (mode.includes('w')) expected.x_min += 1;
  if (mode.includes('e')) expected.x_max += 1;
  if (mode.includes('s')) expected.y_min += 2;
  if (mode.includes('n')) expected.y_max += 2;
  assert.deepEqual(adjust(roi, start, point, mode, axes), expected, mode);
}
assert.equal(adjust(roi, start, { x: 100, y: 100 }, 'sw', axes).x_min, 5);
assert.equal(adjust(roi, start, { x: -100, y: -100 }, 'ne', axes).y_max, 5);
assert.deepEqual(adjust(roi, start, { x: -100, y: 100 }, 'nw', axes), { x_min: 0, x_max: 6, y_min: 4, y_max: 20 });
assert.deepEqual(adjust(roi, start, { x: 1, y: 5 }, 'draw', axes), { x_min: 1, x_max: 3, y_min: 5, y_max: 8 });
assert.deepEqual(adjust(roi, { x: 10, y: 20 }, { x: 10, y: 20 }, 'draw', axes), { x_min: 9, x_max: 10, y_min: 19, y_max: 20 });
assert.deepEqual(adjust(roi, start, start, 'draw', axes), { x_min: 3, x_max: 4, y_min: 8, y_max: 9 });

// Even on nonuniform axes, a newly drawn interval must contain a pixel center.
const uneven = { x: [0, 0.2, 0.6, 2, 3], y: [0, 1, 4, 5] };
for (let i = 0; i < 20; i += 1) {
  const p = { x: i * 3 / 20, y: i * 5 / 20 };
  const drawn = adjust(roi, p, p, 'draw', uneven);
  for (const axis of ['x', 'y']) {
    assert(uneven[axis].some(value => value >= drawn[axis + '_min'] && value <= drawn[axis + '_max']));
  }
}
assert.equal(handles({ left: 10, right: 50, top: 20, bottom: 80 }).length, 8);
console.log('ROI geometry: move, eight resize handles, bounds, minimum size, reverse drawing passed.');
