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

// Decorative icons for the connect card's stat row + badges.
const SPARKLE_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor"><path d="M8 1l1.2 4.8L14 7l-4.8 1.2L8 13l-1.2-4.8L2 7l4.8-1.2z"/></svg>';
const LOTUS_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 2c1 1.5 1 3 0 4-1-1-1-2.5 0-4zM4 4c1.5.5 2.5 1.7 2.7 3-1.3-.2-2.4-1.4-2.7-3zm8 0c-.3 1.6-1.4 2.8-2.7 3 .2-1.3 1.2-2.5 2.7-3zM8 6.2c1.8 0 3.2 1.3 3.2 3H4.8c0-1.7 1.4-3 3.2-3z"/></svg>';
const STAT_ICONS = {
  years: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="6" r="4"/><path d="M5.5 9.5L4 14l4-1.5L12 14l-1.5-4.5"/></svg>',
  users: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.3"/><path d="M2 13c0-2.2 1.8-3.5 4-3.5s4 1.3 4 3.5"/><circle cx="11.5" cy="6.5" r="1.8"/><path d="M10 9.7c1.6.2 2.8 1.3 2.8 3.3"/></svg>',
  rating: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><path d="M8 1.5l1.9 4.1 4.4.5-3.3 3 .9 4.4L8 11.3l-3.9 2.2.9-4.4-3.3-3 4.4-.5z"/></svg>',
};

// A connect card showing up every single turn reads as spammy — require
// at least one turn's gap since the last one before showing another.
let turnsSinceLastCard = Infinity;

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

    turnsSinceLastCard += 1;
    if (data.action && data.action.type === 'connect_popup') {
      if (turnsSinceLastCard >= 2) {
        renderConnectCard(data.action);
        turnsSinceLastCard = 0;
      }
      // Otherwise: a card was just shown last turn — the reply text still
      // carries the offer, but we don't repeat the same card back-to-back.
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
// access to. The visible card is ALWAYS the same anonymous showcase card
// with platform trust stats — no specific name, ever, even if the visitor
// asked for someone specific. The 3-avatar collage is decorative brand
// imagery for "astrologers on the platform" as a category, not a claim
// about who the visitor will actually get — matching identity to a real
// person is the app's own recommend system's job, not this bot's or this
// card's; astrologer_id (when resolved) only travels under the hood to
// the native bridge so Connect still routes correctly.
function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';
  const cardText = action.card || {};

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  const topRow = document.createElement('div');
  topRow.className = 'connect-top-row';

  if (cardText.images && cardText.images.length) {
    const collage = document.createElement('div');
    collage.className = 'connect-collage';
    cardText.images.slice(0, 3).forEach((src, i) => {
      const img = document.createElement('img');
      img.className = `collage-avatar collage-avatar-${i + 1}`;
      img.src = src;
      img.alt = '';
      collage.appendChild(img);
    });
    const badge = document.createElement('div');
    badge.className = 'collage-badge';
    badge.innerHTML = LOTUS_ICON;
    collage.appendChild(badge);
    topRow.appendChild(collage);
  }

  const info = document.createElement('div');
  info.className = 'connect-info';

  if (cardText.badge) {
    const pill = document.createElement('div');
    pill.className = 'connect-badge-pill';
    pill.innerHTML = `${SPARKLE_ICON}<span>${cardText.badge}</span>`;
    info.appendChild(pill);
  }

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

  topRow.appendChild(info);
  card.appendChild(topRow);

  if (cardText.trust && cardText.trust.length) {
    const trust = document.createElement('div');
    trust.className = 'connect-stats';
    // Platform-level signals only — never a specific astrologer's own
    // stats, since none is ever named on this card.
    cardText.trust.forEach((stat, i) => {
      if (i > 0) {
        const divider = document.createElement('div');
        divider.className = 'stat-divider';
        trust.appendChild(divider);
      }
      const cell = document.createElement('div');
      cell.className = 'stat';
      const icon = document.createElement('div');
      icon.className = 'stat-icon';
      icon.innerHTML = STAT_ICONS[stat.icon] || '';
      const value = document.createElement('div');
      value.className = 'stat-value';
      value.textContent = stat.value;
      const label = document.createElement('div');
      label.className = 'stat-label';
      label.textContent = stat.label;
      cell.appendChild(icon);
      cell.appendChild(value);
      cell.appendChild(label);
      trust.appendChild(cell);
    });
    card.appendChild(trust);
  }

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
