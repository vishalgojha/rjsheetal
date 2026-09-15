/* ChatGPT-like command surface for the assistant and its persistent notepad. */
(function () {
  const command = document.querySelector('.assistant-command');
  if (!command) return;
  const form = document.getElementById('assistantChat');
  const input = document.getElementById('assistantInput');
  if (!form || !input) return;

  const esc = value => String(value || '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const style = document.createElement('style');
  style.textContent = `
    .assistant-command { position:relative; overflow:hidden; }
    .sheetal-widget .app { max-width:1280px!important; padding:18px!important; }
    .sheetal-widget .topbar { margin-bottom:12px; }
    .sheetal-widget .top-rj,.sheetal-widget #muteBtn { display:none!important; }
    .sheetal-widget .view[data-view="home"],.sheetal-widget .bottom-nav { display:none!important; }
    .sheetal-widget .assistant-command { min-height:calc(100vh - 80px); margin:0!important; padding:0!important; border-radius:22px!important; display:grid; grid-template-columns:190px minmax(0,1fr) 245px; overflow:hidden; }
    .sheetal-widget .assistant-command > *:not(.agent-rail):not(.agent-context) { grid-column:2; margin-left:26px; margin-right:26px; }
    .sheetal-widget .assistant-command .agent-rail { grid-column:1; grid-row:1 / span 20; }
    .sheetal-widget .assistant-command .agent-context { grid-column:3; grid-row:1 / span 20; }
    .sheetal-widget .assistant-command .workspace-intro { padding:27px 0 8px; }
    .sheetal-widget .assistant-command .workspace-intro { padding:18px 8px 8px; }
    .sheetal-widget .assistant-command .workspace-intro strong { font-size:30px!important; letter-spacing:-.8px; }
    .sheetal-widget .assistant-history { flex:1; max-height:none; min-height:150px; }
    .workspace-intro { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
    .workspace-intro-copy { min-width:0; }
    .workspace-intro-copy strong { display:block; }
    .agent-rail { padding:22px 14px; border-right:1px solid var(--line); background:rgba(0,0,0,.14); }
    .agent-brand { display:flex; align-items:center; gap:8px; margin-bottom:24px; color:var(--ink); font-size:16px; font-weight:800; }
    .agent-mark { display:grid; place-items:center; width:26px; height:26px; border-radius:8px; color:#111; background:#c9f55b; font-size:12px; }
    .agent-rail-label { margin:16px 8px 7px; color:var(--muted); font-size:9px; font-weight:800; letter-spacing:1px; text-transform:uppercase; }
    .agent-rail button { width:100%; margin:2px 0; padding:9px 8px; border:0; border-radius:9px; color:var(--muted); text-align:left; background:transparent; font-size:11px; }
    .agent-rail button:hover,.agent-rail button.active { color:var(--ink); background:rgba(255,255,255,.08); }
    .agent-rail button span { display:inline-block; width:20px; color:#78c9ff; }
    .agent-context { padding:22px 14px; border-left:1px solid var(--line); background:rgba(0,0,0,.1); }
    .agent-context h3 { margin:0; font-size:13px; }
    .context-label { margin:23px 0 8px; color:var(--muted); font-size:9px; font-weight:800; letter-spacing:1px; text-transform:uppercase; }
    .context-card { padding:11px; border:1px solid var(--line); border-radius:11px; background:rgba(255,255,255,.045); }
    .context-card strong,.context-card span { display:block; }
    .context-card strong { font-size:11px; }
    .context-card span { margin-top:4px; color:var(--muted); font-size:10px; line-height:1.35; }
    .context-dot { display:inline-block; width:6px; height:6px; margin-right:5px; border-radius:50%; background:#77d9aa; }
    .widget-mobile-tools { display:none; }
    .workspace-capabilities { position:relative; flex:none; }
    .workspace-capabilities > button,.composer-actions button { border:1px solid var(--line); border-radius:999px; padding:7px 10px; color:var(--muted); background:transparent; font-size:10px; font-weight:700; }
    .capability-popover { position:absolute; z-index:3; right:0; top:36px; width:270px; padding:12px; border:1px solid var(--line); border-radius:15px; color:var(--ink); background:var(--card,#fff); box-shadow:0 18px 40px rgba(0,0,0,.24); }
    .capability-popover strong { display:block; margin-bottom:8px; font-size:12px; }
    .capability-popover div { padding:5px 0; color:var(--muted); font-size:11px; line-height:1.3; }
    .assistant-history { display:flex; flex-direction:column; gap:8px; max-height:220px; overflow:auto; margin:12px 0 4px; padding-right:3px; }
    .assistant-history:empty { display:none; }
    .assistant-bubble { max-width:88%; padding:9px 11px; border-radius:13px; font-size:12px; line-height:1.4; white-space:pre-wrap; }
    .assistant-bubble.user { align-self:flex-end; color:#17120f; background:#c9f55b; border-bottom-right-radius:4px; }
    .assistant-bubble.assistant { align-self:flex-start; color:var(--ink); background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.12); border-bottom-left-radius:4px; }
    .workspace-tools { display:flex; gap:7px; margin:10px 0 0; flex-wrap:wrap; }
    .workspace-tools button { border:1px solid var(--line); border-radius:999px; padding:6px 9px; color:var(--muted); background:transparent; font-size:10px; font-weight:700; }
    .workspace-tools button:hover { color:var(--ink); border-color:var(--orange); }
    .workspace-composer { margin-top:auto; padding:10px; border:1px solid var(--line); border-radius:18px; background:rgba(0,0,0,.16); }
    .assistant-form textarea { min-height:78px!important; resize:vertical; line-height:1.4; border:0!important; background:transparent!important; box-shadow:none!important; font-size:15px!important; }
    .assistant-form textarea { color:#f4f1ea!important; }
    .assistant-form textarea::placeholder { color:#8c91a0!important; opacity:1; }
    .assistant-form { align-items:flex-end; margin-top:0!important; }
    .assistant-form button { min-height:42px; }
    .composer-footer { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:3px; }
    .composer-hint { color:var(--muted); font-size:10px; }
    .composer-actions { display:flex; gap:7px; }
    .composer-actions button:hover,.workspace-capabilities > button:hover { color:var(--ink); border-color:var(--orange); }
    .notepad-drawer { display:none; margin-top:12px; padding:12px; border:1px solid var(--line); border-radius:15px; background:rgba(255,255,255,.06); }
    .notepad-drawer.open { display:block; }
    .notepad-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
    .notepad-head strong { font-size:13px; }
    .notepad-head button { border:0; color:var(--muted); background:transparent; font-size:16px; }
    .notepad-list { display:grid; gap:6px; max-height:170px; overflow:auto; }
    .notepad-item { padding:8px; border-radius:10px; background:rgba(255,255,255,.06); }
    .notepad-item strong,.notepad-item span { display:block; }
    .notepad-item strong { font-size:11px; }
    .notepad-item span { margin-top:3px; color:var(--muted); font-size:10px; line-height:1.35; }
    .notepad-compose { display:grid; gap:6px; margin-top:9px; }
    .notepad-compose input,.notepad-compose textarea { width:100%; border:1px solid var(--line); border-radius:9px; padding:8px; color:var(--ink); background:rgba(255,255,255,.06); font-size:11px; }
    .notepad-compose textarea { min-height:54px; resize:vertical; }
    .notepad-compose button { justify-self:end; border:0; border-radius:9px; padding:8px 11px; color:#17120f; background:#c9f55b; font-size:10px; font-weight:800; }
    .workspace-list { display:grid; gap:5px; }
    .workspace-list div { color:var(--muted); font-size:10px; }
    .assistant-actions[hidden] { display:none!important; }
    @media(max-width:900px){.sheetal-widget .assistant-command{grid-template-columns:150px minmax(0,1fr)}.sheetal-widget .agent-context{display:none}}
    @media(max-width:640px){.sheetal-widget .app{padding:0!important}.sheetal-widget .assistant-command{display:flex;min-height:100dvh;border-radius:0}.sheetal-widget .agent-rail{display:none}.sheetal-widget .assistant-command > *:not(.agent-rail):not(.agent-context){margin-left:16px;margin-right:16px}.sheetal-widget .workspace-intro{padding-top:24px}.widget-mobile-tools{display:flex!important}}
    .assistant-actions .secondary { color:var(--muted)!important; background:transparent!important; border:1px solid var(--line)!important; box-shadow:none!important; }
  `;
  document.head.append(style);

  const description = command.querySelector('p');
  if (description) description.textContent = 'Give Sheetal one instruction. She can act on your notes, tasks, email, music, apps, and day plan.';
  const title = command.querySelector('strong');
  if (title) title.textContent = 'Tell me what you need.';
  const intro = document.createElement('div');
  intro.className = 'workspace-intro';
  intro.innerHTML = '<div class="workspace-intro-copy"><div class="eyebrow">SHEETAL · AGENTIC ASSISTANT</div><strong>Tell me what you need.</strong><p>Ask naturally. I can think through it, remember it, and take the next useful action.</p></div><div class="workspace-capabilities"><button type="button" id="capabilityButton">WHAT I CAN DO</button><div class="capability-popover" hidden><strong>Sheetal can help with</strong><div>Plan your day, tasks, notes, and shopping</div><div>Read and search connected Gmail</div><div>Choose, queue, and control music</div><div>Open apps, websites, Maps, WhatsApp, and phone handoffs</div><div>Remember preferences and keep context</div><div>Use voice or quiet text chat</div></div></div>';
  command.prepend(intro);
  description?.remove();
  command.querySelector('.assistant-prompts')?.remove();
  command.querySelector(':scope > .eyebrow')?.remove();
  title.remove();

  if (document.body.classList.contains('sheetal-widget')) {
    const actionPanel = document.getElementById('assistantActions');
    if (actionPanel) command.append(actionPanel);
    const rail = document.createElement('aside');
    rail.className = 'agent-rail';
    rail.innerHTML = '<div class="agent-brand"><span class="agent-mark">S</span><span>Sheetal</span></div><button class="active" type="button" data-rail="chat"><span>▣</span>Active agent</button><div class="agent-rail-label">Workspace</div><button type="button" data-rail="notes"><span>≡</span>Notepad</button><button type="button" data-rail="tasks"><span>✓</span>Tasks & plan</button><div class="agent-rail-label">Connected</div><button type="button" data-rail="gmail"><span>✉</span>Gmail</button><button type="button" data-rail="music"><span>♫</span>Spotify</button><div class="agent-rail-label">History</div><button type="button" data-rail="history"><span>↺</span>Recent chats</button>';
    command.prepend(rail);
    const context = document.createElement('aside');
    context.className = 'agent-context';
    context.innerHTML = '<h3>Available now</h3><div class="context-label">Agent status</div><div class="context-card"><strong><i class="context-dot"></i>Ready to act</strong><span>Type an instruction or use voice. Sheetal will show the next action here.</span></div><div class="context-label">Connected tools</div><div class="context-card"><strong>Gmail</strong><span>Read/search when connected</span></div><div class="context-card"><strong>Spotify</strong><span>Play, queue, skip, and set mood</span></div><div class="context-card"><strong>Personal memory</strong><span>Notes, tasks, preferences, and plans</span></div><div class="context-label">Fast commands</div><div class="context-card"><span>“Save this as a note”</span><span>“Plan my afternoon”</span><span>“Play something calm”</span></div>';
    command.append(context);
    rail.addEventListener('click', event => {
      const button = event.target.closest('[data-rail]');
      if (!button) return;
      rail.querySelectorAll('button').forEach(item => item.classList.toggle('active', item === button));
      const target = button.dataset.rail;
      if (target === 'notes' || target === 'tasks') document.querySelector(`[data-workspace="${target}"]`)?.click();
      else if (target === 'music') { composerInput.value = 'Help me choose and play music for my current mood.'; composerInput.focus(); }
      else if (target === 'gmail') { composerInput.value = 'Check my important unread email and summarize it.'; composerInput.focus(); }
      else if (target === 'chat') composerInput.focus();
    });
    setTimeout(() => composerInput.focus({ preventScroll: true }), 120);
  }
  input.outerHTML = '<textarea id="assistantInput" autocomplete="off" rows="2" placeholder="Message Sheetal… e.g. turn this into a note, plan my day, or open Spotify" aria-label="Instruction for assistant"></textarea>';
  const composerInput = document.getElementById('assistantInput');
  const history = document.createElement('div');
  history.className = 'assistant-history';
  history.setAttribute('aria-live', 'polite');
  form.before(history);
  const composer = document.createElement('div');
  composer.className = 'workspace-composer';
  form.replaceWith(composer);
  composer.append(form);
  const tools = document.createElement('div');
  tools.className = 'workspace-tools';
  tools.innerHTML = '<button type="button" data-workspace="notes">NOTEPAD</button><button type="button" data-workspace="tasks">OPEN TASKS</button><button type="button" data-workspace="plan">PLAN MY DAY</button><button type="button" data-workspace="note">+ SAVE NOTE</button>';
  const footer = document.createElement('div');
  footer.className = 'composer-footer';
  footer.innerHTML = '<span class="composer-hint">Enter to send · Shift+Enter for a new line</span><div class="composer-actions"><button type="button" id="workspaceVoice">VOICE</button><button type="button" id="workspaceCapabilities">CAPABILITIES</button></div>';
  composer.append(footer);
  composer.after(tools);

  const drawer = document.createElement('section');
  drawer.className = 'notepad-drawer';
  drawer.innerHTML = '<div class="notepad-head"><strong id="workspaceDrawerTitle">Notepad</strong><button type="button" id="workspaceDrawerClose" aria-label="Close">×</button></div><div class="notepad-list" id="workspaceDrawerList"></div><form class="notepad-compose" id="notepadCompose"><input id="notepadTitle" placeholder="Note title"><textarea id="notepadBody" placeholder="Write a note…"></textarea><button type="submit">SAVE NOTE</button></form>';
  tools.after(drawer);

  const addBubble = (text, kind) => {
    if (!text) return;
    const bubble = document.createElement('div');
    bubble.className = 'assistant-bubble ' + kind;
    bubble.textContent = text;
    history.append(bubble);
    history.scrollTop = history.scrollHeight;
  };
  const openDrawer = async (mode) => {
    drawer.classList.add('open');
    const titleNode = document.getElementById('workspaceDrawerTitle');
    const list = document.getElementById('workspaceDrawerList');
    const compose = document.getElementById('notepadCompose');
    compose.hidden = mode !== 'notes';
    titleNode.textContent = mode === 'tasks' ? 'Open tasks' : mode === 'plan' ? 'Today’s plan' : 'Notepad';
    list.innerHTML = '<div>Loading…</div>';
    try {
      const response = await fetch('/api/assistant/dashboard', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'Could not load this');
      if (mode === 'tasks') {
        const tasks = (data.tasks || []).filter(item => item.status !== 'done');
        list.innerHTML = tasks.length ? tasks.map(item => `<div>□ ${esc(item.title)}${item.due ? ` · ${esc(item.due)}` : ''}</div>`).join('') : '<div>No open tasks.</div>';
      } else if (mode === 'plan') {
        const plan = (data.plans || []).slice(-1)[0];
        list.innerHTML = plan ? `<div><strong>${esc(plan.date || 'Today')}</strong></div><div>${esc(plan.summary || plan.focus || JSON.stringify(plan))}</div>` : '<div>No plan yet. Ask Sheetal to plan your day.</div>';
      } else {
        const notes = [...(data.notes || [])].reverse();
        list.innerHTML = notes.length ? notes.map(item => `<article class="notepad-item"><strong>${esc(item.title)}</strong><span>${esc(item.body)}</span></article>`).join('') : '<div>No notes yet.</div>';
      }
    } catch (error) { list.innerHTML = `<div>${esc(error.message || 'Unavailable')}</div>`; }
  };

  tools.addEventListener('click', event => {
    const button = event.target.closest('[data-workspace]');
    if (!button) return;
    const mode = button.dataset.workspace;
    if (mode === 'note') {
      openDrawer('notes');
      document.getElementById('notepadBody')?.focus();
      return;
    }
    if (mode === 'plan') {
      composerInput.value = 'Plan my day from my open tasks, notes, and calendar context.';
      composerInput.focus();
      return;
    }
    openDrawer(mode);
  });
  const capabilityPopover = command.querySelector('.capability-popover');
  const toggleCapabilities = () => { capabilityPopover.hidden = !capabilityPopover.hidden; };
  document.getElementById('capabilityButton')?.addEventListener('click', toggleCapabilities);
  document.getElementById('workspaceCapabilities')?.addEventListener('click', toggleCapabilities);
  document.getElementById('workspaceVoice')?.addEventListener('click', () => document.getElementById('topRj')?.click());
  document.getElementById('workspaceDrawerClose').addEventListener('click', () => drawer.classList.remove('open'));
  document.getElementById('notepadCompose').addEventListener('submit', async event => {
    event.preventDefault();
    const titleValue = document.getElementById('notepadTitle').value.trim();
    const bodyValue = document.getElementById('notepadBody').value.trim();
    if (!bodyValue) return;
    const response = await fetch('/api/notes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: titleValue || 'Quick note', body: bodyValue }) });
    if (!response.ok) return;
    document.getElementById('notepadTitle').value = '';
    document.getElementById('notepadBody').value = '';
    addBubble('Saved to the notepad.', 'assistant');
    openDrawer('notes');
  });
  form.addEventListener('submit', () => {
    const text = composerInput.value.trim();
    if (text) addBubble(text, 'user');
  });
  composerInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.addEventListener('sheetal:assistant-message', event => {
    if (event.detail?.source !== 'user') addBubble(event.detail?.text, 'assistant');
  });
  document.getElementById('assistantActionDismiss')?.addEventListener('click', () => {
    const panel = document.getElementById('assistantActions');
    if (panel) panel.hidden = true;
  });
})();
