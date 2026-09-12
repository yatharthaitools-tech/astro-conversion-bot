const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const photoBtn = document.getElementById('photoBtn');
const photoInput = document.getElementById('photoInput');
const quickReplies = document.getElementById('quickReplies');

const welcomeMessage = "Hi! I'm here to help you figure things out. What's been on your mind?";

// Small inline icons for the connect card's Chat/Call buttons — no emoji,
// matches the quick-reply chip icons for a consistent, premium feel.
const CHAT_ICON = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2.6 3.4A1.4 1.4 0 0 1 4 2h8a1.4 1.4 0 0 1 1.4 1.4v5A1.4 1.4 0 0 1 12 9.8H6.2L3 12.4V9.8h-.4A1.4 1.4 0 0 1 1 8.4v-5z"/></svg>';
const CALL_ICON = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3.2 2.3c.5-.4 1.2-.3 1.6.1l1.2 1.5c.3.4.3.9 0 1.3l-.7.8c.4.9 1 1.7 1.8 2.5.7.7 1.6 1.3 2.5 1.8l.8-.7c.4-.3.9-.3 1.3 0l1.5 1.2c.5.4.5 1.1.1 1.6l-.8.9c-.4.5-1.1.7-1.7.5-2.2-.7-4.2-1.9-5.9-3.6-1.6-1.6-2.9-3.6-3.6-5.9-.2-.6 0-1.3.5-1.7l.9-.8z"/></svg>';

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

// Shown the moment the visitor's message goes out, removed the moment a
// reply (or an error) is ready — so there's never a silent gap while
// Gemini's own tool-calling loop is actually thinking.
function showTypingIndicator() {
  const msg = document.createElement('div');
  msg.className = 'message bot typing-indicator';
  msg.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  chatBody.appendChild(msg);
  chatBody.scrollTop = chatBody.scrollHeight;
  return msg;
}

async function sendToBot(text) {
  const typingEl = showTypingIndicator();
  // A floor on how long it shows, so a near-instant (rule-based fallback)
  // reply doesn't just flash the indicator for a frame — it should still
  // read as "thinking", not glitch.
  const minDelay = new Promise((resolve) => setTimeout(resolve, 500));

  try {
    const [response] = await Promise.all([
      fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: text,
          session_id: getSessionId(),
          history: history.slice(0, -1)
        })
      }),
      minDelay
    ]);

    const data = await response.json();
    const answer = data.answer || "I don't have information regarding that.";
    typingEl.remove();
    appendMessage('bot', answer);
    if (data.action && data.action.type === 'connect_popup') {
      renderConnectCard(data.action);
    }
  } catch (error) {
    await minDelay;
    typingEl.remove();
    appendMessage('bot', "I don't have information regarding that.");
  }
}

async function sendMessage(overrideText) {
  const text = (overrideText !== undefined ? overrideText : chatInput.value || '').trim();
  if (!text) return;

  // Quick replies are an opening prompt, not a persistent menu — once the
  // conversation actually starts, keep the screen to just the chat itself.
  if (quickReplies && !quickReplies.hidden) quickReplies.hidden = true;

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
// access to. The visible card is ALWAYS the same anonymous "connect with a
// top astrologer" card with platform trust stats — no name, no photo,
// ever, even if the visitor asked for someone specific by name. Matching
// identity to a real person is the app's own recommend system's job, not
// this bot's or this card's; astrologer_id (when resolved) only travels
// under the hood to the native bridge so Connect still routes correctly.
function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';
  const cardText = action.card || {};

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  const info = document.createElement('div');
  info.className = 'connect-info';

  const name = document.createElement('div');
  name.className = 'connect-name';
  name.textContent = cardText.title || 'Connect with a top astrologer';
  info.appendChild(name);

  if (cardText.subtitle) {
    const meta = document.createElement('div');
    meta.className = 'connect-meta';
    meta.textContent = cardText.subtitle;
    info.appendChild(meta);
  }

  if (cardText.trust && cardText.trust.length) {
    const trust = document.createElement('div');
    trust.className = 'connect-trust';
    // Exactly the 2 platform-level signals meant to build trust here —
    // never a specific astrologer's own stats, since none is ever named.
    cardText.trust.forEach((text) => {
      const chip = document.createElement('span');
      chip.className = 'trust-chip';
      chip.textContent = text;
      trust.appendChild(chip);
    });
    info.appendChild(trust);
  }

  card.appendChild(info);

  // Chat/Call upfront, equal size, Call promoted through color only (not
  // size) — matches the real app's own Chat/Call pair on each astrologer
  // card, just without looking like an ad.
  const actionRow = document.createElement('div');
  actionRow.className = 'connect-actions';

  const chatBtn = document.createElement('button');
  chatBtn.className = 'connect-btn connect-btn-chat';
  chatBtn.type = 'button';
  chatBtn.innerHTML = `${CHAT_ICON}<span>Chat</span>`;

  const callBtn = document.createElement('button');
  callBtn.className = 'connect-btn connect-btn-call';
  callBtn.type = 'button';
  callBtn.innerHTML = `${CALL_ICON}<span>Call now</span>`;

  [[chatBtn, 'chat'], [callBtn, 'call']].forEach(([btn, mode]) => {
    btn.addEventListener('click', () => {
      chatBtn.disabled = true;
      callBtn.disabled = true;
      btn.innerHTML = '<span>...</span>';
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
    sendMessage(button.getAttribute('data-text'));
  });
});
