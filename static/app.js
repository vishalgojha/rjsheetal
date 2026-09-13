/* Sheetal FM radio shell. Feature data is fetched only when needed. */
const $ = id => document.getElementById(id);
const audio = $('audio');
let toastTimer, statusTimer;

function toast(message) {
  const node = $('toast'); node.textContent = message; node.classList.add('toast-show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('toast-show'), 2600);
}
async function getJSON(url, options = {}, timeout = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {...options, signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'request failed');
    return data;
  } finally { clearTimeout(timer); }
}
function esc(value) { return String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function setPlaying(playing) {
  document.querySelectorAll('.record').forEach(node => node.classList.toggle('paused', !playing));
  document.querySelectorAll('.mini-play,.round-play').forEach(node => node.classList.toggle('playing', playing));
  $('playerPlay').querySelector('.pause').style.display = playing ? 'block' : 'none';
  $('playerPlay').querySelector('.play-icon').style.display = playing ? 'none' : 'block';
}
function setMetadata(title, artist, art) {
  title = title || 'Waiting for the show'; artist = artist || 'Sheetal FM';
  ['homeTitle','nowTitle','playerTitle'].forEach(id => $(id).textContent = title);
  $('homeArtist').textContent = artist; $('nowArtist').textContent = artist; $('playerArtist').textContent = artist;
  document.querySelectorAll('#recordHome,#recordPlayer').forEach(node => { node.classList.toggle('has-art', !!art); node.style.backgroundImage = art ? `url("${art.replace(/"/g, '%22')}")` : ''; });
}
async function refreshStatus() {
  try {
    const data = await getJSON('/api/status', {}, 6000);
    $('airText').textContent = data.on_air ? 'LIVE' : 'OFF AIR';
    $('listenerLabel').textContent = `${data.listeners || 0} listening`;
    if (data.title) setMetadata(data.title, data.artist || 'Sheetal FM · Live Radio', data.art);
    $('progressFill').style.width = data.on_air ? '100%' : '0%';
  } catch (_) { $('airText').textContent = 'OFF AIR'; }
}
function startRadio() {
  if (!audio.getAttribute('src')) audio.src = '/api/stream';
  refreshStatus(); clearInterval(statusTimer); statusTimer = setInterval(refreshStatus, 30000);
}
async function toggleAudio() {
  if (audio.paused) { startRadio(); try { await audio.play(); } catch (_) { toast('Tap Play again to start the live stream.'); } }
  else audio.pause();
}
function loadQueue() {
  getJSON('/api/queue', {}, 6000).then(data => {
    const items = (data.queue || []).filter(item => item.status !== 'done');
    $('queue').innerHTML = items.length ? items.map(item => `<div class="queue-item"><div class="cover">${item.art ? `<img src="${esc(item.art)}" alt="">` : '♪'}</div><div class="track"><strong>${esc(item.name)}</strong><span>${esc(item.artist || 'Sheetal FM')}${item.status === 'claimed' ? ' · ON AIR NOW' : ''}</span></div></div>`).join('') : '<div class="empty">No requests yet — be the first to put one on air.</div>';
  }).catch(() => $('queue').innerHTML = '<div class="empty">Queue temporarily unavailable.</div>');
}
async function requestSong(uri) {
  try { await getJSON('/api/request', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({uri})}, 8000); toast('Added to Sheetal’s queue.'); $('query').value = ''; $('results').innerHTML = ''; }
  catch (error) { toast(error.name === 'AbortError' ? 'Radio temporarily unavailable.' : (error.message || 'Could not request that song.')); }
}
async function loadPlaylist() {
  const button = $('playlistButton'), box = $('playlistResults');
  if (button.dataset.loaded === '1') { box.hidden = !box.hidden; return; }
  button.disabled = true; button.textContent = 'LOADING…'; box.hidden = false; box.innerHTML = '<div class="empty">Loading Sheetal’s playlist…</div>';
  try {
    const data = await getJSON('/api/playlist', {}, 10000);
    box.innerHTML = (data.results || []).map(track => `<div class="result"><div class="cover">${track.art ? `<img src="${esc(track.art)}" alt="">` : '♪'}</div><div class="track"><strong>${esc(track.name)}</strong><span>${esc(track.artist)}</span></div><button data-uri="${esc(track.uri)}">PLAY NEXT</button></div>`).join('') || '<div class="empty">No playlist tracks available.</div>';
    box.querySelectorAll('button').forEach(item => item.addEventListener('click', () => requestSong(item.dataset.uri)));
    button.dataset.loaded = '1'; button.textContent = 'HIDE TRACKS';
  } catch (_) { box.innerHTML = '<div class="empty">Spotify playlist temporarily unavailable.</div>'; button.textContent = 'TRY AGAIN'; }
  finally { button.disabled = false; }
}
let searchController;
async function searchSongs() {
  const query = $('query').value.trim(), box = $('results');
  if (query.length < 2) { box.innerHTML = '<div class="empty">Type at least two characters to search Spotify.</div>'; return; }
  if (searchController) searchController.abort();
  searchController = new AbortController(); const current = searchController;
  box.innerHTML = '<div class="empty">Searching Spotify…</div>'; $('searchButton').disabled = true; $('searchButton').textContent = 'WAIT…';
  const timer = setTimeout(() => current.abort(), 8000);
  try {
    const response = await fetch('/api/search?q=' + encodeURIComponent(query), {signal: current.signal});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Spotify search unavailable');
    box.innerHTML = (data.results || []).map(track => `<div class="result"><div class="cover">${track.art ? `<img src="${esc(track.art)}" alt="">` : '♪'}</div><div class="track"><strong>${esc(track.name)}</strong><span>${esc(track.artist)} · ${Math.floor((track.dur_ms || 0) / 60000)}:${String(Math.floor((track.dur_ms || 0) / 1000) % 60).padStart(2, '0')}</span></div><button data-uri="${esc(track.uri)}">PLAY NEXT</button></div>`).join('') || '<div class="empty">No Spotify tracks found.</div>';
    box.querySelectorAll('button').forEach(button => button.addEventListener('click', () => requestSong(button.dataset.uri)));
  } catch (error) { if (error.name !== 'AbortError' || searchController === current) box.innerHTML = '<div class="empty">Spotify temporarily unavailable. Try again shortly.</div>'; }
  finally { clearTimeout(timer); if (searchController === current) searchController = null; $('searchButton').disabled = false; $('searchButton').textContent = 'SEARCH'; }
}
function showView(name) {
  document.querySelectorAll('.view').forEach(view => view.classList.toggle('active', view.dataset.view === name));
  document.querySelectorAll('[data-nav]').forEach(button => button.classList.toggle('active', button.dataset.nav === name));
  if (name === 'queue') loadQueue();
}
document.querySelectorAll('[data-nav]').forEach(button => button.addEventListener('click', () => showView(button.dataset.nav)));
$('homePlay').addEventListener('click', toggleAudio); $('playerPlay').addEventListener('click', toggleAudio);
$('muteBtn').addEventListener('click', () => { audio.muted = !audio.muted; $('muteBtn').style.color = audio.muted ? 'var(--orange)' : ''; });
audio.addEventListener('play', () => setPlaying(true)); audio.addEventListener('pause', () => setPlaying(false)); audio.addEventListener('error', () => { setPlaying(false); toast('Radio temporarily unavailable.'); });
$('searchButton').addEventListener('click', searchSongs); $('query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchSongs(); } });
$('playlistButton').addEventListener('click', loadPlaylist); $('playlistResults').hidden = true;
if ('mediaSession' in navigator) for (const action of ['play','pause']) try { navigator.mediaSession.setActionHandler(action, () => action === 'play' ? toggleAudio() : audio.pause()); } catch (_) {}
refreshStatus();
