/* Small persistent life layer for the assistant home surface. */
(function () {
  const home = document.querySelector('.view[data-view="home"]');
  const anchor = document.querySelector('.assistant-command');
  if (!home || !anchor) return;
  const escText = window.esc || (value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
  const panel = document.createElement('section');
  panel.className = 'life-panel';
  panel.setAttribute('aria-label', 'Sheetal personal assistant memory');
  panel.innerHTML = `
    <div class="life-head"><div><div class="eyebrow">HOME BASE</div><h2>Keep the important things moving</h2></div><button id="lifeRefresh" type="button">REFRESH</button></div>
    <div class="life-grid">
      <div class="life-box"><strong>Work + home focus</strong><div id="lifeTasks" class="life-list"><span class="life-muted">Loading…</span></div><button class="life-chat" type="button" data-life-prompt="Add a task: ">ADD THROUGH CHAT</button></div>
      <div class="life-box"><strong>Home list</strong><div id="lifeShopping" class="life-list"><span class="life-muted">Loading…</span></div><button class="life-chat" type="button" data-life-prompt="Add to my home shopping list: ">ADD THROUGH CHAT</button></div>
    </div>
    <div class="life-box life-notes"><div class="life-box-head"><strong>Notes</strong><button class="life-chat" id="lifePlan" type="button" data-life-prompt="Plan my day from my open tasks">ASK SHEETAL TO PLAN</button></div><div id="lifeNotes" class="life-list"><span class="life-muted">Loading…</span></div><button class="life-chat life-chat-wide" type="button" data-life-prompt="Remember this: ">SAVE A NOTE THROUGH CHAT</button></div>
    <div id="lifeMemory" class="life-memory"></div>
    <p id="lifeMessage" class="life-message" aria-live="polite"></p>`;
  home.after(panel);

  const $ = id => document.getElementById(id);
  const message = text => { $('lifeMessage').textContent = text || ''; };
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Could not update this');
      return data;
    } finally { clearTimeout(timer); }
  }
  function shortList(items, limit, renderer, empty) {
    if (!items?.length) return `<span class="life-muted">${empty}</span>`;
    const shown = items.slice(0, limit).map(renderer).join('');
    return shown + (items.length > limit ? `<span class="life-muted">+${items.length - limit} more</span>` : '');
  }
  function render(data) {
    const tasks = data.tasks || [], shopping = data.shopping || [], notes = [...(data.notes || [])].reverse(), memory = data.memory || {};
    $('lifeTasks').innerHTML = shortList(tasks, 4, task => `<div class="life-row"><label><input type="checkbox" data-complete-task="${escText(task.id)}"><span>${escText(task.title)}${task.due ? `<small>${escText(task.due)}</small>` : ''}</span></label></div>`, 'No open tasks.');
    $('lifeShopping').innerHTML = shortList(shopping.filter(x => x.status !== 'done'), 4, item => `<div class="life-row"><label><input type="checkbox" data-toggle-shop="${escText(item.id)}"><span>${escText(item.item)}${item.quantity ? `<small>${escText(item.quantity)}</small>` : ''}</span></label></div>`, 'Your list is clear.');
    $('lifeNotes').innerHTML = shortList(notes, 2, note => `<div class="life-note"><strong>${escText(note.title)}</strong><span>${escText(note.body)}</span></div>`, 'No notes yet.');
    const learned = [...(memory.preferences || []), ...(memory.taste_notes || [])].slice(-2).map(x => x.text).filter(Boolean);
    $('lifeMemory').textContent = learned.length ? `Learning about Sheetal: ${learned.join(' · ')}` : 'The assistant will learn useful preferences as Sheetal talks to it.';
    panel.querySelectorAll('[data-complete-task]').forEach(input => input.addEventListener('change', async () => { try { await request('/api/tasks/complete', { method: 'POST', body: JSON.stringify({ id: input.dataset.completeTask }) }); await load(); } catch (e) { message(e.message); input.checked = false; } }));
    panel.querySelectorAll('[data-toggle-shop]').forEach(input => input.addEventListener('change', async () => { try { await request('/api/shopping/toggle', { method: 'POST', body: JSON.stringify({ id: input.dataset.toggleShop }) }); await load(); } catch (e) { message(e.message); input.checked = false; } }));
  }
  async function load() { try { render(await request('/api/assistant/dashboard')); message(''); } catch (e) { message('Your saved items are temporarily unavailable.'); } }
  $('lifeRefresh').addEventListener('click', load);
  panel.querySelectorAll('[data-life-prompt]').forEach(button => button.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('sheetal:assistant-prompt', { detail: { prompt: button.dataset.lifePrompt || '' } }));
    message('Ready in the assistant composer. Add the details, then send.');
  }));
  load();
})();
