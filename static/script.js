const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const photoBtn = document.getElementById('photoBtn');
const photoInput = document.getElementById('photoInput');
const quickReplies = document.getElementById('quickReplies');
const closeBtn = document.getElementById('closeBtn');

const welcomeMessage = "Hi! I'm here to help you figure things out. What's been on your mind?";

// A connect card showing up every single turn reads as spammy — require
// at least one turn's gap since the last one before showing another.
let turnsSinceLastCard = Infinity;

// Nudges a visitor who's gone quiet mid-conversation instead of just
// leaving the chat sitting open with no signal either way. Armed after
// each bot reply, cleared on any new activity (sending a message, or the
// chat ending) — fires once per idle window, not repeatedly.
const INACTIVITY_MS = 20000;
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

// Handed off by the native app via ?user_id=...&oauth_token=... on the
// page it opened this WebView with — see app.py's / route, which reads
// them into these data attributes. Read once at load and carried on
// every /ask call below; empty when this page is opened without them
// (plain browser testing), in which case the backend falls back to its
// own session-derived pseudo-id.
const appUserId = document.body.dataset.userId || '';
const appOauthToken = document.body.dataset.oauthToken || '';
const appUserName = document.body.dataset.userName || '';
const appLtv = document.body.dataset.ltv || '';

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
          history: history.slice(0, -1),
          user_id: appUserId,
          oauth_token: appOauthToken,
          user_name: appUserName,
          ltv: appLtv,
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
    } else if (data.action && data.action.type === 'free_coins_bottomsheet') {
      // Free-coins UI lives entirely in-chat — the real native app has
      // no bottomsheet action to hand this to (its bridge only supports
      // start_random_flow and close_chat), so this card IS the whole
      // celebration. Straight into the connect card right after — a
      // credit alone is a dead end, this is the natural next step while
      // the coins are top of mind.
      showCoinsCreditedCard(data.action.freeCoins);
      if (data.action.connect) {
        renderConnectCard({
          type: 'connect_popup',
          display_mode: data.action.connect.display_mode,
          astrologer: data.action.connect.astrologer,
        });
        turnsSinceLastCard = 0;
      }
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

// Generic native-action bridge — the contract the React Native side reads
// on its WebView onMessage handler: {action: '<name>', payload: {...}}.
// The real app implements exactly two actions: start_random_flow and
// close_chat — nothing else exists on the native side, so every trigger
// this bot ever fires has to be one of those two.
function sendNativeAction(action, params = {}) {
  return sendToNativeHost({ action, payload: params });
}

function triggerNativeConnect(mode, astrologer, isGeneric) {
  // Both the generic ("app's own matching system picks") and the
  // specific (visitor named someone available) cases go through the
  // same start_random_flow action — there's no separate native action
  // for the specific case. astrologer_id is included when known so
  // native can route to that person if it chooses to use the field;
  // omitted entirely for the generic case, same as before.
  const payload = { service_type: mode };
  if (!isGeneric) payload.astrologer_id = astrologer.id;
  sendNativeAction('start_random_flow', payload);
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

  // Close it out first, Connect me (the primary CTA) rightmost.
  actions.appendChild(closeBtn);
  actions.appendChild(connectBtn);
  card.appendChild(actions);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

// A quick in-chat confirmation that coins actually landed — easy to
// miss as just a line of reply text, so this gives it its own visual
// moment alongside the native bottomsheet.
function showCoinsCreditedCard(freeCoins) {
  const card = document.createElement('div');
  card.className = 'message bot coins-card';

  const label = document.createElement('div');
  label.className = 'coins-card-label';
  label.textContent = `🎉 ${freeCoins.coins} free coins added!`;
  card.appendChild(label);

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
  sendNativeAction('close_chat');
  // No host app (plain-browser testing) — there's nothing to dismiss, so
  // lock the widget itself into an ended state instead of leaving it open.
  if (!(window.ReactNativeWebView && typeof window.ReactNativeWebView.postMessage === 'function')) {
    chatInput.disabled = true;
    chatInput.placeholder = 'Chat ended';
    sendButton.disabled = true;
    photoBtn.disabled = true;
    if (closeBtn) closeBtn.disabled = true;
    if (quickReplies) quickReplies.hidden = true;
  }
}

// Stub for the app's real recommend-astrologer flow, which this repo has no
// access to. The card is a real component (markup + CSS below), not a
// baked image — no generated text, name or photo is templated into it
// though; astrologer_id (when resolved) only travels under the hood to
// the native bridge, it never changes what's shown on the card itself.

// Icon glyphs for the stats row and buttons — inline SVG (not another
// image asset) so they scale crisply and inherit currentColor.
const CC_ICONS = {
  medal: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="5"/><path d="M8.5 12.5 L7 21 L12 18 L17 21 L15.5 12.5"/></svg>',
  people: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="8" r="3.2"/><circle cx="17" cy="9" r="2.6"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M15.5 14.3c2.9.3 5 2.3 5 5.2"/></svg>',
  star: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.5l3.09 6.26 6.91 1-5 4.87 1.18 6.87L12 18.27l-6.18 3.23L7 14.63l-5-4.87 6.91-1L12 2.5z"/></svg>',
  chat: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H8l-4 4V5z"/></svg>',
  phone: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6.6 10.8c1.4 2.8 3.8 5.2 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.5 2.3.8 3.6.9.6 0 1 .5 1 1V21c0 .6-.4 1-1 1C10.6 22 2 13.4 2 3c0-.6.4-1 1-1h3.9c.5 0 1 .4 1 1 .1 1.3.4 2.5.9 3.6.1.4.1.8-.2 1.1L6.6 10.8z"/></svg>',
  lotus: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 21c-4-1.5-6-5-6-8 2.5.5 4.5 2 6 4.5C13.5 14 15.5 12.5 18 12c0 3-2 6.5-6 9z"/><path d="M12 17c-1-3-1-6.5 0-10 1 3.5 1 7 0 10z"/><path d="M12 15c-2.5-2-4-4.5-4-7.5C10.5 8.5 12 10.5 12 13c0-2.5 1.5-4.5 4-5.5 0 3-1.5 5.5-4 7.5z"/></svg>',
};

function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  const avatars = document.createElement('div');
  avatars.className = 'cc-avatars';
  const avatarSrcs = [
    '/static/avatars/connect-avatar-2.png',
    '/static/avatars/connect-avatar-1.png',
    '/static/avatars/connect-avatar-3.png',
  ];
  avatarSrcs.forEach((src, i) => {
    const img = document.createElement('img');
    img.className = i === 1 ? 'cc-avatar cc-avatar-main' : 'cc-avatar';
    img.src = src;
    img.alt = '';
    avatars.appendChild(img);
  });
  const lotus = document.createElement('div');
  lotus.className = 'cc-avatar cc-lotus';
  lotus.innerHTML = CC_ICONS.lotus;
  avatars.appendChild(lotus);
  card.appendChild(avatars);

  const badge = document.createElement('div');
  badge.className = 'cc-badge';
  badge.textContent = '✨ Top astrologers for you';
  card.appendChild(badge);

  const heading = document.createElement('h3');
  heading.className = 'cc-heading';
  heading.textContent = 'Get guidance from our best astrologers';
  card.appendChild(heading);

  const sub = document.createElement('p');
  sub.className = 'cc-sub';
  sub.textContent = "We'll connect you with an astrologer who matches your concern.";
  card.appendChild(sub);

  const stats = document.createElement('div');
  stats.className = 'cc-stats';
  [
    [CC_ICONS.medal, '10+ years', 'average experience'],
    [CC_ICONS.people, '1,000+', 'users helped'],
    [CC_ICONS.star, '4.8', 'average rating'],
  ].forEach(([icon, value, label], i) => {
    if (i > 0) {
      const divider = document.createElement('div');
      divider.className = 'cc-divider';
      stats.appendChild(divider);
    }
    const stat = document.createElement('div');
    stat.className = 'cc-stat';
    stat.innerHTML = `<span class="cc-stat-icon">${icon}</span><span class="cc-stat-text"><span class="cc-stat-value">${value}</span><span class="cc-stat-label">${label}</span></span>`;
    stats.appendChild(stat);
  });
  card.appendChild(stats);

  const actions = document.createElement('div');
  actions.className = 'cc-actions';

  const chatBtn = document.createElement('button');
  chatBtn.type = 'button';
  chatBtn.className = 'cc-btn cc-btn-chat';
  chatBtn.innerHTML = `${CC_ICONS.chat}<span>Chat</span>`;

  const callBtn = document.createElement('button');
  callBtn.type = 'button';
  callBtn.className = 'cc-btn cc-btn-call';
  callBtn.innerHTML = `${CC_ICONS.phone}<span>Call now</span>`;

  // service_type sent to native is 'audio', not 'call' — matches the
  // app's own naming for this call type.
  [[chatBtn, 'chat'], [callBtn, 'audio']].forEach(([btn, mode]) => {
    btn.addEventListener('click', () => {
      chatBtn.disabled = true;
      callBtn.disabled = true;
      triggerNativeConnect(mode, astrologer, isGeneric);
    });
  });

  actions.appendChild(chatBtn);
  actions.appendChild(callBtn);
  card.appendChild(actions);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

sendButton.addEventListener('click', () => sendMessage());
chatInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') sendMessage();
});

// The cross in the header — same close_chat action as the feedback
// card's "Skip"/inactivity nudge's "Close it out", just reachable from
// anywhere in the conversation, not only at the end of it. Native side
// dismisses the WebView on close_chat, which drops back to its own home
// screen — there's no separate "open home" action to send on top of it.
if (closeBtn) closeBtn.addEventListener('click', () => closeChat());

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
