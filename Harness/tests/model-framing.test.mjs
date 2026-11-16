import test from 'node:test';
import assert from 'node:assert/strict';
import {modelFrameDistance} from '../studio/src/model-framing.js';

test('the entire model sphere retains a margin in wide, square and narrow panes', () => {
  const radius = Math.sqrt(3) / 2;
  for (const [width, height] of [[856, 280], [664, 280], [358, 250], [250, 500]]) {
    const aspect = width / height;
    const distance = modelFrameDistance(radius, 35, aspect);
    const halfVertical = 35 * Math.PI / 360;
    const halfHorizontal = Math.atan(Math.tan(halfVertical) * aspect);
    const angularRadius = Math.asin(radius / distance);
    assert.ok(angularRadius < halfVertical);
    assert.ok(angularRadius < halfHorizontal);
  }
});

test('default fitting is scale invariant and narrowing the pane increases fit distance', () => {
  assert.equal(modelFrameDistance(20, 35, 2), modelFrameDistance(1, 35, 2) * 20);
  assert.ok(modelFrameDistance(1, 35, .5) > modelFrameDistance(1, 35, 2));
});

test('invalid model bounds, projection and padding are rejected', () => {
  for (const args of [[0,35,1],[-1,35,1],[NaN,35,1],[1,0,1],[1,180,1],[1,35,0],[1,35,Infinity],[1,35,1,.9]]) {
    assert.throws(() => modelFrameDistance(...args), RangeError);
  }
});
