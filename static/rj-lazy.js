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
  const textToggle = document.getElementById('textToggle');
  if (!voiceButton || !topButton) return;

  const AGENT_ID = 'agent_8401m2cyznemf10tav3hh90nqya1';
  // ElevenLabs exposes output volume on a 0–1 scale. Keep the assistant at
  // the loudest supported level without changing Spotify's player volume.
  const AGENT_OUTPUT_VOLUME = 1;
  let session = null;
  let sessionKind = '';
  let loading = false;
  let wake = true;

  // Client tools are executed by the same-origin bridge proxy. The agent runs
  // on ElevenLabs, while the authenticated bridge performs the laptop action.
  async function bridgeTool(name, parameters) {
    const response = await fetch('/api/bridge/tool', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, parameters: parameters || {} }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw Error(data.error || 'laptop bridge action failed');
    return data.result || 'done';
  }
  function searchAction(label, query, webUrl, appUrl) {
    const cleanQuery = String(query || '').trim();
    return {
      url: webUrl,
      appUrl: appUrl || '',
      label,
      fallbackLabel: 'OPEN WEB SEARCH',
      query: cleanQuery,
      newTab: true,
    };
  }

  function appTarget(app, target, text, phone) {
    const name = String(app || '').trim().toLowerCase();
    const value = String(target || '').trim();
    const message = String(text || '').trim();
    if (name === 'browser' || name === 'web' || name === 'chrome' || name === 'tab') {
      if (!/^https?:\/\//i.test(value)) return null;
      return { url: value, label: 'Open in browser', newTab: true };
    }
    if (name === 'spotify') {
      if (/^https:\/\/open\.spotify\.com\//i.test(value)) return { url: value, label: 'OPEN SPOTIFY', newTab: true };
      if (/^spotify:/i.test(value)) return { url: value, label: 'OPEN SPOTIFY' };
      const query = value || message;
      if (!query) return searchAction('OPEN SPOTIFY', '', 'https://open.spotify.com/', 'spotify:');
      return searchAction('OPEN SPOTIFY', query, 'https://open.spotify.com/search/' + encodeURIComponent(query), 'spotify:search:' + encodeURIComponent(query));
    }
    if (name === 'youtube_music' || name === 'youtube music' || name === 'ytmusic' || name === 'music.youtube.com') {
      const query = value || message;
      if (!query) return searchAction('OPEN YOUTUBE MUSIC', '', 'https://music.youtube.com/', 'youtubemusic://');
      return searchAction('OPEN YOUTUBE MUSIC', query, 'https://music.youtube.com/search?q=' + encodeURIComponent(query), 'youtubemusic://search?q=' + encodeURIComponent(query));
    }
    if (name === 'youtube' || name === 'youtube.com') {
      const query = value || message;
      if (!query) return searchAction('OPEN YOUTUBE', '', 'https://www.youtube.com/', '');
      return searchAction('OPEN YOUTUBE', query, 'https://www.youtube.com/results?search_query=' + encodeURIComponent(query), '');
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

  async function prepareAppAction(parameters) {
    if (String(parameters?.app || '').trim().toLowerCase() === 'spotify' && window.sheetalMusicControl) {
      try {
        const result = await window.sheetalMusicControl({ action: 'play_song', query: parameters?.target || parameters?.text || '' });
        if (result?.ok) return `Playing ${result.title || 'the requested song'} on ${result.device || 'Spotify'}. Playback was verified.`;
      } catch (error) {
        console.info('Direct Spotify playback unavailable; preparing handoff.', error);
      }
    }
    try {
      const result = await bridgeTool('open_external_app', parameters);
      return `Sent to the laptop bridge — ${parameters?.app || 'the requested app'} is being opened there. ${result}`;
    } catch (error) {
      console.info('Laptop bridge unavailable; preparing phone handoff.', error);
    }
    const action = appTarget(parameters?.app, parameters?.target, parameters?.text, parameters?.phone);
    if (!action) return 'I could not prepare that app action. I need an app name and a valid target.';
    const panel = document.getElementById('assistantActions');
    if (!panel) return 'The app action is ready, but the action panel is unavailable.';
    const title = document.getElementById('assistantActionTitle');
    const detail = document.getElementById('assistantActionDetail');
    const button = document.getElementById('assistantActionButton');
    const fallback = document.getElementById('assistantActionFallback');
    const dismiss = document.getElementById('assistantActionDismiss');
    if (title) title.textContent = action.label;
    if (detail) {
      detail.textContent = action.query
        ? `Search prepared for “${action.query}”. Tap to open the app; if it is unavailable, use web search.`
        : 'Prepared on this phone. Tap to continue.';
    }
    if (button) {
      button.textContent = action.label;
      button.onclick = () => {
        const url = action.appUrl || action.url;
        if (action.newTab && !action.appUrl) {
          const tab = window.open(url, '_blank', 'noopener,noreferrer');
          if (!tab) window.location.href = url;
          return;
        }
        // A top-level navigation lets Android/iOS hand off supported app
        // schemes. A separate web button below is the reliable fallback.
        window.location.href = url;
      };
    }
    if (fallback) {
      fallback.hidden = !action.fallbackLabel || !action.url || !action.appUrl;
      fallback.textContent = action.fallbackLabel || 'OPEN WEB SEARCH';
      fallback.onclick = () => {
        const tab = window.open(action.url, '_blank', 'noopener,noreferrer');
        if (!tab) window.location.href = action.url;
      };
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

  function focusTextAssistant(prompt = '') {
    if (!chatInput) return;
    if (prompt) chatInput.value = prompt;
    chatInput.focus({ preventScroll: false });
    chatInput.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    setChatStatus('Text mode · no microphone and no spoken reply.');
  }

  window.sheetalAssistantPrompt = focusTextAssistant;
  document.addEventListener('sheetal:assistant-prompt', event => focusTextAssistant(String(event.detail?.prompt || '')));

  async function loadClient() {
    return import('https://esm.sh/@elevenlabs/client');
  }

  async function loadAssistantContext() {
    const fallback = {
      assistant_context: 'No earlier conversation context is available. Start naturally and ask what Sheetal needs.',
      opening_line: 'I’m here. What would help?',
    };
    try {
      const response = await fetch('/api/agent/memory', { cache: 'no-store' });
      if (!response.ok) return fallback;
      const memory = await response.json();
      const turns = Array.isArray(memory?.recent_conversations) ? memory.recent_conversations : [];
      const useful = turns
        .filter(item => item && String(item.text || '').trim())
        .slice(-8)
        .map(item => `${item.source === 'user' ? 'Sheetal' : 'Assistant'}: ${String(item.text).trim().slice(0, 280)}`);
      if (!useful.length) return fallback;
      return {
        assistant_context: useful.join('\n'),
        opening_line: 'I’m here with you. We can continue from last time, or start with something new.',
      };
    } catch (_) {
      return fallback;
    }
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
      const assistantContext = await loadAssistantContext();

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
        // Keep typed requests genuinely silent. The SDK uses this flag to
        // create its lighter TextConversation and the override tells the
        // agent not to generate audio for this session.
        ...(kind === 'text' ? {
          textOnly: true,
          overrides: {
            conversation: { textOnly: true },
            // Text chat should wait for Sheetal's request instead of adding
            // another automatic greeting on top of the typed response.
            agent: { firstMessage: '' },
          },
        } : {}),
        dynamicVariables: assistantContext,
        clientTools: {
          open_external_app: prepareAppAction,
          open_app: (parameters) => bridgeTool('open_app', parameters),
          ui_type: (parameters) => bridgeTool('ui_type', parameters),
          ui_click: (parameters) => bridgeTool('ui_click', parameters),
          type_text: (parameters) => bridgeTool('type_text', parameters),
          press_key: (parameters) => bridgeTool('press_key', parameters),
          browser_open: (parameters) => bridgeTool('browser_open', parameters),
          browser_click: (parameters) => bridgeTool('browser_click', parameters),
          open_url: (parameters) => bridgeTool('open_url', parameters),
          playwright_run: (parameters) => bridgeTool('playwright_run', parameters),
        },
        onConnect() {
          voiceButton.disabled = false;
          voiceButton.textContent = kind === 'voice' ? 'VOICE ON' : 'VOICE OFF';
          voiceButton.classList.toggle('on', kind === 'voice');
          topButton.textContent = 'STOP';
          topButton.classList.add('on');
          state(kind === 'voice' ? 'Voice ready' : 'Chat ready', kind === 'voice' ? 'You can speak now. Music is paused.' : 'Type another request whenever you need help.', 'listening');
          setChatStatus(kind === 'voice' ? (wake ? 'Listening · say “Hey Radio” or speak.' : 'Listening · direct talk mode.') : 'Text mode · no microphone and no spoken reply.');
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
          document.dispatchEvent(new CustomEvent('sheetal:assistant-message', { detail: { text, source } }));
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
        onAgentChatResponsePart(part) {
          if (sessionKind !== 'text') return;
          const partType = part?.type || part?.event || '';
          if (partType === 'stop' || partType === 'end') {
            setChatStatus('Assistant replied.');
            return;
          }
          const text = typeof part === 'string'
            ? part
            : (part?.text || part?.delta || part?.message || part?.agentChatResponsePart?.text || '');
          if (!text) return;
          if (part?.type === 'start' && chatReply) chatReply.textContent = '';
          if (chatReply) chatReply.textContent += text;
          setChatStatus('Assistant is replying in text…');
        },
        onModeChange({ mode }) {
          const speaking = mode === 'speaking';
          state(speaking ? 'Assistant speaking' : 'Assistant listening', speaking ? 'You can interrupt if needed.' : 'Your turn.', mode);
          if (status) status.textContent = speaking ? 'Assistant is speaking.' : 'Listening.';
        },
      });
      if (typeof session?.setVolume === 'function') {
        try {
          await session.setVolume({ volume: AGENT_OUTPUT_VOLUME });
        } catch (_) {}
      }
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
  textToggle?.addEventListener('click', () => {
    if (sessionKind === 'voice') stop();
    focusTextAssistant();
  });
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
