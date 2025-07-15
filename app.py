const socket = io();

document.getElementById('start-bot-btn').addEventListener('click', () => {
    fetch('/start_bot', {method: 'POST'}).then(res => res.json()).then(console.log);
});

document.getElementById('stop-bot-btn').addEventListener('click', () => {
    fetch('/stop_bot', {method: 'POST'}).then(res => res.json()).then(console.log);
});

socket.on('log_message', (msg) => {
    const logContainer = document.getElementById('logs');
    const p = document.createElement('p');
    p.textContent = msg.data;
    logContainer.appendChild(p);
    logContainer.scrollTop = logContainer.scrollHeight;
});

socket.on('bot_status_update', (data) => {
    const statusBadge = document.getElementById('bot-status-badge');
    if (data.status) {
        statusBadge.innerHTML = '<span class="badge bg-success">Çalışıyor</span>';
    } else {
        statusBadge.innerHTML = '<span class="badge bg-danger">Durdu</span>';
    }
});
