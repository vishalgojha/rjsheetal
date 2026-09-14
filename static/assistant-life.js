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
      <div class="life-box"><strong>Work + home focus</strong><div id="lifeTasks" class="life-list"><span class="life-muted">Loading…</span></div><form id="lifeTaskForm" class="life-form"><input id="lifeTaskInput" placeholder="Add a task…" aria-label="New task"><button type="submit">ADD</button></form></div>
      <div class="life-box"><strong>Home list</strong><div id="lifeShopping" class="life-list"><span class="life-muted">Loading…</span></div><form id="lifeShopForm" class="life-form"><input id="lifeShopInput" placeholder="Add to list…" aria-label="Shopping item"><button type="submit">ADD</button></form></div>
    </div>
    <div class="life-box life-notes"><div class="life-box-head"><strong>Notes</strong><button id="lifePlan" type="button">PLAN TODAY</button></div><div id="lifeNotes" class="life-list"><span class="life-muted">Loading…</span></div><form id="lifeNoteForm" class="life-note-form"><input id="lifeNoteTitle" placeholder="Note title…" aria-label="Note title"><textarea id="lifeNoteBody" rows="2" placeholder="Write something to remember…" aria-label="Note body"></textarea><button type="submit">SAVE NOTE</button></form></div>
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
  $('lifeTaskForm').addEventListener('submit', async event => { event.preventDefault(); const input = $('lifeTaskInput'); if (!input.value.trim()) return; try { await request('/api/tasks', { method: 'POST', body: JSON.stringify({ title: input.value }) }); input.value = ''; await load(); } catch (e) { message(e.message); } });
  $('lifeShopForm').addEventListener('submit', async event => { event.preventDefault(); const input = $('lifeShopInput'); if (!input.value.trim()) return; try { await request('/api/shopping', { method: 'POST', body: JSON.stringify({ item: input.value }) }); input.value = ''; await load(); } catch (e) { message(e.message); } });
  $('lifeNoteForm').addEventListener('submit', async event => { event.preventDefault(); const title = $('lifeNoteTitle'), body = $('lifeNoteBody'); if (!body.value.trim()) return; try { await request('/api/notes', { method: 'POST', body: JSON.stringify({ title: title.value, body: body.value }) }); title.value = ''; body.value = ''; await load(); } catch (e) { message(e.message); } });
  $('lifePlan').addEventListener('click', async () => { try { const data = await request('/api/assistant/dashboard'); const items = (data.tasks || []).slice(0, 8).map(x => x.title); await request('/api/plans', { method: 'POST', body: JSON.stringify({ date: new Date().toISOString().slice(0, 10), items, summary: items.length ? 'Built from open tasks.' : 'A gentle day with room to breathe.' }) }); message(items.length ? 'Today’s plan is saved.' : 'Today is saved as a blank plan.'); } catch (e) { message(e.message); } });
  load();
})();
