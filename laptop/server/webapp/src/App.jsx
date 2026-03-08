import { useState, useEffect, useRef } from 'react';

const WS_PORT = 8765;
const MAX_MSGS = 150;
let _id = (() => {
  try {
    const msgs = JSON.parse(localStorage.getItem('pc-msgs') || '[]');
    return msgs.reduce((max, m) => Math.max(max, m.id || 0), 0);
  } catch { return 0; }
})();

export default function App() {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState(() => {
    try { const s = localStorage.getItem('pc-msgs'); return s ? JSON.parse(s) : []; } catch { return []; }
  });
  const [sessions, setSessions] = useState([]);
  const [target, setTarget] = useState('');
  const [input, setInput] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [theme, setTheme] = useState(() => localStorage.getItem('pc-theme') || 'dark');
  const [queue, setQueue] = useState([]);
  const [recording, setRecording] = useState(false);
  const [pastedImage, setPastedImage] = useState(null);
  const [activeTab, setActiveTab] = useState('active');
  const [completedActions, setCompletedActions] = useState(() => {
    try {
      const stored = localStorage.getItem('pc-completed');
      return stored ? new Set(JSON.parse(stored)) : new Set();
    } catch { return new Set(); }
  });
  const [messageTimestamps, setMessageTimestamps] = useState(new Map());

  const wsRef = useRef(null);
  const feedRef = useRef(null);
  const reconnRef = useRef({ timer: null, delay: 1000 });
  const mediaRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pc-theme', theme);
  }, [theme]);

  useEffect(() => {
    try { localStorage.setItem('pc-msgs', JSON.stringify(messages)); } catch {}
  }, [messages]);

  useEffect(() => {
    try { localStorage.setItem('pc-completed', JSON.stringify([...completedActions])); } catch {}
  }, [completedActions]);

  useEffect(() => {
    setCompletedActions(prev => {
      const validIds = new Set();
      messages.forEach(msg => {
        if (prev.has(msg.id)) {
          validIds.add(msg.id);
        }
      });
      return validIds;
    });
  }, [messages]);

  function toast(text) {
    const id = ++_id;
    setToasts(t => [...t, { id, text }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 2500);
  }

  function wsSend(obj) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj));
      return true;
    }
    toast('Not connected');
    return false;
  }

  function markCompleted(msgId) {
    setCompletedActions(prev => new Set([...prev, msgId]));
  }

  function getActiveMessages(messages, target, completedActions, messageTimestamps) {
    const sessionFiltered = target
      ? messages.filter(m => !m.sid || m.sid === target || m.sid.startsWith(target) || target.startsWith(m.sid))
      : messages;

    let mostRecentClaudeId = null;
    for (let i = sessionFiltered.length - 1; i >= 0; i--) {
      if (sessionFiltered[i].kind === 'claude') {
        mostRecentClaudeId = sessionFiltered[i].id;
        break;
      }
    }

    const userMsgs = sessionFiltered.filter(m => m.kind === 'user');
    const recentUserIds = new Set(userMsgs.slice(-3).map(m => m.id));

    return sessionFiltered.filter(msg => {
      switch (msg.kind) {
        case 'divider':
          return true;
        case 'permission':
          return !completedActions.has(msg.id);
        case 'question':
          return !completedActions.has(msg.id);
        case 'tool':
          return msg.done === false;
        case 'claude':
          return msg.id === mostRecentClaudeId;
        case 'user':
          return recentUserIds.has(msg.id);
        default:
          return false;
      }
    });
  }

  function getHistoryMessages(messages, target) {
    return target
      ? messages.filter(m => !m.sid || m.sid === target || m.sid.startsWith(target) || target.startsWith(m.sid))
      : messages;
  }

  function addMsg(msg) {
    setMessages(prev => {
      const next = [...prev, { ...msg, id: ++_id }];
      setMessageTimestamps(m => new Map(m).set(_id, Date.now()));
      return next.length > MAX_MSGS ? next.slice(-MAX_MSGS) : next;
    });
  }

  function addEventMessages(evt) {
    const etype = evt.event_type || '';
    if (!etype || etype === 'tool_start' || etype === 'tool_end') return;

    const sid = evt.session_id || '';
    const project = evt.project || (evt.cwd ? evt.cwd.split(/[/\\]/).pop() : '');
    const ts = formatTime(evt.timestamp);

    addMsg({ kind: 'divider', label: labelFor(etype), color: colorFor(etype), time: ts, sid });

    if (etype === 'permission_request' && evt.tool_name === 'AskUserQuestion') {
      const questions = parseAskUserQuestions(evt.tool_input);
      addMsg({ kind: 'question', questions, sid, project });
    } else if (etype === 'permission_request') {
      addMsg({
        kind: 'permission',
        toolName: evt.tool_name || 'Unknown tool',
        toolInput: formatToolInput(evt.tool_input),
        sid, project,
      });
    } else {
      const text = evt.last_message || evt.notification_message || '';
      addMsg({ kind: 'claude', text: text || 'No message content', project, sid });
    }
  }

  function handleWsMessage(data) {
    const type = data.type || data.event_type || '';
    switch (type) {
      case 'state_sync':
        if (data.sessions) setSessions(data.sessions.sessions || []);
        if (data.recent_events) {
          setMessages(prev => {
            if (prev.length > 0) return prev;
            const msgs = [];
            data.recent_events.forEach(evt => {
              const etype = evt.event_type || '';
              if (!etype || etype === 'tool_start' || etype === 'tool_end') return;
              const sid = evt.session_id || '';
              const project = evt.project || (evt.cwd ? evt.cwd.split(/[/\\]/).pop() : '');
              const ts = formatTime(evt.timestamp);
              msgs.push({ kind: 'divider', label: labelFor(etype), color: colorFor(etype), time: ts, sid, id: ++_id });
              if (etype === 'permission_request' && evt.tool_name === 'AskUserQuestion') {
                const questions = parseAskUserQuestions(evt.tool_input);
                msgs.push({ kind: 'question', questions, sid, project, id: ++_id });
              } else if (etype === 'permission_request') {
                msgs.push({ kind: 'permission', toolName: evt.tool_name || 'Unknown tool', toolInput: formatToolInput(evt.tool_input), sid, project, id: ++_id });
              } else {
                const text = evt.last_message || evt.notification_message || '';
                msgs.push({ kind: 'claude', text: text || 'No message content', project, sid, id: ++_id });
              }
            });
            return msgs;
          });
        }
        break;
      case 'status_response':
        if (data.sessions) setSessions(data.sessions.sessions || []);
        break;
      case 'command_ack': {
        const ackText = data.text || '';
        if (ackText !== 'approve' && ackText !== 'deny') {
          toast('Sent');
        }
        break;
      }
      case 'history_response':
        if (data.events) data.events.forEach(addEventMessages);
        break;
      case 'task_completed':
      case 'permission_request':
      case 'input_needed':
        addEventMessages(data);
        if (data.active_sessions) setSessions(data.active_sessions.sessions || []);
        notifyBrowser(type, data);
        break;
      case 'tool_start':
        addMsg({ kind: 'tool', toolName: data.tool_name || 'tool', summary: summarize(data.tool_input_summary), done: false, sid: data.session_id || '' });
        break;
      case 'tool_end':
        setMessages(prev => {
          const msgs = [...prev];
          for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].kind === 'tool' && msgs[i].toolName === (data.tool_name || 'tool') && !msgs[i].done) {
              msgs[i] = { ...msgs[i], done: true, summary: summarize(data.tool_result_summary) || msgs[i].summary };
              break;
            }
          }
          return msgs;
        });
        break;
      case 'error':
        toast(data.message || 'Error');
        break;
    }
  }

  function connectWs() {
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) return;
    const url = `ws://${location.hostname || 'localhost'}:${WS_PORT}`;
    let socket;
    try { socket = new WebSocket(url); } catch { scheduleReconnect(); return; }
    wsRef.current = socket;

    socket.onopen = () => {
      setConnected(true);
      reconnRef.current.delay = 1000;
      wsSend({ type: 'status_query' });
    };
    socket.onclose = () => { setConnected(false); scheduleReconnect(); };
    socket.onerror = () => {};
    socket.onmessage = (evt) => { try { handleWsMessage(JSON.parse(evt.data)); } catch {} };
  }

  function scheduleReconnect() {
    const r = reconnRef.current;
    if (r.timer) return;
    r.timer = setTimeout(() => { r.timer = null; connectWs(); }, r.delay);
    r.delay = Math.min(r.delay * 1.5, 15000);
  }

  function handleSend() {
    const text = input.trim();
    const hasImage = !!pastedImage;
    if (queue.length === 0 && !text && !hasImage) return;

    // Send image first if present
    if (hasImage) {
      const b64 = pastedImage.split(',')[1];
      if (wsSend({ type: 'image', image: b64, text: text || '', session_id: target })) {
        addMsg({ kind: 'user', text: text || 'Sent image', image: pastedImage, sid: target });
        setPastedImage(null);
        setInput('');
        toast('Sent');
      }
      return;
    }

    const all = [...queue];
    if (text) all.push(text);

    let sent = 0;
    for (const msg of all) {
      if (wsSend({ type: 'command', text: msg, session_id: target })) {
        addMsg({ kind: 'user', text: msg, sid: target });
        sent++;
      } else break;
    }

    if (sent > 0) {
      setQueue([]);
      setInput('');
      toast(sent > 1 ? `Sent ${sent} messages` : 'Sent');
    }
  }

  function handlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        const reader = new FileReader();
        reader.onloadend = () => setPastedImage(reader.result);
        reader.readAsDataURL(file);
        return;
      }
    }
  }

  function handleFileSelect(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => setPastedImage(reader.result);
    reader.readAsDataURL(file);
    e.target.value = '';
  }

  function toggleMic() {
    if (recording) {
      mediaRef.current?.stop();
      return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
      const rec = new MediaRecorder(stream);
      const chunks = [];
      rec.ondataavailable = e => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        setRecording(false);
        mediaRef.current = null;
        const blob = new Blob(chunks);
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(',')[1];
          wsSend({ type: 'voice', audio: b64, session_id: target });
          toast('Transcribing...');
        };
        reader.readAsDataURL(blob);
      };
      mediaRef.current = rec;
      rec.start();
      setRecording(true);
    }).catch(() => toast('Mic access denied'));
  }

  function addToQueue() {
    const text = input.trim();
    if (!text) return;
    setQueue(q => [...q, text]);
    setInput('');
  }

  function removeFromQueue(idx) {
    setQueue(q => q.filter((_, i) => i !== idx));
  }

  useEffect(() => {
    connectWs();
    return () => wsRef.current?.close();
  }, []);

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, target]);

  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="header-title">Ping Claude</span>
          <span className={`dot${connected ? ' on' : ''}`} />
        </div>
        <div className="header-right">
          <select className="session-select" value={target} onChange={e => setTarget(e.target.value)}>
            <option value="">All sessions</option>
            {sessions.map(s => {
              const short = s.session_id.slice(0, 12);
              const proj = s.cwd ? s.cwd.split(/[/\\]/).pop() : '';
              const st = s.status === 'working' ? 'working' : s.status === 'waiting_for_input' ? 'waiting' : 'idle';
              return <option key={s.session_id} value={s.session_id}>{proj || short} ({st})</option>;
            })}
          </select>
          <button className="theme-btn" onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')} aria-label="Toggle theme">
            <svg className="theme-icon-moon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6 0a8 8 0 1 0 8 6 6 6 0 0 1-8-6Z"/></svg>
            <svg className="theme-icon-sun" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="3.5"/><path d="M8 0v2m0 12v2M0 8h2m12 0h2m-2.9-5.1L12.5 4.3M3.5 11.7l-1.4 1.4m0-10.2L3.5 4.3m9 7.4 1.4 1.4" strokeWidth="1.5" stroke="currentColor"/></svg>
          </button>
          <button className="icon-btn" onClick={() => setMenuOpen(true)}><svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><rect y="2" width="16" height="1.5" rx="0.75"/><rect y="7.25" width="16" height="1.5" rx="0.75"/><rect y="12.5" width="16" height="1.5" rx="0.75"/></svg></button>
        </div>
      </header>

      {sessions.length > 0 && (
        <div className="session-bar">
          {sessions.map(s => {
            const proj = s.cwd ? s.cwd.split(/[/\\]/).pop() : s.session_id.slice(0, 12);
            const st = s.status === 'working' ? 'working' : s.status === 'waiting_for_input' ? 'waiting' : 'idle';
            const isTarget = target === s.session_id;
            return (
              <span key={s.session_id} className={`session-tag ${st}${isTarget ? ' active' : ''}`} onClick={() => setTarget(isTarget ? '' : s.session_id)}>
                {st === 'working' && <span className="spinner sm" />}
                {proj}
                <button className="session-end" onClick={e => { e.stopPropagation(); if (confirm(`End session ${proj}?`)) { wsSend({ type: 'end_session', session_id: s.session_id }); toast('Ending...'); } }} title="End session">&times;</button>
              </span>
            );
          })}
        </div>
      )}

      <div className="tab-bar">
        <button className={`tab-btn${activeTab === 'active' ? ' active' : ''}`} onClick={() => setActiveTab('active')}>Active</button>
        <button className={`tab-btn${activeTab === 'history' ? ' active' : ''}`} onClick={() => setActiveTab('history')}>History</button>
      </div>

      <div className="feed" ref={feedRef}>
        {(() => {
          const filtered = activeTab === 'active'
            ? getActiveMessages(messages, target, completedActions, messageTimestamps)
            : getHistoryMessages(messages, target);

          if (filtered.length === 0) {
            const emptyMsg = activeTab === 'active'
              ? 'All caught up! No pending actions.'
              : (target ? 'No messages for this session' : 'Waiting for Claude Code...');
            return (
              <div className="empty">
                <div className="empty-icon"><span className="spinner lg" /></div>
                <div>{emptyMsg}</div>
              </div>
            );
          }

          return filtered.map(msg => (
            <MsgItem
              key={msg.id}
              msg={msg}
              wsSend={wsSend}
              setTarget={setTarget}
              onComplete={markCompleted}
              isCompleted={completedActions.has(msg.id)}
            />
          ));
        })()}
      </div>

      {queue.length > 0 && (
        <div className="queue-bar">
          <span className="queue-label">Queued ({queue.length})</span>
          <div className="queue-items">
            {queue.map((text, i) => (
              <div key={i} className="queue-item">
                <span className="queue-text">{text}</span>
                <button className="queue-remove" onClick={() => removeFromQueue(i)}>×</button>
              </div>
            ))}
          </div>
        </div>
      )}
      {pastedImage && (
        <div className="image-preview">
          <img src={pastedImage} alt="Pasted" />
          <button className="image-remove" onClick={() => setPastedImage(null)}>×</button>
        </div>
      )}
      <div className="input-bar">
        <button className="queue-btn" onClick={addToQueue} title="Queue (Shift+Enter)"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M8 3v10M3 8h10"/></svg></button>
        <button className="attach-btn" onClick={() => fileRef.current?.click()} title="Attach file"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 8l-5.6 5.6a3.5 3.5 0 0 1-5-5L9 3a2.33 2.33 0 0 1 3.3 3.3L6.7 12a1.17 1.17 0 0 1-1.7-1.7L10.6 5"/></svg></button>
        <input type="file" ref={fileRef} accept="image/*" onChange={handleFileSelect} style={{ display: 'none' }} />
        <input
          className="input-field"
          value={input}
          onChange={e => setInput(e.target.value)}
          onPaste={handlePaste}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
            if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); addToQueue(); }
          }}
          placeholder={queue.length ? 'Add more, or send all...' : 'Message agent...'}
        />
        {(input.trim() || pastedImage || queue.length > 0) ? (
          <button className="send-btn" onClick={handleSend}>{queue.length > 0 ? queue.length + 1 : <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M2 1.5l12 6.5-12 6.5V9l8-1-8-1z"/></svg>}</button>
        ) : (
          <button className={`mic-btn${recording ? ' recording' : ''}`} onClick={toggleMic}>{recording ? <span className="mic-pulse" /> : <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a2.5 2.5 0 0 0-2.5 2.5v4a2.5 2.5 0 0 0 5 0v-4A2.5 2.5 0 0 0 8 1z"/><path d="M3.5 7.5a.5.5 0 0 1 1 0 3.5 3.5 0 0 0 7 0 .5.5 0 0 1 1 0 4.5 4.5 0 0 1-4 4.47V13.5h2a.5.5 0 0 1 0 1h-5a.5.5 0 0 1 0-1h2v-1.53a4.5 4.5 0 0 1-4-4.47z"/></svg>}</button>
        )}
      </div>

      {menuOpen && (
        <div className="menu-overlay" onClick={e => { if (e.target === e.currentTarget) setMenuOpen(false); }}>
          <div className="menu-panel">
            <div className="menu-handle" />
<button className="menu-item" onClick={() => { wsSend({ type: 'status_query' }); setMenuOpen(false); toast('Refreshed'); }}>Refresh Status</button>
            <button className="menu-item" onClick={() => { wsSend({ type: 'clear_dead' }); setMenuOpen(false); toast('Clearing...'); }}>Clear Dead Sessions</button>
            <button className="menu-item danger" onClick={() => { if (confirm('End ALL sessions?')) { wsSend({ type: 'end_all' }); setMenuOpen(false); toast('Ending all...'); } }}>End All Sessions</button>
          </div>
        </div>
      )}

      <div className="toast-container">
        {toasts.map(t => <div key={t.id} className="toast">{t.text}</div>)}
      </div>
    </div>
  );
}

// ── Message Components ──

function MsgItem({ msg, wsSend, setTarget, onComplete, isCompleted }) {
  switch (msg.kind) {
    case 'divider':
      return <div className={`divider ${msg.color}`}>{msg.label}{msg.time ? ` · ${msg.time}` : ''}</div>;

    case 'claude':
      return (
        <div className="msg-claude">
          <FormattedText text={msg.text} />
        </div>
      );

    case 'permission':
      return <PermissionCard msg={msg} wsSend={wsSend} onComplete={onComplete} isCompleted={isCompleted} />;

    case 'question':
      return <QuestionCard msg={msg} wsSend={wsSend} onComplete={onComplete} isCompleted={isCompleted} />;

    case 'tool':
      return (
        <div className={`tool-chip${msg.done ? ' done' : ' running'}`}>
          <span className="tool-icon">{msg.done ? <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 8l4 4 6-7"/></svg> : <span className="spinner" />}</span>
          <span className="tool-name">{msg.toolName}</span>
          <span className="tool-summary">{msg.summary}</span>
          <span className="tool-chevron">›</span>
        </div>
      );

    case 'user':
      return (
        <div className="msg-user">
          <div className="msg-text">
            {msg.image && <img src={msg.image} alt="" className="msg-image" />}
            {msg.text}
          </div>
        </div>
      );

    default:
      return null;
  }
}

function PermissionCard({ msg, wsSend, onComplete, isCompleted }) {
  const [acted, setActed] = useState(isCompleted ? 'completed' : null);
  const [expanded, setExpanded] = useState(false);

  function handleApprove() {
    wsSend({ type: 'approve', session_id: msg.sid });
    setActed('approved');
    onComplete?.(msg.id);
  }

  function handleDeny() {
    wsSend({ type: 'deny', session_id: msg.sid });
    setActed('denied');
    onComplete?.(msg.id);
  }

  return (
    <div className="perm-card">
      <div className="perm-tool">
        {msg.toolName}
        {acted && <span className={`perm-status ${acted}`}>{acted === 'approved' ? 'Approved' : 'Denied'}</span>}
      </div>
      {msg.toolInput && (
        <div className={`perm-input${expanded ? ' expanded' : ''}`} onClick={() => setExpanded(!expanded)}>
          <pre>{msg.toolInput.split('\n').map((line, i) => {
            const cls = line.startsWith('+ ') ? 'diff-add' : line.startsWith('- ') ? 'diff-del' : '';
            return <div key={i} className={cls}>{line}</div>;
          })}</pre>
        </div>
      )}
      {!acted && (
        <div className="perm-actions">
          <button className="btn-approve" onClick={handleApprove}>Approve</button>
          <button className="btn-deny" onClick={handleDeny}>Deny</button>
        </div>
      )}
    </div>
  );
}

function QuestionCard({ msg, wsSend, onComplete, isCompleted }) {
  const [answers, setAnswers] = useState({});
  const [otherText, setOtherText] = useState({});
  const [submitted, setSubmitted] = useState(isCompleted);
  const questions = msg.questions || [];

  function selectOption(qIdx, label, multi) {
    if (submitted) return;
    setAnswers(prev => {
      if (multi) {
        const cur = prev[qIdx] || [];
        if (label === '__other__') return { ...prev, [qIdx]: cur.includes('__other__') ? cur.filter(l => l !== '__other__') : [...cur, '__other__'] };
        return { ...prev, [qIdx]: cur.includes(label) ? cur.filter(l => l !== label) : [...cur, label] };
      }
      return { ...prev, [qIdx]: label };
    });
  }

  function submit() {
    wsSend({ type: 'approve', session_id: msg.sid });
    setSubmitted(true);
    onComplete?.(msg.id);
  }

  const hasAnswer = questions.some((q, i) => {
    const a = answers[i];
    if (a === '__other__') return (otherText[i] || '').trim().length > 0;
    if (Array.isArray(a)) return a.length > 0 && a.every(l => l !== '__other__' || (otherText[i] || '').trim());
    return a !== undefined;
  });

  if (questions.length === 0) return null;

  return (
    <div className="question-card">
      {questions.map((q, qi) => {
        const isOther = q.multiSelect ? (answers[qi] || []).includes('__other__') : answers[qi] === '__other__';
        return (
          <div key={qi} className="question-block">
            <div className="question-text">{q.question}</div>
            <div className="question-options">
              {(q.options || []).map((opt, oi) => {
                const selected = q.multiSelect
                  ? (answers[qi] || []).includes(opt.label)
                  : answers[qi] === opt.label;
                return (
                  <button
                    key={oi}
                    className={`question-opt${selected ? ' selected' : ''}${submitted ? ' locked' : ''}`}
                    onClick={() => selectOption(qi, opt.label, q.multiSelect)}
                    disabled={submitted}
                  >
                    <span className="opt-label">{opt.label}</span>
                    {opt.description && <span className="opt-desc">{opt.description}</span>}
                  </button>
                );
              })}
              <div
                className={`question-opt other-opt${isOther ? ' selected' : ''}${submitted ? ' locked' : ''}`}
                onClick={() => { if (!submitted && !isOther) selectOption(qi, '__other__', q.multiSelect); }}
              >
                <input
                  className="other-inline"
                  placeholder="Other"
                  value={otherText[qi] || ''}
                  onChange={e => {
                    if (submitted) return;
                    setOtherText(prev => ({ ...prev, [qi]: e.target.value }));
                    if (!isOther) selectOption(qi, '__other__', q.multiSelect);
                  }}
                  disabled={submitted}
                />
                <span className="opt-desc">Type your answer</span>
              </div>
            </div>
          </div>
        );
      })}
      {!submitted && (
        <button className="question-submit" onClick={submit} disabled={!hasAnswer}>
          Submit
        </button>
      )}
      {submitted && <div className="question-submitted">Answered</div>}
    </div>
  );
}

function parseAskUserQuestions(toolInput) {
  if (!toolInput) return [];
  let obj = toolInput;
  if (typeof obj === 'string') {
    try { obj = JSON.parse(obj); } catch { return []; }
  }
  if (typeof obj !== 'object') return [];
  return obj.questions || [];
}

// ── Formatted Text (code blocks + inline code) ──

function FormattedText({ text }) {
  if (!text) return null;

  const parts = [];
  const codeBlockRe = /```(\w*)\n?([\s\S]*?)```/g;
  let last = 0;
  let match;

  while ((match = codeBlockRe.exec(text)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', content: text.slice(last, match.index) });
    }
    parts.push({ type: 'code', lang: match[1], content: match[2].replace(/\n$/, '') });
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    parts.push({ type: 'text', content: text.slice(last) });
  }

  if (parts.length === 0) return <div className="msg-text">{text}</div>;

  return (
    <div className="msg-text">
      {parts.map((p, i) =>
        p.type === 'code' ? (
          <div key={i} className="code-block">
            {p.lang && <span className="code-lang">{p.lang}</span>}
            <pre><code>{p.content}</code></pre>
          </div>
        ) : (
          <span key={i}>{renderInline(p.content)}</span>
        )
      )}
    </div>
  );
}

function renderInline(text) {
  const lines = text.split('\n');
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Heading
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      elements.push(<div key={i} className={`md-h md-h${level}`}>{inlineTokens(headingMatch[2])}</div>);
      i++;
      continue;
    }

    // Bullet list (- or * at start)
    const bulletMatch = line.match(/^(\s*)[-*]\s+(.+)$/);
    if (bulletMatch) {
      const items = [];
      while (i < lines.length) {
        const bm = lines[i].match(/^(\s*)[-*]\s+(.+)$/);
        if (!bm) break;
        items.push(<li key={i}>{inlineTokens(bm[2])}</li>);
        i++;
      }
      elements.push(<ul key={`ul-${i}`} className="md-list">{items}</ul>);
      continue;
    }

    // Numbered list
    const numMatch = line.match(/^(\s*)\d+[.)]\s+(.+)$/);
    if (numMatch) {
      const items = [];
      while (i < lines.length) {
        const nm = lines[i].match(/^(\s*)\d+[.)]\s+(.+)$/);
        if (!nm) break;
        items.push(<li key={i}>{inlineTokens(nm[2])}</li>);
        i++;
      }
      elements.push(<ol key={`ol-${i}`} className="md-list">{items}</ol>);
      continue;
    }

    // Blank line = paragraph break
    if (line.trim() === '') {
      elements.push(<div key={i} className="md-break" />);
      i++;
      continue;
    }

    // Regular text line
    elements.push(<span key={i}>{elements.length > 0 && '\n'}{inlineTokens(line)}</span>);
    i++;
  }

  return elements;
}

function inlineTokens(text) {
  const tokens = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g);
  return tokens.map((t, i) => {
    if (t.startsWith('`') && t.endsWith('`')) {
      return <code key={i} className="inline-code">{t.slice(1, -1)}</code>;
    }
    if (t.startsWith('**') && t.endsWith('**')) {
      return <strong key={i}>{t.slice(2, -2)}</strong>;
    }
    if (t.startsWith('*') && t.endsWith('*')) {
      return <em key={i}>{t.slice(1, -1)}</em>;
    }
    return t;
  });
}

// ── Helpers ──

function labelFor(t) {
  return { task_completed: 'Task Completed', permission_request: 'Permission Request', input_needed: 'Input Needed' }[t] || t.replace(/_/g, ' ');
}

function colorFor(t) {
  return { task_completed: 'green', permission_request: 'amber', input_needed: 'blue' }[t] || '';
}

function formatTime(ts) {
  if (!ts) return '';
  try { return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch { return ''; }
}

function summarizeInput(input) {
  if (typeof input === 'string') return input.slice(0, 200);
  try { return JSON.stringify(input).slice(0, 200); } catch { return ''; }
}

function formatToolInput(input) {
  if (!input) return '';
  let obj = input;
  if (typeof obj === 'string') {
    try { obj = JSON.parse(obj); } catch { return input; }
  }
  if (typeof obj !== 'object') return String(obj);
  try {
    // Edit tool — show file path + diff
    if (obj.file_path && (obj.old_string || obj.new_string)) {
      let out = obj.file_path;
      if (obj.old_string) out += '\n\n- ' + obj.old_string.split('\n').join('\n- ');
      if (obj.new_string) out += '\n\n+ ' + obj.new_string.split('\n').join('\n+ ');
      return out;
    }
    // Bash tool — show command
    if (obj.command) return '$ ' + obj.command;
    // Write tool
    if (obj.file_path && obj.content) return obj.file_path + '\n\n' + obj.content;
    // Fallback — pretty print
    return JSON.stringify(obj, null, 2);
  } catch { return String(input); }
}

function summarize(s) {
  if (!s) return '';
  s = String(s);
  return s.length > 80 ? s.slice(0, 80) + '…' : s;
}

function notifyBrowser(type, data) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  if (document.visibilityState === 'visible') return;
  const p = data.project || '';
  let title, body;
  if (type === 'task_completed') { title = 'Task Completed' + (p ? ' — ' + p : ''); body = (data.last_message || '').slice(0, 120); }
  else if (type === 'permission_request') { title = 'Permission Request' + (p ? ' — ' + p : ''); body = (data.tool_name || 'Tool') + ' needs approval'; }
  else if (type === 'input_needed') { title = 'Input Needed' + (p ? ' — ' + p : ''); body = (data.last_message || '').slice(0, 120); }
  else return;
  try { new Notification(title, { body, icon: '/icon-192.png' }); } catch {}
}
