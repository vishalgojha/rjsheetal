/* The ElevenLabs client is downloaded only after the listener asks for RJ. */
(function(){
  const button = document.getElementById('voiceToggle'), wakeButton = document.getElementById('wakeToggle');
  const status = document.getElementById('rjStatus'), orb = document.getElementById('agentOrb'), title = document.getElementById('agentTitle'), detail = document.getElementById('agentDetail');
  let session = null, wake = true, loading = false;
  function state(a,b,c){ title.textContent=a; detail.textContent=b; orb.className='orb '+(c||''); }
  function istNow(){ return new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date())+' IST'; }
  async function start(){
    if (loading) return; loading = true; button.disabled = true; button.textContent = 'LOADING…'; state('Loading RJ…','Preparing the Hindi voice only now.','');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw Error('Microphone access requires HTTPS and a supported browser.');
      const {Conversation} = await import('https://esm.sh/@elevenlabs/client@1.22.0');
      const microphone = await navigator.mediaDevices.getUserMedia({audio:true});
      microphone.getTracks().forEach(track => track.stop());
      session = await Conversation.startSession({agentId:'agent_8401m2cyznemf10tav3hh90nqya1', overrides:{agent:{firstMessage:'',prompt:`You are Sheetal FM’s Hindi RJ. The current India time is ${istNow()}. Use Asia/Kolkata/IST for time questions. Stay silent unless directly addressed with Hey Radio. Reply warmly and briefly in Hindi.`}}, onConnect(){button.disabled=false;button.textContent='VOICE ON';button.classList.add('on');status.textContent=wake?'On call · say “Hey Radio”.':'Direct talk mode.';state('RJ on call','Hindi voice is ready.','listening')}, onDisconnect(){session=null;button.disabled=false;button.textContent='VOICE OFF';button.classList.remove('on');status.textContent='On-call mode.';state('Voice is off','The station keeps playing.','')}, onError(){status.textContent='RJ is temporarily unavailable.'}, onModeChange({mode}){state(mode==='speaking'?'RJ speaking':'RJ listening',mode==='speaking'?'Sheetal FM is on air.':'Waiting for Sheetal.',mode)}});
    } catch (error) { console.error('Sheetal FM RJ startup failed',error); button.disabled=false;button.textContent='TALK TO RJ';status.textContent=error.name==='NotAllowedError'?'Microphone permission was blocked. Allow it in the address bar and try again.':(error.message||'RJ service is unavailable.');state('Voice is off','Try again when you are ready.',''); }
    loading = false;
  }
  button.textContent='TALK TO RJ'; button.addEventListener('click', async () => { if(session){await session.endSession();session=null;} else await start(); });
  wakeButton.addEventListener('click', () => { wake=!wake; wakeButton.textContent=wake?'WAKE: HEY RADIO':'DIRECT TALK'; wakeButton.classList.toggle('off',!wake); if(session)status.textContent=wake?'Wake mode · say “Hey Radio”.':'Direct talk mode enabled.'; });
})();
