(() => {
  const API = '/api/v1';
  const money = (amount, currency = 'PKR') => `${currency} ${Number(amount || 0).toLocaleString()}`;
  async function ensureCustomer() {
    let id = localStorage.getItem('aisa_customer_id');
    if (id) return id;
    const name = prompt('What name should we use for your order?') || 'Website visitor';
    const response = await fetch(`${API}/customers`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name, channel:'web'}) });
    if (!response.ok) throw new Error('Could not create your customer profile. Please refresh and try again.');
    id = (await response.json()).id;
    localStorage.setItem('aisa_customer_id', id);
    return id;
  }
  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || 'Something went wrong. Please try again.');
    return body;
  }
  async function renderCart() {
    const holder = document.querySelector('#cart-items');
    const status = document.querySelector('#cart-status');
    if (!holder) return;
    try {
      const id = await ensureCustomer();
      const cart = await api(`${API}/carts/${id}`);
      holder.replaceChildren();
      document.querySelector('#checkout-panel').hidden = cart.items.length === 0;
      status.textContent = cart.items.length ? '' : 'Your cart is empty. Browse the catalogue to add a product.';
      for (const item of cart.items) {
        const row = document.createElement('article'); row.className = 'cart-row';
        const info = document.createElement('div'); const title = document.createElement('h3'); title.textContent = item.name;
        const desc = document.createElement('p'); desc.className = 'muted'; desc.textContent = item.description;
        const price = document.createElement('strong'); price.textContent = money(item.line_total, item.currency);
        info.append(title, desc, price);
        const controls = document.createElement('div'); controls.className = 'cart-controls';
        const qty = document.createElement('input'); qty.type = 'number'; qty.min = '1'; qty.max = '100'; qty.value = item.quantity; qty.setAttribute('aria-label', `Quantity for ${item.name}`);
        qty.addEventListener('change', async () => { try { await api(`${API}/carts/${id}/items/${encodeURIComponent(item.product_id)}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({quantity:Number(qty.value)})}); await renderCart(); } catch (e) { status.textContent = e.message; } });
        const remove = document.createElement('button'); remove.className = 'button secondary'; remove.textContent = 'Remove';
        remove.addEventListener('click', async () => { try { await api(`${API}/carts/${id}/items/${encodeURIComponent(item.product_id)}`, {method:'DELETE'}); await renderCart(); } catch (e) { status.textContent = e.message; } });
        controls.append(qty, remove); row.append(info, controls); holder.append(row);
      }
      const total = document.querySelector('#cart-total'); if (total) total.textContent = `Subtotal: ${money(cart.subtotal, cart.currency)}`;
      await updateCartCount(id);
    } catch (e) { status.textContent = e.message; }
  }
  async function updateCartCount(id) {
    const badge = document.querySelector('#cart-count'); if (!badge) return;
    try { const cart = await api(`${API}/carts/${id}`); badge.textContent = String(cart.items.reduce((sum, item) => sum + item.quantity, 0)); } catch { badge.textContent = '0'; }
  }
  async function initStore() {
    const products = document.querySelector('#products');
    if (products) {
      try {
        const items = await api(`${API}/products`);
        products.replaceChildren();
        for (const product of items) {
          const card = document.createElement('article'); card.className = 'card';
          const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = product.category;
          const title = document.createElement('h3'); title.textContent = product.name;
          const description = document.createElement('p'); description.className = 'muted'; description.textContent = product.description;
          const price = document.createElement('strong'); price.textContent = product.price == null ? 'Ask us' : money(product.price, product.currency);
          card.append(tag, title, description, price);
          if (product.price != null) {
            const add = document.createElement('button'); add.className = 'button add-cart'; add.textContent = 'Add to cart';
            add.addEventListener('click', async () => { add.disabled = true; try { const id = await ensureCustomer(); await api(`${API}/carts/${id}/items`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:product.id,quantity:1})}); await updateCartCount(id); add.textContent = 'Added'; setTimeout(() => { add.textContent = 'Add to cart'; add.disabled = false; }, 1200); } catch (e) { alert(e.message); add.disabled = false; } });
            card.append(add);
          }
          products.append(card);
        }
        const id = localStorage.getItem('aisa_customer_id'); if (id) updateCartCount(id);
      } catch (e) { products.textContent = e.message; }
    }
    document.querySelector('#checkout-button')?.addEventListener('click', async event => {
      const button = event.currentTarget; button.disabled = true;
      const status = document.querySelector('#cart-status');
      try {
        const id = await ensureCustomer();
        const result = await api(`${API}/carts/${id}/checkout`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({payment_method:document.querySelector('#payment-method').value})});
        document.querySelector('#checkout-panel').hidden = true;
        document.querySelector('#cart-items').replaceChildren();
        status.className = 'checkout-success';
        status.textContent = `Order request ${result.checkout_id.slice(0,8)} received. ${result.message} Your total is ${money(result.subtotal, result.currency)}.`;
      } catch (e) { status.textContent = e.message; }
      finally { button.disabled = false; }
    });
    renderCart();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initStore); else initStore();
})();
