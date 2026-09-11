async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const s = await res.json();
    document.getElementById('st-state').textContent = 'online';
    document.getElementById('st-backend').textContent = s.realtimeBackend;
    document.getElementById('st-telnyx').textContent = s.telnyxBackend;
    document.getElementById('st-model').textContent = s.model;
    document.getElementById('st-active').textContent = s.activeCalls;
  } catch {
    document.getElementById('st-state').textContent = 'offline';
  }
}

function addBubble(entry) {
  const el = document.createElement('div');
  el.className = `bubble ${entry.role}`;
  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = entry.role === 'agent' ? 'AI Agent' : 'Caller';
  const text = document.createElement('span');
  text.textContent = entry.text;
  el.appendChild(who);
  el.appendChild(text);
  return el;
}

async function runCall() {
  const btn = document.getElementById('run');
  const transcript = document.getElementById('transcript');
  btn.disabled = true;
  btn.textContent = 'Calling…';
  transcript.innerHTML = '<p class="muted">Dialing and streaming audio…</p>';

  try {
    const res = await fetch('/demo/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const result = await res.json();

    transcript.innerHTML = '';
    for (const entry of result.transcript) {
      transcript.appendChild(addBubble(entry));
    }

    document.getElementById('m-in').textContent = result.metrics.framesFromCaller;
    document.getElementById('m-out').textContent = result.metrics.framesToCaller;
    document.getElementById('m-dur').textContent = result.durationMs;
    document.getElementById('metrics').classList.remove('hidden');

    const player = document.getElementById('player');
    player.src = `data:audio/wav;base64,${result.agentAudioWavBase64}`;
    player.classList.remove('hidden');

    await loadStatus();
  } catch (err) {
    transcript.innerHTML = `<p class="muted">Call failed: ${err}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Place simulated call';
  }
}

document.getElementById('run').addEventListener('click', runCall);
loadStatus();
