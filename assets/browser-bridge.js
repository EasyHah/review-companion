(function () {
  'use strict';
  var supplied = new URLSearchParams(location.hash.slice(1)).get('token');
  var token = supplied || sessionStorage.getItem('review-token');
  if (supplied) { sessionStorage.setItem('review-token', supplied); history.replaceState(null, '', '/'); }
  var session = null;
  async function request(url, body) {
    var response = await fetch(url, { method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(25000) });
    var data = await response.json();
    if (!response.ok) throw new Error(data.error && data.error.message || '连接失效，请在当前宿主会话重新打开总览');
    return data;
  }
  async function prepareDraft(text) {
    var owner = session.session;
    var draft = await request('/api/draft', { session: owner, text: text });
    for (var i = 0; i < 24; i++) {
      await new Promise(function (resolve) { setTimeout(resolve, 400); });
      if (session.session !== owner) throw new Error('会话已切换，请重新选择文件');
      var result = await request('/api/draft-status', { session: owner, id: draft.id });
      if (result.status === 'filled') return { filled: true };
      if (result.status !== 'pending') {
        var reasons = { occupied: '输入框已有文字，已保留原文', busy: '宿主正在回答，请稍后重试', unsupported: '当前模式不支持填入输入框', expired: '宿主未确认接收问题', failed: '输入框填入失败' };
        throw new Error(reasons[result.status] || '问题未填入输入框');
      }
    }
    throw new Error('宿主未确认接收问题');
  }
  async function refreshSession() {
    var next = await request('/api/session');
    var changed = session && session.session !== next.session;
    session = next;
    var names = { pi:'Pi', pi_web:'Pi Web / RPC', claude:'Claude', codex:'Codex' };
    var label = document.getElementById('host-session');
    if (label) label.textContent = (names[next.host] || '宿主') + ' 会话 ' + String(next.label).slice(0, 12) + ' · ' + next.repository + (next.input_mode === 'draft' ? ' · 准备问题后自行发送' : ' · 复制问题后自行发送');
    if (next.input_mode === 'draft') window.reviewCompanionDraft = prepareDraft;
    else delete window.reviewCompanionDraft;
    if (changed && window.ReviewCompanion && window.ReviewCompanion.resetSession) window.ReviewCompanion.resetSession();
    return session;
  }
  var ready = refreshSession(); ready.catch(function () {});
  window.reviewCompanionHost = { callTool: async function (name, args) {
    await ready;
    var owner = session.session;
    var result = await request('/api/tool', { session: owner, name: name, arguments: args });
    if (session.session !== owner) throw new Error('会话已变化，请重新选择操作');
    return result;
  } };
  setInterval(function () { refreshSession().catch(function () {
    var label = document.getElementById('host-session');
    if (label) label.textContent = '连接已断开，请在当前宿主会话重新打开总览';
  }); }, 2000);
})();
