import test from 'node:test';
import assert from 'node:assert/strict';
import {printerApiPath, createRequestGate, isHomeAssistant, isCachedHistory, galleryRows, cachedFileStats} from '../studio/src/presentation.js';

test('printer requests preserve other filters and safely encode device identifiers', () => {
  assert.equal(printerApiPath('/api/history', ''), '/api/history');
  assert.equal(printerApiPath('/api/history', null), '/api/history');
  assert.equal(printerApiPath('/api/history?limit=8&printer_id=old', 'a/b &?'), '/api/history?limit=8&printer_id=a%2Fb+%26%3F');
  assert.equal(printerApiPath('/api/overview', 'printer-two'), '/api/overview?printer_id=printer-two');
});

test('a newer printer request prevents an older response replacing the chosen printer', async () => {
  const gate = createRequestGate(), first = gate.begin();
  let finishOld, finishNew, rendered;
  const old = new Promise(resolve => { finishOld = resolve; }).then(value => { if (first.isCurrent()) rendered = value; });
  const second = gate.begin();
  assert.equal(first.signal.aborted, true);
  const current = new Promise(resolve => { finishNew = resolve; }).then(value => { if (second.isCurrent()) rendered = value; });
  finishNew('P1P'); await current;
  finishOld('A1'); await old;
  assert.equal(rendered, 'P1P');
  gate.cancel();
  assert.equal(second.signal.aborted, true);
  assert.equal(second.isCurrent(), false);
});

test('unmount cancellation invalidates pending requests without a successor', () => {
  const gate = createRequestGate(), request = gate.begin();
  assert.equal(request.isCurrent(), true);
  gate.cancel();
  assert.equal(request.isCurrent(), false);
});

test('HA cached files appear in the gallery without becoming completed prints', () => {
  const rows = [{id: 'cached', name: 'Bracket', printer_name: 'A1', status: 'archived', is_print_event: false, download_url: '/file/part.3mf', model_preview: {url: '/file/part.png'}}, {id: 'no-preview', name: 'Hook', status: 'archived', is_print_event: false}];
  const data = {source: 'homeassistant_cache', items: rows, counts: {prints: 0, completed: 0}};
  assert.equal(isCachedHistory(data), true);
  assert.deepEqual(galleryRows(data, 'a1').map(row => row.id), ['cached']);
  assert.deepEqual(cachedFileStats(data), {files: 2, previews: 1, downloads: 1});
  assert.equal(data.counts.completed, 0);
  assert.equal(galleryRows({items: rows}).length, 0);
});

test('provider detection and server cache totals handle empty or partial records', () => {
  assert.equal(isHomeAssistant({device: {provider: 'homeassistant'}}), true);
  assert.equal(isHomeAssistant(null), false);
  assert.equal(isCachedHistory({provider: 'bambuddy'}), false);
  assert.equal(isCachedHistory({provider: 'homeassistant'}), true);
  assert.deepEqual(cachedFileStats({cache_count: 9, items: [{archive_photo_url: '/photo.png'}]}), {files: 9, previews: 1, downloads: 0});
  assert.deepEqual(cachedFileStats(null), {files: 0, previews: 0, downloads: 0});
});
