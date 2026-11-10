/* runtime.js — 共通ランタイム(タブ切替のみ)。冪等・依存ゼロ。 */
(function(){
  "use strict";
  var roots = document.querySelectorAll('.hpv1[data-hp-root]');
  roots.forEach(function(root){
    if (root.dataset.hpInit) return;      /* 再実行に対して冪等 */
    root.dataset.hpInit = '1';
    var tabs = root.querySelectorAll('.tab');
    var panels = root.querySelectorAll('.panel');
    function select(id){
      tabs.forEach(function(t){ t.setAttribute('aria-selected', String(t.dataset.tab === id)); });
      panels.forEach(function(p){ p.classList.toggle('active', p.dataset.panel === id); });
      if (history.replaceState) history.replaceState(null, '', '#' + id);
    }
    tabs.forEach(function(t){
      t.addEventListener('click', function(){ select(t.dataset.tab); });
    });
    var h = location.hash.replace('#','');
    if (h && root.querySelector('.panel[data-panel="' + h.replace(/[^a-z0-9_-]/g,'') + '"]')) select(h);
  });
})();

/* 折れ線の指した時点の値(DESIGN.md ADR-18)。値の字は描く道具が表と同じ書き方で作る(data-ln の t)。マウス・指・矢印キーで、その時点の縦線と系列ごとの値を出す。冪等・依存ゼロ。 */
(function(){
  "use strict";
  var NS = 'http://www.w3.org/2000/svg';
  document.querySelectorAll('.hpv1 .ln[data-ln]').forEach(function(ln){
    if (ln.dataset.lnInit) return;
    ln.dataset.lnInit = '1';
    var d = JSON.parse(ln.dataset.ln), n = d.x.length, plot = ln.querySelector('.ln-plot'), svg = plot.querySelector('svg');
    if (!svg || n < 1) return;
    var help = document.createElement('p'); help.className = 'ln-help';
    help.textContent = 'グラフの上にマウスを置くか指で触れると、またはグラフを選んで左右の矢印キーを押すと、その時点の値を表示します。';
    var rd = document.createElement('p'); rd.className = 'ln-read'; rd.setAttribute('aria-live', 'polite');
    /* 値の行はグラフの下に置く(行が増えてもグラフが動かず、指の下からずれないように) */
    ln.parentNode.insertBefore(help, ln); ln.parentNode.insertBefore(rd, ln.nextSibling);
    var g = document.createElementNS(NS, 'g'); svg.appendChild(g);
    plot.tabIndex = 0;
    plot.setAttribute('aria-label', 'グラフ。左右の矢印キーで時点の値を表示します');
    var cur = null;
    function show(i){
      cur = i;
      if (i == null) { rd.textContent = ''; g.textContent = ''; return; }
      var x = (n > 1 ? 1000 * i / (n - 1) : 500).toFixed(1);
      var l = document.createElementNS(NS, 'line');
      [['x1', x], ['x2', x], ['y1', '0'], ['y2', '1000'], ['stroke', 'currentColor'], ['stroke-opacity', '.35'], ['stroke-width', '1'], ['vector-effect', 'non-scaling-stroke']].forEach(function(a){ l.setAttribute(a[0], a[1]); });
      g.textContent = ''; g.appendChild(l);
      rd.textContent = '';
      var b = document.createElement('b'); b.textContent = d.x[i]; rd.appendChild(b);
      d.s.forEach(function(s){
        var sp = document.createElement('span'), sw = document.createElement('i');
        sw.className = 'sw'; sw.style.background = s.c; sp.appendChild(sw);
        sp.appendChild(document.createTextNode(s.k + ' ' + (s.t[i] === '' ? '値なし' : s.t[i] + d.u)));
        rd.appendChild(sp);
      });
    }
    function at(e){ var r = svg.getBoundingClientRect(); if (!r.width) return 0; return Math.max(0, Math.min(n - 1, Math.round((e.clientX - r.left) / r.width * (n - 1)))); }
    plot.addEventListener('pointermove', function(e){ show(at(e)); });
    plot.addEventListener('pointerdown', function(e){ show(at(e)); });
    plot.addEventListener('pointerleave', function(e){ if (e.pointerType === 'mouse') show(null); });
    plot.addEventListener('blur', function(){ show(null); });
    plot.addEventListener('keydown', function(e){
      var t = e.key === 'ArrowLeft' ? (cur == null ? n - 1 : cur - 1) : e.key === 'ArrowRight' ? (cur == null ? n - 1 : cur + 1) : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : null;
      if (t == null) return; e.preventDefault(); show(Math.max(0, Math.min(n - 1, t)));
    });
  });
})();
