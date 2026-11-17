const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Element {
  constructor() {
    this.listeners = {};
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.value = '';
    this.className = '';
    this.scrollTop = 0;
  }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  querySelector(selector) {
    if (selector === 'input') return this.input;
    if (selector === 'form') return this.form;
    if (selector === '.chat-box') return this.chatBox;
    return null;
  }
  async fire(type, event = {}) {
    for (const callback of this.listeners[type] || []) await callback(event);
  }
  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent ?? this.children.map(child => child.textContent || '').join(''); }
}

function buildBrowserHarness() {
  const box = new Element();
  const input = new Element();
  const form = new Element();
  form.input = input;
  const newChat = new Element();
  const elements = { '#chat-messages': box, '#chat-form': form, '#new-chat': newChat };
  const data = new Map([['aisa_customer_id', 'old-customer']]);
  const ready = {};
  const requests = [];
  let releaseHistory;
  const oldHistory = new Promise(resolve => { releaseHistory = resolve; });
  const document = {
    addEventListener(type, callback) { ready[type] = callback; },
    querySelector(selector) { return elements[selector] || null; },
    createElement() { return new Element(); },
  };
  const fetch = async (url, options) => {
    requests.push({ url, options });
    if (url.includes('/conversations/old-customer')) return oldHistory;
    if (url.endsWith('/customers')) return { ok: true, json: async () => ({ id: 'new-customer' }) };
    if (url.includes('/conversations/new-customer/messages')) return { ok: true, json: async () => ({ reply: 'Fresh response', handoff_required: false }) };
    throw new Error(`Unexpected request: ${url}`);
  };
  const context = {
    document,
    fetch,
    localStorage: {
      getItem(key) { return data.get(key) ?? null; },
      setItem(key, value) { data.set(key, String(value)); },
      removeItem(key) { data.delete(key); },
    },
    prompt: () => 'Test user',
    console,
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'assets', 'chat.js'), 'utf8');
  vm.runInNewContext(source, context, { filename: 'chat.js' });
  return { box, input, form, newChat, ready, data, requests, releaseHistory };
}

test('New chat clears the active identity, ignores a pending old-history response, and sends under a new customer', async () => {
  const ui = buildBrowserHarness();
  ui.ready.DOMContentLoaded();
  await ui.newChat.fire('click');

  assert.equal(ui.data.has('aisa_customer_id'), false);
  assert.match(ui.box.textContent, /New conversation started/);

  ui.releaseHistory({ json: async () => ({ messages: [{ role: 'customer', content: 'stale old message' }] }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.doesNotMatch(ui.box.textContent, /stale old message/);

  ui.input.value = 'I need a blender';
  await ui.form.fire('submit', { preventDefault() {} });
  assert.equal(ui.data.get('aisa_customer_id'), 'new-customer');
  assert.ok(ui.requests.some(request => request.url.includes('/conversations/new-customer/messages')));
  assert.match(ui.box.textContent, /Fresh response/);
});
