/* path.js — marks the learner's path on every page. build.py prepends `var LEARNING_PATH = {...}`:
   { chapters: {file: [step, ...]}, questions: {qid: step} }. Runs after app.js has built the sidebar. */
document.addEventListener('DOMContentLoaded', function () {
  var P = window.LEARNING_PATH || { chapters: {}, questions: {} };
  var here = location.pathname.split('/').pop() || 'index.html';

  var nav = document.querySelector('.sidebar nav');
  if (nav) {
    nav.insertAdjacentHTML('afterbegin',
      '<div class="sect-label">Your path</div><ol><li><a href="path.html"' +
      (here === 'path.html' ? ' class="active"' : '') + '><span class="num">&#9733;</span>Your learning path</a></li></ol>');
  }
  document.querySelectorAll('a[data-navfor]').forEach(function (a) {
    var s = P.chapters[a.getAttribute('data-navfor')];
    if (s) { a.classList.add('on-path'); a.title = 'On your path: step ' + s.join(', '); }
  });

  document.querySelectorAll('.q[data-q]').forEach(function (q) {
    var id = q.getAttribute('data-q');
    if (!q.id) q.id = id;
    var s = P.questions[id], head = q.querySelector('.q-head');
    if (s && head) {
      q.classList.add('on-path');
      head.insertAdjacentHTML('beforeend', '<a class="path-badge" href="path.html#step-' + s + '">path &middot; step ' + s + '</a>');
    }
  });

  // ids above (and heading ids from app.js) exist only now, so the browser's own hash jump missed them
  if (location.hash) {
    var t = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (t) t.scrollIntoView();
  }
});
