const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendBtn');
const chatBody = document.getElementById('chatBody');

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
        renderConnectAction(data.action);
      }
    }, 250);
  } catch (error) {
    setTimeout(() => appendMessage('bot', "I don't have information regarding that."), 250);
  }
}

// Stub for Step 5's "existing recommend-astrologer flow" hand-off — this repo
// has no such system to call into, so this just surfaces the entry point:
// scroll the astrologer already picked server-side into view and highlight it.
function renderConnectAction(action) {
  const btn = document.createElement('button');
  btn.className = 'quick-reply connect-action';
  btn.type = 'button';
  btn.textContent = action.label;
  btn.addEventListener('click', () => {
    btn.disabled = true;
    const cards = document.querySelectorAll('.right-panel .package-card strong');
    const match = Array.from(cards).find((el) => el.textContent === action.astrologer?.name);
    const card = match ? match.closest('.package-card') : null;
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      card.classList.add('connect-highlight');
      setTimeout(() => card.classList.remove('connect-highlight'), 2000);
    }
  });
  chatBody.appendChild(btn);
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
