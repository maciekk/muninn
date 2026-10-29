const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const URL_PRODUCT = 'https://www.newegg.ca/p/example';
const source = fs.readFileSync(path.join(__dirname, '../extensions/firefox/background.js'), 'utf8');

function harness() {
  let clock = 0;
  let listener;
  const calls = {create: [], update: [], reload: [], api: [], read: []};
  const state = {
    url: URL_PRODUCT, price: '$899.99', job: null, apiError: null,
    beforeRead: null, tabs: new Map(), reads: 0
  };
  const context = vm.createContext({
    URL, AbortController, TextEncoder,
    Date: {now: () => { clock += 1000; return clock; }},
    setTimeout: (callback, ms) => { if (ms <= 2000) queueMicrotask(callback); return 1; },
    clearTimeout: () => {}, setInterval: () => {},
    fetch: async (url, options) => {
      calls.api.push({url, options});
      if (state.apiError) throw new Error(state.apiError);
      return {ok: true, json: async () => ({job: state.job})};
    },
    browser: {
      storage: {local: {set: async () => {}}},
      runtime: {onMessage: {addListener: fn => { listener = fn; }}},
      tabs: {
        create: async options => {
          calls.create.push(options);
          const tab = {id: 17, url: options.url, status: 'complete'};
          state.tabs.set(tab.id, tab);
          return tab;
        },
        get: async id => {
          if (!state.tabs.has(id)) throw new Error('Tab closed');
          return state.tabs.get(id);
        },
        update: async (id, options) => {
          calls.update.push({id, options});
          Object.assign(state.tabs.get(id), options);
        },
        reload: async id => calls.reload.push(id),
        executeScript: async id => {
          calls.read.push(id);
          state.reads++;
          if (state.beforeRead) state.beforeRead();
          return [{url: state.url, price: state.price, html: '<main>saved page</main>'}];
        }
      }
    }
  });
  vm.runInContext(source + '\nglobalThis.worker = {collect, poll, enable: () => {enabled = true; token = "test-token";}, state: () => ({enabled, status})};', context);
  return {worker: context.worker, calls, state, message: msg => listener(msg)};
}

test('collects stable price in an owned tab, then reloads only that tab', async () => {
  const {worker, calls} = harness();
  worker.enable();
  const result = await worker.collect({id: 'job1', url: URL_PRODUCT});
  assert.equal(result.html, '<main>saved page</main>');
  assert.equal(calls.create.length, 1);
  assert.equal(calls.read.length, 3);
  assert.ok(calls.read.every(id => id === 17));
  await worker.collect({id: 'job2', url: URL_PRODUCT});
  assert.equal(calls.create.length, 1);
  assert.deepEqual(calls.reload, [17]);
});

test('waits for verification/price rather than capturing an empty page', async () => {
  const {worker, state} = harness();
  worker.enable();
  state.price = '';
  state.beforeRead = () => { if (state.reads >= 4) state.price = '$899.99'; };
  const result = await worker.collect({id: 'job', url: URL_PRODUCT});
  assert.equal(state.reads, 6);
  assert.equal(result.url, URL_PRODUCT);
});

test('does not return prices from a redirected variant', async () => {
  const {worker, state} = harness();
  worker.enable();
  state.url = 'https://www.newegg.ca/p/other';
  await assert.rejects(worker.collect({id: 'job', url: URL_PRODUCT}), /No stable main-product price/);
});

test('never opens an unrelated site, even if requested by bridge', async () => {
  const {worker, calls} = harness();
  worker.enable();
  for (const url of ['https://evil.com', 'https://newegg.ca.evil.com', 'http://newegg.ca', 'https://user@newegg.ca']) {
    await assert.rejects(worker.collect({id: 'job', url}), /non-Newegg/);
  }
  assert.equal(calls.create.length, 0);
});

test('closing its owned tab allows a new owned tab without touching others', async () => {
  const {worker, state, calls} = harness();
  worker.enable();
  await worker.collect({id: 'job1', url: URL_PRODUCT});
  state.tabs.clear();
  await worker.collect({id: 'job2', url: URL_PRODUCT});
  assert.equal(calls.create.length, 2);
  assert.equal(calls.update.length, 0);
});

test('poll returns rendered HTML with the original job id', async () => {
  const {worker, state, calls} = harness();
  state.job = {id: 'job1', url: URL_PRODUCT};
  worker.enable();
  await worker.poll();
  const result = calls.api.find(call => call.url.endsWith('/result'));
  assert.equal(JSON.parse(result.options.body).id, 'job1');
  assert.equal(JSON.parse(result.options.body).html, '<main>saved page</main>');
});

test('poll reports collection errors and disconnects if bridge is unreachable', async () => {
  const {worker, state, calls} = harness();
  worker.enable();
  state.job = {id: 'job1', url: 'https://evil.com'};
  await worker.poll();
  assert.match(JSON.parse(calls.api.at(-1).options.body).error, /non-Newegg/);
  state.apiError = 'NetworkError';
  await worker.poll();
  assert.equal(worker.state().enabled, false);
  assert.match(worker.state().status, /NetworkError/);
});

test('disconnect prevents navigation and collection', async () => {
  const {worker, message, calls} = harness();
  worker.enable();
  await message({action: 'disconnect'});
  await worker.poll();
  assert.equal(calls.api.length, 0);
  assert.equal(worker.state().enabled, false);
});
