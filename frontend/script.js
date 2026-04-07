const API_BASE = '/api';  // Vercel проксирует /api на serverless функцию

let currentThreadId = null;

async function fetchLetters() {
    const res = await fetch(`${API_BASE}/letters`);
    return await res.json();
}

async function fetchThread(threadId) {
    const res = await fetch(`${API_BASE}/threads/${threadId}`);
    if (!res.ok) return [];
    return await res.json();
}

function renderLetterList(letters) {
    const ul = document.getElementById('letterList');
    ul.innerHTML = '';
    // Группируем по thread_id, показываем последнее письмо из каждого треда
    const threadsMap = new Map();
    letters.forEach(letter => {
        if (!threadsMap.has(letter.thread_id) || new Date(letter.created_at) > new Date(threadsMap.get(letter.thread_id).created_at)) {
            threadsMap.set(letter.thread_id, letter);
        }
    });
    const threads = Array.from(threadsMap.values()).sort((a,b) => new Date(b.created_at) - new Date(a.created_at));
    threads.forEach(thread => {
        const li = document.createElement('li');
        li.textContent = `${thread.subject} (${thread.sender})`;
        li.onclick = () => loadThread(thread.thread_id);
        ul.appendChild(li);
    });
}

async function loadThread(threadId) {
    currentThreadId = threadId;
    const letters = await fetchThread(threadId);
    const container = document.getElementById('threadView');
    container.innerHTML = '';
    letters.forEach(letter => {
        const div = document.createElement('div');
        div.className = `letter ${letter.sender === 'User' ? 'outgoing' : 'incoming'}`;
        div.innerHTML = `
            <div class="letter-header">От: ${letter.sender} | ${new Date(letter.created_at).toLocaleString()}</div>
            <div class="letter-subject">${escapeHtml(letter.subject)}</div>
            <div class="letter-body">${escapeHtml(letter.body)}</div>
            <button class="reply-btn" data-id="${letter.id}">Ответить</button>
        `;
        container.appendChild(div);
    });
    document.querySelectorAll('.reply-btn').forEach(btn => {
        btn.onclick = (e) => {
            const letterId = btn.getAttribute('data-id');
            openReplyModal(letterId);
        };
    });
}

function escapeHtml(str) {
    return str.replace(/[&<>]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        return m;
    });
}

async function createNewLetter(sender, subject, body) {
    const res = await fetch(`${API_BASE}/letters`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sender, subject, body, thread_id: null })
    });
    if (res.ok) {
        const newLetter = await res.json();
        await loadThread(newLetter.thread_id);
        await refreshList();
    } else {
        alert('Ошибка создания письма');
    }
}

async function sendReply(letterId, message, provider) {
    const res = await fetch(`${API_BASE}/letters/${letterId}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, provider })
    });
    if (res.ok) {
        await loadThread(currentThreadId);
        await refreshList();
    } else {
        alert('Ошибка при получении ответа от ИИ');
    }
}

async function refreshList() {
    const letters = await fetchLetters();
    renderLetterList(letters);
    if (currentThreadId) loadThread(currentThreadId);
}

// Модальные окна
const newModal = document.getElementById('newLetterModal');
const replyModal = document.getElementById('replyModal');
let replyToLetterId = null;

document.getElementById('newLetterBtn').onclick = () => newModal.classList.remove('hidden');
document.querySelectorAll('.close').forEach(el => {
    el.onclick = () => {
        newModal.classList.add('hidden');
        replyModal.classList.add('hidden');
    };
});
document.getElementById('sendNewLetter').onclick = () => {
    const sender = document.getElementById('newSender').value;
    const subject = document.getElementById('newSubject').value;
    const body = document.getElementById('newBody').value;
    if (sender && subject && body) {
        createNewLetter(sender, subject, body);
        newModal.classList.add('hidden');
        document.getElementById('newSender').value = 'User';
        document.getElementById('newSubject').value = '';
        document.getElementById('newBody').value = '';
    } else alert('Заполните все поля');
};

function openReplyModal(letterId) {
    replyToLetterId = letterId;
    replyModal.classList.remove('hidden');
    document.getElementById('replyMessage').value = '';
}
document.getElementById('sendReply').onclick = () => {
    const message = document.getElementById('replyMessage').value;
    const provider = document.getElementById('replyProvider').value;
    if (!message) return alert('Введите текст ответа');
    sendReply(replyToLetterId, message, provider);
    replyModal.classList.add('hidden');
};

// Инициализация
refreshList();