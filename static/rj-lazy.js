/* The ElevenLabs client is downloaded only after the listener asks for RJ. */
(function(){
  const button = document.getElementById('voiceToggle'), wakeButton = document.getElementById('wakeToggle');
  const status = document.getElementById('rjStatus'), orb = document.getElementById('agentOrb'), title = document.getElementById('agentTitle'), detail = document.getElementById('agentDetail');
  let session = null, wake = true, loading = false;
  function state(a,b,c){ title.textContent=a; detail.textContent=b; orb.className='orb '+(c||''); }
  function istNow(){ return new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date())+' IST'; }
  async function memory(){ try { const r=await fetch('/api/memory/context'); return r.ok ? await r.json() : {}; } catch (_) { return {}; } }
  async function start(){
    if (loading) return; loading = true; button.disabled = true; button.textContent = 'LOADING…'; state('Loading RJ…','Preparing the Hindi voice only now.','');
    try {
      document.dispatchEvent(new CustomEvent('sheetal:rj-pause'));
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw Error('Microphone access requires HTTPS and a supported browser.');
      // Use the current browser SDK and the public WebRTC path. Keeping the
      // session options minimal is important: this agent does not allow client
      // prompt/first-message overrides, and malformed overrides make the
      // ElevenLabs session fail with "Invalid message received" immediately.
      const {Conversation} = await import('https://esm.sh/@elevenlabs/client');
      const microphone = await navigator.mediaDevices.getUserMedia({audio:true});
      microphone.getTracks().forEach(track => track.stop());
      // The agent is public, so no ElevenLabs API key belongs in this page.
      // IST, personality, and memory are configured server-side in the agent.
      session = await Conversation.startSession({agentId:'agent_8401m2cyznemf10tav3hh90nqya1', connectionType:'webrtc', onConnect(){button.disabled=false;button.textContent='VOICE ON';button.classList.add('on');status.textContent=wake?'On call · say “Hey Radio”.':'Direct talk mode.';state('RJ on call','Hindi voice is ready. Music is paused while we talk.','listening')}, onDisconnect(){session=null;button.disabled=false;button.textContent='VOICE OFF';button.classList.remove('on');status.textContent='On-call mode.';state('Voice is off','The station keeps playing.','');document.dispatchEvent(new CustomEvent('sheetal:rj-resume'))}, onError(error){console.error('Sheetal FM ElevenLabs error',error);session=null;button.disabled=false;button.textContent='TALK TO RJ';button.classList.remove('on');status.textContent=error?.message?'RJ error: '+error.message.slice(0,140):'RJ connection error — try again.';state('Voice is off','The RJ session ended. Try again in a moment.','');document.dispatchEvent(new CustomEvent('sheetal:rj-resume'))}, onMessage(message){const text=typeof message==='string'?message:message?.message, source=message?.source;if(text)fetch('/api/memory/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'conversation',text,source}),keepalive:true}).catch(()=>{});if(text&&source==='user')document.dispatchEvent(new CustomEvent('sheetal:rj-command',{detail:{text}}));}, onModeChange({mode}){state(mode==='speaking'?'RJ speaking':'RJ listening',mode==='speaking'?'Sheetal FM is on air.':'Waiting for Sheetal.',mode)}});
    } catch (error) { console.error('Sheetal FM RJ startup failed',error); button.disabled=false;button.textContent='TALK TO RJ';status.textContent=error.name==='NotAllowedError'?'Microphone permission was blocked. Allow it in the address bar and try again.':(error.message||'RJ service is unavailable.');state('Voice is off','Try again when you are ready.',''); }
    loading = false;
  }
  button.textContent='TALK TO RJ'; button.addEventListener('click', async () => { if(session){await session.endSession();session=null;} else await start(); });
  document.addEventListener('sheetal:call-rj', () => { if(!session) start(); });
  wakeButton.addEventListener('click', () => { wake=!wake; wakeButton.textContent=wake?'WAKE: HEY RADIO':'DIRECT TALK'; wakeButton.classList.toggle('off',!wake); if(session)status.textContent=wake?'Wake mode · say “Hey Radio”.':'Direct talk mode enabled.'; });
  // RJ webhook requests are written by the server, so refresh the visible
  // queue while a conversation is active instead of waiting for navigation.
  setInterval(() => { if(session) window.loadQueue?.(); }, 1200);
})();
