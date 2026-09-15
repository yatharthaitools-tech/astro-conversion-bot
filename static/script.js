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

// Nudges a visitor who's gone quiet mid-conversation instead of just
// leaving the chat sitting open with no signal either way. Armed after
// each bot reply, cleared on any new activity (sending a message, or the
// chat ending) — fires once per idle window, not repeatedly.
const INACTIVITY_MS = 10000;
let inactivityTimer = null;
let hasStartedConversation = false;

function armInactivityTimer() {
  clearInactivityTimer();
  if (!hasStartedConversation || chatInput.disabled) return;
  inactivityTimer = setTimeout(showInactivityNudge, INACTIVITY_MS);
}

function clearInactivityTimer() {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
}

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
    hasStartedConversation = true;

    turnsSinceLastCard += 1;
    if (data.action && data.action.type === 'connect_popup') {
      if (turnsSinceLastCard >= 2) {
        renderConnectCard(data.action);
        turnsSinceLastCard = 0;
      }
      // Otherwise: a card was just shown last turn — the reply text still
      // carries the offer, but we don't repeat the same card back-to-back.
    }

    if (data.show_feedback) {
      showFeedbackPrompt(data.session_id);
    } else {
      armInactivityTimer();
    }
  } catch (error) {
    await minDelay;
    typingEl.remove();
    appendMessage('bot', "I don't have information regarding that.");
    hasStartedConversation = true;
    armInactivityTimer();
  }
}

async function sendMessage(overrideText) {
  const text = (overrideText !== undefined ? overrideText : chatInput.value || '').trim();
  if (!text) return;

  clearInactivityTimer();

  // Quick replies are an opening prompt, not a persistent menu — once the
  // conversation actually starts, keep the screen to just the chat itself.
  if (quickReplies && !quickReplies.hidden) quickReplies.hidden = true;

  appendMessage('user', text);
  chatInput.value = '';
  await sendToBot(text);
}

async function handlePhotoUpload(file) {
  clearInactivityTimer();
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
function sendToNativeHost(payload) {
  if (window.ReactNativeWebView && typeof window.ReactNativeWebView.postMessage === 'function') {
    window.ReactNativeWebView.postMessage(JSON.stringify(payload));
    return true;
  }
  // Only reached outside the app's WebView (e.g. testing in a plain
  // browser) — makes the bridge call visible for local testing instead
  // of silently doing nothing.
  appendMessage('bot', `[dev fallback — no host app detected] Would send: ${JSON.stringify(payload)}`);
  return false;
}

function triggerNativeConnect(mode, astrologer, isGeneric) {
  sendToNativeHost({
    type: 'CONNECT_ASTROLOGER',
    mode, // 'chat' | 'call'
    matchType: isGeneric ? 'best_match' : 'specific',
    astrologerId: isGeneric ? null : astrologer.id,
  });
}

// Fires once after INACTIVITY_MS of no visitor activity mid-conversation
// — rather than leaving them sitting on a reply with no signal either
// way, offers the two real next steps directly: connect with someone, or
// close this out. Both route through the normal sendMessage path (same
// as a quick reply) instead of a one-off client-side action, so the
// model still decides how to actually handle it.
function showInactivityNudge() {
  if (chatInput.disabled) return;

  const card = document.createElement('div');
  card.className = 'message bot nudge-card';

  const label = document.createElement('div');
  label.className = 'nudge-label';
  label.textContent = "Still there? Want me to connect you with an astrologer, or should we close this out?";
  card.appendChild(label);

  const actions = document.createElement('div');
  actions.className = 'nudge-actions';

  const connectBtn = document.createElement('button');
  connectBtn.type = 'button';
  connectBtn.className = 'nudge-btn nudge-btn-primary';
  connectBtn.textContent = 'Connect me';
  connectBtn.addEventListener('click', () => {
    card.remove();
    sendMessage('Yes, connect me with an astrologer');
  });

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'nudge-btn';
  closeBtn.textContent = 'Close it out';
  closeBtn.addEventListener('click', () => {
    card.remove();
    sendMessage("I'm done, please close this out");
  });

  actions.appendChild(connectBtn);
  actions.appendChild(closeBtn);
  card.appendChild(actions);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

// A resolved issue shouldn't leave the chat sitting open indefinitely —
// ask for a quick rating, then actually end the session: tell the native
// host to dismiss the WebView, and lock the widget itself either way so
// there's a real endpoint instead of an idle chat waiting forever.
//
// Rendered as its own card (not a plain message bubble) so it reads as a
// distinct moment rather than getting lost in the scroll — stars fill up
// to whichever one you're hovering, and a click confirms in place instead
// of yanking the card out and popping a separate "thanks" bubble.
function showFeedbackPrompt(sessionId) {
  const card = document.createElement('div');
  card.className = 'message bot feedback-card';

  const label = document.createElement('div');
  label.className = 'feedback-label';
  label.textContent = 'How was this conversation?';
  card.appendChild(label);

  const stars = document.createElement('div');
  stars.className = 'feedback-stars';
  const starEls = [];
  for (let i = 1; i <= 5; i++) {
    const star = document.createElement('button');
    star.type = 'button';
    star.className = 'feedback-star';
    star.textContent = '★';
    star.dataset.value = i;
    star.setAttribute('aria-label', `${i} star${i > 1 ? 's' : ''}`);
    star.addEventListener('mouseenter', () => fillStars(starEls, i));
    star.addEventListener('click', () => submitFeedback(sessionId, i, card, label, starEls, skip));
    stars.appendChild(star);
    starEls.push(star);
  }
  stars.addEventListener('mouseleave', () => fillStars(starEls, 0));
  card.appendChild(stars);

  const skip = document.createElement('button');
  skip.type = 'button';
  skip.className = 'feedback-skip';
  skip.textContent = 'Skip';
  skip.addEventListener('click', () => {
    card.remove();
    closeChat();
  });
  card.appendChild(skip);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

function fillStars(starEls, upTo) {
  starEls.forEach((star, idx) => star.classList.toggle('filled', idx < upTo));
}

async function submitFeedback(sessionId, rating, card, label, starEls, skip) {
  fillStars(starEls, rating);
  starEls.forEach((star) => { star.disabled = true; });
  skip.remove();
  label.textContent = 'Thanks for the feedback!';
  card.classList.add('feedback-done');

  try {
    await fetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, rating }),
    });
  } catch (error) {
    // Best-effort — a failed rating POST shouldn't block closing the chat.
  }
  setTimeout(closeChat, 700);
}

function closeChat() {
  clearInactivityTimer();
  sendToNativeHost({ type: 'CLOSE_CHAT' });
  // No host app (plain-browser testing) — there's nothing to dismiss, so
  // lock the widget itself into an ended state instead of leaving it open.
  if (!(window.ReactNativeWebView && typeof window.ReactNativeWebView.postMessage === 'function')) {
    chatInput.disabled = true;
    chatInput.placeholder = 'Chat ended';
    sendButton.disabled = true;
    photoBtn.disabled = true;
    if (quickReplies) quickReplies.hidden = true;
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

sendButton.addEventListener('click', () => sendMessage());
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
