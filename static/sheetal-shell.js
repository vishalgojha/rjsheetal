/* Installable surfaces: a tiny desktop widget and a controller-first phone UI. */
(function () {
  const params = new URLSearchParams(location.search);
  const mode = params.get('widget') === '1' ? 'widget' : params.get('controller') === '1' ? 'controller' : '';
  if (mode) document.body.classList.add('sheetal-' + mode);
  // Desktop root is the agent workspace too. The legacy dashboard remains
  // available to mobile navigation, but should never compete with the main
  // command surface on a desktop/widget window.
  if (!mode && window.matchMedia('(min-width: 700px)').matches) document.body.classList.add('sheetal-widget');

  const style = document.createElement('style');
  style.textContent = `
    .sheetal-widget { background:#171212 !important; }
    .sheetal-widget .app { max-width:440px; min-height:100vh; padding:18px; background:rgba(27,20,18,.96)!important; border:1px solid rgba(255,255,255,.1); border-radius:24px; box-shadow:0 18px 60px rgba(0,0,0,.35); }
    .sheetal-widget .topbar { margin-bottom:14px; }
    .sheetal-widget .hello small { color:#c9aaa0; }
    .sheetal-widget .hello strong { color:#fff8f3; }
    .sheetal-widget .top-rj, .sheetal-widget #muteBtn, .sheetal-widget .bottom-nav, .sheetal-widget .role-switcher, .sheetal-widget .quick-grid, .sheetal-widget .playlist-panel, .sheetal-widget .life-panel, .sheetal-widget .email-panel, .sheetal-widget .view:not([data-view="home"]) { display:none!important; }
    .sheetal-widget .assistant-command { margin:0 0 12px!important; }
    .sheetal-widget .view[data-view="home"] { display:block!important; }
    .sheetal-widget .view[data-view="home"] > .eyebrow, .sheetal-widget .view[data-view="home"] > h1, .sheetal-widget .view[data-view="home"] > .sub, .sheetal-widget .view[data-view="home"] > .section-head, .sheetal-widget .view[data-view="home"] > .music-connection, .sheetal-widget .view[data-view="home"] > .device-handoff { display:none; }
    .sheetal-widget .live-card { margin-top:0!important; min-height:0!important; padding:16px!important; border-radius:18px!important; }
    .sheetal-widget .live-card .record-wrap, .sheetal-widget .hero-actions { display:none; }
    .sheetal-widget .now-card { margin-top:12px; }
    .sheetal-widget .assistant-command strong { font-size:16px!important; }
    .sheetal-controller .app { max-width:560px; }
    .sheetal-controller .view[data-view="home"] { display:block!important; }
    .sheetal-controller .view:not([data-view="home"]), .sheetal-controller .playlist-panel { display:none!important; }
    .sheetal-controller .bottom-nav { display:none; }
    .sheetal-controller .top-rj { display:none; }
    @media (min-width:700px) { .sheetal-widget .app { margin:18px; min-height:calc(100vh - 36px); } }
  `;
  document.head.append(style);

  let deferredInstall;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault(); deferredInstall = event;
    const button = document.getElementById('installBtn');
    if (button) button.hidden = false;
  });
  document.getElementById('installBtn')?.addEventListener('click', async () => {
    if (!deferredInstall) return;
    deferredInstall.prompt();
    await deferredInstall.userChoice;
    deferredInstall = null;
    document.getElementById('installBtn').hidden = true;
  });

  if (mode === 'widget' && params.get('focus') === 'ask') {
    setTimeout(() => document.getElementById('assistantInput')?.focus(), 500);
  }
})();
