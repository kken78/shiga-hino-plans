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
