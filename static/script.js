const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');
const chatFab = document.getElementById('chatFab');
const chatOverlay = document.getElementById('chatOverlay');
const chatClose = document.getElementById('chatClose');

const welcomeMessage = "Hello! I can help with love, career, finance, marriage, or kundali guidance. Ask me about consultations, packages, or booking support.";

function openChat() {
  chatOverlay.hidden = false;
  chatFab.hidden = true;
  chatInput.focus();
}

function closeChat() {
  chatOverlay.hidden = true;
  chatFab.hidden = false;
}

chatFab.addEventListener('click', openChat);
chatClose.addEventListener('click', closeChat);

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
}

if (chatBody && chatBody.children.length === 0) {
  appendMessage('bot', welcomeMessage);
}

async function sendMessage() {
  const text = (chatInput.value || '').trim();
  if (!text) return;

  appendMessage('user', text);
  chatInput.value = '';

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

// Stub for the app's real recommend-astrologer flow, which this repo has no
// access to — renders the astrologer the backend already matched as an
// inline card, same shape as the real app's in-chat connect card.
function renderConnectCard(action) {
  const astrologer = action.astrologer;
  if (!astrologer) return;

  const card = document.createElement('div');
  card.className = 'message bot connect-card';

  const avatar = document.createElement('img');
  avatar.className = 'avatar';
  avatar.src = astrologer.image;
  avatar.alt = astrologer.name;

  const info = document.createElement('div');
  info.className = 'connect-info';
  const name = document.createElement('div');
  name.className = 'connect-name';
  name.textContent = `${astrologer.name} · ★${astrologer.rating}`;
  const meta = document.createElement('div');
  meta.className = 'connect-meta';
  meta.textContent = astrologer.availability;
  info.appendChild(name);
  info.appendChild(meta);

  const btn = document.createElement('button');
  btn.className = 'connect-btn';
  btn.type = 'button';
  btn.textContent = action.label;
  btn.addEventListener('click', () => {
    btn.disabled = true;
    btn.textContent = '...';
    setTimeout(() => appendMessage('bot', `Connecting you to ${astrologer.name}...`), 200);
  });

  card.appendChild(avatar);
  card.appendChild(info);
  card.appendChild(btn);
  chatBody.appendChild(card);
  chatBody.scrollTop = chatBody.scrollHeight;
}

sendButton.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') sendMessage();
});

document.querySelectorAll('.quick-reply').forEach((button) => {
  button.addEventListener('click', () => {
    const value = button.getAttribute('data-text');
    chatInput.value = value;
    chatInput.focus();
  });
});
