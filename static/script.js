const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const photoBtn = document.getElementById('photoBtn');
const photoInput = document.getElementById('photoInput');
const quickReplies = document.getElementById('quickReplies');

const welcomeMessage = "Hi! I'm here to help you figure things out. What's been on your mind?";

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
// access to. The card is a single fixed design asset
// (static/avatars/connect-card.png), shown completely unmodified — no
// generated text, name or photo ever overlaid on it. Two invisible tap
// targets sit over the Chat/Call areas already baked into that image so
// the native bridge still fires; astrologer_id (when resolved) only
// travels under the hood there, it never changes what's shown.
function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';
  const imageSrc = (action.card || {}).image;
  if (!imageSrc) return;

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  const frame = document.createElement('div');
  frame.className = 'connect-card-frame';

  const img = document.createElement('img');
  img.className = 'connect-card-image';
  img.src = imageSrc;
  img.alt = 'Connect with a top astrologer';
  frame.appendChild(img);

  const chatHit = document.createElement('button');
  chatHit.type = 'button';
  chatHit.className = 'connect-hitarea connect-hitarea-chat';
  chatHit.setAttribute('aria-label', 'Chat');

  const callHit = document.createElement('button');
  callHit.type = 'button';
  callHit.className = 'connect-hitarea connect-hitarea-call';
  callHit.setAttribute('aria-label', 'Call now');

  [[chatHit, 'chat'], [callHit, 'call']].forEach(([btn, mode]) => {
    btn.addEventListener('click', () => {
      chatHit.disabled = true;
      callHit.disabled = true;
      triggerNativeConnect(mode, astrologer, isGeneric);
    });
  });

  frame.appendChild(chatHit);
  frame.appendChild(callHit);
  card.appendChild(frame);

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
