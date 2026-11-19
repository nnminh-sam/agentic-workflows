/* Kaneki Desktop icon set: 24×24 line icons, 1.75 stroke, round caps, drawn for this system.
   Injects one hidden <svg> sprite so pages can write <svg class="k-icon"><use href="#i-play"/></svg>
   (works from file:// too, unlike an external sprite). window.KanekiIcons lists the names. */
(function () {
  var P = {
    play: '<path d="M8 5.5v13l10.5-6.5z"/>',
    pause: '<path d="M9 5v14M15 5v14"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    search: '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    'chevron-right': '<path d="m9 6 6 6-6 6"/>',
    'chevron-up': '<path d="m6 15 6-6 6 6"/>',
    'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    'check-circle': '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.8"/>',
    'x-circle': '<circle cx="12" cy="12" r="8.5"/><path d="m9.5 9.5 5 5M14.5 9.5l-5 5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    file: '<path d="M14 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5M9 13h6M9 16.5h4"/>',
    external: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    reopen: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4.5V9h4.5"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="m7 10 3 2.5L7 15M12.5 15H17"/>',
    branch: '<circle cx="6.5" cy="5.5" r="2"/><circle cx="6.5" cy="18.5" r="2"/><circle cx="17.5" cy="7.5" r="2"/><path d="M6.5 7.5v9M17.5 9.5c0 4-4 4.5-11 7"/>',
    coins: '<ellipse cx="9" cy="7" rx="5" ry="2.5"/><path d="M4 7v4c0 1.4 2.2 2.5 5 2.5s5-1.1 5-2.5V7"/><path d="M10 15.5c.5 1.2 2.5 2 5 2 2.8 0 5-1.1 5-2.5v-4c0-1.2-1.6-2.2-3.8-2.4"/>',
    list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    layers: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/>',
    more: '<path d="M6 12h.01M12 12h.01M18 12h.01"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.5 12h-2M21.5 12h-2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
    command: '<path d="M9 6.5A2.5 2.5 0 1 0 6.5 9H9zM15 6.5A2.5 2.5 0 1 1 17.5 9H15zM9 17.5A2.5 2.5 0 1 1 6.5 15H9zM15 17.5a2.5 2.5 0 1 0 2.5-2.5H15z"/><path d="M9 9h6v6H9z"/>',
    agent: '<rect x="6" y="7" width="12" height="11" rx="3"/><path d="M12 7V4M9.5 12h.01M14.5 12h.01M9.5 15h5M3.5 11.5v3M20.5 11.5v3"/>',
    activity: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
    filter: '<path d="M4 5.5h16l-6 7.5v5l-4 1.5v-6.5z"/>',
    folder: '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.5l2 2.5H19a1.5 1.5 0 0 1 1.5 1.5v7.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    gate: '<path d="M5 20V9a7 7 0 0 1 14 0v11"/><path d="M3.5 20h17M12 13v3"/>',
    pipeline: '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h3M14 12h3"/>',
    zed: '<path d="M5 5.5h14L5 18.5h14"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14.5h10"/>',
    spark: '<path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'
  };
  var NS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('style', 'position:absolute;width:0;height:0;overflow:hidden');
  svg.innerHTML = Object.keys(P).map(function (k) {
    return '<symbol id="i-' + k + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' + P[k] + '</symbol>';
  }).join('');
  function inject() { document.body.insertBefore(svg, document.body.firstChild); }
  if (document.body) inject(); else document.addEventListener('DOMContentLoaded', inject);
  window.KanekiIcons = Object.keys(P);
  window.kIcon = function (name, cls) {
    return '<svg class="k-icon' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  };
})();
