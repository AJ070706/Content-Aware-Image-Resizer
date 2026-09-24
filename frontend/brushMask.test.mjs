import assert from 'node:assert/strict';
import test from 'node:test';
import { BrushMask } from './src/brushMask.ts';

function count(mask, mark) {
  return mask.pixels.reduce((total, pixel) => total + Number(pixel === mark), 0);
}

test('painting the same pixels repeatedly leaves the unique counts unchanged', () => {
  const mask = new BrushMask(60, 40);
  const from = { x: 10, y: 20 };
  const to = { x: 40, y: 20 };
  const first = mask.paint(from, to, 10, 1);
  assert.ok(first.changes.length > 0);
  assert.equal(mask.protectedCount, first.changes.length);
  assert.equal(mask.removedCount, 0);
  assert.equal(mask.paint(from, to, 10, 1).changes.length, 0);
  assert.equal(mask.paint(to, from, 10, 1).changes.length, 0);
  assert.equal(mask.protectedCount, count(mask, 1));
});

test('a long brush stroke is one solid region without stamp rings or gaps', () => {
  const mask = new BrushMask(90, 30);
  const stroke = mask.paint({ x: 8, y: 15 }, { x: 81, y: 15 }, 12, 2);
  assert.ok(stroke.bounds);
  for (let x = 8; x < 81; x++) {
    assert.equal(mask.pixels[15 * mask.width + x], 2);
    assert.equal(mask.pixels[12 * mask.width + x], 2);
  }
  assert.equal(mask.removedCount, count(mask, 2));
});

test('switching tools replaces pixels, erasing removes them, and rollback restores them', () => {
  const mask = new BrushMask(24, 24);
  const point = { x: 12, y: 12 };
  const protectedStroke = mask.paint(point, point, 8, 1);
  const originalCount = mask.protectedCount;
  const removeStroke = mask.paint(point, point, 8, 2);
  assert.equal(mask.protectedCount, 0);
  assert.equal(mask.removedCount, originalCount);
  mask.restore(removeStroke.changes);
  assert.equal(mask.protectedCount, originalCount);
  assert.equal(mask.removedCount, 0);
  const eraseStroke = mask.paint(point, point, 8, 0);
  assert.equal(eraseStroke.changes.length, originalCount);
  assert.equal(mask.protectedCount, 0);
  assert.equal(mask.removedCount, 0);
  mask.restore(eraseStroke.changes);
  assert.equal(mask.protectedCount, count(mask, 1));
  assert.equal(protectedStroke.changes.length, originalCount);
});

test('brushes clip cleanly to image edges', () => {
  const mask = new BrushMask(10, 10);
  mask.paint({ x: 0, y: 0 }, { x: 10, y: 10 }, 6, 1);
  assert.equal(mask.protectedCount, count(mask, 1));
  assert.ok(mask.protectedCount > 0);
  assert.equal(mask.pixels[0], 1);
  assert.equal(mask.pixels[99], 1);
});
