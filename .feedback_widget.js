/* feedback_widget.js – 汎用フィードバックウィジェット (vanilla JS, zero deps) */
(function () {
  'use strict';

  var cfg = {};
  var STORAGE_KEY = 'feedback_widget_draft';

  function init(options) {
    cfg = Object.assign({
      appName: 'App',
      mailto: '',
      position: 'top-right',
      buttonLabel: 'フィードバック',
    }, options);

    injectStyles();
    injectHTML();
    bindEvents();
    loadDraft();
  }

  /* ── スタイル ── */
  function injectStyles() {
    var style = document.createElement('style');
    style.textContent = [
      '#fb-btn:hover{opacity:0.8;}',
      '#fb-panel{position:fixed;top:48px;right:16px;z-index:99999;',
      'width:320px;background:#fff;border-radius:8px;',
      'box-shadow:0 4px 20px rgba(0,0,0,.25);font-family:sans-serif;font-size:13px;',
      'display:none;flex-direction:column;overflow:hidden;}',
      '#fb-panel.fb-open{display:flex;}',
      '#fb-header{background:#2c3e50;color:#fff;padding:10px 14px;',
      'display:flex;align-items:center;justify-content:space-between;}',
      '#fb-header span{font-weight:bold;font-size:14px;}',
      '#fb-close{background:none;border:none;color:#fff;font-size:18px;',
      'cursor:pointer;line-height:1;padding:0;}',
      '#fb-body{padding:14px;display:flex;flex-direction:column;gap:10px;}',
      '.fb-label{font-size:12px;color:#555;margin-bottom:3px;}',
      '.fb-radios{display:flex;gap:12px;}',
      '.fb-radios label{display:flex;align-items:center;gap:4px;cursor:pointer;}',
      '#fb-text{width:100%;box-sizing:border-box;height:110px;',
      'padding:7px;border:1px solid #ccc;border-radius:4px;',
      'font-size:13px;resize:vertical;font-family:sans-serif;}',
      '#fb-text:focus{outline:none;border-color:#2980b9;}',
      '#fb-email{width:100%;box-sizing:border-box;padding:6px 8px;',
      'border:1px solid #ccc;border-radius:4px;font-size:13px;}',
      '#fb-email:focus{outline:none;border-color:#2980b9;}',
      '#fb-footer{padding:10px 14px;display:flex;justify-content:flex-end;gap:8px;',
      'border-top:1px solid #eee;}',
      '#fb-cancel{padding:6px 14px;border:1px solid #ccc;border-radius:4px;',
      'background:#fff;cursor:pointer;font-size:13px;}',
      '#fb-submit{padding:6px 14px;border:none;border-radius:4px;',
      'background:#2980b9;color:#fff;cursor:pointer;font-size:13px;}',
      '#fb-submit:hover{background:#206ea0;}',
      '#fb-err{color:#c0392b;font-size:12px;display:none;}',
    ].join('');
    document.head.appendChild(style);
  }

  /* ── HTML ── */
  function injectHTML() {
    var html = [
      '<div id="fb-panel">',
      '  <div id="fb-header">',
      '    <span>バグ報告 / ご要望</span>',
      '    <button id="fb-close" title="閉じる">×</button>',
      '  </div>',
      '  <div id="fb-body">',
      '    <div>',
      '      <div class="fb-label">種別</div>',
      '      <div class="fb-radios">',
      '        <label><input type="radio" name="fb-type" value="バグ報告" checked> バグ報告</label>',
      '        <label><input type="radio" name="fb-type" value="機能要望"> 機能要望</label>',
      '        <label><input type="radio" name="fb-type" value="その他"> その他</label>',
      '      </div>',
      '    </div>',
      '    <div>',
      '      <div class="fb-label">内容 <span style="color:#c0392b">*</span></div>',
      '      <textarea id="fb-text" placeholder="内容を入力してください" maxlength="2000"></textarea>',
      '      <div id="fb-err">内容を入力してください</div>',
      '    </div>',
      '    <div>',
      '      <div class="fb-label">返信先メール（任意）</div>',
      '      <input type="email" id="fb-email" placeholder="your@email.com">',
      '    </div>',
      '  </div>',
      '  <div id="fb-footer">',
      '    <button id="fb-cancel">キャンセル</button>',
      '    <button id="fb-submit">送信</button>',
      '  </div>',
      '</div>',
    ].join('\n');

    var wrap = document.createElement('div');
    wrap.innerHTML = html;
    document.body.appendChild(wrap);
  }

  /* ── イベント ── */
  function bindEvents() {
    document.getElementById('fb-btn').addEventListener('click', openPanel);
    document.getElementById('fb-close').addEventListener('click', closePanel);
    document.getElementById('fb-cancel').addEventListener('click', closePanel);
    document.getElementById('fb-submit').addEventListener('click', submit);
    document.getElementById('fb-text').addEventListener('input', function () {
      saveDraft();
      document.getElementById('fb-err').style.display = 'none';
    });
    document.getElementById('fb-email').addEventListener('input', saveDraft);
    document.querySelectorAll('input[name="fb-type"]').forEach(function (r) {
      r.addEventListener('change', saveDraft);
    });
  }

  function openPanel() {
    document.getElementById('fb-panel').classList.add('fb-open');
    document.getElementById('fb-text').focus();
  }

  function closePanel() {
    document.getElementById('fb-panel').classList.remove('fb-open');
    document.getElementById('fb-err').style.display = 'none';
  }

  /* ── 送信 ── */
  function submit() {
    var text = document.getElementById('fb-text').value.trim();
    if (!text) {
      document.getElementById('fb-err').style.display = 'block';
      document.getElementById('fb-text').focus();
      return;
    }

    var type = (document.querySelector('input[name="fb-type"]:checked') || {}).value || '不明';
    var email = document.getElementById('fb-email').value.trim();
    var now = new Date().toLocaleString('ja-JP');
    var ua = navigator.userAgent;
    var url = location.href;
    var screen_size = window.screen.width + 'x' + window.screen.height;

    var body = [
      '【フィードバック】' + cfg.appName,
      '─────────────────────────────',
      '種別    : ' + type,
      '日時    : ' + now,
      'アプリ  : ' + cfg.appName,
      'URL     : ' + url,
      'ブラウザ: ' + ua,
      '画面    : ' + screen_size,
      '',
      '内容:',
      text,
      '',
      email ? ('返信先 : ' + email) : '',
      '─────────────────────────────',
    ].filter(function (l, i) {
      // 末尾の空返信先行を除く（空文字の行はfilterで消す、ただし内容の空行は残す）
      return !(i > 10 && l === '');
    }).join('\n');

    var subject = '【' + cfg.appName + '】' + type + ' – ' + now;
    var mailto = 'mailto:' + cfg.mailto
      + '?subject=' + encodeURIComponent(subject)
      + '&body=' + encodeURIComponent(body);

    var iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    document.body.appendChild(iframe);
    iframe.src = mailto;
    setTimeout(function () { document.body.removeChild(iframe); }, 2000);

    clearDraft();
    showSuccess();
  }

  /* ── 下書き保存 ── */
  function saveDraft() {
    try {
      var type = (document.querySelector('input[name="fb-type"]:checked') || {}).value || '';
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        type: type,
        text: document.getElementById('fb-text').value,
        email: document.getElementById('fb-email').value,
      }));
    } catch (e) {}
  }

  function loadDraft() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var d = JSON.parse(raw);
      if (d.text) document.getElementById('fb-text').value = d.text;
      if (d.email) document.getElementById('fb-email').value = d.email;
      if (d.type) {
        var r = document.querySelector('input[name="fb-type"][value="' + d.type + '"]');
        if (r) r.checked = true;
      }
    } catch (e) {}
  }

  function clearDraft() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    document.getElementById('fb-text').value = '';
    document.getElementById('fb-email').value = '';
    document.querySelectorAll('input[name="fb-type"]')[0].checked = true;
  }

  /* ── 成功表示 ── */
  function showSuccess() {
    var body   = document.getElementById('fb-body');
    var footer = document.getElementById('fb-footer');
    body.innerHTML =
      '<div style="text-align:center;padding:28px 12px;">' +
      '<div style="font-size:40px;color:#27ae60;margin-bottom:10px">&#10003;</div>' +
      '<div style="font-size:15px;font-weight:bold;color:#27ae60;margin-bottom:8px">送信しました！</div>' +
      '<div style="font-size:12px;color:#666;line-height:1.6">メーラーが開きます。<br>内容を確認して送信してください。</div>' +
      '</div>';
    footer.style.justifyContent = 'center';
    footer.innerHTML = '<button id="fb-close2" style="padding:6px 24px;border:none;border-radius:4px;background:#2c3e50;color:#fff;cursor:pointer;font-size:13px">閉じる</button>';
    document.getElementById('fb-close2').addEventListener('click', function () {
      closePanel();
      setTimeout(resetPanel, 350);
    });
  }

  function resetPanel() {
    var body = document.getElementById('fb-body');
    body.innerHTML =
      '<div>' +
      '  <div class="fb-label">種別</div>' +
      '  <div class="fb-radios">' +
      '    <label><input type="radio" name="fb-type" value="バグ報告" checked> バグ報告</label>' +
      '    <label><input type="radio" name="fb-type" value="機能要望"> 機能要望</label>' +
      '    <label><input type="radio" name="fb-type" value="その他"> その他</label>' +
      '  </div>' +
      '</div>' +
      '<div>' +
      '  <div class="fb-label">内容 <span style="color:#c0392b">*</span></div>' +
      '  <textarea id="fb-text" placeholder="内容を入力してください" maxlength="2000"></textarea>' +
      '  <div id="fb-err" style="color:#c0392b;font-size:12px;display:none">内容を入力してください</div>' +
      '</div>' +
      '<div>' +
      '  <div class="fb-label">返信先メール（任意）</div>' +
      '  <input type="email" id="fb-email" placeholder="your@email.com">' +
      '</div>';
    var footer = document.getElementById('fb-footer');
    footer.style.justifyContent = '';
    footer.innerHTML =
      '<button id="fb-cancel">キャンセル</button>' +
      '<button id="fb-submit">送信</button>';
    document.getElementById('fb-cancel').addEventListener('click', closePanel);
    document.getElementById('fb-submit').addEventListener('click', submit);
    document.getElementById('fb-text').addEventListener('input', function () {
      saveDraft();
      document.getElementById('fb-err').style.display = 'none';
    });
    document.getElementById('fb-email').addEventListener('input', saveDraft);
    document.querySelectorAll('input[name="fb-type"]').forEach(function (r) {
      r.addEventListener('change', saveDraft);
    });
  }

  /* ── ユーティリティ ── */
  function esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  window.FeedbackWidget = { init: init };
})();
