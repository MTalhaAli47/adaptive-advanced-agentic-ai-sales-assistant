const API = '/api/v1';

function getCustomer() {
  return localStorage.getItem('aisa_customer_id');
}

async function ensureCustomer() {
  const existing = getCustomer();
  if (existing) return existing;
  const name = prompt('What name should we use?') || 'Website visitor';
  const response = await fetch(`${API}/customers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, channel: 'web' }),
  });
  const customer = await response.json();
  localStorage.setItem('aisa_customer_id', customer.id);
  return customer.id;
}

function addBubble(box, role, text) {
  const bubble = document.createElement('div');
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  box.append(bubble);
  box.scrollTop = box.scrollHeight;
}

async function setupChat(form, box) {
  const saved = getCustomer();
  if (saved) {
    try {
      const history = await fetch(`${API}/conversations/${encodeURIComponent(saved)}`).then(r => r.json());
      if (getCustomer() === saved && history.messages.length) {
        box.replaceChildren();
        history.messages.forEach(message => addBubble(box, message.role === 'customer' ? 'customer' : 'assistant', message.content));
      }
    } catch { /* The first message can still start a fresh conversation. */ }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const input = form.querySelector('input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addBubble(box, 'customer', text);
    try {
      const customerId = await ensureCustomer();
      const response = await fetch(`${API}/conversations/${encodeURIComponent(customerId)}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Request failed');
      addBubble(box, 'assistant', result.reply);
      if (result.handoff_required) addBubble(box, 'assistant', 'A salesperson will follow up with you.');
    } catch {
      addBubble(box, 'assistant', 'I could not reach the service. Please try again shortly.');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const box = document.querySelector('#chat-messages');
  const form = document.querySelector('#chat-form');
  if (box && form) {
    addBubble(box, 'assistant', 'Hello! Tell me what you are looking for and I’ll help you find the next step.');
    setupChat(form, box);
    const newChat = document.querySelector('#new-chat');
    if (newChat) {
      newChat.addEventListener('click', () => {
        localStorage.removeItem('aisa_customer_id');
        box.replaceChildren();
        addBubble(box, 'assistant', 'New conversation started. What are you looking for today?');
      });
    }
  }

  const toggle = document.querySelector('#widget-toggle');
  const widget = document.querySelector('#widget');
  if (toggle && widget) {
    toggle.addEventListener('click', () => {
      widget.hidden = !widget.hidden;
      if (!widget.dataset.ready) {
        const widgetBox = widget.querySelector('.chat-box');
        setupChat(widget.querySelector('form'), widgetBox);
        addBubble(widgetBox, 'assistant', 'Hi! How can I help today?');
        widget.dataset.ready = '1';
      }
    });
    const close = document.querySelector('#widget-close');
    if (close) close.addEventListener('click', () => { widget.hidden = true; });
    const widgetNewChat = document.querySelector('#widget-new-chat');
    if (widgetNewChat) {
      widgetNewChat.addEventListener('click', () => {
        localStorage.removeItem('aisa_customer_id');
        const widgetBox = widget.querySelector('.chat-box');
        widgetBox.replaceChildren();
        addBubble(widgetBox, 'assistant', 'New conversation started. What are you looking for today?');
      });
    }
  }
});
