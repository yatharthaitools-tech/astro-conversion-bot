const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const photoBtn = document.getElementById('photoBtn');
const photoInput = document.getElementById('photoInput');
const quickReplies = document.getElementById('quickReplies');
const closeBtn = document.getElementById('closeBtn');
const offlineBanner = document.getElementById('offlineBanner');
const brandStatus = document.getElementById('brandStatus');

// QA list — iOS blank space below the input / flicker-shift on keyboard
// open / attachment row misplaced on keyboard toggle: all three traced
// to the same cause. iOS leaves html/body's 100vh alone when the
// keyboard opens and instead tries to SCROLL the page to keep the
// focused input visible; styles.css now pins html/body with
// position:fixed so there's no room left to scroll, which leaves
// --app-height (real visible height) and --app-offset-top (how far iOS
// still nudges the visual viewport's own origin) as the only two values
// that actually need to track the keyboard. Both come from the same
// visualViewport object; 'scroll' fires for the offset shifting even
// when 'resize' doesn't.
function syncViewport() {
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  const offsetTop = vv ? vv.offsetTop : 0;
  document.documentElement.style.setProperty('--app-height', `${height}px`);
  document.documentElement.style.setProperty('--app-offset-top', `${offsetTop}px`);
}
syncViewport();
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', syncViewport);
  window.visualViewport.addEventListener('scroll', syncViewport);
} else {
  window.addEventListener('resize', syncViewport);
}

// QA list: a lost connection mid-chat otherwise just looks like the bot
// giving a wrong/generic answer (sendToBot's catch block), and the
// header's "online" dot kept showing green the whole time regardless of
// actual connectivity — both now track the same online/offline signal.
function syncConnectivity() {
  const offline = !navigator.onLine;
  if (offlineBanner) offlineBanner.classList.toggle('visible', offline);
  if (brandStatus) brandStatus.classList.toggle('offline', offline);
}
syncConnectivity();
window.addEventListener('online', syncConnectivity);
window.addEventListener('offline', syncConnectivity);

// Tara's avatar photo, reused next to every plain-text bot reply and
// the typing indicator — same image as the header's own avatar
// (templates/index.html), cropped from the same roster photo already
// used elsewhere in this app (static/avatars/connect-avatar-1.png's
// center portrait, see the crop this file was generated from).
const TARA_AVATAR_IMG = '<img src="/static/avatars/tara-avatar.png" alt="" />';

// Nudges a visitor who's gone quiet mid-conversation instead of just
// leaving the chat sitting open with no signal either way. Armed after
// each bot reply, cleared on any new activity (sending a message, or the
// chat ending) — fires once per idle window, not repeatedly.
const INACTIVITY_MS = 20000;
let inactivityTimer = null;
let hasStartedConversation = false;

// #3: once a ticket exists for this session, a real Zoho agent might
// reply — poll for that so it shows up in THIS chat window instead of
// the visitor needing a separate channel. Starts the moment /ask first
// reports ticket_raised (see sendToBot below) and never turns back off
// mid-session (a resolved ticket could still get a closing note) —
// stopped only when the chat itself closes.
const AGENT_POLL_MS = 8000;
let hasOpenTicket = false;
let agentPollTimer = null;
let agentMessagesSince = null;

function appendAgentMessage(text) {
  const row = document.createElement('div');
  row.className = 'message-row agent-row';
  row.style.flexDirection = 'column';
  row.style.alignItems = 'flex-start';
  const label = document.createElement('div');
  label.className = 'agent-label';
  label.textContent = 'AstroLokal Support';
  const msg = document.createElement('div');
  msg.className = 'message agent';
  msg.textContent = text;
  row.appendChild(label);
  row.appendChild(msg);
  chatBody.appendChild(row);
  chatBody.scrollTop = chatBody.scrollHeight;
}

async function pollAgentMessages() {
  try {
    const params = agentMessagesSince ? `?since=${encodeURIComponent(agentMessagesSince)}` : '';
    const response = await fetch(`/conversations/${getSessionId()}/agent-messages${params}`);
    const data = await response.json();
    for (const m of data.messages || []) {
      appendAgentMessage(m.text);
      agentMessagesSince = m.created_at;
    }
  } catch (error) {
    // Best-effort — a failed poll just tries again next interval.
  }
}

function startAgentMessagePolling() {
  if (agentPollTimer) return;
  agentMessagesSince = new Date().toISOString();
  agentPollTimer = setInterval(pollAgentMessages, AGENT_POLL_MS);
}

function stopAgentMessagePolling() {
  if (agentPollTimer) clearInterval(agentPollTimer);
  agentPollTimer = null;
}

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

// Same identity trust rule as agent/context.py's resolve_session(): a
// name is only usable when it rode along with a real identity (both
// user_id and oauth_token present — a bare name with no token is
// exactly what a spoofed/untrusted request would send), and "Guest"
// (the app's own placeholder for an anonymous session) is never usable
// either way, case-insensitively, same as the backend.
const GREETING_PLACEHOLDER_NAMES = new Set(['guest']);

function resolveGreetingName() {
  if (!appUserId || !appOauthToken) return null;
  const trimmed = appUserName.trim();
  if (!trimmed || GREETING_PLACEHOLDER_NAMES.has(trimmed.toLowerCase())) return null;
  return trimmed;
}

function buildWelcomeMessage() {
  const name = resolveGreetingName();
  return name ? `Hi ${name}! How can I help you today?` : 'Hi! How can I help you today?';
}

function getSessionId() {
  let sessionId = sessionStorage.getItem('astro_session_id');
  if (!sessionId) {
    sessionId = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    sessionStorage.setItem('astro_session_id', sessionId);
  }
  return sessionId;
}

// UI interaction analytics — fire-and-forget, must never affect the chat
// itself even if the call fails or the browser blocks it. keepalive lets
// the request survive a page teardown right after firing (e.g.
// consulation_ended, right before the native host may dismiss this
// WebView). Backed by app.py's /event route -> dashboard_db.record_
// event(), read by the admin Analytics page's Events section. Event
// names/field names below intentionally match the analytics team's own
// schema verbatim (including its "consulation" spelling), not a typo.
function trackEvent(eventType, eventData) {
  try {
    fetch('/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        event_data: eventData || {},
        session_id: getSessionId(),
        user_id: appUserId,
      }),
      keepalive: true,
    }).catch(() => {});
  } catch (error) {
    // Analytics must never break the chat itself.
  }
}

const history = [];

function appendMessage(sender, text) {
  const msg = document.createElement('div');
  msg.className = `message ${sender}`;
  msg.textContent = text;

  if (sender === 'bot') {
    // Plain bot replies sit next to a small Tara avatar — reads as
    // someone actually answering, not a wall of unattributed bubbles.
    // The special cards (connect/feedback/nudge/coins) skip this on
    // purpose: they're already distinct, full-width moments of their
    // own, not conversational lines.
    const row = document.createElement('div');
    row.className = 'message-row';
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.innerHTML = TARA_AVATAR_IMG;
    row.appendChild(avatar);
    row.appendChild(msg);
    chatBody.appendChild(row);
  } else {
    chatBody.appendChild(msg);
  }

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
  // Threads a text marker into history so later turns can still find this
  // photo was shared (e.g. for evidence_url on a ticket raised afterward).
  // The actual vision analysis — app.py reading the file and attaching it
  // to the Gemini call — only happens for the turn that's shared right
  // now (see app.py's load_current_photo), not every time this marker is
  // found in history again.
  history.push({ sender, text: `[Shared a photo: ${url}]` });
}

// "Session closed on 1 Oct 2026, 3:05 PM" divider. Deliberately NOT pushed
// into `history` — that array is what gets sent to the model as
// conversation, and this is UI-only.
function appendSessionClosedNote(isoTime) {
  const last = chatBody.lastElementChild;
  if (last && last.classList.contains('session-closed-note')) return;
  const when = isoTime ? new Date(isoTime) : new Date();
  const valid = !isNaN(when.getTime());
  const stamp = (valid ? when : new Date()).toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  const note = document.createElement('div');
  note.className = 'session-closed-note';
  note.textContent = `Session closed on ${stamp}`;
  chatBody.appendChild(note);
  chatBody.scrollTop = chatBody.scrollHeight;
}

const _PHOTO_MARKER_RE = /\[Shared a photo: (\S+)\]/;

// #3 on the QA list: a reload within the same browser/WebView session
// (sessionStorage intact) always showed a blank chat with the welcome
// message again, even though the real conversation was sitting in
// Postgres the whole time (dashboard_db.record_turn persists every
// turn) — it just never got read back. An existing session_id here
// means this is a continuing session, not a first-ever visit, so fetch
// and replay the real history instead of starting over.
async function restoreHistoryOrShowWelcome() {
  const existingSessionId = sessionStorage.getItem('astro_session_id');
  if (existingSessionId) {
    try {
      const response = await fetch(`/history/${existingSessionId}`);
      const data = await response.json();
      if (data.messages && data.messages.length > 0) {
        for (const m of data.messages) {
          if (m.role === 'system') {
            appendSessionClosedNote(m.created_at);
            continue;
          }
          const match = m.role === 'user' ? m.text.match(_PHOTO_MARKER_RE) : null;
          if (match) {
            appendImageMessage('user', match[1]);
          } else if (m.role === 'agent') {
            appendAgentMessage(m.text);
          } else {
            appendMessage(m.role, m.text);
          }
        }
        hasStartedConversation = true;
        if (data.has_ticket) {
          hasOpenTicket = true;
          startAgentMessagePolling();
        }
        return;
      }
    } catch (error) {
      // Best-effort — fall through to the normal welcome below rather
      // than leaving the chat stuck on a blank screen.
    }
  }

  appendMessage('bot', buildWelcomeMessage());
  // Session-lifecycle pair with consulation_ended (closeChat below) —
  // spelling/field names match the analytics team's own event schema
  // verbatim, not a typo left in by accident.
  trackEvent('consulation_started', {
    screen_name: 'chatbot_screen',
    event_timestamp: new Date().toISOString(),
    event_type: 'app',
  });
}

if (chatBody && chatBody.children.length === 0) {
  restoreHistoryOrShowWelcome();
}

// Shown the moment the visitor's message goes out, removed the moment a
// reply (or an error) is ready — so there's never a silent gap while
// Gemini's own tool-calling loop is actually thinking.
function showTypingIndicator() {
  const row = document.createElement('div');
  row.className = 'message-row';
  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.innerHTML = TARA_AVATAR_IMG;
  const msg = document.createElement('div');
  msg.className = 'message bot typing-indicator';
  msg.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  row.appendChild(avatar);
  row.appendChild(msg);
  chatBody.appendChild(row);
  chatBody.scrollTop = chatBody.scrollHeight;
  return row;
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

    if (data.action && data.action.type === 'connect_popup') {
      // Always render when triggered — the model is required to only
      // reference "tap below"/"connect you" text in the same turn it
      // actually calls trigger_recommend_astrologer, so suppressing the
      // card here would leave that text dangling with nothing below it.
      renderConnectCard(data.action);
    } else if (data.action && data.action.type === 'free_coins_bottomsheet') {
      // Free-coins UI lives entirely in-chat — the real native app has
      // no bottomsheet action to hand this to (its bridge only supports
      // start_random_flow and close_webview), so this card IS the whole
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
      }
    }

    if (data.ticket_raised && !hasOpenTicket) {
      hasOpenTicket = true;
      startAgentMessagePolling();
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

  // chip_selected also covers a free-typed message (chip_name/chip_id
  // 'N/A') — overrideText means this came from a quick reply instead
  // (tracked with its real chip_name/chip_id at the click handler
  // below) or the inactivity nudge, so skip it here either way.
  if (overrideText === undefined) {
    trackEvent('chip_selected', {
      screen_name: 'chatbot_screen',
      chip_name: 'N/A',
      chip_id: 'N/A',
      event_type: 'tap',
    });
  }

  clearInactivityTimer();

  // Quick replies are an opening prompt, not a persistent menu — once the
  // conversation actually starts, keep the screen to just the chat itself.
  if (quickReplies && !quickReplies.hidden) quickReplies.hidden = true;

  appendMessage('user', text);
  chatInput.value = '';
  // A programmatic .value clear doesn't fire 'input', so the listener
  // above never sees this — set it directly or the send button would
  // stay enabled-looking with an empty field right after sending.
  sendButton.disabled = true;
  await sendToBot(text);
}

async function handlePhotoUpload(file) {
  clearInactivityTimer();
  const formData = new FormData();
  formData.append('file', file);

  // #7 on the QA list ("chat breaks when multiple images are uploaded"):
  // the picker is single-select, but a fast double-tap on the camera
  // button (or a native file picker that fires 'change' more than once)
  // could still start two overlapping uploads — each with its own
  // typing indicator and its own sendToBot call racing the other's. Lock
  // the button for the whole upload+reply round trip so that can't happen.
  photoBtn.disabled = true;
  try {
    const response = await fetch('/upload', { method: 'POST', body: formData });
    const data = await response.json();
    if (!data.url) throw new Error('upload failed');
    appendImageMessage('user', data.url);
    // Deliberately NOT presuming what the photo is for (face/palm reading,
    // a payment screenshot, evidence for a complaint, etc.) — the backend
    // now actually sends the image itself to Gemini for a real look
    // (app.py's ask() + agent/orchestrator.py), so the model figures out
    // what's in it and responds accordingly instead of the old hardcoded
    // "face or palm reading" assumption forcing every photo down the same
    // path regardless of what it actually shows.
    //
    // The marker also needs to be in the outgoing question text itself, not
    // just history — sendToBot's history payload excludes the message
    // currently being sent (history.slice(0, -1)), so a marker only pushed
    // via appendImageMessage would never actually reach the backend for
    // THIS turn. Embedding it here too means find_last_attachment_url()
    // can find it either way.
    await sendToBot(`[Shared a photo: ${data.url}]`);
  } catch (error) {
    appendMessage('bot', "Sorry, I couldn't upload that photo — please try again.");
  } finally {
    photoBtn.disabled = false;
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
// close_webview — nothing else exists on the native side, so every
// trigger this bot ever fires has to be one of those two.
function sendNativeAction(action, params = {}) {
  return sendToNativeHost({ action, payload: params });
}

function triggerNativeConnect(mode) {
  // Both the generic ("app's own matching system picks") and the
  // specific (visitor named someone available) cases go through the
  // same start_random_flow action — there's no separate native action
  // for the specific case, and no astrologer_id in the payload either
  // way: it's the "random" flow, native's own matching system always
  // picks, this bot never routes to a specific person via the bridge.
  sendNativeAction('start_random_flow', { service_type: mode });
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
    trackEvent('nudge_connect_tap', { screen_name: 'chatbot_screen', event_type: 'tap' });
    card.remove();
    sendMessage('Yes, connect me with an astrologer');
  });

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'nudge-btn';
  closeBtn.textContent = 'Close it out';
  closeBtn.addEventListener('click', () => {
    trackEvent('nudge_close_tap', { screen_name: 'chatbot_screen', event_type: 'tap' });
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
    trackEvent('feedback_skip_tap', { screen_name: 'chatbot_screen', event_type: 'tap' });
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
  trackEvent('rating_given', { rating });
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

// Default close destination: the app's own Profile tab — closing the
// chat drops the visitor there rather than wherever they happened to
// open it from.
const PROFILE_DEEPLINK = 'astrolokal://BottomTabs?screen=Profile';

// deeplink lets a caller send the visitor somewhere specific on close
// (e.g. a particular app screen) instead of native's default dismiss
// behavior. source identifies which webview triggered this, so if the
// app ever embeds more than one WebView that all close through this
// same action, native can tell them apart.
function closeChat(deeplink = PROFILE_DEEPLINK) {
  clearInactivityTimer();
  stopAgentMessagePolling();
  // Persist the close (so it shows in restored history too) and show the
  // note right away; the server's timestamp replaces the local one on reload.
  appendSessionClosedNote();
  fetch('/close', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: getSessionId() }),
  }).catch(() => {});
  sendNativeAction('close_webview', { deeplink, source: 'chat_bot' });
  // Session-lifecycle pair with consulation_started above — fired from
  // here (not a specific button) since this is the one place the chat
  // session actually ends, whichever path got it there (header cross,
  // feedback skip, or the auto-close after a rating).
  trackEvent('consulation_ended', {
    screen_name: 'chatbot_screen',
    event_timestamp: new Date().toISOString(),
    event_type: 'app',
  });
  // No host app (plain-browser testing) — there's nothing to dismiss.
  // Keep the input usable rather than locking it: closing is a soft
  // "wrap up" signal, not a hard stop, so someone who changes their
  // mind and keeps typing should still be able to continue the chat.
  if (!(window.ReactNativeWebView && typeof window.ReactNativeWebView.postMessage === 'function')) {
    if (quickReplies) quickReplies.hidden = true;
  }
}

// Stub for the app's real recommend-astrologer flow, which this repo has no
// access to. The card itself is the fixed design reference image
// (static/avatars/connect-card.jpg) — no generated text, name or photo
// is templated into it; astrologer_id (when resolved) only travels
// under the hood to the native bridge, it never changes what's shown
// on the card. Only the Chat/Call buttons below the image are real.

// Icon glyphs for the connect card's Chat/Call buttons — inline SVG
// (not an image asset) so they scale crisply and inherit currentColor.
const CC_ICONS = {
  chat: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H8l-4 4V5z"/></svg>',
  phone: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6.6 10.8c1.4 2.8 3.8 5.2 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.5 2.3.8 3.6.9.6 0 1 .5 1 1V21c0 .6-.4 1-1 1C10.6 22 2 13.4 2 3c0-.6.4-1 1-1h3.9c.5 0 1 .4 1 1 .1 1.3.4 2.5.9 3.6.1.4.1.8-.2 1.1L6.6 10.8z"/></svg>',
};

function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;
  const isGeneric = action.display_mode !== 'specific';

  trackEvent('viewed_connect_card', {
    screen_name: 'chatbot_screen',
    event_type: 'screen_view',
  });

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  // The design reference itself, shown as-is — no generated text, no
  // per-astrologer data templated into it, same "always the same fixed
  // design" contract as before, just the actual picture now instead of
  // a hand-built markup recreation of it. Only the Chat/Call buttons
  // below are real, functional elements (an image can't be clickable).
  const img = document.createElement('img');
  img.className = 'cc-image';
  img.src = '/static/avatars/connect-card.jpg';
  img.alt = 'Top astrologers for you — get guidance from our best astrologers';
  card.appendChild(img);

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
      trackEvent('tap_connect_card', {
        screen_name: 'chatbot_screen',
        event_type: 'tap',
        service_type: mode,
      });
      chatBtn.disabled = true;
      callBtn.disabled = true;
      triggerNativeConnect(mode);
    });
  });

  actions.appendChild(chatBtn);
  actions.appendChild(callBtn);
  card.appendChild(actions);

  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

// #1 on the QA list: the send button looked identically "live" whether
// or not there was anything to send — toggle its real disabled state (and
// the .send-btn:disabled styling, styles.css) off the input itself.
sendButton.disabled = !chatInput.value.trim();
chatInput.addEventListener('input', () => {
  sendButton.disabled = !chatInput.value.trim();
});

sendButton.addEventListener('click', () => {
  trackEvent('send_button_tap', { screen_name: 'chatbot_screen', event_type: 'tap' });
  sendMessage();
  // #12: without this, some mobile WebViews drop focus (and dismiss the
  // keyboard) the moment the DOM updates from clearing chatInput.value —
  // explicitly re-focusing keeps the keyboard up so the visitor can just
  // keep typing their next message.
  chatInput.focus();
});
chatInput.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  sendMessage();
  chatInput.focus();
});

// The cross in the header — same close_webview action as the feedback
// card's "Skip"/inactivity nudge's "Close it out", just reachable from
// anywhere in the conversation, not only at the end of it. Uses
// closeChat's PROFILE_DEEPLINK default, same as those other paths.
// consulation_ended fires from inside closeChat() itself, not here —
// same event regardless of which path closed the chat.
if (closeBtn) closeBtn.addEventListener('click', () => {
  trackEvent('close_button_tap', { screen_name: 'chatbot_screen', event_type: 'tap' });
  closeChat();
});

photoBtn.addEventListener('click', () => {
  trackEvent('upload_image_tap', {});
  photoInput.click();
});
photoInput.addEventListener('change', () => {
  const file = photoInput.files[0];
  photoInput.value = '';
  if (file) handlePhotoUpload(file);
});

document.querySelectorAll('.quick-reply').forEach((button) => {
  button.addEventListener('click', () => {
    const question = button.getAttribute('data-text');
    trackEvent('chip_selected', {
      screen_name: 'chatbot_screen',
      chip_name: question,
      chip_id: button.getAttribute('data-chip-id') || 'N/A',
      event_type: 'tap',
    });
    sendMessage(question);
  });
});
