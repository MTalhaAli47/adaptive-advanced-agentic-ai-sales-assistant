const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('Admin order page renders confirmation, payment, fulfillment, and escaped customer details', async () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
  const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0]?.[1];
  assert.ok(script, 'admin page script exists');

  const elements = new Map();
  const getElement = selector => {
    if (!elements.has(selector)) elements.set(selector, { innerHTML: '', textContent: '', listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback; }, querySelectorAll() { return []; } });
    return elements.get(selector);
  };
  const order = {
    id: 'order-12345678', customer_name: '<Amina>', customer_contact: null,
    created_at: '2026-01-01T00:00:00Z', product_name: 'Lenovo RTX 5060', quantity: 1,
    unit_price: 680000, currency: 'PKR', status: 'pending_confirmation', payment_status: 'not_paid',
    fulfillment_status: 'not_started', payment_method: null, payment_reference: null, admin_note: null,
  };
  const requests = [];
  const context = {
    document: {
      querySelector: getElement,
      querySelectorAll: () => [],
    },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async url => {
      requests.push(url);
      if (url.endsWith('/admin/identity')) return { ok: true, status: 200, json: async () => ({ id: 'admin-001', login_id: 'admin' }) };
      if (url.endsWith('/admin/summary')) return { ok: true, json: async () => ({ total: 1, awaiting_confirmation: 1, confirmed: 0, not_paid: 1, paid: 0, delivered: 0, appointments_requested: 1, appointments_confirmed: 0 }) };
      if (url.endsWith('/admin/orders')) return { ok: true, json: async () => [order] };
      if (url.endsWith('/admin/appointments')) return { ok: true, json: async () => [{ id: 'appointment-1', customer_name: 'Amina', requested_time: 'Friday 3pm', status: 'requested' }] };
      if (url.endsWith('/admin/products')) return { ok: true, json: async () => [] };
      if (url.endsWith('/admin/product-relationships')) return { ok: true, json: async () => [] };
      throw new Error(`Unexpected request: ${url}`);
    },
    console, Date, Number, encodeURIComponent,
  };
  vm.runInNewContext(script, context, { filename: 'admin.html' });
  await context.load();

  const table = getElement('#orders').innerHTML;
  assert.match(table, /&lt;Amina&gt;/);
  assert.match(table, /Not confirmed/);
  assert.match(table, /Not paid/);
  assert.match(table, /Fulfillment status/);
  assert.match(table, /value="shipped"/);
  assert.match(getElement('#appointments').innerHTML, /Friday 3pm/);
  assert.match(getElement('#appointments').innerHTML, /value="confirmed"/);
  assert.ok(requests.some(url => url.endsWith('/admin/orders')));
  assert.match(getElement('#summary').innerHTML, /Total orders/);
});
