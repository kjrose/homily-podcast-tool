/* Offline DOM/event contract: no external dependencies or network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../homily_monitor/editorial-catalog.json'), 'utf8'));

class Element {
  constructor(tag, text = '') { this.tagName = tag; this.children = []; this.listeners = {}; this.value = ''; this._text = text; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  set innerHTML(value) { throw new Error('Dynamic HTML must use text nodes'); }
  get elements() { return Object.fromEntries(all(this).filter(node => node.name).map(node => [node.name, node])); }
  get isConnected() { return all(root).includes(this); }
  async emit(type) {
    const event = { target: this, currentTarget: this, preventDefault() {} };
    const pending = (this.listeners[type] || []).map(callback => callback(event));
    // Browser currentTarget is cleared after dispatch, before asynchronous callbacks resume.
    event.currentTarget = null;
    await Promise.all(pending);
  }
}
function all(node) { return [node, ...node.children.flatMap(all)]; }
const root = new Element('div'); root.id = 'homily-studio';
const document = {
  getElementById: id => all(root).find(node => node.id === id) || null,
  createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text),
};
class FormData {
  constructor(form) {
    this.values = Object.fromEntries(Object.entries(form.elements)
      .filter(([, node]) => node.type !== 'checkbox' || node.checked)
      .map(([key, node]) => [key, node.type === 'checkbox' ? 'on' : node.value]));
  }
  get(key) { return this.values[key] ?? null; }
}
const clone = value => JSON.parse(JSON.stringify(value));
let state = { active: clone(catalog.default_profile), draft: clone(catalog.default_profile), history: [], catalog };
let revision = 0, failSave = false;
const calls = [];
async function fetch(url, options) {
  assert.equal(options.headers['X-WP-Nonce'], 'test-nonce');
  assert.equal(options.credentials, 'same-origin');
  const route = url.replace('https://example.org/wp-json/homily-studio/v1/', '');
  const body = options.body ? JSON.parse(options.body) : undefined;
  calls.push({ route, method: options.method, body });
  let value, ok = true;
  if (route === 'workspace') value = clone(state);
  else if (route === 'sources') value = [];
  else if (route === 'draft') {
    if (failSave) { ok = false; value = { message: 'Revision conflict' }; }
    else { state.draft = { ...clone(body.profile), revision: `test-${++revision}` }; value = clone(state.draft); }
  } else if (route === 'activate') { state.history.unshift({ profile: state.active }); state.active = clone(state.draft); value = clone(state.active); }
  else if (route === 'jobs' && options.method === 'POST') value = { ids: [1] };
  else if (route === 'jobs') value = [{ id: 1, label: 'Draft', kind: 'concept', status: 'complete' }];
  else if (route === 'jobs/1') value = { id: 1, label: 'Draft', kind: 'concept', status: 'complete', profile: state.draft,
    input: {}, has_image: false, result: { image_prompt: '<source text> Reviewed prompt', metadata: { title: '<img onerror=example>', description: 'Safe text' } } };
  else throw new Error(`Unexpected route ${route}`);
  return { ok, json: async () => value };
}
const context = { document, Node: Element, FormData, fetch, homilyStudio: {
  root: 'https://example.org/wp-json/homily-studio/v1/', nonce: 'test-nonce', recordingExample: 'Mass-2026-10-01_09-00.mp3',
}, setInterval() {}, setTimeout, URL, Blob };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../wordpress-plugin/homily-studio/assets/admin.js'), 'utf8'), context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const button = label => all(root).find(node => node.tagName === 'button' && node.textContent === label);
const field = name => all(root).find(node => node.name === name);

(async () => {
  await settle();
  assert.ok(field('prompt_title')); assert.ok(field('prompt_description')); assert.ok(field('prompt_image_render'));
  field('voice').value = 'Our edited site voice';
  field('prompt_title').value = 'Write three simple words.';
  await button('Save draft').emit('click');
  assert.equal(button('Save draft').disabled, false, 'Async action re-enables its button');
  assert.equal(state.draft.voice, 'Our edited site voice');
  assert.notEqual(state.active.voice, 'Our edited site voice', 'Saving does not activate');
  await button('Preview lab').emit('click'); await settle();
  field('transcript').value = 'The source transcript we want to keep.';
  field('title').value = 'Hope';
  await button('Queue preview').emit('click');
  const queued = calls.find(call => call.route === 'jobs' && call.method === 'POST');
  assert.equal(queued.body.profile.voice, 'Our edited site voice');
  assert.equal(queued.body.input.transcript, 'The source transcript we want to keep.');
  await button('Style library').emit('click');
  await button('Preview lab').emit('click'); await settle();
  assert.equal(field('transcript').value, 'The source transcript we want to keep.', 'Tab changes preserve preview input');
  await button('Draft #1 · concept · complete').emit('click');
  assert.ok(all(root).find(node => node.tagName === 'h3' && node.textContent === '<img onerror=example>'), 'Model content remains literal text');
  await button('Render this reviewed image prompt').emit('click');
  assert.equal(calls.at(-2).body.concept_job_id, 1, 'Reviewed prompt queues by its saved job');
  await button('Editorial settings').emit('click');
  await button('Activate draft').emit('click');
  assert.equal(state.active.voice, 'Our edited site voice');
  failSave = true;
  await button('Save draft').emit('click');
  assert.equal(button('Save draft').disabled, false);
  assert.match(document.getElementById('hs-message').textContent, /Revision conflict/);
  console.log('Admin workspace contract: editing, preview queue, activation, safe text rendering and async button recovery passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
