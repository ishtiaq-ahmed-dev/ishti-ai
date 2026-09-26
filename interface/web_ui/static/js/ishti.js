/**
 * ISHTI Intelligent Assistant - Frontend Cyber-HUD Controller
 * Controls holographic core canvas, real-time WebSocket events,
 * animated streaming text responses, dedicated execution and research views,
 * clickable DAG inspection drawer, session management, error log,
 * and system doctor diagnostics.
 */

let ws = null;
let isListening = false;
let recognition = null;
let currentConsequentialTaskId = null;
let currentSessionId = null;
let activeExecutionData = {
  prompt: '',
  tasks: {},
  stages: {},
  totalTasks: 0,
  completedTasks: 0
};

document.addEventListener('DOMContentLoaded', () => {
  initVisualizer();
  initWebSocket();
  initSpeechRecognition();
  initNavigation();
  initDrawer();
  fetchInitialStatus();
  fetchReflections();
  fetchResearchArchives();
  fetchSessions();

  // Command & UI Event Listeners
  const cmdForm = document.getElementById('cmdForm');
  if (cmdForm) cmdForm.addEventListener('submit', handleCommandSubmit);

  const voiceToggleBtn = document.getElementById('voiceToggleBtn');
  if (voiceToggleBtn) voiceToggleBtn.addEventListener('click', toggleVoiceInput);

  const reactorWrapper = document.getElementById('reactorWrapper');
  if (reactorWrapper) reactorWrapper.addEventListener('click', toggleVoiceInput);

  const btnApprove = document.getElementById('btnApprove');
  if (btnApprove) btnApprove.addEventListener('click', () => sendConfirmation(true));

  const btnReject = document.getElementById('btnReject');
  if (btnReject) btnReject.addEventListener('click', () => sendConfirmation(false));

  const btnRebenchmark = document.getElementById('btnRebenchmark');
  if (btnRebenchmark) btnRebenchmark.addEventListener('click', triggerRebenchmark);

  const btnRunDoctor = document.getElementById('btnRunDoctor');
  if (btnRunDoctor) btnRunDoctor.addEventListener('click', runDoctorDiagnostics);

  const btnClearLog = document.getElementById('btnClearLog');
  if (btnClearLog) btnClearLog.addEventListener('click', () => {
    const el = document.getElementById('eventLogStream');
    if (el) el.innerHTML = '';
  });

  const btnCopyReport = document.getElementById('btnCopyReport');
  if (btnCopyReport) btnCopyReport.addEventListener('click', copyResearchReport);

  // New Chat button
  const btnNewChat = document.getElementById('btnNewChat');
  if (btnNewChat) btnNewChat.addEventListener('click', createNewChat);

  // Error log buttons
  const btnClearErrors = document.getElementById('btnClearErrors');
  if (btnClearErrors) btnClearErrors.addEventListener('click', clearErrorLog);

  const btnRefreshErrors = document.getElementById('btnRefreshErrors');
  if (btnRefreshErrors) btnRefreshErrors.addEventListener('click', fetchErrorLog);
});

// ========================================================
// NAVIGATION & VIEW SWITCHER
// ========================================================
function initNavigation() {
  const tabs = document.querySelectorAll('.nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetView = tab.getAttribute('data-view');
      switchView(targetView);
      // Auto-load error log when switching to it
      if (targetView === 'viewErrors') fetchErrorLog();
    });
  });
}

function switchView(targetViewId) {
  // Normalize if short name passed (e.g. "executions" -> "viewExecutions")
  if (!targetViewId.startsWith('view')) {
    targetViewId = 'view' + targetViewId.charAt(0).toUpperCase() + targetViewId.slice(1);
  }

  // Deactivate all tabs and views
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));

  // Activate selected tab
  const activeTab = document.querySelector(`.nav-tab[data-view="${targetViewId}"]`);
  if (activeTab) activeTab.classList.add('active');

  // Activate selected view container
  const activeView = document.getElementById(targetViewId);
  if (activeView) {
    activeView.classList.add('active');
    console.log(`[HUD] Navigated to view: ${targetViewId}`);
  }
}

// ========================================================
// SESSION MANAGEMENT
// ========================================================
async function fetchSessions() {
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    currentSessionId = data.active_session_id || null;
    renderSessionsList(data.sessions || []);
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}

function renderSessionsList(sessions) {
  const container = document.getElementById('sessionsList');
  if (!container) return;
  container.innerHTML = '';

  if (!sessions.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:11px;">No sessions yet. Click + NEW to start.</div>';
    return;
  }

  sessions.forEach(sess => {
    const isActive = sess.session_id === currentSessionId;
    const item = document.createElement('div');
    item.className = `session-item ${isActive ? 'active' : ''}`;
    item.innerHTML = `
      <div class="session-item-content">
        <div class="session-item-title">${escapeHtml(sess.title || 'Untitled')}</div>
        <div class="session-item-meta">${sess.message_count || 0} msgs • ${escapeHtml(sess.updated_at || '').slice(11, 16) || 'now'}</div>
      </div>
      <button class="session-delete-btn" title="Delete session" data-sid="${escapeHtml(sess.session_id)}">✕</button>
    `;

    // Click to switch session
    item.querySelector('.session-item-content').addEventListener('click', () => {
      switchToSession(sess.session_id);
    });

    // Delete button
    item.querySelector('.session-delete-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      deleteSession(sess.session_id);
    });

    container.appendChild(item);
  });
}

async function createNewChat() {
  try {
    const res = await fetch('/api/sessions/new', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: 'New Conversation'})
    });
    const data = await res.json();
    currentSessionId = data.session_id;

    // Clear the chat feed
    const feed = document.getElementById('outputFeed');
    if (feed) {
      feed.innerHTML = '';
      const greeting = document.createElement('div');
      greeting.className = 'feed-item ishti msg-entrance';
      greeting.innerHTML = `<strong>ISHTI:</strong><div>New conversation started. How can I assist you?</div>`;
      feed.appendChild(greeting);
    }

    await fetchSessions();
    switchView('viewChat');
  } catch (err) {
    console.error('Failed to create new chat:', err);
  }
}

async function switchToSession(sessionId) {
  try {
    // Activate on backend
    await fetch(`/api/sessions/${sessionId}/activate`, { method: 'POST' });
    currentSessionId = sessionId;

    // Load session messages
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    const data = await res.json();

    // Repopulate chat feed
    const feed = document.getElementById('outputFeed');
    if (feed) {
      feed.innerHTML = '';
      const msgs = data.messages || [];
      if (!msgs.length) {
        const empty = document.createElement('div');
        empty.className = 'feed-item ishti msg-entrance';
        empty.innerHTML = `<strong>ISHTI:</strong><div>Session loaded. Ready for commands.</div>`;
        feed.appendChild(empty);
      } else {
        msgs.forEach(msg => {
          const div = document.createElement('div');
          div.className = `feed-item ${msg.role === 'user' ? 'user' : 'ishti'} msg-entrance`;
          const label = msg.role === 'user' ? 'YOU' : 'ISHTI';
          div.innerHTML = `<strong>${label}:</strong> <div style="margin-top:4px;">${formatMarkdown(msg.content)}</div>`;
          feed.appendChild(div);
        });
        feed.scrollTop = feed.scrollHeight;
      }
    }

    await fetchSessions();
  } catch (err) {
    console.error('Failed to switch session:', err);
  }
}

async function deleteSession(sessionId) {
  if (!confirm('Delete this conversation session?')) return;
  try {
    await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
    // If we deleted the active session, create a new one
    if (sessionId === currentSessionId) {
      await createNewChat();
    } else {
      await fetchSessions();
    }
  } catch (err) {
    console.error('Failed to delete session:', err);
  }
}

// ========================================================
// ERROR LOG MANAGEMENT
// ========================================================
async function fetchErrorLog() {
  try {
    const res = await fetch('/api/errors');
    const data = await res.json();
    const errors = data.errors || [];
    const badge = document.getElementById('errorCountBadge');
    if (badge) badge.textContent = `${errors.length} ERROR${errors.length !== 1 ? 'S' : ''}`;

    const grid = document.getElementById('errorLogGrid');
    if (!grid) return;

    if (!errors.length) {
      grid.innerHTML = '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:40px 0;">No errors logged. System is clean. 🟢</div>';
      return;
    }

    grid.innerHTML = '';
    errors.forEach(err => {
      const card = document.createElement('div');
      const isResolved = err.resolved;
      card.className = `error-log-card ${isResolved ? 'resolved' : 'unresolved'}`;
      card.innerHTML = `
        <div class="error-log-header">
          <span class="error-id">${escapeHtml(err.error_id)}</span>
          <span class="error-severity ${err.severity === 'ERROR' ? 'sev-error' : 'sev-warn'}">${escapeHtml(err.severity)}</span>
        </div>
        <div class="error-subsystem">${escapeHtml(err.subsystem)}</div>
        <div class="error-message">${escapeHtml(err.error_message)}</div>
        <div class="error-timestamp">${escapeHtml(err.timestamp)}</div>
        ${err.user_prompt ? `<div class="error-prompt">Prompt: "${escapeHtml(err.user_prompt.slice(0, 80))}"</div>` : ''}
        <div class="error-fix">💡 ${escapeHtml(err.suggested_fix)}</div>
        <details class="error-trace-details">
          <summary>Stack Trace</summary>
          <pre class="error-trace">${escapeHtml(err.traceback || 'No traceback')}</pre>
        </details>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    console.error('Failed to fetch error log:', err);
  }
}

async function clearErrorLog() {
  if (!confirm('Clear ALL error log records?')) return;
  try {
    await fetch('/api/errors/clear', { method: 'POST' });
    await fetchErrorLog();
  } catch (err) {
    console.error('Failed to clear error log:', err);
  }
}

// ========================================================
// DRAWER FOR CLICKABLE DAG NODE INSPECTION
// ========================================================
function initDrawer() {
  const btnClose = document.getElementById('btnCloseDrawer');
  const drawer = document.getElementById('nodeDrawer');
  if (btnClose && drawer) {
    btnClose.addEventListener('click', () => {
      drawer.classList.remove('active');
    });
  }
}

function openNodeDrawer(taskData) {
  const drawer = document.getElementById('nodeDrawer');
  if (!drawer) return;

  document.getElementById('drawerTaskName').textContent = taskData.name || taskData.task_id || 'Subtask';
  document.getElementById('drawerToolType').textContent = (taskData.type || 'tool').toUpperCase();
  document.getElementById('drawerDuration').textContent = `Status: ${(taskData.status || 'unknown').toUpperCase()} • Duration: ${taskData.duration || '0.2s'}`;
  
  let outputText = taskData.notes || taskData.output_preview || taskData.output || 'No output recorded yet.';
  if (typeof outputText === 'object') {
    outputText = JSON.stringify(outputText, null, 2);
  }
  document.getElementById('drawerOutput').textContent = outputText;

  drawer.classList.add('active');
}

// ========================================================
// INITIAL STATUS FETCH
// ========================================================
async function fetchInitialStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    
    if (document.getElementById('valModel')) {
      document.getElementById('valModel').textContent = data.primary_model || 'qwen2.5:14b';
    }
    if (document.getElementById('valFastModel')) {
      document.getElementById('valFastModel').textContent = data.fast_model || 'llama3.2:3b';
    }
    if (document.getElementById('badgeModel')) {
      document.getElementById('badgeModel').textContent = 'MODEL: ' + (data.primary_model || 'LOCAL');
    }
    
    renderSkillsList(data.skills || []);
    renderSkillsFullGrid(data.skills || []);

    // Render cached last execution if available
    if (data.last_execution && data.last_execution.subtask_results && data.last_execution.subtask_results.length) {
      renderExecutionFromData(data.last_execution);
    }

    // Render cached last research if available
    if (data.last_research) {
      renderResearchFromData(data.last_research);
    }
  } catch (err) {
    console.error('Failed to load initial status:', err);
  }
}

async function fetchReflections() {
  try {
    const res = await fetch('/api/reflections');
    const data = await res.json();
    const content = data.content || 'System ready.';
    const box1 = document.getElementById('reflectionsLog');
    if (box1) box1.textContent = content;
    const box2 = document.getElementById('memoryReflectionsBox');
    if (box2) box2.textContent = content;
  } catch (err) {
    console.error('Failed to load reflections:', err);
  }
}

async function fetchResearchArchives() {
  try {
    const res = await fetch('/api/research');
    const data = await res.json();
    const container = document.getElementById('researchArchiveList');
    if (!container) return;

    const archives = Array.isArray(data) ? data : (data.archives || []);
    if (!archives.length) {
      container.innerHTML = '<div style="color:var(--text-dim);font-size:12px;">No research archives saved yet.</div>';
      return;
    }

    container.innerHTML = '';
    archives.forEach(arch => {
      const card = document.createElement('div');
      card.className = 'metric-card';
      card.style.cursor = 'pointer';
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div style="font-weight:600;color:var(--cyan-glow);">${escapeHtml(arch.topic || 'Untitled Research')}</div>
          <span style="font-size:10px;color:var(--gold-accent);">${escapeHtml(arch.date || '')}</span>
        </div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px;">Sources gathered: ${arch.sources_count || 0}</div>
        <div style="font-size:11px;color:#a0b8d4;margin-top:6px;line-height:1.4;">${escapeHtml((arch.summary || '').slice(0, 180))}...</div>
      `;
      card.addEventListener('click', () => {
        renderResearchFromData(arch);
        switchView('viewResearch');
      });
      container.appendChild(card);
    });
  } catch (err) {
    console.error('Failed to load research archives:', err);
  }
}

function renderSkillsList(skills) {
  const container = document.getElementById('skillsList');
  if (!container) return;
  container.innerHTML = '';
  if (!skills.length) {
    container.innerHTML = '<div style="color:#7f93ad;font-size:11px;">No skills registered.</div>';
    return;
  }
  skills.forEach(s => {
    const div = document.createElement('div');
    div.className = 'skill-pill';
    div.innerHTML = `
      <div class="skill-pill-name">⚡ ${escapeHtml(s.name || s.skill_id)}</div>
      <div class="skill-pill-meta">${escapeHtml(s.description ? s.description.slice(0, 60) + '...' : '')}</div>
    `;
    container.appendChild(div);
  });
}

function renderSkillsFullGrid(skills) {
  const container = document.getElementById('skillsFullGrid');
  if (!container) return;
  container.innerHTML = '';
  if (!skills.length) {
    container.innerHTML = '<div style="color:var(--text-dim);font-size:12px;">No learned skills saved yet.</div>';
    return;
  }
  skills.forEach(s => {
    const card = document.createElement('div');
    card.className = 'glass-panel';
    card.style.padding = '14px';
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="font-family:var(--font-hud); font-size:13px; color:var(--cyan-glow);">⚡ ${escapeHtml(s.name || s.skill_id)}</span>
        <span class="badge ${s.status === 'broken' ? 'offline' : 'online'}" style="font-size:9px;">${escapeHtml((s.status || '').toUpperCase())} v${escapeHtml(s.version || '')}</span>
      </div>
      <div style="font-size:11.5px; color:#fff; margin-bottom:10px; line-height:1.4;">
        ${escapeHtml(s.description || '')}
      </div>
      <div style="font-size:10px; color:var(--text-dim); font-family:var(--font-mono);">
        HANDLES: ${escapeHtml(s.route || '')}${s.problem ? ' | PROBLEM: ' + escapeHtml(s.problem) : ''}
      </div>
    `;
    container.appendChild(card);
  });
}

// ========================================================
// WEBSOCKET INITIALIZATION & EVENT DISPATCHER
// ========================================================
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[HUD] Connected to ISHTI WebSocket Core');
    setReactorState('idle', 'STANDBY');
    updateAnswerStatus(null);
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleSystemEvent(msg.type, msg.data);
    } catch (e) {
      console.warn('WS parse error:', e);
    }
  };

  ws.onclose = () => {
    console.log('[HUD] WebSocket disconnected, reconnecting in 3s...');
    setTimeout(initWebSocket, 3000);
  };
}

function handleSystemEvent(type, data) {
  console.log('[EVENT]', type, data);

  switch (type) {
    case 'view_navigate':
      if (data && (data.view || data.target_view)) {
        switchView(data.view || data.target_view);
      }
      break;

    case 'status_update':
      if (data && data.status) {
        updateAnswerStatus(data.status, data.detail);
      }
      break;

    case 'status_change':
      if (data && data.state) {
        updateAnswerStatus(data.state, data.detail);
        if (data.progress_pct !== undefined) {
          const fill = document.getElementById('researchProgressFill');
          if (fill) fill.style.width = data.progress_pct + '%';
        }
      }
      break;

    case 'research_started':
      switchView('viewResearch');
      handleResearchUpdate({
        topic: data.topic,
        status: 'FORMULATING QUERIES',
        progress: 15,
        queries: [],
        sources: []
      });
      break;

    case 'research_queries_generated':
      handleResearchUpdate({
        queries: data.queries,
        status: 'SEARCHING MULTIPLE QUERIES',
        progress: 35
      });
      break;

    case 'research_sources_found':
      handleResearchUpdate({
        sources: data.sources,
        status: 'EXTRACTING EVIDENCE',
        progress: 55
      });
      break;

    case 'research_reading_source':
      updateAnswerStatus('RESEARCHING', `Reading source ${data.source_num}/${data.total}: ${(data.title || '').slice(0, 35)}...`);
      break;

    case 'research_verifying':
      updateAnswerStatus('VERIFYING', 'Cross-checking facts & resolving conflicts');
      handleResearchUpdate({
        status: 'VERIFYING',
        progress: 85
      });
      break;

    case 'research_completed':
      setReactorState('idle', 'ONLINE');
      updateAnswerStatus('COMPLETED', 'Research report synthesized');
      handleResearchUpdate({
        topic: data.topic,
        status: 'COMPLETED',
        progress: 100,
        queries: data.queries,
        sources: data.sources,
        report: data.report
      });
      fetchResearchArchives();
      break;

    case 'execution_started':
      setReactorState('executing', 'PLANNING...');
      updateAnswerStatus('THINKING / PLANNING', data.prompt);
      setupNewExecution(data.prompt, data.task_count || 1);
      appendEventLog(`DAG Execution started: "${(data.prompt || '').slice(0, 45)}..."`);
      break;

    case 'stage_started':
      setReactorState('executing', `STAGE ${data.stage}`);
      updateAnswerStatus('EXECUTING', `Stage ${data.stage} (${data.task_count} subtasks)`);
      addStageSection(data.stage, data.task_count);
      appendEventLog(`Stage ${data.stage} initialized with ${data.task_count} tasks`);
      break;

    case 'task_started':
      updateTaskCard(data.task_id, data.name, 'running', data.type, null, null);
      updateAnswerStatus('EXECUTING', data.name);
      appendEventLog(`Task Started: ${data.name || data.task_id} [${data.type || 'action'}]`);
      break;

    case 'task_finished':
      updateTaskCard(data.task_id, null, data.status, null, data.notes, data.output_preview);
      activeExecutionData.completedTasks++;
      updateExecutionProgress();
      appendEventLog(`Task Finished: ${data.name || data.task_id} -> ${data.status.toUpperCase()}`);
      break;

    case 'execution_finished':
      setReactorState('idle', 'ONLINE');
      updateAnswerStatus('COMPLETED', 'Task finished');
      finishExecution(data.status || 'completed');
      appendEventLog(`DAG Execution Completed [Status: ${data.status || 'OK'}]`);
      fetchInitialStatus();
      fetchReflections();
      break;

    case 'research_update':
      handleResearchUpdate(data);
      break;

    case 'consequential_modal_open':
      openConsequentialModal(data.task_id, data.name, data.details);
      break;

    case 'benchmark_completed':
      fetchInitialStatus();
      alert('Model re-benchmark finished! Optimal local routing matrix updated.');
      break;

    default:
      console.log('Unhandled event type:', type);
  }
}

// ========================================================
// ANSWER STATUS INDICATOR (THINKING, SEARCHING, ETC.)
// ========================================================
function updateAnswerStatus(state, detail = null) {
  const container = document.getElementById('answerStatusContainer');
  const pill = document.getElementById('answerStatusPill');
  const text = document.getElementById('answerStatusText');
  if (!container || !pill || !text) return;

  if (!state) {
    container.style.display = 'none';
    return;
  }

  container.style.display = 'block';
  let label = state.toUpperCase();
  if (detail) {
    label += ` — ${detail}`;
  }
  text.textContent = label;

  pill.className = 'answer-status-pill';
  if (state.includes('SEARCH')) {
    pill.classList.add('searching');
  } else if (state.includes('RESEARCH')) {
    pill.classList.add('researching');
  } else if (state.includes('VERIF')) {
    pill.classList.add('verifying');
  } else if (state.includes('RESPOND')) {
    pill.classList.add('responding');
  }
}

// ========================================================
// COMMAND SUBMISSION & STREAMING ANIMATION
// ========================================================
async function handleCommandSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('cmdInput');
  const text = input.value.trim();
  if (!text) return;

  // Append user message with animation
  appendFeedMessage('user', text);
  input.value = '';
  setReactorState('executing', 'PROCESSING');
  updateAnswerStatus('THINKING / PLANNING', 'Classifying intent');

  try {
    // NDJSON stream: {"token"} lines while the local model writes, then {"done", "result"}
    const res = await fetch('/api/execute_stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: text, session_id: currentSessionId || '' })
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let pending = '', live = null, liveText = '', data = {};
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const msg = JSON.parse(line);
        if (msg.token) {
          if (!live) {
            live = startAnswerBubble('ishti');
            updateAnswerStatus('RESPONDING', 'Streaming from local model');
          }
          liveText += msg.token;
          live.contentDiv.innerHTML = formatMarkdown(liveText);
          document.getElementById('outputFeed').scrollTop = 1e9;
        } else if (msg.done) {
          data = msg.result;
        }
      }
    }

    // Update session_id from backend response
    if (data.session_id) {
      currentSessionId = data.session_id;
    }

    // Final answer: animate if nothing streamed, otherwise just settle the live bubble
    updateAnswerStatus('RESPONDING', 'Synthesizing output');
    await streamAnswerIntoFeed('ishti', data.final_answer || 'Command executed.', data.sources || [], data, live);

    // If deep research results returned, render them
    if (data.research) {
      renderResearchFromData(data.research);
    }

    // Refresh session list to show updated titles/counts
    fetchSessions();
  } catch (err) {
    appendFeedMessage('ishti', `Error executing command: ${err.message}`);
  } finally {
    setReactorState('idle', 'ONLINE');
    setTimeout(() => updateAnswerStatus(null), 2500);
  }
}

// Appends immediate static message (e.g. user prompt) with entrance animation
function appendFeedMessage(role, text) {
  const feed = document.getElementById('outputFeed');
  if (!feed) return;
  const div = document.createElement('div');
  div.className = `feed-item ${role} msg-entrance`;
  div.innerHTML = `<strong>${role === 'user' ? 'YOU' : 'ISHTI'}:</strong> <div style="margin-top:4px;">${formatMarkdown(text)}</div>`;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

// Empty ISHTI answer bubble with a cursor; returns its parts so tokens can be written into it
function startAnswerBubble(role) {
  const feed = document.getElementById('outputFeed');
  const item = document.createElement('div');
  item.className = `feed-item ${role} msg-entrance`;
  const header = document.createElement('strong');
  header.textContent = 'ISHTI:';
  item.appendChild(header);
  const contentDiv = document.createElement('div');
  contentDiv.className = 'msg-content streaming-answer';
  item.appendChild(contentDiv);
  const cursor = document.createElement('span');
  cursor.className = 'streaming-cursor';
  item.appendChild(cursor);
  feed.appendChild(item);
  feed.scrollTop = feed.scrollHeight;
  return { item, contentDiv, cursor };
}

// Smooth word/token streaming animation with cursor and expandable sources
async function streamAnswerIntoFeed(role, fullText, sources = [], metadata = {}, live = null) {
  const feed = document.getElementById('outputFeed');
  if (!feed) return;
  const { item, contentDiv, cursor } = live || startAnswerBubble(role);

  if (live) {
    contentDiv.innerHTML = formatMarkdown(fullText);  // already streamed live; show the final cleaned text
  } else {
    // Stream text word-by-word with small smooth delay
    const words = fullText.split(/(\s+)/);
    let currentOutput = '';
    for (let i = 0; i < words.length; i++) {
      currentOutput += words[i];
      contentDiv.innerHTML = formatMarkdown(currentOutput);
      feed.scrollTop = feed.scrollHeight;
      if (i % 2 === 0) {
        await new Promise(r => setTimeout(r, 14));
      }
    }
  }

  // Remove cursor once finished
  cursor.remove();

  // If sources are present, append expandable Sources card
  if (sources && sources.length > 0) {
    const sourcesContainer = document.createElement('div');
    sourcesContainer.className = 'sources-container';
    sourcesContainer.innerHTML = `
      <div style="margin-top:10px;">
        <button class="sources-toggle-btn">
          <span>🌐 SOURCES (${sources.length})</span>
          <span style="font-size:10px;">▼</span>
        </button>
        <div class="sources-dropdown" style="display:none; margin-top:8px;"></div>
      </div>
    `;

    const toggleBtn = sourcesContainer.querySelector('.sources-toggle-btn');
    const dropdown = sourcesContainer.querySelector('.sources-dropdown');

    // Populate dropdown with source cards
    sources.forEach(src => {
      const card = document.createElement('div');
      card.className = 'source-card';
      const domain = getDomainFromUrl(src.url || '');
      card.innerHTML = `
        <div class="source-card-header">
          <span class="source-card-domain">${escapeHtml(domain || 'WEB')}</span>
          ${src.verified ? '<span class="source-card-badge">VERIFIED</span>' : ''}
        </div>
        <div class="source-card-title">${escapeHtml(src.title || 'Source Title')}</div>
        <div class="source-card-snippet">${escapeHtml((src.snippet || src.content || '').slice(0, 140))}...</div>
        <div class="source-card-footer">
          <a href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer" class="source-open-link">[OPEN]</a>
        </div>
      `;
      dropdown.appendChild(card);
    });

    toggleBtn.addEventListener('click', () => {
      const isVisible = dropdown.style.display !== 'none';
      dropdown.style.display = isVisible ? 'none' : 'grid';
      toggleBtn.querySelector('span:last-child').textContent = isVisible ? '▼' : '▲';
    });

    item.appendChild(sourcesContainer);
    feed.scrollTop = feed.scrollHeight;
  }
}

// ========================================================
// LIVE EXECUTION / DAG TRACKING
// ========================================================
function setupNewExecution(prompt, taskCount) {
  activeExecutionData = {
    prompt: prompt,
    tasks: {},
    stages: {},
    totalTasks: taskCount,
    completedTasks: 0
  };

  const title = document.getElementById('execTaskTitle');
  if (title) title.textContent = prompt || 'Execution in progress';

  const badge = document.getElementById('execStatusBadge');
  if (badge) {
    badge.textContent = 'RUNNING';
    badge.className = 'badge online';
  }

  const badgeCount = document.getElementById('badgeExecCount');
  if (badgeCount) badgeCount.textContent = '1';

  const stagesCont = document.getElementById('execStagesContainer');
  if (stagesCont) stagesCont.innerHTML = '';

  updateExecutionProgress();
}

function updateExecutionProgress() {
  const percent = activeExecutionData.totalTasks > 0
    ? Math.min(100, Math.round((activeExecutionData.completedTasks / activeExecutionData.totalTasks) * 100))
    : 0;

  const fill = document.getElementById('execProgressFill');
  if (fill) fill.style.width = percent + '%';

  const text = document.getElementById('execProgressText');
  if (text) text.textContent = percent + '%';
}

function addStageSection(stageNum, count) {
  const container = document.getElementById('execStagesContainer');
  if (!container) return;

  let stageCard = document.getElementById(`exec_stage_${stageNum}`);
  if (!stageCard) {
    stageCard = document.createElement('div');
    stageCard.id = `exec_stage_${stageNum}`;
    stageCard.className = 'stage-card';
    stageCard.innerHTML = `
      <div class="stage-header">
        <span>STAGE ${stageNum} ${count > 1 ? '(CONCURRENT / PARALLEL EXECUTION)' : '(SEQUENTIAL STEP)'}</span>
        <span>Tasks: ${count}</span>
      </div>
      <div class="tasks-grid" id="stage_grid_${stageNum}"></div>
    `;
    container.appendChild(stageCard);
  }
}

function updateTaskCard(taskId, taskName, status, type = null, notes = null, preview = null) {
  activeExecutionData.tasks[taskId] = {
    taskId,
    name: taskName || (activeExecutionData.tasks[taskId] ? activeExecutionData.tasks[taskId].name : taskId),
    status,
    type: type || (activeExecutionData.tasks[taskId] ? activeExecutionData.tasks[taskId].type : 'action'),
    notes: notes || preview,
    output_preview: preview
  };

  const taskData = activeExecutionData.tasks[taskId];
  let item = document.getElementById(`task_item_${taskId}`);

  if (!item) {
    const grids = document.querySelectorAll('.tasks-grid');
    const targetGrid = grids.length ? grids[grids.length - 1] : document.getElementById('execStagesContainer');
    item = document.createElement('div');
    item.id = `task_item_${taskId}`;
    item.className = `task-item ${status}`;
    item.innerHTML = `
      <div class="task-name">${escapeHtml(taskData.name)}</div>
      <div class="task-meta">${escapeHtml(taskData.type)} • <span class="task-status">${status.toUpperCase()}</span></div>
      <div class="task-preview" style="font-size:10px;color:#a0b8d4;margin-top:4px;"></div>
    `;
    // Clickable inspection drawer trigger
    item.addEventListener('click', () => openNodeDrawer(taskData));
    if (targetGrid) targetGrid.appendChild(item);
  } else {
    item.className = `task-item ${status}`;
    item.querySelector('.task-status').textContent = status.toUpperCase();
    if (preview) {
      item.querySelector('.task-preview').textContent = String(preview).slice(0, 80) + '...';
    }
    // Re-bind click with latest data
    item.onclick = () => openNodeDrawer(taskData);
  }
}

function finishExecution(status) {
  const badge = document.getElementById('execStatusBadge');
  if (badge) {
    badge.textContent = status.toUpperCase();
    badge.className = status === 'completed' ? 'badge online' : 'badge model';
  }
  const fill = document.getElementById('execProgressFill');
  if (fill) fill.style.width = '100%';
  const text = document.getElementById('execProgressText');
  if (text) text.textContent = '100%';
}

function appendEventLog(msg) {
  const stream = document.getElementById('eventLogStream');
  if (!stream) return;
  const now = new Date();
  const ts = now.toTimeString().split(' ')[0];
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `<span class="log-ts">${ts}</span> <span>${escapeHtml(msg)}</span>`;
  stream.appendChild(entry);
  stream.scrollTop = stream.scrollHeight;
}

function renderExecutionFromData(exec) {
  setupNewExecution(exec.prompt || 'Previous Task', (exec.subtask_results || []).length);
  addStageSection(1, (exec.subtask_results || []).length);
  (exec.subtask_results || []).forEach(t => {
    updateTaskCard(t.task_id, t.name, t.status, t.type || 'tool', t.notes, t.output);
  });
  finishExecution(exec.status || 'completed');
}

// ========================================================
// DEEP RESEARCH VIEW
// ========================================================
function handleResearchUpdate(data) {
  if (data.topic) {
    const topicText = document.getElementById('researchTopicText');
    if (topicText) topicText.textContent = data.topic;
  }

  const badge = document.getElementById('researchStatusBadge');
  if (badge && data.status) {
    badge.textContent = data.status.toUpperCase();
  }

  // Update Progress Fill
  if (data.progress !== undefined) {
    const fill = document.getElementById('researchProgressFill');
    if (fill) fill.style.width = data.progress + '%';
  }

  // Render Queries
  if (data.queries && Array.isArray(data.queries)) {
    const qList = document.getElementById('researchQueriesList');
    if (qList) {
      qList.innerHTML = '';
      data.queries.forEach((q, idx) => {
        const item = document.createElement('div');
        item.className = 'research-query-item';
        item.innerHTML = `
          <span style="color:var(--cyan-glow);">Q${idx + 1}:</span>
          <span>${escapeHtml(q)}</span>
        `;
        qList.appendChild(item);
      });
    }
  }

  // Render Sources Grid
  if (data.sources && Array.isArray(data.sources)) {
    renderResearchSources(data.sources);
  }

  // Render Final Report
  if (data.report) {
    const reportBox = document.getElementById('researchReportContent');
    if (reportBox) {
      reportBox.innerHTML = formatMarkdown(data.report);
    }
  }
}

function renderResearchSources(sources) {
  const container = document.getElementById('researchSourcesGrid');
  const countBadge = document.getElementById('researchSourcesCount');
  if (countBadge) countBadge.textContent = sources.length;
  if (!container) return;

  container.innerHTML = '';
  if (!sources.length) {
    container.innerHTML = '<div style="color:var(--text-dim); font-size:11px;">No sources gathered yet.</div>';
    return;
  }

  sources.forEach(src => {
    const card = document.createElement('div');
    card.className = 'source-card';
    const domain = getDomainFromUrl(src.url || '');
    card.innerHTML = `
      <div class="source-card-header">
        <span class="source-card-domain">${escapeHtml(domain || 'WEB')}</span>
        <span class="source-card-badge">VERIFIED ONLINE</span>
      </div>
      <div class="source-card-title">${escapeHtml(src.title || 'Online Source')}</div>
      <div class="source-card-snippet">${escapeHtml((src.snippet || src.content || '').slice(0, 160))}...</div>
      <div class="source-card-footer">
        <span style="font-size:9.5px;color:var(--text-dim);">${escapeHtml(src.publication_date || '')}</span>
        <a href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer" class="source-open-link">[OPEN SOURCE]</a>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderResearchFromData(res) {
  handleResearchUpdate({
    topic: res.topic || 'Archived Research',
    status: 'COMPLETED',
    progress: 100,
    queries: res.queries || [],
    sources: res.sources || [],
    report: res.report || res.summary || ''
  });
}

function copyResearchReport() {
  const reportBox = document.getElementById('researchReportContent');
  if (!reportBox) return;
  navigator.clipboard.writeText(reportBox.innerText).then(() => {
    alert('Research Report copied to clipboard!');
  }).catch(e => {
    console.error('Clipboard copy error:', e);
  });
}

// ========================================================
// DOCTOR DIAGNOSTICS SUITE
// ========================================================
async function runDoctorDiagnostics() {
  const btn = document.getElementById('btnRunDoctor');
  const lastRun = document.getElementById('docLastRunText');
  const overallBadge = document.getElementById('docOverallBadge');
  const grid = document.getElementById('doctorCardsGrid');
  if (!btn || !grid) return;

  btn.textContent = 'PROBING SUBSYSTEMS...';
  btn.disabled = true;
  if (lastRun) lastRun.textContent = 'Probe in progress: Testing local hardware, Ollama, Playwright, Web Search & Audio...';

  try {
    const res = await fetch('/api/doctor');
    const data = await res.json();

    btn.textContent = 'RUN FULL DIAGNOSTICS';
    btn.disabled = false;
    if (lastRun) lastRun.textContent = `Last probed at: ${new Date().toLocaleTimeString()}`;

    // Overall Status Badge
    if (overallBadge) {
      if (data.overall_status === 'HEALTHY' || data.summary.failed === 0) {
        overallBadge.textContent = 'STATUS: 100% HEALTHY';
        overallBadge.className = 'badge online';
      } else {
        overallBadge.textContent = `STATUS: ${data.summary.failed} FAILING`;
        overallBadge.className = 'badge model';
      }
    }

    grid.innerHTML = '';
    const checksData = data.checks || {};

    for (const [catName, catChecks] of Object.entries(checksData)) {
      if (!Array.isArray(catChecks) || !catChecks.length) continue;
      const card = document.createElement('div');
      card.className = 'doctor-card';
      
      let itemsHtml = '';
      catChecks.forEach(c => {
        const isPass = c.status === 'PASS';
        const isWarn = c.status === 'WARN';
        const statusClass = isPass ? 'pass' : (isWarn ? 'warn' : 'fail');
        const icon = isPass ? '✓' : (isWarn ? '!' : '✗');

        itemsHtml += `
          <div class="doctor-check-item">
            <div style="display:flex;align-items:center;gap:6px;">
              <span class="doctor-status-icon ${statusClass}">${icon}</span>
              <span style="font-weight:600;color:#fff;">${escapeHtml(c.test || c.name || '')}</span>
            </div>
            <div style="color:${isPass ? 'var(--cyan-glow)' : 'var(--gold-accent)'};font-size:10.5px;">${escapeHtml(c.details || c.message || '')}</div>
          </div>
        `;
      });

      card.innerHTML = `
        <div class="doctor-card-title">${escapeHtml(catName)}</div>
        <div style="display:flex;flex-direction:column;gap:8px;">${itemsHtml}</div>
      `;
      grid.appendChild(card);
    }
  } catch (err) {
    btn.textContent = 'RUN FULL DIAGNOSTICS';
    btn.disabled = false;
    alert('Diagnostics error: ' + err.message);
  }
}

// ========================================================
// RE-BENCHMARK TRIGGER
// ========================================================
async function triggerRebenchmark() {
  if (!confirm('Run local model benchmark suite against all installed Ollama models on the RTX 5070 Ti?')) return;
  setReactorState('executing', 'BENCHMARKING');
  try {
    const res = await fetch('/api/rebenchmark', { method: 'POST' });
    const data = await res.json();
    console.log('Benchmark response:', data);
  } catch (err) {
    alert('Benchmark error: ' + err.message);
  } finally {
    setReactorState('idle', 'ONLINE');
  }
}

// ========================================================
// CONSEQUENTIAL ACTION GATE CONFIRMATION
// ========================================================
function openConsequentialModal(taskId, name, details) {
  currentConsequentialTaskId = taskId;
  const nameEl = document.getElementById('modalTaskName');
  if (nameEl) nameEl.textContent = name || 'Action Verification';
  const detEl = document.getElementById('modalDetails');
  if (detEl) detEl.textContent = details || 'Confirm real-world payload execution.';
  
  const modal = document.getElementById('modalOverlay');
  if (modal) modal.classList.add('active');
  setReactorState('listening', 'CONFIRM GATE');
}

async function sendConfirmation(approved) {
  const modal = document.getElementById('modalOverlay');
  if (modal) modal.classList.remove('active');
  if (!currentConsequentialTaskId) return;

  try {
    await fetch('/api/confirm_action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: currentConsequentialTaskId, approved })
    });
  } catch (e) {
    console.error('Confirmation post error:', e);
  }
  currentConsequentialTaskId = null;
  setReactorState('idle', 'ONLINE');
}

// Keyboard shortcuts for modal
window.addEventListener('keydown', (e) => {
  const modal = document.getElementById('modalOverlay');
  if (!modal || !modal.classList.contains('active')) return;
  if (e.key === 'Escape') {
    sendConfirmation(false);
  } else if (e.key.toLowerCase() === 'y') {
    sendConfirmation(true);
  }
});

// ========================================================
// VOICE INPUT (local: MediaRecorder -> /api/stt -> faster-whisper on this PC)
// Web Speech API was removed: in Chrome it streams audio to Google's servers.
// ========================================================
let mediaRecorder = null;

function initSpeechRecognition() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    console.warn('Microphone recording not available in this browser.');
  }
}

function setVoiceButton(active) {
  const btn = document.getElementById('voiceToggleBtn');
  if (btn) btn.classList.toggle('active', active);
}

async function toggleVoiceInput() {
  if (isListening && mediaRecorder) {
    mediaRecorder.stop();  // onstop sends the clip
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    alert('Microphone blocked: ' + err.message);
    return;
  }
  const chunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mediaRecorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    isListening = false;
    setVoiceButton(false);
    setReactorState('thinking', 'TRANSCRIBING...');
    const form = new FormData();
    form.append('audio', new Blob(chunks, { type: mediaRecorder.mimeType }), 'speech.webm');
    try {
      const res = await fetch('/api/stt', { method: 'POST', body: form });
      const { text } = await res.json();
      const input = document.getElementById('cmdInput');
      if (text && input) {
        input.value = text;
        document.getElementById('cmdForm').dispatchEvent(new Event('submit'));
      } else {
        setReactorState('idle', 'NO SPEECH HEARD');
      }
    } catch (err) {
      console.warn('STT failed:', err);
      setReactorState('idle', 'ONLINE');
    }
  };
  mediaRecorder.start();
  isListening = true;
  setVoiceButton(true);
  setReactorState('listening', 'LISTENING... (click to stop)');
}

// ========================================================
// REACTOR CORE UI & AUDIO VISUALIZER
// ========================================================
function setReactorState(state, text) {
  const core = document.getElementById('reactorCore');
  const txt = document.getElementById('coreStatusText');
  if (core) core.className = 'reactor-core ' + state;
  if (txt) txt.textContent = text;
}

function initVisualizer() {
  const canvas = document.getElementById('audioVisualizer');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  canvas.width = 260;
  canvas.height = 260;

  const centerX = canvas.width / 2;
  const centerY = canvas.height / 2;
  const radius = 95;
  const bars = 48;
  let angleOffset = 0;

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    angleOffset += 0.015;

    for (let i = 0; i < bars; i++) {
      const angle = (i * 2 * Math.PI) / bars + angleOffset;
      let height = 8 + Math.sin(angle * 4 + Date.now() * 0.003) * 6;
      if (isListening) height += Math.random() * 16;

      const x1 = centerX + Math.cos(angle) * radius;
      const y1 = centerY + Math.sin(angle) * radius;
      const x2 = centerX + Math.cos(angle) * (radius + height);
      const y2 = centerY + Math.sin(angle) * (radius + height);

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.strokeStyle = isListening ? 'rgba(255, 183, 0, 0.8)' : 'rgba(0, 240, 255, 0.6)';
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.stroke();
    }
    requestAnimationFrame(draw);
  }
  draw();
}

// ========================================================
// UTILITIES
// ========================================================
function escapeHtml(str) {
  return String(str || '').replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[m]);
}

function getDomainFromUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, '');
  } catch (e) {
    return 'web';
  }
}

function formatMarkdown(text) {
  let escaped = escapeHtml(text);
  // Bold
  escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  // Inline code
  escaped = escaped.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headers
  escaped = escaped.replace(/^### (.*$)/gim, '<h4 style="color:var(--cyan-glow);margin:10px 0 4px 0;">$1</h4>');
  escaped = escaped.replace(/^## (.*$)/gim, '<h3 style="color:var(--cyan-glow);margin:12px 0 6px 0;">$1</h3>');
  escaped = escaped.replace(/^# (.*$)/gim, '<h2 style="color:var(--cyan-glow);margin:14px 0 8px 0;">$1</h2>');
  // Bullet lists
  escaped = escaped.replace(/^\* (.*$)/gim, '<li style="margin-left:16px;">$1</li>');
  escaped = escaped.replace(/^- (.*$)/gim, '<li style="margin-left:16px;">$1</li>');
  // Line breaks
  escaped = escaped.replace(/\n/g, '<br/>');
  return escaped;
}
