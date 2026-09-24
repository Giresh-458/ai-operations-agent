let currentTaskId = null;
let previousSnapshot = null;
let currentProvider = null;
let currentApiKey = null;

async function connectProvider() {
    const provider = document.getElementById('providerSelect').value;
    const apiKey = document.getElementById('apiKeyInput').value;
    const errDiv = document.getElementById('providerError');
    errDiv.style.display = 'none';

    if (!provider) {
        errDiv.textContent = 'Please select a provider.';
        errDiv.style.display = 'block';
        return;
    }
    if (!apiKey) {
        errDiv.textContent = 'Please enter an API key.';
        errDiv.style.display = 'block';
        return;
    }

    try {
        const res = await fetch('/validate_provider', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ provider, api_key: apiKey })
        });
        
        const data = await res.json();
        
        if (!res.ok) {
            errDiv.textContent = data.detail || 'Unable to connect. Please check your API key.';
            errDiv.style.display = 'block';
            return;
        }

        // Success
        currentProvider = provider;
        currentApiKey = apiKey;
        
        document.getElementById('providerSetupForm').style.display = 'none';
        document.getElementById('providerConnectedState').style.display = 'flex';
        document.getElementById('connectedProviderName').textContent = provider;
        document.getElementById('connectedModelName').textContent = data.model || 'Unknown';
        document.getElementById('providerSuccessMsg').textContent = data.message;
        
        document.getElementById('heroCardSection').style.opacity = '1';
        document.getElementById('heroCardSection').style.pointerEvents = 'auto';
        document.getElementById('requestCardSection').style.opacity = '1';
        document.getElementById('requestCardSection').style.pointerEvents = 'auto';
        
    } catch (e) {
        errDiv.textContent = 'Network error while connecting.';
        errDiv.style.display = 'block';
    }
}

function changeProvider() {
    currentProvider = null;
    currentApiKey = null;
    
    document.getElementById('apiKeyInput').value = '';
    
    document.getElementById('providerSetupForm').style.display = 'block';
    document.getElementById('providerConnectedState').style.display = 'none';
    
    document.getElementById('heroCardSection').style.opacity = '0.5';
    document.getElementById('heroCardSection').style.pointerEvents = 'none';
    document.getElementById('requestCardSection').style.opacity = '0.5';
    document.getElementById('requestCardSection').style.pointerEvents = 'none';
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
    }[ch]));
}

function useExample(text) {
    document.getElementById('requestInput').value = text;
    document.getElementById('requestInput').focus();
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    const btn = [...document.querySelectorAll('.tab-btn')].find(b => b.getAttribute('onclick')?.includes(tabId));
    if (btn) btn.classList.add('active');
    if (tabId === 'dataTab') refreshDatabaseView();
    if (tabId === 'historyTab') loadTaskHistory();
}

function resetPipeline() {
    document.querySelectorAll('.step').forEach(el => el.classList.remove('active','done'));
    document.getElementById('step-intent').classList.add('active');
    document.getElementById('workflowLabel').textContent = 'Processing…';
}

function renderParameters(params) {
    const box = document.getElementById('parameterChips');
    const entries = Object.entries(params || {});
    if (!entries.length) {
        box.innerHTML = '<span class="empty-state">No parameters extracted.</span>';
        return;
    }
    box.innerHTML = entries.map(([k,v]) =>
        `<span class="chip"><b>${escapeHtml(k)}</b>${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : v)}</span>`
    ).join('');
}

function updateUI(data) {
    document.getElementById('taskStatus').textContent = data.status || 'UNKNOWN';
    document.getElementById('taskStatus').className = `badge ${data.status || ''}`;
    document.getElementById('taskIntent').textContent = data.intent || '—';
    document.getElementById('selectedTool').textContent = data.selected_tool || data.intent || '—';
    renderParameters(data.parameters || {});

    const result = data.result || {};
    const message = result.message || (result.error ? result.error : '');
    const resultBox = document.getElementById('resultSummary');
    if (message) {
        const cls = data.status === 'FAILED' || data.status === 'ERROR' ? 'danger-result' :
                    data.status === 'WAITING_FOR_APPROVAL' ? 'warning-result' : 'success-result';
        resultBox.innerHTML = `<span class="${cls}">${escapeHtml(message)}</span>`;
    } else {
        resultBox.innerHTML = '<span class="empty-state">No operation result yet.</span>';
    }
    document.getElementById('taskResult').textContent = JSON.stringify(result, null, 2);

    const approval = document.getElementById('humanApproval');
    approval.style.display = data.status === 'WAITING_FOR_APPROVAL' ? 'grid' : 'none';
    document.getElementById('workflowLabel').textContent =
        data.status === 'WAITING_FOR_APPROVAL' ? 'Paused for approval' : (data.status || 'Ready');
}

function markPipeline(logs) {
    document.querySelectorAll('.step').forEach(el => el.classList.remove('active','done'));
    document.getElementById('step-intent').classList.add('active');

    for (const log of logs) {
        if (log.action === 'INTENT_DETECTED' && log.status === 'SUCCESS') {
            doneThen('step-intent','step-validate');
        }
        if (log.action === 'VALIDATION_SUCCESS' && log.status === 'SUCCESS') {
            doneThen('step-validate','step-tool');
        }
        if (log.action === 'TOOL_SELECTED' && log.status === 'SUCCESS') {
            doneThen('step-tool','step-execute');
        }
        if (log.action === 'TOOL_EXECUTED' && log.status === 'SUCCESS') {
            document.getElementById('step-execute').classList.remove('active');
            document.getElementById('step-execute').classList.add('done');
        }
    }

    if (logs.some(l => l.action === 'HUMAN_APPROVAL_REQUIRED')) {
        document.getElementById('step-validate').classList.add('done');
        document.getElementById('step-tool').classList.remove('active');
        document.getElementById('workflowLabel').textContent = 'Waiting for human approval';
    }
}

function doneThen(doneId, activeId) {
    document.getElementById(doneId).classList.remove('active');
    document.getElementById(doneId).classList.add('done');
    document.getElementById(activeId).classList.add('active');
}

async function executeTask() {
    const input = document.getElementById('requestInput');
    const request = input.value.trim();
    if (!request) return;

    resetPipeline();
    document.getElementById('taskStatus').textContent = 'PROCESSING';
    document.getElementById('taskStatus').className = 'badge';
    document.getElementById('resultSummary').innerHTML = '<span class="empty-state">Agent is processing the request…</span>';
    document.getElementById('taskResult').textContent = '{}';
    document.getElementById('logList').innerHTML = '<div class="empty-state">Loading audit trail…</div>';

    try {
        const response = await fetch('/tasks', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({
                request: request,
                provider: currentProvider,
                api_key: currentApiKey
            })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        currentTaskId = data.task_id;
        updateUI(data);
        await fetchLogs(currentTaskId);
        await refreshDatabaseView();
        await loadTaskHistory();
    } catch (err) {
        document.getElementById('taskStatus').textContent = 'ERROR';
        document.getElementById('taskStatus').className = 'badge FAILED';
        document.getElementById('resultSummary').innerHTML = `<span class="danger-result">${escapeHtml(err.message)}</span>`;
    }
}

async function fetchLogs(taskId) {
    if (!taskId) return;
    try {
        const response = await fetch(`/tasks/${taskId}/logs`);
        const logs = await response.json();
        markPipeline(logs);
        document.getElementById('logCount').textContent = `${logs.length} event${logs.length === 1 ? '' : 's'}`;

        const list = document.getElementById('logList');
        if (!logs.length) {
            list.innerHTML = '<div class="empty-state">No audit events found.</div>';
            return;
        }
        list.innerHTML = logs.map(log => {
            const cls = log.status === 'SUCCESS' ? 'success' : log.status === 'FAILED' ? 'failed' : '';
            const detail = log.details ? escapeHtml(log.details) : '';
            return `<div class="timeline-row">
                <span class="timeline-dot ${cls}"></span>
                <div><div class="timeline-action">${escapeHtml(log.action)}</div>${detail ? `<div class="timeline-detail">${detail}</div>` : ''}</div>
                <span class="timeline-time">${escapeHtml(log.created_at || '')}</span>
            </div>`;
        }).join('');
    } catch (err) {
        console.error(err);
    }
}

async function approveTask() {
    if (!currentTaskId) return;
    try {
        const response = await fetch(`/tasks/${currentTaskId}/approve`, {method:'POST'});
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        updateUI(data);
        await fetchLogs(currentTaskId);
        await refreshDatabaseView();
        await loadTaskHistory();
    } catch (err) { alert(err.message); }
}

async function rejectTask() {
    if (!currentTaskId) return;
    try {
        const response = await fetch(`/tasks/${currentTaskId}/reject`, {method:'POST'});
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        const taskResponse = await fetch(`/tasks/${currentTaskId}`);
        updateUI(await taskResponse.json());
        await fetchLogs(currentTaskId);
        await refreshDatabaseView();
        await loadTaskHistory();
    } catch (err) { alert(err.message); }
}

function statusPill(status) {
    const s = String(status || '');
    const cls = ['DELIVERED','SHIPPED','SENT','COMPLETED','OPEN','APPROVED'].includes(s) ? 'good' :
                ['PENDING','PROCESSING','HIGH','CRITICAL','WAITING_FOR_APPROVAL'].includes(s) ? 'warn' :
                ['CANCELLED','FAILED','REJECTED','ERROR'].includes(s) ? 'bad' : '';
    return `<span class="status-pill ${cls}">${escapeHtml(s)}</span>`;
}

function renderRows(id, rows, renderer, emptyCols) {
    const body = document.querySelector(`#${id} tbody`);
    body.innerHTML = rows.length ? rows.map(renderer).join('') :
        `<tr><td colspan="${emptyCols}" class="empty-state">No records</td></tr>`;
}

async function refreshDatabaseView() {
    try {
        const res = await fetch('/database');
        if (!res.ok) throw new Error('Database endpoint unavailable');
        const data = await res.json();

        document.getElementById('ordersCount').textContent = data.orders.length;
        document.getElementById('ticketsCount').textContent = data.tickets.length;
        document.getElementById('customersCount').textContent = data.customers.length;
        document.getElementById('notificationsCount').textContent = data.notifications.length;

        document.getElementById('dbOrdersCount').textContent = data.orders.length;
        document.getElementById('dbTicketsCount').textContent = data.tickets.length;
        document.getElementById('dbCustomersCount').textContent = data.customers.length;
        document.getElementById('dbNotificationsCount').textContent = data.notifications.length;

        renderRows('ordersTable', data.orders,
            o => `<tr><td>#${escapeHtml(o.id)}</td><td>${escapeHtml(o.customer_id)}</td><td>${statusPill(o.status)}</td></tr>`, 3);

        renderRows('customersTable', data.customers,
            c => `<tr><td>#${escapeHtml(c.id)}</td><td><b>${escapeHtml(c.name)}</b></td><td>${escapeHtml(c.email)}</td></tr>`, 3);

        renderRows('ticketsTable', data.tickets,
            t => `<tr><td>#${escapeHtml(t.id)}</td><td>${escapeHtml(t.customer_id)}</td><td>${escapeHtml(t.issue)}</td><td>${statusPill(t.priority)}</td><td>${statusPill(t.status)}</td></tr>`, 5);

        renderRows('notificationsTable', data.notifications,
            n => `<tr><td>#${escapeHtml(n.id)}</td><td>${escapeHtml(n.customer_id)}</td><td>${escapeHtml(n.message)}</td><td>${statusPill(n.status)}</td></tr>`, 4);

        if (previousSnapshot) {
            const changed = [];
            for (const key of ['orders','tickets','customers','notifications']) {
                if (JSON.stringify(previousSnapshot[key]) !== JSON.stringify(data[key])) changed.push(key);
            }
            document.getElementById('impactMessage').textContent = changed.length
                ? `Database refreshed. Changed dataset(s): ${changed.join(', ')}.`
                : 'Database refreshed. No records changed in this operation.';
        } else {
            document.getElementById('impactMessage').textContent = 'Live database state loaded successfully.';
        }
        previousSnapshot = data;
    } catch (err) {
        document.getElementById('impactMessage').textContent = `Unable to load database: ${err.message}`;
    }
}

async function loadTaskHistory() {
    try {
        const res = await fetch('/tasks');
        if (!res.ok) throw new Error('Task history unavailable');
        const tasks = await res.json();
        const body = document.querySelector('#historyTable tbody');
        body.innerHTML = tasks.length ? tasks.map(t => `
            <tr>
                <td>#${escapeHtml(t.id)}</td>
                <td title="${escapeHtml(t.request)}">${escapeHtml(t.request.length > 65 ? t.request.slice(0,65)+'…' : t.request)}</td>
                <td>${escapeHtml(t.intent || '—')}</td>
                <td>${statusPill(t.status)}</td>
                <td><button class="history-action" onclick="openTask(${t.id})">View</button></td>
            </tr>
        `).join('') : '<tr><td colspan="5" class="empty-state">No tasks yet.</td></tr>';
    } catch (err) { console.error(err); }
}

async function openTask(taskId) {
    try {
        const res = await fetch(`/tasks/${taskId}`);
        const data = await res.json();
        currentTaskId = taskId;
        switchTab('agentTab');
        updateUI(data);
        await fetchLogs(taskId);
    } catch (err) { alert(err.message); }
}

window.addEventListener('load', async () => {
    document.getElementById('dashboardGrid').style.display = 'grid';
    await refreshDatabaseView();
    await loadTaskHistory();
});
