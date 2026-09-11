const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const photoBtn = document.getElementById('photoBtn');
const photoInput = document.getElementById('photoInput');

const welcomeMessage = "Hello! I can help with love, career, finance, marriage, or kundali guidance. Ask me about consultations, packages, or booking support.";

function getSessionId() {
  let sessionId = sessionStorage.getItem('astro_session_id');
  if (!sessionId) {
    sessionId = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    sessionStorage.setItem('astro_session_id', sessionId);
  }
  return sessionId;
}

const history = [];

function appendMessage(sender, text) {
  const msg = document.createElement('div');
  msg.className = `message ${sender}`;
  msg.textContent = text;
  chatBody.appendChild(msg);
  chatBody.scrollTop = chatBody.scrollHeight;
  history.push({ sender, text });
  return msg;
}

function appendImageMessage(sender, url) {
  const msg = document.createElement('div');
  msg.className = `message ${sender} photo-message`;
  const img = document.createElement('img');
  img.src = url;
  img.alt = 'Uploaded photo';
  msg.appendChild(img);
  chatBody.appendChild(msg);
  chatBody.scrollTop = chatBody.scrollHeight;
  // No vision analysis on our side — this just threads a text marker into
  // history so the bot's reply at least knows a photo was shared.
  history.push({ sender, text: `[Shared a photo: ${url}]` });
}

if (chatBody && chatBody.children.length === 0) {
  appendMessage('bot', welcomeMessage);
}

async function sendToBot(text) {
  try {
    const response = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: text,
        session_id: getSessionId(),
        history: history.slice(0, -1)
      })
    });

    const data = await response.json();
    const answer = data.answer || "I don't have information regarding that.";
    setTimeout(() => {
      appendMessage('bot', answer);
      if (data.action && data.action.type === 'connect_popup') {
        renderConnectCard(data.action);
      }
    }, 250);
  } catch (error) {
    setTimeout(() => appendMessage('bot', "I don't have information regarding that."), 250);
  }
}

async function sendMessage() {
  const text = (chatInput.value || '').trim();
  if (!text) return;

  appendMessage('user', text);
  chatInput.value = '';
  await sendToBot(text);
}

async function handlePhotoUpload(file) {
  const formData = new FormData();
  formData.append('file', file);

  try {
    const response = await fetch('/upload', { method: 'POST', body: formData });
    const data = await response.json();
    if (!data.url) throw new Error('upload failed');
    appendImageMessage('user', data.url);
    // The marker also needs to be in the outgoing question text itself, not
    // just history — sendToBot's history payload excludes the message
    // currently being sent (history.slice(0, -1)), so a marker only pushed
    // via appendImageMessage would never actually reach the backend for
    // THIS turn. Embedding it here too means find_last_attachment_url()
    // can find it either way.
    await sendToBot(`I just shared a photo — can you connect me with someone for a face or palm reading based on it? [Shared a photo: ${data.url}]`);
  } catch (error) {
    appendMessage('bot', "Sorry, I couldn't upload that photo — please try again.");
  }
}

// Bridge to the React Native host app (react-native-webview convention):
// window.ReactNativeWebView.postMessage is injected automatically whenever
// this page is opened inside a RN <WebView>. The RN side's onMessage
// handler owns everything downstream — wallet-balance check, profile-
// created check, select-profile sheet, start call/chat — none of that is
// rebuilt here, we only hand off with a well-defined message.
//
// matchType 'best_match' (generic card) deliberately omits astrologerId —
// astrologer matching isn't this bot's job, the native app's existing
// recommendation system picks. 'specific' (visitor named someone) passes
// the real astrologerId so native can go straight to that person.
function triggerNativeConnect(mode, astrologer, isGeneric) {
  const payload = {
    type: 'CONNECT_ASTROLOGER',
    mode, // 'chat' | 'call'
    matchType: isGeneric ? 'best_match' : 'specific',
    astrologerId: isGeneric ? null : astrologer.id,
  };

  if (window.ReactNativeWebView && typeof window.ReactNativeWebView.postMessage === 'function') {
    window.ReactNativeWebView.postMessage(JSON.stringify(payload));
  } else {
    // Only reached outside the app's WebView (e.g. testing in a plain
    // browser) — makes the bridge call visible for local testing instead
    // of silently doing nothing.
    appendMessage('bot', `[dev fallback — no host app detected] Would send: ${JSON.stringify(payload)}`);
  }
}

// Stub for the app's real recommend-astrologer flow, which this repo has no
// access to. Matching is that system's job, not this bot's — so by default
// ("general" mode) the card never reveals a name/photo, just an exclusive-
// feeling generic pitch. Only when the visitor asked for someone by name
// ("specific" mode) does the real card with name/photo/rating show.
function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';

  const card = document.createElement('div');
  card.className = isGeneric ? 'message bot connect-card connect-card-generic' : 'message bot connect-card';

  const dismissBtn = document.createElement('button');
  dismissBtn.className = 'connect-dismiss';
  dismissBtn.type = 'button';
  dismissBtn.setAttribute('aria-label', 'Dismiss');
  dismissBtn.textContent = '✕';
  dismissBtn.addEventListener('click', () => card.remove());
  card.appendChild(dismissBtn);

  const info = document.createElement('div');
  info.className = 'connect-info';

  if (isGeneric) {
    const badge = document.createElement('div');
    badge.className = 'connect-badge';
    badge.textContent = '✨ Exclusive Match';
    const name = document.createElement('div');
    name.className = 'connect-name';
    name.textContent = (action.generic && action.generic.title) || 'Our Top-Rated Astrologer';
    const meta = document.createElement('div');
    meta.className = 'connect-meta';
    meta.textContent = (action.generic && action.generic.subtitle) || 'Hand-picked for you';
    info.appendChild(badge);
    info.appendChild(name);
    info.appendChild(meta);
  } else {
    const name = document.createElement('div');
    name.className = 'connect-name';
    name.textContent = `${astrologer.name} · ★${astrologer.rating}`;
    const meta = document.createElement('div');
    meta.className = 'connect-meta';
    meta.textContent = astrologer.availability;
    info.appendChild(name);
    info.appendChild(meta);
  }

  if (isGeneric) {
    card.appendChild(info);
  } else {
    const top = document.createElement('div');
    top.className = 'connect-top';
    const avatar = document.createElement('img');
    avatar.className = 'avatar';
    avatar.src = astrologer.image;
    avatar.alt = astrologer.name;
    top.appendChild(avatar);
    top.appendChild(info);
    card.appendChild(top);
  }

  // Chat/Call upfront, Call visually promoted (bigger, filled) since it's
  // the preferred conversion path — matches the real app's own Chat/Call
  // pair on each astrologer card.
  const actionRow = document.createElement('div');
  actionRow.className = 'connect-actions';

  const chatBtn = document.createElement('button');
  chatBtn.className = 'connect-btn connect-btn-chat';
  chatBtn.type = 'button';
  chatBtn.textContent = '💬 Chat';

  const callBtn = document.createElement('button');
  callBtn.className = 'connect-btn connect-btn-call';
  callBtn.type = 'button';
  callBtn.textContent = '📞 Call now';

  [[chatBtn, 'chat'], [callBtn, 'call']].forEach(([btn, mode]) => {
    btn.addEventListener('click', () => {
      chatBtn.disabled = true;
      callBtn.disabled = true;
      btn.textContent = '...';
      triggerNativeConnect(mode, astrologer, isGeneric);
    });
  });

  actionRow.appendChild(chatBtn);
  actionRow.appendChild(callBtn);
  card.appendChild(actionRow);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

sendButton.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') sendMessage();
});

photoBtn.addEventListener('click', () => photoInput.click());
photoInput.addEventListener('change', () => {
  const file = photoInput.files[0];
  photoInput.value = '';
  if (file) handlePhotoUpload(file);
});

document.querySelectorAll('.quick-reply').forEach((button) => {
  button.addEventListener('click', () => {
    const value = button.getAttribute('data-text');
    chatInput.value = value;
    chatInput.focus();
  });
});
