import test from 'node:test';
import assert from 'node:assert/strict';
import {historyPage} from '../studio/src/history-pagination.js';

test('all cached files remain reachable across compact history pages without duplication', () => {
  const files = Array.from({length: 146}, (_, id) => ({id}));
  const first = historyPage(files);
  assert.equal(first.items.length, 24);
  assert.equal(first.pages, 7);
  assert.deepEqual([first.from, first.to, first.total], [1, 24, 146]);
  const seen = Array.from({length: first.pages}, (_, page) => historyPage(files, page + 1).items).flat();
  assert.deepEqual(seen, files);
  assert.deepEqual([historyPage(files, 7).from, historyPage(files, 7).to], [145, 146]);
});

test('empty collections and stale page selections stay within the available range', () => {
  assert.deepEqual(historyPage([], 9), {items: [], page: 1, pages: 1, total: 0, from: 0, to: 0});
  for (const requested of [-4, 0, 1.5, Infinity, NaN]) assert.equal(historyPage([1, 2], requested).page, 1);
  assert.equal(historyPage(Array.from({length: 25}), 999).page, 2);
});

test('search results from any original page paginate after filtering the complete dataset', () => {
  const files = Array.from({length: 146}, (_, id) => ({id, name: id === 140 ? 'Target file' : 'Other'}));
  const filtered = files.filter(file => file.name.includes('Target'));
  assert.deepEqual(historyPage(filtered, 6).items.map(file => file.id), [140]);
  assert.equal(historyPage(filtered, 6).page, 1);
});
