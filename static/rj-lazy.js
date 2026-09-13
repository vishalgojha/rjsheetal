/* Assistant connection: voice is optional; typed chat uses the same agent. */
(function () {
  const voiceButton = document.getElementById('voiceToggle');
  const topButton = document.getElementById('topRj');
  const wakeButton = document.getElementById('wakeToggle');
  const status = document.getElementById('rjStatus');
  const orb = document.getElementById('agentOrb');
  const title = document.getElementById('agentTitle');
  const detail = document.getElementById('agentDetail');
  const chat = document.getElementById('assistantChat');
  const chatInput = document.getElementById('assistantInput');
  const chatButton = document.getElementById('assistantSend');
  const chatStatus = document.getElementById('assistantStatus');
  const chatReply = document.getElementById('assistantReply');
  if (!voiceButton || !topButton) return;

  const AGENT_ID = 'agent_8401m2cyznemf10tav3hh90nqya1';
  let session = null;
  let sessionKind = '';
  let loading = false;
  let wake = true;

  // Client tools run inside Sheetal's phone/browser. The agent itself runs on
  // ElevenLabs' infrastructure, so it cannot click an installed app directly.
  // We prepare a safe, user-tapped handoff instead.
  function appTarget(app, target, text, phone) {
    const name = String(app || '').trim().toLowerCase();
    const value = String(target || '').trim();
    const message = String(text || '').trim();
    if (name === 'spotify') {
      if (/^https:\/\/open\.spotify\.com\//i.test(value)) return { url: value, label: 'Open Spotify' };
      if (/^spotify:/i.test(value)) return { url: value, label: 'Open Spotify' };
      const query = value || message;
      if (!query) return null;
      return { url: 'https://open.spotify.com/search/' + encodeURIComponent(query), label: 'Open Spotify' };
    }
    if (name === 'whatsapp') {
      const digits = String(phone || '').replace(/[^0-9]/g, '');
      if (digits && !/^\d{7,15}$/.test(digits)) return null;
      const url = digits ? 'https://wa.me/' + digits : 'https://wa.me/';
      return { url: url + (message ? '?text=' + encodeURIComponent(message) : ''), label: message ? 'Open WhatsApp draft' : 'Open WhatsApp' };
    }
    if (name === 'phone' || name === 'call') {
      const digits = String(phone || value).replace(/[^0-9+]/g, '');
      if (!/^\+?[0-9]{7,15}$/.test(digits)) return null;
      return { url: 'tel:' + digits, label: 'Call ' + digits };
    }
    if (name === 'maps' || name === 'map') {
      const query = value || message;
      if (!query) return null;
      return { url: 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(query), label: 'Open Maps' };
    }
    return null;
  }

  function prepareAppAction(parameters) {
    const action = appTarget(parameters?.app, parameters?.target, parameters?.text, parameters?.phone);
    if (!action) return 'I could not prepare that app action. I need an app name and a valid target.';
    const panel = document.getElementById('assistantActions');
    if (!panel) return 'The app action is ready, but the action panel is unavailable.';
    const title = document.getElementById('assistantActionTitle');
    const detail = document.getElementById('assistantActionDetail');
    const button = document.getElementById('assistantActionButton');
    const dismiss = document.getElementById('assistantActionDismiss');
    if (title) title.textContent = action.label;
    if (detail) detail.textContent = parameters?.text ? 'Prepared on this phone. Review it before sending.' : 'Prepared on this phone. Tap to continue.';
    if (button) {
      button.textContent = action.label;
      button.onclick = () => { window.location.href = action.url; };
    }
    if (dismiss) dismiss.onclick = () => { panel.hidden = true; };
    panel.hidden = false;
    panel.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
    return 'The action is prepared on Sheetal’s phone. Tell her to tap the visible action button to continue. Do not claim that the other app opened or that a message was sent until she confirms it.';
  }

  function state(nextTitle, nextDetail, mode) {
    if (title) title.textContent = nextTitle;
    if (detail) detail.textContent = nextDetail;
    if (orb) orb.className = 'orb ' + (mode || '');
  }

  function setChatStatus(text) {
    if (chatStatus) chatStatus.textContent = text;
  }

  async function loadClient() {
    return import('https://esm.sh/@elevenlabs/client');
  }

  async function start(kind) {
    if (loading || session) return session;
    loading = true;
    sessionKind = kind;
    voiceButton.disabled = true;
    if (kind === 'voice') voiceButton.textContent = 'CONNECTING…';
    topButton.textContent = 'CONNECTING…';
    state(kind === 'voice' ? 'Starting voice' : 'Starting chat', 'Connecting to your assistant…', '');
    setChatStatus(kind === 'voice' ? 'Allow microphone access when the browser asks.' : 'Connecting securely…');

    try {
      document.dispatchEvent(new CustomEvent('sheetal:rj-pause'));
      const { Conversation } = await loadClient();

      if (kind === 'voice') {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
          throw Error('Voice needs HTTPS and a supported browser. You can still use typed chat.');
        }
        // Ask from the click gesture so browsers do not defer the prompt.
        const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
        microphone.getTracks().forEach(track => track.stop());
      }

      session = await Conversation.startSession({
        agentId: AGENT_ID,
        connectionType: kind === 'voice' ? 'webrtc' : 'websocket',
        clientTools: {
          open_external_app: prepareAppAction,
        },
        onConnect() {
          voiceButton.disabled = false;
          voiceButton.textContent = 'VOICE ON';
          voiceButton.classList.add('on');
          topButton.textContent = 'STOP';
          topButton.classList.add('on');
          state(kind === 'voice' ? 'Voice ready' : 'Chat ready', kind === 'voice' ? 'You can speak now. Music is paused.' : 'Type another request whenever you need help.', 'listening');
          setChatStatus(kind === 'voice' ? (wake ? 'Listening · say “Hey Radio” or speak.' : 'Listening · direct talk mode.') : 'Connected · your message will be answered here.');
        },
        onStatusChange({ status: nextStatus }) {
          if (nextStatus === 'connecting') state('Connecting', 'Almost there…', '');
          if (nextStatus === 'disconnected' && !session) state('Assistant offline', 'Try again in a moment, or use typed chat.', '');
        },
        onDisconnect() {
          session = null;
          sessionKind = '';
          loading = false;
          voiceButton.disabled = false;
          voiceButton.textContent = 'TALK TO ASSISTANT';
          voiceButton.classList.remove('on');
          topButton.classList.remove('on');
          topButton.textContent = 'ASK ASSISTANT';
          if (status) status.textContent = 'Ready when you need help.';
          state('Assistant is off', 'Music can continue. Start voice or send a typed message.', '');
          setChatStatus('Ready for another request.');
          document.dispatchEvent(new CustomEvent('sheetal:rj-resume'));
        },
        onError(error) {
          console.error('Assistant connection error', error);
          session = null;
          sessionKind = '';
          loading = false;
          voiceButton.disabled = false;
          voiceButton.textContent = 'TALK TO ASSISTANT';
          voiceButton.classList.remove('on');
          topButton.classList.remove('on');
          topButton.textContent = 'ASK ASSISTANT';
          const message = error?.message || 'The assistant could not connect.';
          if (status) status.textContent = message.slice(0, 180);
          state('Could not connect', kind === 'voice' ? 'Check microphone permission, then try again.' : 'Try sending again in a moment.', '');
          setChatStatus(kind === 'voice' ? 'Voice failed. You can still try typed chat.' : 'Chat failed. Try again or use voice.');
          document.dispatchEvent(new CustomEvent('sheetal:rj-resume'));
        },
        onMessage(message) {
          const text = typeof message === 'string' ? message : message?.message;
          const source = message?.source;
          if (!text) return;
          fetch('/api/memory/event', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'conversation', text, source }), keepalive: true,
          }).catch(() => {});
          if (source === 'user') {
            document.dispatchEvent(new CustomEvent('sheetal:rj-command', { detail: { text } }));
          } else {
            if (chatReply) chatReply.textContent = text;
            setChatStatus('Assistant replied.');
          }
        },
        onModeChange({ mode }) {
          const speaking = mode === 'speaking';
          state(speaking ? 'Assistant speaking' : 'Assistant listening', speaking ? 'You can interrupt if needed.' : 'Your turn.', mode);
          if (status) status.textContent = speaking ? 'Assistant is speaking.' : 'Listening.';
        },
      });
      return session;
    } catch (error) {
      loading = false;
      session = null;
      sessionKind = '';
      voiceButton.disabled = false;
      voiceButton.textContent = 'TALK TO ASSISTANT';
      voiceButton.classList.remove('on');
      topButton.classList.remove('on');
      topButton.textContent = 'ASK ASSISTANT';
      const message = error?.name === 'NotAllowedError'
        ? 'Microphone permission was blocked. Allow it from the address bar and try again.'
        : (error?.message || 'Assistant service is unavailable.');
      if (status) status.textContent = message;
      state('Assistant is off', 'Try again when you are ready.', '');
      setChatStatus(kind === 'voice' ? message : 'Typed chat is unavailable right now. Try again.');
      document.dispatchEvent(new CustomEvent('sheetal:rj-resume'));
      throw error;
    }
  }

  async function stop() {
    if (!session) return;
    const current = session;
    session = null;
    try { await current.endSession(); } catch (_) {}
  }

  async function sendTypedMessage(event) {
    event?.preventDefault();
    const text = chatInput?.value.trim();
    if (!text || loading) return;
    if (chatButton) chatButton.disabled = true;
    if (chatReply) chatReply.textContent = '';
    setChatStatus(session ? 'Sending…' : 'Connecting typed chat…');
    try {
      if (!session) await start('text');
      if (!session?.sendUserMessage) throw Error('This assistant does not support typed messages yet.');
      session.sendUserActivity?.();
      session.sendUserMessage(text);
      chatInput.value = '';
      setChatStatus('Sent · waiting for the assistant…');
    } catch (error) {
      setChatStatus(error?.message || 'Could not send that request.');
    } finally {
      if (chatButton) chatButton.disabled = false;
    }
  }

  voiceButton.textContent = 'TALK TO ASSISTANT';
  voiceButton.addEventListener('click', () => session ? stop() : start('voice'));
  topButton.addEventListener('click', () => session ? stop() : start('voice'));
  chat?.addEventListener('submit', sendTypedMessage);
  chatInput?.addEventListener('input', () => session?.sendUserActivity?.());
  wakeButton?.addEventListener('click', () => {
    wake = !wake;
    wakeButton.textContent = wake ? 'WAKE: HEY RADIO' : 'DIRECT TALK';
    wakeButton.classList.toggle('off', !wake);
    if (session) setChatStatus(wake ? 'Wake mode · say “Hey Radio”.' : 'Direct talk mode enabled.');
  });

  document.addEventListener('sheetal:call-rj', () => { if (!session) start('voice'); });
  setInterval(() => {
    const active = !!session || loading;
    topButton.classList.toggle('on', active);
    topButton.textContent = active ? 'STOP' : 'ASK ASSISTANT';
    topButton.setAttribute('aria-pressed', active ? 'true' : 'false');
    if (!loading) voiceButton.textContent = session ? 'VOICE ON' : 'TALK TO ASSISTANT';
  }, 250);
  setInterval(() => { if (session) window.loadQueue?.(); }, 1500);
})();
