#!/usr/bin/env python3
"""
Bruker TOF スペクトルをHTMLで開き、ブラウザ上でピークを手動選択できるツール。

使い方:
  python3 peak_picker_html.py <input_path> [--html-out output.html]
"""
import argparse
import itertools
import json
import shutil
import sys
import threading
import time
import urllib.request
from pathlib import Path

__version__ = "2026.03.08"
_UPDATE_URL = "https://raw.githubusercontent.com/nasu802/bruker_tof_reader/main/peak_picker_html.py"


def _check_update() -> str:
    """GitHubの最新バージョンを確認して通知する（オフライン時・git管理下は無視）。
    アップデートがある場合はHTMLバナー用のHTML文字列を返す。なければ空文字。"""
    if (Path(__file__).parent / ".git").exists():
        return ""
    try:
        req = urllib.request.Request(_UPDATE_URL, headers={"User-Agent": "bruker_tof_reader"})
        with urllib.request.urlopen(req, timeout=3) as r:
            for line in r.read().decode("utf-8").splitlines():
                if line.startswith("__version__"):
                    latest = line.split('"')[1]
                    if latest != __version__:
                        print(f"[INFO] アップデートがあります: {__version__} → {latest}")
                        return (
                            f'<style>'
                            f'@keyframes ub-in{{from{{transform:translateY(-120%);opacity:0}}to{{transform:translateY(0);opacity:1}}}}'
                            f'@keyframes ub-out{{from{{transform:translateY(0);opacity:1}}to{{transform:translateY(-120%);opacity:0}}}}'
                            f'#update-banner{{position:fixed;top:16px;left:16px;'
                            f'background:#27ae60;color:#fff;border-radius:14px;'
                            f'padding:11px 16px;font-size:13px;display:flex;align-items:center;gap:12px;'
                            f'box-shadow:0 4px 20px rgba(0,0,0,0.35);z-index:99999;white-space:nowrap;'
                            f'animation:ub-in 0.4s cubic-bezier(0.34,1.56,0.64,1) forwards;}}'
                            f'#update-banner.hide{{animation:ub-out 0.3s ease-in forwards;}}'
                            f'</style>'
                            f'<div id="update-banner">'
                            f'<span>アップデートがあります&nbsp;<span style="opacity:0.7;font-size:11px">{__version__} → {latest}</span></span>'
                            f'<button onclick="var b=document.getElementById(\'update-banner\');b.classList.add(\'hide\');setTimeout(function(){{b.remove()}},300);" '
                            f'style="background:rgba(255,255,255,0.15);border:none;color:#fff;font-size:12px;cursor:pointer;'
                            f'padding:3px 9px;border-radius:8px;margin-left:4px;">✕</button>'
                            f'</div>'
                        )
                    break
    except Exception:
        pass
    return ""

import numpy as np

# ── Plotly ローカルキャッシュ ────────────────────────────────────
_PLOTLY_CDN  = "https://cdn.plot.ly/plotly-2.35.2.min.js"
_PLOTLY_FILE = Path(__file__).parent / "plotly-2.35.2.min.js"


def _ensure_plotly() -> None:
    """Plotly JS がローカルになければ CDN からダウンロードする（初回のみ）。"""
    if _PLOTLY_FILE.exists():
        return
    print("Plotly JS をダウンロード中 (初回のみ)...", end="", flush=True)
    urllib.request.urlretrieve(_PLOTLY_CDN, _PLOTLY_FILE)
    print(" 完了")



import importlib.util as _ilu
import sys as _sys
def _load(name, fname):
    spec = _ilu.spec_from_file_location(name, Path(__file__).parent / fname)
    mod = _ilu.module_from_spec(spec)
    _sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
_loader = _load("bruker_tof_loader", ".bruker_tof_loader.py")
_utils  = _load("spectrum_utils",    ".spectrum_utils.py")
load_bruker_tof           = _loader.load_bruker_tof
find_measurement_dir      = _utils.find_measurement_dir
find_all_measurement_dirs = _utils.find_all_measurement_dirs
pick_peaks_numpy          = _utils.pick_peaks_numpy
pick_peaks_snr            = _utils.pick_peaks_snr


def lttb_downsample(x: np.ndarray, y: np.ndarray, threshold: int):
    """Largest-Triangle-Three-Buckets: 見た目を保ちながら点数を削減。"""
    n = len(x)
    if n <= threshold:
        return x, y
    sampled = np.empty(threshold, dtype=np.intp)
    sampled[0] = 0
    sampled[-1] = n - 1
    bucket_size = (n - 2) / (threshold - 2)
    a = 0
    for i in range(threshold - 2):
        avg_s = int((i + 1) * bucket_size) + 1
        avg_e = min(int((i + 2) * bucket_size) + 1, n)
        avg_x = float(x[avg_s:avg_e].mean())
        avg_y = float(y[avg_s:avg_e].mean())
        rs = int(i * bucket_size) + 1
        re = min(int((i + 1) * bucket_size) + 1, n)
        ax, ay = float(x[a]), float(y[a])
        areas = np.abs((ax - avg_x) * (y[rs:re] - ay) - (ax - x[rs:re]) * (avg_y - ay))
        a = rs + int(np.argmax(areas))
        sampled[i + 1] = a
    return x[sampled], y[sampled]


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Peak Picker – {title}</title>
<script>{plotly_js}</script>
<style>
  body { margin: 0; font-family: sans-serif; background: #f5f5f5; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
  #header { background: #2c3e50; color: #fff; padding: 10px 18px; display: flex; align-items: center; gap: 16px; }
  #header h1 { margin: 0; font-size: 16px; flex: 1; }
  #controls { background: #fff; border-bottom: 1px solid #ddd; padding: 8px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; justify-content: center; }
  .ctrl-group { display: flex; align-items: center; gap: 6px; font-size: 13px; }
  label { white-space: nowrap; }
  input[type=number] { width: 70px; }
  button { padding: 5px 12px; cursor: pointer; border-radius: 4px; border: 1px solid #aaa; background: #fff; font-size: 13px; }
  button.primary { background: #2980b9; color: #fff; border-color: #2980b9; }
  button.active  { background: #27ae60; color: #fff; border-color: #27ae60; }
  button.danger  { background: #c0392b; color: #fff; border-color: #c0392b; }
  #body-wrap { display: flex; flex: 1; min-height: 0; }
  #sidebar {
    width: 160px; min-width: 120px; background: #fff; border-right: 1px solid #ddd;
    display: flex; flex-direction: column; gap: 0; overflow: hidden;
  }
  #sample-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; }
  #sidebar-label { font-size: 11px; color: #999; padding: 8px 10px 4px; font-weight: bold; letter-spacing: 0.05em; }
  .spec-item { display: flex; align-items: stretch; border-bottom: 1px solid #eee; }
  .spec-btn {
    flex: 1; text-align: left; padding: 8px 6px 8px 10px; border: none;
    background: #fff; cursor: pointer; font-size: 12px; color: #333;
    border-radius: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
  }
  .spec-btn:hover { background: #eaf4fb; }
  .spec-btn.active { background: #2980b9; color: #fff; font-weight: bold; }
  .spec-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; flex-shrink: 0; vertical-align: middle; }
  .spec-color-input { width: 14px; height: 14px; border: none; padding: 0; cursor: pointer; border-radius: 50%; flex-shrink: 0; margin-right: 5px; vertical-align: middle; -webkit-appearance: none; appearance: none; background: none; }
  .spec-color-input::-webkit-color-swatch-wrapper { padding: 0; border-radius: 50%; }
  .spec-color-input::-webkit-color-swatch { border: none; border-radius: 50%; }
  .overlay-btn { width: 26px; border: none; border-left: 1px solid #eee; background: #fff; cursor: pointer; font-size: 14px; color: #ccc; padding: 0; flex-shrink: 0; }
  .overlay-btn:hover { background: #f0f0f0; }
  .overlay-btn.on { color: #2980b9; background: #eaf4fb; }
  .spec-scale { display: none; align-items: center; gap: 4px; padding: 3px 8px 5px; background: #f5f8fc; border-bottom: 1px solid #eee; font-size: 11px; color: #666; }
  .spec-scale.visible { display: flex; }
  .spec-scale input[type=range] { flex: 1; min-width: 0; height: 4px; accent-color: #2980b9; }
  .spec-scale .sv { min-width: 28px; text-align: right; font-variant-numeric: tabular-nums; }
  #memo-wrap { border-top: 1px solid #ddd; display: flex; flex-direction: column; flex-shrink: 0; }
  #memo-label { font-size: 11px; color: #999; padding: 5px 8px 5px 10px; font-weight: bold; letter-spacing: 0.05em; cursor: pointer; user-select: none; display: flex; justify-content: space-between; align-items: center; }
  #memo-label:hover { background: #f5f5f5; }
  #memo-wrap.collapsed #memo { display: none; }
  #memo { width: 100%; box-sizing: border-box; border: none; border-top: 1px solid #eee; resize: none; font-size: 12px; font-family: sans-serif; padding: 6px 8px; color: #333; background: #fafafa; outline: none; min-height: 80px; }
  #memo:focus { background: #fff; }
  #img-wrap { border-top: 1px solid #ddd; display: flex; flex-direction: column; flex-shrink: 0; }
  #img-label { font-size: 11px; color: #999; padding: 5px 8px 5px 10px; font-weight: bold; letter-spacing: 0.05em; cursor: pointer; user-select: none; display: flex; justify-content: space-between; align-items: center; }
  #img-label:hover { background: #f5f5f5; }
  #img-wrap.collapsed #img-body { display: none; }
  #img-body { padding: 6px 8px; display: flex; flex-direction: column; gap: 4px; }
  #img-drop { border: 1px dashed #ccc; border-radius: 4px; min-height: 56px; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #bbb; text-align: center; line-height: 1.6; padding: 4px; }
  #img-drop.over { border-color: #2980b9; background: #eaf4fb; color: #2980b9; }
  #compound-img { max-width: 100%; border-radius: 4px; display: block; }
  #img-btns { display: none; flex-direction: row; gap: 4px; }
  #img-btns button { font-size: 11px; padding: 2px 6px; cursor: pointer; border: 1px solid #ccc; border-radius: 4px; background: #fff; flex: 1; }
  #chart-wrap { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  #chart { width: 100%; flex: 1; min-height: 0; cursor: crosshair; }
  #chart * { cursor: crosshair !important; }
  #status { font-size: 12px; color: #555; padding: 4px 16px; background: #fafafa; border-top: 1px solid #eee; }
  #status:empty { display: none; }
  #tips-bar { font-size: 11px; color: #888; padding: 3px 16px; background: #f0f4f8; border-top: 1px solid #e2e8f0; display: flex; align-items: center; gap: 6px; min-height: 22px; }
  #tips-bar .tips-icon { color: #aab; flex-shrink: 0; }
  #tips-text { transition: opacity 0.8s; }
  #loading {
    position: fixed; inset: 0; background: #fff;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    z-index: 9999; font-family: sans-serif; gap: 20px;
  }
  #loading p { font-size: 18px; color: #2c3e50; margin: 0; }
  .spinner {
    width: 48px; height: 48px; border: 5px solid #ddd;
    border-top-color: #2980b9; border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
{update_banner}
<div id="loading">
  <div class="spinner"></div>
  <p>スペクトルを読み込み中...</p>
</div>
<div id="header">
  <h1 id="header-title">Peak Picker ― {title}</h1>
  <span style="font-size:12px;opacity:0.8">ラベル密度</span>
  <input id="label-gap-slider" type="range" min="0" max="200" step="1" value="60" style="width:120px;accent-color:#aaa" oninput="updateLabelGap(this.value)" title="ラベル表示密度（右端: 全表示 / 左端: 非表示）">
  <button onclick="resetLabelGap()" title="ラベル密度を初期値に戻す" style="font-size:11px;padding:3px 7px;background:#aaa;color:#fff;border-color:#aaa;">↺</button>
  <span style="display:inline-flex;gap:0"><button onclick="savePng()" style="border-radius:4px 0 0 4px">グラフ保存</button><button id="btn-legend" onclick="toggleLegend()" title="凡例表示切替" style="padding:3px 7px;border-left:none;border-radius:0 4px 4px 0;">≡</button></span>
  <button onclick="clearAll()" class="danger">全消去</button>
  <button id="fb-btn" title="バグ報告・ご要望" style="background:#34495e;color:#fff;border-color:#34495e;font-size:12px;padding:4px 10px;">お悩みボックス</button>
</div>
<div id="controls">
  <input type="hidden" id="snapMz" value="2">
  <input type="hidden" id="minRelPct" value="1">
  <input type="hidden" id="maxPeaks" value="5">
  <div class="ctrl-group" style="gap:6px;padding:0 20px;border-left:2px solid #e0e0e0;border-right:2px solid #e0e0e0;margin-right:60px;">
    <button id="btn-zoom"      onclick="setMode('zoom')"          class="primary">ズーム</button>
    <button onclick="resetZoom()" title="ダブルクリックでも可">全体表示</button>
    <button id="btn-edit"      onclick="setMode('edit')"                         >ピーク編集</button>
    <button id="btn-del-range" onclick="setMode('delete-range')"                 >範囲削除</button>
    <button id="btn-det-range" onclick="setMode('detect-range')"                 >範囲検出</button>
  </div>
</div>
<div id="body-wrap">
  <div id="sidebar">
    <div id="sidebar-label">SAMPLES</div>
    <div id="sample-list"></div>
    <div id="memo-wrap" class="collapsed">
      <div id="memo-label">MEMO <span id="memo-toggle">▸</span></div>
      <textarea id="memo" placeholder="メモを入力..."></textarea>
    </div>
    <div id="img-wrap" class="collapsed">
      <div id="img-label">化合物 <span id="img-toggle">▸</span></div>
      <div id="img-body">
        <div id="img-drop">画像をペースト<br>またはドロップ<br><label style="margin-top:4px;display:inline-block;cursor:pointer;color:#2980b9;font-size:11px">ファイルを開く<input type="file" accept="image/*" style="display:none" id="img-file-input"></label></div>
        <img id="compound-img" style="display:none" alt="compound">
        <div id="img-btns" style="display:none;gap:4px;display:none;flex-direction:row;">
          <button id="img-dl">保存</button>
          <button id="img-clear">削除</button>
        </div>
      </div>
    </div>
  </div>
  <div id="chart-wrap">
    <div id="chart"></div>
    <div id="status"></div>
    <div id="tips-bar"><span class="tips-icon">💡</span><span id="tips-text"></span></div>
  </div>
</div>

<script>
// ── データ（Pythonが埋め込む） ──────────────────────────────
const ALL_SPECTRA = {spectra_json};

// ── カラー・線種パレット ────────────────────────────────────
const SPEC_COLORS = ['#000000','#2980b9','#27ae60','#8e44ad','#e67e22','#16a085','#c0392b','#607d8b'];
const SPEC_DASHES = ['solid','dash','dot','dashdot','longdash','longdashdot'];
function getSpecColor(idx) { return SPEC_COLORS[idx % SPEC_COLORS.length]; }
function getSpecDash(overlayOrder) { return SPEC_DASHES[(overlayOrder % (SPEC_DASHES.length - 1)) + 1]; }

// ── 状態 ─────────────────────────────────────────────────────
let currentIdx  = 0;
let X_DATA      = ALL_SPECTRA[0].x;
let Y_DATA      = ALL_SPECTRA[0].y;
// 初期ピークの強度をLTTB表示データに合わせてスナップ（フルresデータとのズレを解消）
let allPeaks = ALL_SPECTRA.map(s => {
  const sx = s.x, sy = s.y;
  return s.initPeaks.map(p => {
    let best = 0, minD = Infinity;
    for (let j = 0; j < sx.length; j++) {
      const d = Math.abs(sx[j] - p.mz);
      if (d < minD) { minD = d; best = j; }
    }
    // hill-climb to true local max in display data
    while (best > 0 && sy[best - 1] > sy[best]) best--;
    while (best < sy.length - 1 && sy[best + 1] > sy[best]) best++;
    return { mz: sx[best], intensity: sy[best] };
  });
});
let allHistory  = ALL_SPECTRA.map(() => []);
let peaks       = allPeaks[0];
let history     = allHistory[0];

// ── メモ（永続化 + 最小化） ───────────────────────────────────
(function() {
  const key    = 'peak_picker_memo__' + document.title;
  const colKey = 'peak_picker_memo_col__' + document.title;
  const el     = document.getElementById('memo');
  const wrap   = document.getElementById('memo-wrap');
  const toggle = document.getElementById('memo-toggle');
  el.value = (() => { try { return localStorage.getItem(key) || ''; } catch(e) { return ''; } })();
  el.addEventListener('input', () => { try { localStorage.setItem(key, el.value); } catch(e) {} });
  if (localStorage.getItem(colKey) === '0') { wrap.classList.remove('collapsed'); toggle.textContent = '▾'; }
  document.getElementById('memo-label').addEventListener('click', () => {
    const c = wrap.classList.toggle('collapsed');
    toggle.textContent = c ? '▸' : '▾';
    try { localStorage.setItem(colKey, c ? '1' : '0'); } catch(e) {}
  });
})();

// ── 化合物画像（ペースト / ドロップ / ファイル選択 + 最小化） ─
(function() {
  const imgKey = 'peak_picker_img__' + document.title;
  const colKey = 'peak_picker_img_col__' + document.title;
  const wrap   = document.getElementById('img-wrap');
  const toggle = document.getElementById('img-toggle');
  const drop   = document.getElementById('img-drop');
  const img    = document.getElementById('compound-img');
  const btns   = document.getElementById('img-btns');
  const dlBtn  = document.getElementById('img-dl');
  const clearBtn = document.getElementById('img-clear');

  function setImage(dataUrl) {
    img.src = dataUrl; img.style.display = 'block';
    drop.style.display = 'none'; btns.style.display = 'flex';
    try { localStorage.setItem(imgKey, dataUrl); } catch(e) {}
  }
  function clearImage() {
    img.src = ''; img.style.display = 'none';
    drop.style.display = 'flex'; btns.style.display = 'none';
    try { localStorage.removeItem(imgKey); } catch(e) {}
  }
  function readFile(file) {
    if (!file || !file.type.startsWith('image/')) return;
    const r = new FileReader();
    r.onload = e => setImage(e.target.result);
    r.readAsDataURL(file);
  }

  // 復元
  const saved = (() => { try { return localStorage.getItem(imgKey); } catch(e) { return null; } })();
  if (saved) setImage(saved);
  if (localStorage.getItem(colKey) === '0') { wrap.classList.remove('collapsed'); toggle.textContent = '▾'; }

  // 最小化トグル
  document.getElementById('img-label').addEventListener('click', () => {
    const c = wrap.classList.toggle('collapsed');
    toggle.textContent = c ? '▸' : '▾';
    try { localStorage.setItem(colKey, c ? '1' : '0'); } catch(e) {}
  });

  // ダウンロード（pictures/ フォルダに保存してください）
  dlBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = img.src;
    a.download = (ALL_SPECTRA[currentIdx]?.name || 'compound').replace(/[\/\\:*?"<>|]/g, '_') + '_compound.png';
    a.click();
  });

  clearBtn.addEventListener('click', clearImage);

  // ドラッグ＆ドロップ
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); readFile(e.dataTransfer.files[0]); });

  // ファイル選択
  document.getElementById('img-file-input').addEventListener('change', e => readFile(e.target.files[0]));

  // ペースト（クリップボードから画像）
  document.addEventListener('paste', e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) { readFile(item.getAsFile()); break; }
    }
  });
})();

// ── ピーク永続化（localStorage） ─────────────────────────────
const _PEAKS_KEY = 'peak_picker_peaks__' + document.title;
function savePeaks() {
  try { localStorage.setItem(_PEAKS_KEY, JSON.stringify(allPeaks)); } catch(e) {}
}
(function loadPeaks() {
  try {
    const raw = localStorage.getItem(_PEAKS_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!Array.isArray(saved) || saved.length !== allPeaks.length) return;
    allPeaks = saved;
    peaks = allPeaks[0];
  } catch(e) {}
})();
let mode        = 'zoom';
let currentXRange = null;
let overlayIdxs = new Set(); // アクティブ以外でオーバーレイ表示中のインデックス
let allScales   = ALL_SPECTRA.map(() => 1.0); // サンプルごとの強度スケール
let allColors   = ALL_SPECTRA.map((_, i) => getSpecColor(i)); // サンプルごとの色

// ── サイドバーボタン生成 ─────────────────────────────────────
const sidebar = document.getElementById('sample-list');
ALL_SPECTRA.forEach((s, i) => {
  const group = document.createElement('div');
  const item = document.createElement('div');
  item.className = 'spec-item';
  const btn = document.createElement('button');
  btn.id = `spec-btn-${i}`;
  btn.className = 'spec-btn' + (i === 0 ? ' active' : '');
  btn.title = s.name;
  const ci = document.createElement('input');
  ci.type = 'color'; ci.value = allColors[i]; ci.className = 'spec-color-input'; ci.title = '色を変更';
  ci.addEventListener('input', e => { e.stopPropagation(); updateSpecColor(i, e.target.value); });
  ci.addEventListener('click', e => e.stopPropagation());
  btn.appendChild(ci);
  btn.appendChild(document.createTextNode(' ' + s.name));
  btn.onclick = () => switchSpectrum(i);
  const ovBtn = document.createElement('button');
  ovBtn.className = 'overlay-btn';
  ovBtn.id = `overlay-btn-${i}`;
  ovBtn.title = 'オーバーレイ表示切替';
  ovBtn.textContent = '⊕';
  ovBtn.onclick = () => toggleOverlay(i);
  item.appendChild(btn);
  item.appendChild(ovBtn);
  // スケール行
  const scaleRow = document.createElement('div');
  scaleRow.className = 'spec-scale' + (i === 0 ? ' visible' : '');
  scaleRow.id = `spec-scale-${i}`;
  scaleRow.innerHTML = `<span id="scale-color-${i}" style="color:${allColors[i]};font-weight:bold">×</span><input type="range" min="-100" max="100" step="1" value="0" oninput="updateScale(${i},this.value)"><span class="sv" id="scale-val-${i}">1.00</span>`;
  group.appendChild(item);
  group.appendChild(scaleRow);
  sidebar.appendChild(group);
});

// ── スペクトル切替 ────────────────────────────────────────────
function switchSpectrum(idx) {
  if (idx === currentIdx) return;
  // 旧アクティブのスケール行を隠す（オーバーレイでなければ）
  if (!overlayIdxs.has(currentIdx)) {
    document.getElementById(`spec-scale-${currentIdx}`).className = 'spec-scale';
  }
  // 新しいアクティブがオーバーレイに入っていたら除去
  if (overlayIdxs.has(idx)) {
    overlayIdxs.delete(idx);
    document.getElementById(`overlay-btn-${idx}`).className = 'overlay-btn';
  }
  currentIdx = idx;
  // 新アクティブのスケール行を表示
  document.getElementById(`spec-scale-${idx}`).className = 'spec-scale visible';
  X_DATA  = ALL_SPECTRA[idx].x;
  Y_DATA  = ALL_SPECTRA[idx].y;
  peaks   = allPeaks[idx];
  history = allHistory[idx];
  const color = allColors[idx];
  const scale = allScales[idx];
  Plotly.restyle('chart', { x: [X_DATA], y: [Y_DATA.map(v => v * scale)], 'line.color': [color], 'line.width': [1.5], 'line.dash': ['solid'], name: [ALL_SPECTRA[idx].name], type: ['scattergl'] }, [0]);
  Plotly.restyle('chart', { 'marker.color': [color], 'textfont.color': [color] }, [1]);
  renderAll();
  rebuildOverlayTraces();
  // ズーム範囲を保持（xは現在のまま、yはauto）
  if (currentXRange) {
    Plotly.relayout('chart', { 'xaxis.range': currentXRange, 'yaxis.autorange': true });
  } else {
    resetZoom();
  }
  ALL_SPECTRA.forEach((_, j) => {
    const b = document.getElementById(`spec-btn-${j}`);
    if (b) b.className = 'spec-btn' + (j === idx ? ' active' : '');
  });
  document.getElementById('header-title').textContent = `Peak Picker ― ${ALL_SPECTRA[idx].name}`;
}

// ── オーバーレイ管理 ──────────────────────────────────────────
function toggleOverlay(idx) {
  if (idx === currentIdx) return;
  if (overlayIdxs.has(idx)) {
    overlayIdxs.delete(idx);
    document.getElementById(`overlay-btn-${idx}`).className = 'overlay-btn';
    document.getElementById(`spec-scale-${idx}`).className = 'spec-scale';
  } else {
    overlayIdxs.add(idx);
    document.getElementById(`overlay-btn-${idx}`).className = 'overlay-btn on';
    document.getElementById(`spec-scale-${idx}`).className = 'spec-scale visible';
  }
  rebuildOverlayTraces();
}

function updateScale(idx, val) {
  const scale = Math.pow(10, parseFloat(val) / 50);
  allScales[idx] = scale;
  document.getElementById(`scale-val-${idx}`).textContent = scale.toFixed(2);
  if (idx === currentIdx) {
    const scale = allScales[idx];
    Plotly.restyle('chart', { y: [Y_DATA.map(v => v * scale)] }, [0]);
    Plotly.restyle('chart', { y: [peaks.map(p => p.intensity * scale)] }, [1]).then(renderLabels);
  } else {
    rebuildOverlayTraces();
  }
}

function updateSpecColor(idx, color) {
  allColors[idx] = color;
  const scaleColorEl = document.getElementById(`scale-color-${idx}`);
  if (scaleColorEl) scaleColorEl.style.color = color;
  if (idx === currentIdx) {
    Plotly.restyle('chart', { 'line.color': [color] }, [0]);
    Plotly.restyle('chart', { 'marker.color': [color], 'textfont.color': [color] }, [1]);
    renderLabels();
  }
  if (overlayIdxs.has(idx)) rebuildOverlayTraces();
}

function rebuildOverlayTraces() {
  const nCurrent = document.getElementById('chart').data.length;
  const removeIdxs = [];
  for (let i = 2; i < nCurrent; i++) removeIdxs.push(i);
  const doAdd = () => {
    if (overlayIdxs.size === 0) return;
    const newTraces = [];
    let order = 0;
    overlayIdxs.forEach(idx => {
      const s = ALL_SPECTRA[idx];
      const color = allColors[idx];
      const dash  = getSpecDash(order++);
      const scale = allScales[idx];
      newTraces.push({
        type: 'scattergl',
        x: s.x, y: s.y.map(v => v * scale),
        mode: 'lines', name: s.name,
        line: { color, width: 1.5, dash },
        hovertemplate: `m/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra>${s.name} ×${scale.toFixed(2)}</extra>`,
      });
      newTraces.push({
        x: allPeaks[idx].map(p => p.mz),
        y: allPeaks[idx].map(p => p.intensity * scale),
        mode: 'markers', showlegend: false,
        marker: { color, size: 5, symbol: 'diamond' },
        hovertemplate: `m/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra>${s.name}</extra>`,
      });
    });
    Plotly.addTraces('chart', newTraces);
  };
  if (removeIdxs.length > 0) {
    Plotly.deleteTraces('chart', removeIdxs).then(doAdd);
  } else {
    doAdd();
  }
}

// ── Plotly 初期化 ──────────────────────────────────────────
const specTrace = {
  type: 'scattergl',
  x: X_DATA, y: Y_DATA,
  mode: 'lines',
  name: ALL_SPECTRA[0].name,
  line: { color: allColors[0], width: 1.5 },
  hovertemplate: 'm/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra></extra>',
};
const peakTrace = {
  x: [], y: [],
  mode: 'markers',
  name: 'Peaks',
  marker: { color: allColors[0], size: 5, symbol: 'circle' },
  text: [],
  textfont: { size: 11, color: allColors[0] },
  hovertemplate: 'm/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra></extra>',
  showlegend: false,
};
const layout = {
  margin: { t: 30, r: 10, b: 50, l: 60 },
  xaxis: {
    title: { text: 'm/z', font: { size: 13 } },
    showgrid: true, gridcolor: '#e8e8e8', gridwidth: 1,
    showline: true, linecolor: '#aaa',
    tickfont: { size: 12 },
    nticks: 20,          // 目盛り本数の目安
    zeroline: false,
  },
  yaxis: { title: 'Intensity', showgrid: false, zeroline: false },
  hovermode: 'x',
  dragmode: 'zoom',
  plot_bgcolor: 'white',
  showlegend: false,
  legend: { x: 1, xanchor: 'right', y: 1, font: { size: 11 } },
};
// 初期ピークはすでに allPeaks[0] に入っているのでそのまま使う

// newPlot の Promise が解決したらローディング非表示 & ラベル再計算
Plotly.newPlot('chart', [specTrace, peakTrace], layout, { responsive: true, scrollZoom: true })
  .then(function() {
    renderAll();
    setMode('zoom');
    document.getElementById('loading').style.display = 'none';
    // offsetWidth が確定してからラベルを再描画
    requestAnimationFrame(() => requestAnimationFrame(renderLabels));
  });

// ── 履歴（Undo） ────────────────────────────────────────────
function saveHistory() {
  allHistory[currentIdx].push(JSON.parse(JSON.stringify(peaks)));
  if (allHistory[currentIdx].length > 50) allHistory[currentIdx].shift();
}
function undo() {
  if (allHistory[currentIdx].length === 0) return;
  allPeaks[currentIdx] = allHistory[currentIdx].pop();
  peaks = allPeaks[currentIdx];
  renderAll();
  setStatus('元に戻しました');
}
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
    e.preventDefault();
    undo();
  }
});

// ── モード切替 ───────────────────────────────────────────────
function setMode(m) {
  mode = m;
  document.getElementById('btn-zoom').className      = m === 'zoom'         ? 'primary' : '';
  document.getElementById('btn-edit').className      = m === 'edit'         ? 'active'  : '';
  document.getElementById('btn-del-range').className = m === 'delete-range' ? 'active'  : '';
  document.getElementById('btn-det-range').className = m === 'detect-range' ? 'active'  : '';
  // zoom は Plotly ビルトインの 'zoom'（ドラッグ箱ズーム＋ダブルクリックリセット対応）
  // edit は 'pan'（ドラッグでパン、クリックでピーク操作）
  // delete/detect は 'select'（ドラッグで範囲選択）
  const dm = m === 'zoom' ? 'zoom' : m === 'edit' ? 'pan' : 'select';
  Plotly.relayout('chart', { dragmode: dm });
  setStatus('');
}
function setStatus(msg) {
  document.getElementById('status').textContent = msg;
}

// ── クリックハンドラ（ピーク編集モードのみ） ─────────────────
document.getElementById('chart').on('plotly_click', function(data) {
  if (mode !== 'edit') return;
  const pt = data.points[0];
  if (!pt) return;
  const clickX = pt.x;
  const snapMz = parseFloat(document.getElementById('snapMz').value) || 2.0;
  const peak = findLocalMax(clickX, snapMz);
  if (!peak) return;
  saveHistory();
  const existIdx = peaks.findIndex(p => Math.abs(p.mz - peak.mz) < 0.01);
  if (existIdx >= 0) {
    peaks.splice(existIdx, 1);
    setStatus(`削除: m/z ${peak.mz.toFixed(4)}`);
  } else {
    peaks.push(peak);
    setStatus(`追加: m/z ${peak.mz.toFixed(4)}`);
  }
  peaks.sort((a, b) => a.mz - b.mz);
  renderAll();
});

// ── ズーム変化を監視してラベル更新 ──────────────────────────
document.getElementById('chart').on('plotly_relayout', function(ev) {
  const x0 = ev['xaxis.range[0]'], x1 = ev['xaxis.range[1]'];
  if (x0 !== undefined && x1 !== undefined) {
    currentXRange = [parseFloat(x0), parseFloat(x1)];
    renderLabels();
  } else if (ev['xaxis.autorange']) {
    currentXRange = null;
    renderLabels();
  }
});

// ── 範囲選択ハンドラ ─────────────────────────────────────────
function clearSelection() {
  Plotly.restyle('chart', { selectedpoints: [null] }, [0, 1]);
  // 選択ボックスの残影を消す（Plotly 2.x）
  try { Plotly.relayout('chart', { selections: [] }); } catch(e) {}
}

document.getElementById('chart').on('plotly_selected', function(data) {
  if (!data || !data.range || !data.range.x) { clearSelection(); return; }
  const lo = Math.min(data.range.x[0], data.range.x[1]);
  const hi = Math.max(data.range.x[0], data.range.x[1]);

  if (mode !== 'delete-range' && mode !== 'detect-range') { clearSelection(); return; }

  saveHistory();
  if (mode === 'delete-range') {
    const before = peaks.length;
    allPeaks[currentIdx] = peaks = peaks.filter(p => p.mz < lo || p.mz > hi);
    setStatus(`範囲削除: ${before - peaks.length} 件削除`);
    renderAll();
  } else if (mode === 'detect-range') {
    detectPeaksInRange(lo, hi);
    renderAll();
  }
  clearSelection();
});

// ドラッグせずにクリックだけした場合も選択解除
document.getElementById('chart').on('plotly_deselect', clearSelection);

// ズームリセット
function resetZoom() {
  Plotly.relayout('chart', { 'xaxis.autorange': true, 'yaxis.autorange': true });
  currentXRange = null;
  renderLabels();
}
// ダブルクリックでも（動く環境なら）
document.addEventListener('dblclick', function(e) {
  if (document.getElementById('chart').contains(e.target)) resetZoom();
});

// ── 局所最大スナップ ──────────────────────────────────────────
function findLocalMax(clickX, halfWindowMz) {
  let bestIdx = -1, minDist = Infinity;
  for (let i = 0; i < X_DATA.length; i++) {
    const d = Math.abs(X_DATA[i] - clickX);
    if (d < minDist) { minDist = d; bestIdx = i; }
  }
  if (bestIdx < 0) return null;
  const dx = Math.abs(X_DATA[Math.min(bestIdx + 1, X_DATA.length - 1)] - X_DATA[bestIdx]) || 0.01;
  const hw = Math.max(3, Math.round(halfWindowMz / dx));
  const l = Math.max(0, bestIdx - hw);
  const r = Math.min(X_DATA.length - 1, bestIdx + hw);
  // 窓内の最高点を探す
  let maxIdx = l;
  for (let i = l + 1; i <= r; i++) {
    if (Y_DATA[i] > Y_DATA[maxIdx]) maxIdx = i;
  }
  // LTTBの間引き誤差を補正: 真の局所最大まで登る
  while (maxIdx > 0 && Y_DATA[maxIdx - 1] > Y_DATA[maxIdx]) maxIdx--;
  while (maxIdx < Y_DATA.length - 1 && Y_DATA[maxIdx + 1] > Y_DATA[maxIdx]) maxIdx++;
  return { mz: X_DATA[maxIdx], intensity: Y_DATA[maxIdx] };
}

// ── prominence 計算（全データ配列に対して） ──────────────────
function calcProminence(idx) {
  const h = Y_DATA[idx];
  // 左側: より高い点が見つかるまでの最小値
  let leftMin = h;
  for (let i = idx - 1; i >= 0; i--) {
    if (Y_DATA[i] >= h) break;
    if (Y_DATA[i] < leftMin) leftMin = Y_DATA[i];
  }
  // 右側: より高い点が見つかるまでの最小値
  let rightMin = h;
  for (let i = idx + 1; i < Y_DATA.length; i++) {
    if (Y_DATA[i] >= h) break;
    if (Y_DATA[i] < rightMin) rightMin = Y_DATA[i];
  }
  // prominence = 両側の谷の高い方との差
  return h - Math.max(leftMin, rightMin);
}

// ── 範囲内ピーク自動検出 ─────────────────────────────────────
function detectPeaksInRange(lo, hi) {
  const snapMz  = parseFloat(document.getElementById('snapMz').value)    || 2.0;
  const minRel  = (parseFloat(document.getElementById('minRelPct').value) || 1.0) / 100.0;
  const maxPks  = parseInt(document.getElementById('maxPeaks').value)     || 5;

  // 範囲内のインデックスを収集
  const idxs = [];
  for (let i = 0; i < X_DATA.length; i++) {
    if (X_DATA[i] >= lo && X_DATA[i] <= hi) idxs.push(i);
  }
  if (idxs.length < 3) return;

  // 範囲内最大強度
  let yMax = -Infinity;
  for (const i of idxs) if (Y_DATA[i] > yMax) yMax = Y_DATA[i];
  const threshold = yMax * minRel;
  const minProm  = yMax * minRel;  // prominence も同じ相対閾値で判定

  // 局所最大（plateau対策で片側 >=）＋ 強度しきい値
  const cands = [];
  for (let k = 1; k < idxs.length - 1; k++) {
    const i = idxs[k], prev = idxs[k - 1], next = idxs[k + 1];
    if (Y_DATA[i] > Y_DATA[prev] && Y_DATA[i] >= Y_DATA[next] && Y_DATA[i] >= threshold) {
      cands.push(i);
    }
  }
  if (cands.length === 0) { setStatus('範囲検出: 候補なし'); return; }

  // prominence で絞り込み
  const withProm = cands
    .map(i => ({ i, intensity: Y_DATA[i], prom: calcProminence(i) }))
    .filter(o => o.prom >= minProm);
  if (withProm.length === 0) { setStatus('範囲検出: prominenceフィルタで候補なし（閾値を下げてください）'); return; }

  // prominence の高い順にソートして距離間引き → 上位 maxPks 件
  withProm.sort((a, b) => b.prom - a.prom);
  const kept = [];
  for (const o of withProm) {
    if (kept.length >= maxPks) break;
    const mz = X_DATA[o.i];
    if (kept.every(k => Math.abs(X_DATA[k.i] - mz) >= snapMz)) kept.push(o);
  }

  let added = 0;
  for (const o of kept) {
    if (addPeakRaw(X_DATA[o.i], Y_DATA[o.i])) added++;
  }
  peaks.sort((a, b) => a.mz - b.mz);
  setStatus(`範囲検出: ${added} 件追加（候補${withProm.length}→prominence上位${kept.length}）`);
}

// ── 追加ヘルパー ─────────────────────────────────────────────
function addPeakRaw(mz, intensity) {
  if (peaks.findIndex(p => Math.abs(p.mz - mz) < 0.01) >= 0) return false;
  peaks.push({ mz, intensity });
  return true;
}

// ── ラベル間引き ──────────────────────────────────────────────
function renderLabels() {
  // スライダー値(px)から最小m/z間隔を計算: minMzGap = max(slider, 14px) / pxPerMz
  if (labelGap === 0) { Plotly.relayout('chart', { annotations: [] }); return; }
  const chartEl  = document.getElementById('chart');
  const plotWidth = Math.max(100, chartEl.offsetWidth - 70);

  let lo, hi;
  if (currentXRange) {
    lo = currentXRange[0]; hi = currentXRange[1];
  } else {
    lo = X_DATA[0] ?? 0; hi = X_DATA[X_DATA.length - 1] ?? 1;
  }
  const pxPerMz  = plotWidth / Math.max(hi - lo, 0.01);
  const minMzGap = Math.max(labelGap, 0.1) / pxPerMz;  // スライダー(px)→m/z換算

  // 表示範囲内のピークを強度降順で greedy 選択
  const candidates = peaks
    .map((p, i) => ({ i, p }))
    .filter(o => o.p.mz >= lo && o.p.mz <= hi)
    .sort((a, b) => b.p.intensity - a.p.intensity);

  const showSet = new Set();
  if (showAllLabels) {
    candidates.forEach(({ i }) => showSet.add(i));
  } else {
    const shown = [];
    for (const { i, p } of candidates) {
      if (shown.every(sp => Math.abs(p.mz - sp.mz) >= minMzGap)) {
        showSet.add(i); shown.push(p);
      }
    }
  }

  const scale = allScales[currentIdx];
  const color = allColors[currentIdx];
  const annotations = [...showSet].map(i => ({
    x: peaks[i].mz, y: peaks[i].intensity * scale,
    text: peaks[i].mz.toFixed(2),
    showarrow: false, textangle: -90,
    xanchor: 'center', yanchor: 'bottom',
    font: { size: 11, color },
    xref: 'x', yref: 'y',
  }));
  Plotly.relayout('chart', { annotations });
}

// ── 描画 & テーブル更新 ─────────────────────────────────────
function renderAll() {
  const scale = allScales[currentIdx];
  Plotly.restyle('chart', {
    x: [peaks.map(p => p.mz)],
    y: [peaks.map(p => p.intensity * scale)],
  }, [1]).then(renderLabels);
  savePeaks();
}

// ── PNG保存 ─────────────────────────────────────────────────
const SAMPLE_NAME = '{title}';
function savePng() {
  Plotly.downloadImage('chart', { format: 'png', filename: SAMPLE_NAME, scale: 3 });
}
let legendVisible = false;
function toggleLegend() {
  legendVisible = !legendVisible;
  document.getElementById('btn-legend').className = legendVisible ? 'primary' : '';
  Plotly.relayout('chart', { showlegend: legendVisible });
}

// ── ラベル全表示トグル ────────────────────────────────────────
let showAllLabels = false;
const LABEL_GAP_DEFAULT = 25;  // px
const LABEL_SLIDER_DEFAULT = 60;  // 指数スケールでgap≈25px
let labelGap = LABEL_GAP_DEFAULT;
function updateLabelGap(val) {
  const v = parseFloat(val);
  showAllLabels = false;
  // 指数スケール: 右に行くほど加速度的にラベルが増える
  // val=0→非表示, val=60→25px(デフォ), val=200→0.4px(ほぼ全表示)
  labelGap = v === 0 ? 0 : 150 * Math.exp(-0.03 * v);
  renderLabels();
}
function resetLabelGap() {
  showAllLabels = false;
  labelGap = LABEL_GAP_DEFAULT;
  document.getElementById('label-gap-slider').value = LABEL_SLIDER_DEFAULT;
  renderLabels();
}
function toggleAllLabels() {
  showAllLabels = !showAllLabels;
  const btn = document.getElementById('btn-all-labels');
  btn.className = showAllLabels ? 'primary' : '';
  renderLabels();
}

// ── 全消去 ──────────────────────────────────────────────────
function clearAll() {
  if (peaks.length === 0) return;
  if (!confirm(`${peaks.length} 件のピークを全て削除しますか？`)) return;
  saveHistory();
  allPeaks[currentIdx] = peaks = [];
  renderAll();
}

// ── Tips ─────────────────────────────────────────────────────
(function () {
  const TIPS = [
    'ピーク編集モードでグラフをクリックすると、クリック位置の一番近い山頂にピークが追加されます。',
    'すでにあるピークをクリックすると削除できます。ピーク編集モードのまま操作できます。',
    'Cmd+Z（Mac）/ Ctrl+Z（Win）でピーク操作を元に戻せます。全消去後でも戻せます。',
    '全消去はピークラベルを消すだけです。スペクトルデータは消えません。',
    'グラフをダブルクリックするとズームがリセットされて全体表示に戻ります。',
    'マウスホイールでもズームできます。ドラッグで範囲ズームも可。',
    'グラフの左端の縦軸（数字が並んでいるところ）をドラッグすると、グラフを上下にスライドできます。横軸も同様です。',
    'サイドバーの + ボタンで複数サンプルを重ねて比較できます。何サンプルでも重ねられます。',
    '重ね表示中のスライダーで強度スケールをサンプルごとに個別調整できます。',
    '範囲削除モードでドラッグすると、範囲内のピークをまとめて削除できます。',
    '範囲検出モードでドラッグすると、その範囲だけ自動ピーク検出して追加できます。',
    '範囲検出と手動ピーク追加を組み合わせると効率よくピークを揃えられます。',
    'このHTMLは1ファイルに全部入っています。メール・Teams・ラボノートにそのまま添付できます。',
    'HTMLをラボノートに添付しておけば、後からブラウザで開くだけでピーク選択しながら見返せます。',
    'インターネットがなくても動きます。',
    'ピーク編集モードでクリックすると、クリック位置付近の山頂に自動で吸い付きます。',
    'サイドバーのサンプル名をクリックすると表示サンプルを切り替えられます。',
    'ピークラベルはズームに合わせて自動で表示数が調整されます。拡大すると隠れていたラベルも出てきます。',
    '全スペクトルデータが HTML 1ファイルに内包されています。フォルダごと送らなくてOKです。',
    'サイドバーの色丸をクリックすると、サンプルの表示色をその場で変更できます。',
    'スケールスライダーで縦軸の表示倍率をサンプルごとに調整できます（データ値は変わりません）。',
    'ピーク編集モードのボタンが緑色になっているか確認しながら使いましょう。',
    '操作後のm/z値はステータスバー（グラフ下）に表示されます。',
    '測定日や試料名をフォルダ名に入れておくと、サイドバーにそのまま表示されます。',
    'サンプルフォルダの親フォルダを指定するだけで、中の測定を全部まとめて読み込みます。',
    '生成されたHTMLのファイル名には測定フォルダ名が自動で使われます。',
    '複数の親フォルダを同時に指定して、異なる実験をまとめて比較できます（コマンドライン版）。',
    'HTMLファイルはそのままバージョン管理（git）に入れてもOKです。差分もテキストです。',
    'ピーク編集モードでクリックしてもピークが追加されない場合は、ズームモードになっていないか確認を。',
    '生成したHTMLを複数タブで開いて並べると、異なる試料を同時に見比べられます。',
    'バグがあったり要望などあれば直接いってください。',
    'ターミナルで打ち込んでいるコマンド（python3 peak_picker_html.py ...）は、mal など好きな短縮名でエイリアスに設定できます。詳しくはAIに聞いてみてください。',
    'Claude Code を入れると、こういうツールを自分でどんどん作ったり改造したりできるようになる。研究がはかどる。',
    'LTTBアルゴリズムでダウンサンプリングしているので、波形の見た目はほぼ保たれている。気になる人はソースを読んでみよう。筆者は仕組みは理解できていない。',
    'PlotlyのJSがHTMLに丸ごと埋め込まれているので、インターネットなしでも完全に動きます。',
    'ヘッダーのラベル密度スライダーは右に動かすほどラベルが増え、左に動かすと減ります。',
    'サンプルはフォルダ名のアルファベット順で並びます。日付や番号をフォルダ名に入れると順番が整います。',
    '処理が完了すると、data/ の中身は自動で archive/ に移動し、HTMLは html/ フォルダに格納されます。次の実験データをそのまま data/ に入れるだけで使えます。',
    'HTMLファイルが4〜5MBと大きいのは、グラフライブラリ Plotly.js（約4.3MB）をHTML内に丸ごと埋め込んでいるから。おかげでオフラインでも動く。',
    'サイドバー下部のMEMOエリアに気づいたことや条件をメモしておけます。ブラウザを閉じても消えません。',
    'ピークの追加・削除はブラウザを閉じても保存されています。同じHTMLを開き直すと前回の状態が復元されます。',
    'サンプルを切り替えてもズーム範囲はそのまま維持されます。同じm/z範囲で複数サンプルを見比べるときに便利です。',
    'サイドバーの化合物エリアに構造式などの画像を貼り付けられます。ChemDrawなどからCtrl+V（Win）/ Cmd+V（Mac）で直接ペースト可。',
    'MEMO・化合物エリアのラベル部分をクリックすると折りたたみ/展開できます。開いた状態・閉じた状態はHTMLごとに記憶されます。',
    '化合物エリアの画像はダウンロードして pictures/ フォルダに保存しておくと整理しやすいです。',
    'このツールはGitHubで公開されています。アップデートがあれば git pull の1コマンドで最新版に更新できます。gitが初めての人はAIに聞くと5分で使えるようになります。',
    'バグや要望があったら、ヘッダーのお悩みボックスから送れます。',
  ];

  const RARE_TIPS_1PCT = [
    'このツールで予約10分を削れるのでお金僕にください。',
    'スペクトルをじっと見ていると、どれが本物のピークかわからなくなることがある。',
    'サンプル名に絵文字を入れてもHTMLは壊れません。たぶん。',
    'このHTMLを10年後に開いても動く予定です。ブラウザが生きていれば。',
    '測定がうまくいった日は、データを見るだけで気分がいい。',
    'なぜかよくわからないが、測定がうまくいかないときは装置より自分を疑ったほうがいい場合が多い。',
  ];

  const RARE_TIPS = [
    'これを見ているということは確率0.1%を引き当てた。運がいい。',
    'peak_picker_select.py はmacOSの NSGenericException: Collection was mutated while being enumerated に敗北して削除された。ご冥福をお祈りします。',
    'ピーク全消去して保存したHTMLを同僚に送ると、ピーク選びを丸投げできる。',
    'Claude Code を入れると、こういうツールを自分でどんどん作ったり改造したりできるようになる。研究がはかどる。',
    'スペクトルデータはJSONとしてHTMLに丸ごと埋め込まれている。ブラウザの開発者ツールで ALL_SPECTRA と打つとデータが丸見えになる。',
    'このHTMLは理論上、現在のブラウザが存在する限り開き続けられる。PDFより長生きかもしれない。',
    '起動時に重いのはHTMLを1から生成しているから。スペクトルデータを全部JSONに変換して埋め込んでいる。しばらく待つのが正解。',
  ];

  let lastIdx = -1;
  let lastRare1Idx = -1;
  let lastRareIdx = -1;
  function showTip() {
    const r = Math.random();
    let pool, lastRef, setLast;
    if (r < 0.001) {
      pool = RARE_TIPS; lastRef = lastRareIdx;
      setLast = i => lastRareIdx = i;
    } else if (r < 0.01) {
      pool = RARE_TIPS_1PCT; lastRef = lastRare1Idx;
      setLast = i => lastRare1Idx = i;
    } else {
      pool = TIPS; lastRef = lastIdx;
      setLast = i => lastIdx = i;
    }
    let idx;
    do { idx = Math.floor(Math.random() * pool.length); } while (idx === lastRef);
    setLast(idx);
    const el = document.getElementById('tips-text');
    el.style.opacity = 0;
    setTimeout(() => { el.textContent = pool[idx]; el.style.opacity = 1; }, 800);
  }
  showTip();
  setInterval(showTip, 20000);
})();
</script>
<script>{feedback_widget_js}</script>
<script>
if (window.FeedbackWidget) {
  FeedbackWidget.init({
    appName: document.title,
    webhookUrl: 'https://discord.com/api/webhooks/1479781802695458950/aNXMSkC-gyoYzhJBzNnXYC9qABehlTax3nzuOqmRTpvnCYRJSN0UeQydaIFsGZza6MdV',
  });
}
</script>
</body>
</html>
"""


DEFAULTS = dict(
    procno=1,
    min_mz=50.0,
    max_mz=None,
    max_points=5000,
    top_n=50,
    snr=5.0,
    noise_window=50.0,
    min_width=3,
)


def main():
    ap = argparse.ArgumentParser(description="Bruker TOF ブラウザ上インタラクティブピーク選択")
    ap.add_argument("input_paths", nargs="+", help="測定フォルダ or 親フォルダ（複数指定可）")
    ap.add_argument("--procno", type=int, default=DEFAULTS['procno'])
    ap.add_argument("--min-mz", type=float, default=DEFAULTS['min_mz'])
    ap.add_argument("--max-mz", type=float, default=DEFAULTS['max_mz'])
    ap.add_argument("--min-rel-height", type=float, default=0.01,
                    help="pick_peaks_numpy 用: 最大強度に対する相対しきい値")
    ap.add_argument("--top-n", type=int, default=DEFAULTS['top_n'])
    ap.add_argument("--html-out", default=None,
                    help="出力HTMLファイル名（省略時は入力フォルダ名から自動生成）")
    ap.add_argument("--max-points", type=int, default=DEFAULTS['max_points'],
                    help="表示データ点数の上限・LTTBで削減（default: 5000）")
    # pick_peaks_snr 用オプション
    ap.add_argument("--algo", choices=["snr", "numpy"], default="snr",
                    help="ピーク検出アルゴリズム: snr=ローカルSNR+幅フィルタ(新), numpy=旧")
    ap.add_argument("--snr", type=float, default=DEFAULTS['snr'],
                    help="[snr] ノイズの何倍以上を本物ピークとするか (default: 5)")
    ap.add_argument("--noise-window", type=float, default=DEFAULTS['noise_window'],
                    help="[snr] ノイズ推定窓幅 m/z (default: 50)")
    ap.add_argument("--min-width", type=int, default=DEFAULTS['min_width'],
                    help="[snr] 最小ピーク幅 点数 (default: 3)")
    args = ap.parse_args()

    # ── スピナー用コンテキストマネージャ ──────────────────────
    from contextlib import contextmanager

    @contextmanager
    def spinner(msg, done_msg=None):
        stop = threading.Event()
        def _spin():
            for ch in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
                if stop.is_set():
                    break
                sys.stdout.write(f"\r{ch} {msg}")
                sys.stdout.flush()
                time.sleep(0.08)
            label = done_msg or msg
            sys.stdout.write(f"\r✓ {label}{''.join([' '] * 10)}\n")
            sys.stdout.flush()
        t = threading.Thread(target=_spin, daemon=True)
        t.start()
        try:
            yield
        finally:
            stop.set()
            t.join()

    # アップデート確認（オフライン時は無視）
    update_banner = _check_update()

    # Plotly JS をローカルに確保（初回のみダウンロード）
    _ensure_plotly()

    # 各パスを展開して測定フォルダを収集
    all_dirs = []
    for input_path in args.input_paths:
        if not Path(input_path).exists():
            print(f"[ERR] パスが存在しません: {Path(input_path).resolve()}")
            print(f"      データフォルダのパスを確認してください。")
            continue
        found = find_all_measurement_dirs(input_path)
        if not found:
            print(f"[WARN] 測定フォルダが見つかりませんでした: {input_path}")
        all_dirs.extend(found)
    if not all_dirs:
        sys.exit("[ERR] 有効な測定フォルダが1件もありませんでした")

    # 名前順にソート（親フォルダ名 → 測定フォルダ名）
    all_dirs.sort(key=lambda d: (d.parent.name, d.name))

    # ボタン名: 親フォルダ名を優先。同じ親が複数あれば "親/測定" で区別
    parent_counts: dict[str, int] = {}
    for d in all_dirs:
        parent_counts[d.parent.name] = parent_counts.get(d.parent.name, 0) + 1

    def button_name(d: Path) -> str:
        pname = d.parent.name
        if parent_counts[pname] > 1:
            return f"{pname}/{d.name}"
        return pname

    # HTML出力名: 省略時は入力フォルダと同じ場所に測定フォルダ名で自動生成
    save_dir = Path(__file__).parent
    if args.html_out:
        html_out_path = Path(args.html_out)
    else:
        sample_names = list(dict.fromkeys(d.parent.name for d in all_dirs))
        dir_names = sample_names[:3]
        base = "_".join(dir_names) + ("_etc" if len(sample_names) > 3 else "")
        html_out_path = save_dir / f"{base}_peaks.html"

    # 各フォルダのデータ読み込み＋ピーク検出
    spectra = []
    for i, measurement_dir in enumerate(all_dirs):
        with spinner(f"[{i+1}/{len(all_dirs)}] データ読み込み中... {measurement_dir.name}"):
            res = load_bruker_tof(measurement_dir, procno=args.procno, prefer_processed=True,
                                  strictness="strict", axis_mode="mz")
        print(f"  フォルダ: {measurement_dir.name}  status: {res['status']}")

        x = res["arrays"]["mz"] if res["arrays"]["mz"] is not None else res["arrays"]["point_index"]
        y = res["arrays"]["intensity_primary"]
        if x is None or y is None:
            print(f"  [SKIP] x/y が取得できませんでした: {measurement_dir.name}")
            continue

        mask = np.isfinite(x) & np.isfinite(y)
        if args.min_mz is not None:
            mask &= (x >= args.min_mz)
        if args.max_mz is not None:
            mask &= (x <= args.max_mz)
        xm, ym = x[mask], y[mask]
        # LTTBダウンサンプリング（点数削減）
        xd, yd = lttb_downsample(xm, ym, args.max_points)
        # 精度丸め（JSONサイズ削減）: m/z→4桁, intensity→1桁
        xf = np.round(xd, 4).tolist()
        yf = np.round(yd, 1).tolist()
        print(f"  点数: {len(xm)} → {len(xf)}")

        pk_msg = f"ピーク検出中... {measurement_dir.name}"
        with spinner(pk_msg):
            if args.algo == "snr":
                init_peaks = pick_peaks_snr(
                    x, y,
                    min_mz=args.min_mz, max_mz=args.max_mz,
                    snr_threshold=args.snr,
                    noise_window_mz=args.noise_window,
                    min_width_points=args.min_width,
                    top_n=args.top_n,
                )
            else:
                init_peaks = pick_peaks_numpy(
                    x, y,
                    min_mz=args.min_mz, max_mz=args.max_mz,
                    min_rel_height=args.min_rel_height, top_n=args.top_n,
                )
        init_peaks_simple = [{"mz": p["mz"], "intensity": p["intensity"]} for p in init_peaks]
        print(f"  自動ピーク: {len(init_peaks_simple)} 件")

        spectra.append({
            "name": button_name(measurement_dir),
            "x": xf,
            "y": yf,
            "initPeaks": init_peaks_simple,
        })

    if not spectra:
        sys.exit("[ERR] 有効なスペクトルが1件もありませんでした")

    # HTML生成
    first_name = spectra[0]["name"]
    with spinner("HTML を生成中...", "HTML を生成しました"):
        widget_js_path = Path(__file__).parent / ".feedback_widget.js"
        feedback_js = widget_js_path.read_text(encoding="utf-8") if widget_js_path.exists() else ""
        html = (HTML_TEMPLATE
                .replace("{title}", first_name)
                .replace("{spectra_json}", json.dumps(spectra))
                .replace("{feedback_widget_js}", feedback_js)
                .replace("{update_banner}", update_banner)
                .replace("{plotly_js}", _PLOTLY_FILE.read_text(encoding="utf-8")))
        html_out_path.write_text(html, encoding="utf-8")

    # ブラウザで開く
    with spinner("ブラウザで開いています...", "ブラウザで開きました"):
        import webbrowser
        webbrowser.open(html_out_path.resolve().as_uri())
        time.sleep(1.5)

    # HTML を html/ フォルダへ移動
    html_dir = save_dir / "html"
    html_dir.mkdir(exist_ok=True)
    new_html_path = html_dir / html_out_path.name
    shutil.move(str(html_out_path), str(new_html_path))
    print(f"✓ HTML → html/{html_out_path.name}")

    # pictures/ フォルダを用意（化合物構造画像の置き場）
    (save_dir / "pictures").mkdir(exist_ok=True)

    # 処理済みデータを archive/ フォルダへ移動し data/ を空にする
    archive_dir = save_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    for input_path in args.input_paths:
        src = Path(input_path).resolve()
        if not src.is_dir():
            continue
        for child in sorted(src.iterdir()):
            if not child.is_dir():
                continue
            dest = archive_dir / child.name
            if dest.exists():
                dest = archive_dir / f"{child.name}_{time.strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(child), str(dest))
            print(f"✓ data/{child.name} → archive/{dest.name}")


if __name__ == "__main__":
    main()
