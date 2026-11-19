/* Component registry for gallery.html: one entry per component, each with live examples (HTML that uses
   components.css only), the classes it defines, and its do/don't rules. Sample data: ticket TODO-1-2 (2026-11-17). */
(function () {
  var I = function (n, c) { return window.kIcon(n, c); };
  var C = [];
  function add(o) { C.push(o); }

  /* ------------------------------------------------------------------ Actions */
  add({ id: 'button', group: 'Actions', name: 'Button', classes: ['k-btn', 'k-btn--primary', 'k-btn--attention', 'k-btn--danger', 'k-btn--ghost', 'k-btn--sm', 'k-btn--lg'],
    desc: 'Starts an action. One primary button per view: the next step the user is most likely to take.',
    examples: [
      { title: 'Variants', html:
'<div class="g-row">\n  <button class="k-btn k-btn--primary">' + I('play') + 'Run next node</button>\n  <button class="k-btn">' + I('sliders') + 'Run policy</button>\n  <button class="k-btn k-btn--attention">' + I('gate') + 'Review gate</button>\n  <button class="k-btn k-btn--danger">' + I('stop') + 'Stop session</button>\n  <button class="k-btn k-btn--ghost">Cancel</button>\n</div>' },
      { title: 'Sizes', html:
'<div class="g-row">\n  <button class="k-btn k-btn--primary k-btn--sm">Small</button>\n  <button class="k-btn k-btn--primary">Medium</button>\n  <button class="k-btn k-btn--primary k-btn--lg">Large · Confirm</button>\n</div>' },
      { title: 'States', html:
'<div class="g-row">\n  <button class="k-btn k-btn--primary">Rest</button>\n  <button class="k-btn k-btn--primary is-hover">Hover</button>\n  <button class="k-btn k-btn--primary is-focus">Focus</button>\n  <button class="k-btn k-btn--primary is-active">Pressed</button>\n  <button class="k-btn k-btn--primary" disabled>Disabled</button>\n</div>\n<div class="g-row">\n  <button class="k-btn">Rest</button>\n  <button class="k-btn is-hover">Hover</button>\n  <button class="k-btn is-focus">Focus</button>\n  <button class="k-btn" disabled>Disabled</button>\n</div>' },
      { title: 'With shortcut', html:
'<button class="k-btn k-btn--primary k-btn--lg">Confirm<span class="k-btn__kbd">Ctrl ↵</span></button>' }
    ],
    dos: ['Name the action with a verb and its object: "Run next node", "Review gate".', 'Use <code>k-btn--attention</code> only for the button that answers something waiting on the user.', 'Use <code>k-btn--danger</code> for actions that stop or discard work.'],
    donts: ['Two primary buttons side by side.', 'A button that changes ticket state without opening a command preview first.'] });

  add({ id: 'icon-button', group: 'Actions', name: 'Icon button', classes: ['k-btn--icon'],
    desc: 'A square button with only an icon, for compact toolbars and row actions. Always has an aria-label and a tooltip.',
    examples: [{ title: 'Variants and sizes', html:
'<div class="g-row">\n  <button class="k-btn k-btn--icon" aria-label="Settings">' + I('sliders') + '</button>\n  <button class="k-btn k-btn--icon k-btn--ghost" aria-label="More">' + I('more') + '</button>\n  <button class="k-btn k-btn--icon k-btn--ghost k-btn--sm" aria-label="Close">' + I('x', 'k-icon--sm') + '</button>\n  <button class="k-btn k-btn--icon k-btn--primary" aria-label="Run">' + I('play') + '</button>\n  <button class="k-btn k-btn--icon is-focus" aria-label="Copy">' + I('copy') + '</button>\n</div>' }],
    dos: ['Give every icon button an <code>aria-label</code>.'], donts: ['Icon-only buttons for actions that change ticket state.'] });

  add({ id: 'button-group', group: 'Actions', name: 'Button group', classes: ['k-btn-group'],
    desc: 'Joins related buttons that act on the same thing.',
    examples: [{ title: 'Session actions', html:
'<div class="k-btn-group">\n  <button class="k-btn k-btn--sm">' + I('terminal', 'k-icon--sm') + 'Focus</button>\n  <button class="k-btn k-btn--sm">' + I('pause', 'k-icon--sm') + 'Pause</button>\n  <button class="k-btn k-btn--sm">' + I('stop', 'k-icon--sm') + 'Stop</button>\n</div>' }],
    dos: ['Keep groups to 2–4 buttons of the same size.'], donts: ['Mix primary and secondary buttons in one group.'] });

  add({ id: 'link', group: 'Actions', name: 'Link', classes: ['k-link'],
    desc: 'Inline navigation: open a file in Zed, jump to a node.',
    examples: [{ title: 'Inline', html:
'<p style="margin:0">Evidence is in <a class="k-link" href="#">run-report.md</a>; open it in <a class="k-link is-hover" href="#">Zed ' + I('external', 'k-icon--sm') + '</a>.</p>' }],
    dos: ['Use links for navigation, buttons for actions.'], donts: ['Links that run commands.'] });

  add({ id: 'kbd', group: 'Actions', name: 'Kbd and command trigger', classes: ['k-kbd', 'k-cmdk'],
    desc: 'Shows a keyboard shortcut; the command trigger opens the palette from the top bar.',
    examples: [
      { title: 'Keys', html: '<div class="g-row"><span class="k-kbd">Ctrl</span><span class="k-kbd">K</span><span class="k-kbd">↵</span><span class="k-kbd">Esc</span><span class="k-kbd">⇧</span></div>' },
      { title: 'Command trigger', html:
'<div class="g-row">\n  <button class="k-cmdk">' + I('search') + '<span class="k-cmdk__text">Search or run a command</span><span class="k-kbd">Ctrl</span><span class="k-kbd">K</span></button>\n  <button class="k-cmdk is-hover">' + I('search') + '<span class="k-cmdk__text">Hover</span><span class="k-kbd">Ctrl</span><span class="k-kbd">K</span></button>\n</div>' }],
    dos: ['Show the shortcut wherever the action has one.'], donts: ['Invent shortcuts that clash with Zed or Windows.'] });

  /* ------------------------------------------------------------------ Inputs */
  add({ id: 'input', group: 'Inputs', name: 'Text input', classes: ['k-field', 'k-label', 'k-input', 'k-input-wrap', 'k-hint', 'k-error'],
    desc: 'Single-line text with a label above it. Hints say what to type; errors say what is wrong and how to fix it.',
    examples: [{ title: 'States', html:
'<div class="g-grid2">\n  <label class="k-field"><span class="k-label">Ticket id</span><input class="k-input" placeholder="e.g. TODO-5"><span class="k-hint">Letters, digits and dashes.</span></label>\n  <label class="k-field"><span class="k-label">Search tickets</span><span class="k-input-wrap">' + I('search') + '<input class="k-input" value="foundation"></span></label>\n  <label class="k-field"><span class="k-label">Focused</span><input class="k-input is-focus" value="TODO-1-2"></label>\n  <label class="k-field"><span class="k-label">Reason</span><input class="k-input is-invalid" value=""><span class="k-error">' + I('alert', 'k-icon--sm') + 'invalidate needs a reason.</span></label>\n  <label class="k-field"><span class="k-label">Disabled</span><input class="k-input" value="locked by policy" disabled></label>\n  <label class="k-field"><span class="k-label">Small</span><input class="k-input k-input--sm" value="2"></label>\n</div>' }],
    dos: ['Always a visible label; placeholder is only an example.'], donts: ['Red borders without an error message.'] });

  add({ id: 'textarea', group: 'Inputs', name: 'Textarea', classes: ['k-textarea'],
    desc: 'Multi-line note, e.g. the optional gate note. Works with Vietnamese Telex input.',
    examples: [{ title: 'Gate note', html:
'<label class="k-field" style="max-width:520px"><span class="k-label">Note <span class="k-muted">(optional)</span></span><textarea class="k-textarea">wording NGs accepted; tracked for TODO-2</textarea><span class="k-hint">Saved as --note in decisions.jsonl.</span></label>' }],
    dos: ['Say where the text ends up.'], donts: ['Make a note required when the CLI does not require one.'] });

  add({ id: 'select', group: 'Inputs', name: 'Select', classes: ['k-select-wrap', 'k-select'],
    desc: 'Picks one value from a short list, such as the node to reopen.',
    examples: [{ title: 'Default', html:
'<label class="k-field" style="max-width:280px"><span class="k-label">Reopen node</span><span class="k-select-wrap"><select class="k-select"><option>testcase</option><option>coding</option><option>feature-plan</option></select>' + I('chevron-down') + '</span></label>' }],
    dos: ['Order options the way the pipeline runs.'], donts: ['Selects with more than ~12 options: use the palette.'] });

  add({ id: 'stepper', group: 'Inputs', name: 'Number stepper', classes: ['k-stepper'],
    desc: 'Small whole numbers with a known range: loop cap, escalation threshold.',
    examples: [{ title: 'Default', html:
'<div class="k-stepper"><button aria-label="Less">' + I('minus', 'k-icon--sm') + '</button><input value="2" aria-label="Loop cap"><button aria-label="More">' + I('plus', 'k-icon--sm') + '</button></div>' }],
    dos: ['Use for values from 0 to about 10.'], donts: ['Use for money or token counts.'] });

  add({ id: 'check', group: 'Inputs', name: 'Checkbox and radio', classes: ['k-check', 'k-check__sub'],
    desc: 'Checkbox for independent options; radio for one choice out of a few.',
    examples: [{ title: 'States', html:
'<div class="g-grid2">\n  <div style="display:grid;gap:10px">\n    <label class="k-check"><input type="checkbox" checked><span>Stop at deliverable-ready</span></label>\n    <label class="k-check"><input type="checkbox"><span>Auto-run after a pass<span class="k-check__sub">Starts the next node in a fresh session.</span></span></label>\n    <label class="k-check"><input type="checkbox" checked disabled><span>Never ship (locked)</span></label>\n  </div>\n  <div style="display:grid;gap:10px">\n    <label class="k-check"><input type="radio" name="r" checked><span>One node per session</span></label>\n    <label class="k-check"><input type="radio" name="r"><span>One session per run</span></label>\n  </div>\n</div>' }],
    dos: ['Click the whole label, not just the box.'], donts: ['Use a checkbox for an action that happens right away: use a switch.'] });

  add({ id: 'switch', group: 'Inputs', name: 'Switch', classes: ['k-switch', 'is-on', 'is-locked'],
    desc: 'Turns a setting on or off immediately. A locked switch is shown on and dimmed, with the reason next to it.',
    examples: [{ title: 'States', html:
'<div class="g-row">\n  <input type="checkbox" class="k-switch" checked aria-label="On">\n  <input type="checkbox" class="k-switch" aria-label="Off">\n  <input type="checkbox" class="k-switch is-focus" checked aria-label="Focus">\n  <input type="checkbox" class="k-switch" checked disabled aria-label="Locked">\n  <span class="k-muted" style="font-size:12px">' + I('lock', 'k-icon--sm') + ' Locked on in this workspace</span>\n</div>' }],
    dos: ['Pair with a label that reads true when on.'], donts: ['Use for choices that need a confirm step.'] });

  add({ id: 'segmented', group: 'Inputs', name: 'Segmented control', classes: ['k-segmented', 'is-selected'],
    desc: 'Switches between 2–4 views or modes of the same thing.',
    examples: [{ title: 'Modes', html:
'<div class="g-row">\n  <div class="k-segmented"><button class="is-selected">One node per session</button><button>One session per run</button></div>\n  <div class="k-segmented"><button class="is-selected">' + I('sun', 'k-icon--sm') + 'Light</button><button>' + I('moon', 'k-icon--sm') + 'Dark</button></div>\n</div>' }],
    dos: ['Keep labels short and parallel.'], donts: ['More than 4 segments.'] });

  add({ id: 'choice', group: 'Inputs', name: 'Choice card', classes: ['k-choice', 'is-selected'],
    desc: 'A radio option with a title and an explanation, for decisions with consequences (gate approve/reject).',
    examples: [{ title: 'Gate decision', html:
'<div style="display:grid;gap:8px;max-width:560px">\n  <label class="k-choice"><input type="radio" name="g"><span><span class="k-choice__title">Approve</span><br><span class="k-choice__sub">Accept the 2 wording NGs and continue to final-review.</span></span></label>\n  <label class="k-choice is-selected"><input type="radio" name="g" checked><span><span class="k-choice__title">Reject and reopen</span><br><span class="k-choice__sub">Sends the ticket back; later nodes become stale.</span></span></label>\n</div>' }],
    dos: ['Say what happens next in the sub line.'], donts: ['Pre-select the destructive option.'] });

  /* ------------------------------------------------------------------ Status */
  add({ id: 'badge', group: 'Status', name: 'Badge', classes: ['k-badge', 'k-badge--outline', 'k-badge--accent', 'k-badge--ok', 'k-badge--warn', 'k-badge--bad'],
    desc: 'A short label for a property: lane, generation, event kind.',
    examples: [{ title: 'Tones', html:
'<div class="g-row">\n  <span class="k-badge">full lane</span>\n  <span class="k-badge k-badge--outline">generation 2</span>\n  <span class="k-badge k-badge--accent">record</span>\n  <span class="k-badge k-badge--ok">gate approved</span>\n  <span class="k-badge k-badge--warn"><span class="k-dot k-dot--attention"></span>needs you</span>\n  <span class="k-badge k-badge--bad">invalidate</span>\n</div>' }],
    dos: ['One or two words.'], donts: ['Badges as buttons.'] });

  add({ id: 'tier', group: 'Status', name: 'Tier badge', classes: ['k-tier', 'k-tier--haiku', 'k-tier--sonnet', 'k-tier--opus', 'k-tier__model'],
    desc: 'Which tier ran a unit and which model the profile mapped it to. Tier first, model second.',
    examples: [{ title: 'Tiers', html:
'<div class="g-row">\n  <span class="k-tier k-tier--haiku">light <span class="k-tier__model">· haiku</span></span>\n  <span class="k-tier k-tier--sonnet">routine <span class="k-tier__model">· sonnet</span></span>\n  <span class="k-tier k-tier--opus">deep <span class="k-tier__model">· opus</span></span>\n</div>' }],
    dos: ['Read <code>tier · model</code>.'], donts: ['Use model colours for anything else but tiers and cost.'] });

  add({ id: 'status', group: 'Status', name: 'Status dot and label', classes: ['k-dot', 'k-dot--attention', 'k-dot--run', 'k-dot--ok', 'k-dot--bad', 'k-dot--idle', 'k-status'],
    desc: 'Where a ticket or session stands. The dot is never alone: a label always says the state.',
    examples: [{ title: 'States', html:
'<div class="g-row" style="gap:20px">\n  <span class="k-status k-status--attention"><span class="k-dot k-dot--attention"></span>Waiting on you</span>\n  <span class="k-status k-status--run"><span class="k-dot k-dot--run"></span>Running</span>\n  <span class="k-status"><span class="k-dot k-dot--ok"></span>Done</span>\n  <span class="k-status"><span class="k-dot k-dot--bad"></span>Failed</span>\n  <span class="k-status"><span class="k-dot k-dot--idle"></span>Idle</span>\n</div>' }],
    dos: ['Amber only for "waiting on you".'], donts: ['A dot without a label.'] });

  add({ id: 'pill', group: 'Status', name: 'Attention pill', classes: ['k-pill', 'k-pill--attention', 'k-pill__count'],
    desc: 'The top bar\'s count of decisions waiting on the user; clicking it opens the oldest one.',
    examples: [{ title: 'Count', html:
'<div class="g-row">\n  <button class="k-pill k-pill--attention"><span class="k-pill__count">1</span>gate needs you</button>\n  <button class="k-pill k-pill--attention"><span class="k-pill__count">3</span>decisions need you</button>\n</div>' }],
    dos: ['Hide it when the count is 0.'], donts: ['Use it for anything that is not waiting on the user.'] });

  add({ id: 'verdict', group: 'Status', name: 'Verdict and delta', classes: ['k-verdict', 'k-verdict--pass', 'k-verdict--fail', 'k-verdict--pending', 'k-delta', 'k-delta--better', 'k-delta--worse'],
    desc: 'A reviewer verdict with an icon and its word; a delta compares two runs.',
    examples: [{ title: 'Verdicts and deltas', html:
'<div class="g-row" style="gap:20px">\n  <span class="k-verdict k-verdict--pass">' + I('check-circle', 'k-icon--sm') + 'PASS</span>\n  <span class="k-verdict k-verdict--pass">' + I('check-circle', 'k-icon--sm') + 'APPROVED</span>\n  <span class="k-verdict k-verdict--fail">' + I('x-circle', 'k-icon--sm') + 'FAIL</span>\n  <span class="k-verdict k-verdict--pending">' + I('clock', 'k-icon--sm') + 'recorded</span>\n  <span class="k-delta k-delta--better">−18%</span>\n  <span class="k-delta k-delta--worse">+103%</span>\n</div>' }],
    dos: ['Keep the harness word (PASS, NEEDS CHANGES) verbatim.'], donts: ['Colour without the word.'] });

  add({ id: 'progress', group: 'Status', name: 'Progress', classes: ['k-segments', 'k-progress', 'k-spinner', 'k-skeleton'],
    desc: 'Segments show a ticket\'s nodes at a glance; a bar shows a known fraction; a spinner and skeleton show loading.',
    examples: [{ title: 'Kinds', html:
'<div style="display:grid;gap:16px;max-width:420px">\n  <div class="k-segments"><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-attention"></i><i></i><i></i></div>\n  <div class="k-segments"><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-run"></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>\n  <div class="k-progress"><i style="width:62%"></i></div>\n  <div class="g-row"><span class="k-spinner"></span><span class="k-muted">Reading decisions.jsonl…</span></div>\n  <div style="display:grid;gap:8px"><div class="k-skeleton" style="width:60%"></div><div class="k-skeleton" style="width:85%"></div><div class="k-skeleton" style="width:40%"></div></div>\n</div>' }],
    dos: ['One segment per node in the trace.'], donts: ['A spinner for more than a few seconds without text.'] });

  /* ------------------------------------------------------------------ Navigation */
  add({ id: 'tabs', group: 'Navigation', name: 'Tabs', classes: ['k-tabs', 'k-tab', 'k-tab__count', 'is-selected'],
    desc: 'Switches between views of one ticket.',
    examples: [{ title: 'Ticket views', html:
'<nav class="k-tabs">\n  <button class="k-tab is-selected">' + I('pipeline', 'k-icon--sm') + 'Pipeline</button>\n  <button class="k-tab">' + I('coins', 'k-icon--sm') + 'Cost</button>\n  <button class="k-tab is-hover">' + I('list', 'k-icon--sm') + 'Decisions <span class="k-tab__count">24</span></button>\n</nav>' }],
    dos: ['Keep the selected tab when switching tickets.'], donts: ['Tabs that start actions.'] });

  add({ id: 'ticket', group: 'Navigation', name: 'Ticket row', classes: ['k-ticket', 'k-ticket__head', 'k-ticket__count', 'k-ticket__title', 'k-ticket__who', 'is-selected'],
    desc: 'One ticket in the rail: id, title, progress, and who it is waiting on.',
    examples: [{ title: 'States', html:
'<div style="display:grid;gap:2px;max-width:280px;padding:8px;border-radius:12px;background:var(--canvas)">\n  <div class="k-ticket is-selected"><div class="k-ticket__head"><span class="k-dot k-dot--attention"></span>TODO-1-2<span class="k-ticket__count">9/12</span></div><div class="k-ticket__title">Foundation (tiered redo)</div><div class="k-segments"><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-attention"></i><i></i><i></i></div><div class="k-ticket__who"><b>You</b> · gate on execute-and-evidence</div></div>\n  <div class="k-ticket is-hover"><div class="k-ticket__head"><span class="k-dot k-dot--run"></span>TODO-2<span class="k-ticket__count">3/12</span></div><div class="k-ticket__title">Auth &amp; users</div><div class="k-segments"><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-run"></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><div class="k-ticket__who">Agent · feature-plan</div></div>\n  <div class="k-ticket"><div class="k-ticket__head"><span class="k-dot k-dot--ok"></span>TODO-1<span class="k-ticket__count">12/12</span></div><div class="k-ticket__title">Foundation</div><div class="k-segments"><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i><i class="is-ok"></i></div><div class="k-ticket__who">Shipped 2026-11-17</div></div>\n</div>' }],
    dos: ['Sort tickets waiting on the user first.'], donts: ['Truncate the ticket id.'] });

  add({ id: 'pipeline', group: 'Navigation', name: 'Pipeline', classes: ['k-pipeline', 'k-node', 'k-node--done', 'k-node--run', 'k-node--attention', 'k-node--fail', 'k-node--stale', 'k-node__gen', 'k-edge', 'k-loopnote'],
    desc: 'The ticket\'s trace, node by node. Each state has its own icon, so the strip reads without colour.',
    examples: [
      { title: 'Every node state', wide: true, html:
'<div class="k-pipeline">\n  <button class="k-node k-node--done"><span class="k-node__dot">' + I('check') + '</span><span class="k-node__label">testcase</span></button><span class="k-edge k-edge--done"></span>\n  <button class="k-node k-node--done"><span class="k-node__dot">' + I('check') + '<span class="k-node__gen">g2</span></span><span class="k-node__label">code-review</span></button><span class="k-edge k-edge--done"></span>\n  <button class="k-node k-node--run"><span class="k-node__dot">' + I('activity') + '</span><span class="k-node__label">execute-and-evidence</span></button><span class="k-edge"></span>\n  <button class="k-node k-node--attention is-selected"><span class="k-node__dot">' + I('gate') + '</span><span class="k-node__label">waiting on you</span></button><span class="k-edge"></span>\n  <button class="k-node k-node--fail"><span class="k-node__dot">' + I('x') + '</span><span class="k-node__label">rejected</span></button><span class="k-edge"></span>\n  <button class="k-node k-node--stale"><span class="k-node__dot">' + I('reopen') + '</span><span class="k-node__label">stale</span></button><span class="k-edge"></span>\n  <button class="k-node"><span class="k-node__dot">11</span><span class="k-node__label">pending</span></button>\n</div>' },
      { title: 'Loop-back note', html: '<div class="k-loopnote">' + I('reopen', 'k-icon--sm') + '<b>execute-and-evidence g1</b> rejected → coding reopened · 2026-11-17 08:54 UTC</div>' }],
    dos: ['Show the generation on any node that ran more than once.'], donts: ['Hide pending nodes: the user needs to see what is left.'] });

  add({ id: 'breadcrumb', group: 'Navigation', name: 'Breadcrumb', classes: ['k-breadcrumb'],
    desc: 'Where the view sits: workspace, ticket, node.',
    examples: [{ title: 'Default', html: '<div class="k-breadcrumb">todo-nestjs-backend ' + I('chevron-right', 'k-icon--sm') + ' TODO-1-2 ' + I('chevron-right', 'k-icon--sm') + ' <b>execute-and-evidence</b></div>' }],
    dos: ['Each part is clickable except the last.'], donts: ['More than 4 levels.'] });

  /* ------------------------------------------------------------------ Data display */
  add({ id: 'card', group: 'Data display', name: 'Card', classes: ['k-card', 'k-card__head', 'k-card__title', 'k-card__body', 'k-card__foot'],
    desc: 'Groups one subject: a node, a cost breakdown. Head, body and an optional foot for actions.',
    examples: [{ title: 'Node card', wide: true, html:
'<article class="k-card" style="max-width:640px">\n  <header class="k-card__head"><h4 class="k-card__title">execute-and-evidence</h4><span class="k-badge k-badge--outline">generation 2</span><span class="k-spacer"></span><button class="k-btn k-btn--ghost k-btn--icon k-btn--sm" aria-label="More">' + I('more', 'k-icon--sm') + '</button></header>\n  <div class="k-card__body"><dl class="k-kv"><dt>Units</dt><dd>worker · reviewer</dd><dt>Verdict</dt><dd><span class="k-verdict k-verdict--pass">' + I('check-circle', 'k-icon--sm') + 'PASS</span></dd><dt>Tokens</dt><dd class="k-num">202,710</dd></dl></div>\n  <footer class="k-card__foot"><span class="k-muted" style="font-size:12px">Updated 09:52 UTC</span><span class="k-spacer"></span><button class="k-btn k-btn--sm">' + I('file', 'k-icon--sm') + 'Open run-report</button></footer>\n</article>' }],
    dos: ['One subject per card.'], donts: ['Cards inside cards.'] });

  add({ id: 'stat', group: 'Data display', name: 'Stat tile', classes: ['k-stats', 'k-stat', 'k-stat__label', 'k-stat__value', 'k-stat__meta', 'k-stat--attention'],
    desc: 'One headline number with what it measures. Used on the Cost tab.',
    examples: [{ title: 'Cost headline', wide: true, html:
'<div class="k-stats">\n  <div class="k-stat"><span class="k-stat__label">' + I('coins', 'k-icon--sm') + 'Real cost</span><span class="k-stat__value">$4.81</span><span class="k-stat__meta"><span class="k-delta k-delta--better">−18%</span> vs TODO-1</span></div>\n  <div class="k-stat"><span class="k-stat__label">' + I('activity', 'k-icon--sm') + 'Tokens processed</span><span class="k-stat__value">22.4M</span><span class="k-stat__meta">session logs</span></div>\n  <div class="k-stat k-stat--attention"><span class="k-stat__label">' + I('alert', 'k-icon--sm') + 'Harness count</span><span class="k-stat__value">1.39M</span><span class="k-stat__meta">6% of real</span></div>\n  <div class="k-stat"><span class="k-stat__label">' + I('reopen', 'k-icon--sm') + 'Fix loops</span><span class="k-stat__value">1</span><span class="k-stat__meta">so far</span></div>\n</div>' }],
    dos: ['Say the source of the number in the meta line.'], donts: ['More than 4 tiles in a row.'] });

  add({ id: 'table', group: 'Data display', name: 'Table', classes: ['k-table-wrap', 'k-table', 'k-table--compact', 'is-num', 'is-selected'],
    desc: 'Rows of the same kind with comparable columns. Numbers right-aligned in tabular figures; header sticks while scrolling.',
    examples: [{ title: 'Units of a node', wide: true, html:
'<div class="k-table-wrap">\n<table class="k-table">\n  <thead><tr><th>Unit</th><th>Tier · model</th><th>Verdict</th><th class="is-num">Tokens</th><th class="is-num">Rounds</th></tr></thead>\n  <tbody>\n    <tr><td>worker</td><td><span class="k-tier k-tier--sonnet">routine <span class="k-tier__model">· sonnet</span></span></td><td><span class="k-verdict k-verdict--pending">' + I('clock', 'k-icon--sm') + 'recorded</span></td><td class="is-num">157,309</td><td class="is-num">2</td></tr>\n    <tr class="is-selected"><td>reviewer</td><td><span class="k-tier k-tier--sonnet">routine <span class="k-tier__model">· sonnet</span></span></td><td><span class="k-verdict k-verdict--pass">' + I('check-circle', 'k-icon--sm') + 'PASS</span></td><td class="is-num">45,401</td><td class="is-num">1</td></tr>\n    <tr class="is-hover"><td>semantic-review</td><td><span class="k-tier k-tier--opus">deep <span class="k-tier__model">· opus</span></span></td><td><span class="k-verdict k-verdict--fail">' + I('x-circle', 'k-icon--sm') + 'FAIL</span></td><td class="is-num">154,030</td><td class="is-num">2</td></tr>\n  </tbody>\n</table>\n</div>' },
      { title: 'Compare runs (compact)', wide: true, html:
'<div class="k-table-wrap">\n<table class="k-table k-table--compact">\n  <thead><tr><th>Measure</th><th class="is-num">TODO-1</th><th class="is-num">TODO-1-2</th><th class="is-num">Change</th></tr></thead>\n  <tbody>\n    <tr><td>Cost to first coding gate</td><td class="is-num">$4.93</td><td class="is-num">$4.03</td><td class="is-num"><span class="k-delta k-delta--better">−18%</span></td></tr>\n    <tr><td>Tokens to first coding gate</td><td class="is-num">8.0M</td><td class="is-num">16.2M</td><td class="is-num"><span class="k-delta k-delta--worse">+103%</span></td></tr>\n    <tr><td>Fix loops</td><td class="is-num">3</td><td class="is-num">1</td><td class="is-num">—</td></tr>\n  </tbody>\n</table>\n</div>' }],
    dos: ['First column is the row\'s name, in ink.'], donts: ['Zebra stripes; hairlines are enough.'] });

  add({ id: 'kv', group: 'Data display', name: 'Key-value list', classes: ['k-kv'],
    desc: 'Properties of one object, label left, value right.',
    examples: [{ title: 'Session', html:
'<dl class="k-kv">\n  <dt>Session</dt><dd>todo12-evidence2</dd>\n  <dt>Node</dt><dd>execute-and-evidence · g2</dd>\n  <dt>PID</dt><dd class="k-mono">18324</dd>\n  <dt>Started</dt><dd>2026-11-17 09:38 UTC</dd>\n</dl>' }],
    dos: ['Values that are ids go in mono.'], donts: ['Long paragraphs as values.'] });

  add({ id: 'bars', group: 'Data display', name: 'Cost bars', classes: ['k-bars', 'k-bar', 'k-bar__label', 'k-bar__track', 'k-bar__fill', 'k-bar__value'],
    desc: 'Compares a few amounts. The fill uses the model colour; the value is always printed.',
    examples: [{ title: 'Cost by model (sample)', html:
'<div class="k-bars" style="max-width:520px">\n  <div class="k-bar"><span class="k-bar__label"><span class="k-dot" style="background:var(--opus)"></span>opus</span><span class="k-bar__track"><i class="k-bar__fill" style="width:100%;background:var(--opus)"></i></span><span class="k-bar__value">$2.31</span></div>\n  <div class="k-bar"><span class="k-bar__label"><span class="k-dot" style="background:var(--sonnet)"></span>sonnet</span><span class="k-bar__track"><i class="k-bar__fill" style="width:71%;background:var(--sonnet)"></i></span><span class="k-bar__value">$1.64</span></div>\n  <div class="k-bar"><span class="k-bar__label"><span class="k-dot" style="background:var(--haiku)"></span>haiku</span><span class="k-bar__track"><i class="k-bar__fill" style="width:37%;background:var(--haiku)"></i></span><span class="k-bar__value">$0.86</span></div>\n</div>' }],
    dos: ['Sort largest first.'], donts: ['Bars without values.'] });

  add({ id: 'events', group: 'Data display', name: 'Event timeline', classes: ['k-events', 'k-event', 'k-event__time', 'k-event__who'],
    desc: 'The decision log, newest first: time, kind, what happened, who.',
    examples: [{ title: 'Decisions', wide: true, html:
'<div class="k-events">\n  <div class="k-event"><span class="k-event__time">09:52</span><span class="k-badge k-badge--accent">record</span><span>execute-and-evidence reviewer PASS</span><span class="k-event__who">controller</span></div>\n  <div class="k-event"><span class="k-event__time">08:54</span><span class="k-badge k-badge--bad">invalidate</span><span>coding reopened</span><span class="k-event__who">you</span></div>\n  <div class="k-event"><span class="k-event__time">08:40</span><span class="k-badge k-badge--ok">gate</span><span>testcase-review approved</span><span class="k-event__who">controller</span></div>\n</div>' }],
    dos: ['Say who decided: controller or you.'], donts: ['Hide refusals.'] });

  add({ id: 'log', group: 'Data display', name: 'Live log', classes: ['k-log'],
    desc: 'A compact stream of events for the dock.',
    examples: [{ title: 'Default', html:
'<div class="k-log">\n  <div>09:52 <span class="is-accent">record</span> <b>execute-and-evidence reviewer PASS</b></div>\n  <div>09:31 <span class="is-accent">record</span> <b>execute-and-evidence worker g2</b></div>\n  <div>08:54 <span class="is-bad">invalidate</span> <b>coding reopened</b></div>\n  <div>08:40 <span class="is-ok">gate</span> <b>testcase-review approved</b></div>\n</div>' }],
    dos: ['Times in UTC, as the decision log stores them.'], donts: ['Wrap lines; let them truncate.'] });

  add({ id: 'code', group: 'Data display', name: 'Code block', classes: ['k-code'],
    desc: 'A read-only preview of a file or output (the inspector).',
    examples: [{ title: 'run-report.md preview', html:
'<pre class="k-code"><span class="t-h">## Summary</span>\nPlanned 139 · run 138 · <span class="t-ok">OK 136</span> · <span class="t-bad">NG 2</span> · N/A 1\n\n<span class="t-h">## NG</span>\n<span class="t-bad">TC-TOOL-11</span>  grep "Todo" hits Swagger title  <span class="t-dim">src/app.setup.ts:60</span>\n<span class="t-bad">TC-DOCK-04</span>  /missing/x.db created by root</pre>' }],
    dos: ['Offer "Open in Zed" next to every preview.'], donts: ['Editing in the preview.'] });

  add({ id: 'command', group: 'Data display', name: 'Command preview', classes: ['k-command', 'k-command__head', 'k-command__body'],
    desc: 'The exact CLI command a confirm button will run. Every state change shows one first.',
    examples: [{ title: 'Will run', html:
'<div class="k-command" style="max-width:640px">\n  <div class="k-command__head">' + I('terminal', 'k-icon--sm') + '<span class="k-overline">Will run</span><span class="k-spacer"></span><button class="k-btn k-btn--ghost k-btn--sm">' + I('copy', 'k-icon--sm') + 'Copy</button></div>\n  <pre class="k-command__body"><span class="t-dim">sdlc</span> gate --ticket TODO-1-2 --node execute-and-evidence --status rejected --actor user --reopen testcase --note "wording NGs: fix the two case texts"</pre>\n</div>' }],
    dos: ['Show it before every gate, invalidate or lane change.'], donts: ['Run anything the preview does not show.'] });

  add({ id: 'chip', group: 'Data display', name: 'File chip', classes: ['k-chips', 'k-chip'],
    desc: 'A file that belongs to the node; click to preview it in the inspector.',
    examples: [{ title: 'Node files', html:
'<div class="k-chips">\n  <button class="k-chip">' + I('file') + 'packet.md</button>\n  <button class="k-chip">' + I('file') + 'result.md</button>\n  <button class="k-chip is-hover">' + I('file') + 'run-report.md</button>\n  <button class="k-chip">' + I('file') + 'STATUS.md</button>\n</div>' }],
    dos: ['Use the real file name.'], donts: ['More than one row: use a list.'] });

  add({ id: 'empty', group: 'Data display', name: 'Empty state', classes: ['k-empty', 'k-empty__icon', 'k-empty__title'],
    desc: 'What appears when there is nothing yet, and how to make the first thing.',
    examples: [{ title: 'No tickets', html:
'<div class="k-card"><div class="k-empty"><span class="k-empty__icon">' + I('folder') + '</span><span class="k-empty__title">No tickets in this workspace</span><span>Tickets appear here when <code class="k-code-inline">sdlc init</code> creates them under .sdlc/tickets.</span><button class="k-btn k-btn--primary" style="margin-top:8px">' + I('plus') + 'New ticket</button></div></div>' }],
    dos: ['Name what will appear and the first step.'], donts: ['Jokes or illustrations.'] });

  /* ------------------------------------------------------------------ Feedback */
  add({ id: 'banner', group: 'Feedback', name: 'Banner', classes: ['k-banner', 'k-banner--attention', 'k-banner--ok', 'k-banner--bad', 'k-banner--info'],
    desc: 'A state that applies to the whole card or view, with its action on the right.',
    examples: [{ title: 'Tones', wide: true, html:
'<div style="display:grid;gap:10px">\n  <div class="k-banner k-banner--attention">' + I('gate') + '<div class="k-banner__text"><div class="k-banner__title">Gate waiting for you since 09:52 UTC</div><div class="k-banner__sub">2 NG · reviewer PASS</div></div><button class="k-btn k-btn--attention k-btn--sm">Review gate' + I('arrow-right', 'k-icon--sm') + '</button></div>\n  <div class="k-banner k-banner--ok">' + I('check-circle') + '<div class="k-banner__text"><div class="k-banner__title">Gate approved by you</div></div></div>\n  <div class="k-banner k-banner--bad">' + I('x-circle') + '<div class="k-banner__text"><div class="k-banner__title">Reviewer FAIL on code-review g1</div><div class="k-banner__sub">Coding was reopened automatically.</div></div></div>\n  <div class="k-banner k-banner--info">' + I('info') + '<div class="k-banner__text"><div class="k-banner__title">Lite lane</div><div class="k-banner__sub">No worktree; changes happen in place on the ticket branch.</div></div></div>\n</div>' }],
    dos: ['One banner per card.'], donts: ['Banners for things that pass in a second: use a toast.'] });

  add({ id: 'callout', group: 'Feedback', name: 'Callout', classes: ['k-callout', 'k-callout--info', 'k-callout--warn', 'k-callout--bad', 'k-callout--ok'],
    desc: 'A quiet note inside content: a hint, a caveat.',
    examples: [{ title: 'Tones', html:
'<div style="display:grid;gap:8px;max-width:600px">\n  <div class="k-callout k-callout--info">' + I('info') + '<div><b>Real cost</b> comes from the Claude Code session logs, not the harness count.</div></div>\n  <div class="k-callout k-callout--warn">' + I('alert') + '<div><b>Loop cap reached.</b> Another needs-changes round needs your approval (G4).</div></div>\n</div>' }],
    dos: ['Start with the point in bold.'], donts: ['Stack more than two.'] });

  add({ id: 'toast', group: 'Feedback', name: 'Toast', classes: ['k-toasts', 'k-toast', 'k-toast--attention', 'k-toast--ok', 'k-toast--bad', 'k-toast__icon', 'k-toast__body', 'k-toast__title'],
    desc: 'A short notice from the background: a gate needs you, a session is waiting. Bottom right, above the dock.',
    examples: [{ title: 'Kinds', html:
'<div class="k-toasts">\n  <div class="k-toast k-toast--attention"><span class="k-toast__icon">' + I('gate') + '</span><div class="k-toast__body"><span class="k-toast__title">Gate needs you</span>execute-and-evidence g2 · 2 NG, reviewer PASS</div><button class="k-btn k-btn--ghost k-btn--icon k-btn--sm" aria-label="Dismiss">' + I('x', 'k-icon--sm') + '</button></div>\n  <div class="k-toast k-toast--ok"><span class="k-toast__icon">' + I('check') + '</span><div class="k-toast__body"><span class="k-toast__title">Gate approved</span>testcase-review · controller</div></div>\n  <div class="k-toast k-toast--bad"><span class="k-toast__icon">' + I('x') + '</span><div class="k-toast__body"><span class="k-toast__title">Reviewer FAIL</span>code-review g1 · technical-review</div></div>\n  <div class="k-toast"><span class="k-toast__icon">' + I('terminal') + '</span><div class="k-toast__body"><span class="k-toast__title">Session waiting</span>todo12-evidence2 asks for a permission</div></div>\n</div>' }],
    dos: ['Click a toast to jump to its ticket.'], donts: ['Toasts that need an answer: open the dialog instead.'] });

  add({ id: 'tooltip', group: 'Feedback', name: 'Tooltip', classes: ['k-tooltip'],
    desc: 'The name of an icon button, or a short explanation, on hover or focus.',
    examples: [{ title: 'Default', html: '<div style="padding-top:8px"><span class="k-tooltip">Stop the claude process (PID 18324)</span></div>' }],
    dos: ['One short line.'], donts: ['Information that is only in a tooltip.'] });

  /* ------------------------------------------------------------------ Overlays */
  add({ id: 'menu', group: 'Overlays', name: 'Menu', classes: ['k-menu', 'k-menu__item', 'k-menu__item--danger', 'k-menu__hint', 'k-menu__sep', 'k-menu__label'],
    desc: 'Secondary actions for one object, opened from a "more" button.',
    examples: [{ title: 'Node actions', html:
'<div class="k-menu" style="width:260px">\n  <div class="k-menu__label">execute-and-evidence</div>\n  <button class="k-menu__item">' + I('file') + 'Open packet<span class="k-menu__hint">P</span></button>\n  <button class="k-menu__item is-hover">' + I('external') + 'Open in Zed<span class="k-menu__hint">Ctrl O</span></button>\n  <button class="k-menu__item">' + I('copy') + 'Copy dispatch id</button>\n  <div class="k-menu__sep"></div>\n  <button class="k-menu__item k-menu__item--danger">' + I('reopen') + 'Reopen this node…</button>\n</div>' }],
    dos: ['End destructive items with "…" when they open a confirm.'], donts: ['Nested menus.'] });

  add({ id: 'dialog', group: 'Overlays', name: 'Dialog (gate)', classes: ['k-scrim', 'k-dialog', 'k-dialog__head', 'k-dialog__icon', 'k-dialog__title', 'k-dialog__sub', 'k-dialog__body', 'k-dialog__foot', 'k-evidence', 'k-stale-list'],
    desc: 'The gate decision: question, evidence, choices, what goes stale, note and the command. The core screen of the app.',
    examples: [{ title: 'Gate approval', wide: true, html:
'<div class="k-scrim">\n<div class="k-dialog" role="dialog" aria-label="Gate approval">\n  <header class="k-dialog__head"><span class="k-dialog__icon">' + I('gate') + '</span><div style="flex:1"><h3 class="k-dialog__title">Approve execute-and-evidence?</h3><p class="k-dialog__sub">TODO-1-2 · generation 2 · asked 09:52 UTC</p></div><button class="k-btn k-btn--ghost k-btn--icon k-btn--sm" aria-label="Close">' + I('x', 'k-icon--sm') + '</button></header>\n  <div class="k-dialog__body">\n    <div class="k-evidence"><div class="k-evidence__item k-evidence__item--ok"><span class="k-evidence__value">136</span><span class="k-evidence__label">OK</span></div><div class="k-evidence__item k-evidence__item--bad"><span class="k-evidence__value">2</span><span class="k-evidence__label">NG</span></div><div class="k-evidence__item"><span class="k-evidence__value">1</span><span class="k-evidence__label">N/A</span></div><div class="k-evidence__item"><span class="k-evidence__value" style="font-size:15px;line-height:28px"><span class="k-verdict k-verdict--pass">' + I('check-circle', 'k-icon--sm') + 'PASS</span></span><span class="k-evidence__label">Reviewer · routine</span></div></div>\n    <div class="k-table-wrap"><table class="k-table k-table--compact"><tbody><tr><td class="k-mono">TC-TOOL-11</td><td>grep for "Todo" hits the Swagger title</td><td class="k-muted">testcase wording</td></tr><tr><td class="k-mono">TC-DOCK-04</td><td>/missing/x.db is created by root</td><td class="k-muted">testcase wording</td></tr></tbody></table></div>\n    <div style="display:grid;gap:8px"><label class="k-choice"><input type="radio" name="gd"><span><span class="k-choice__title">Approve</span><br><span class="k-choice__sub">Accept the 2 wording NGs and continue to final-review.</span></span></label><label class="k-choice is-selected"><input type="radio" name="gd" checked><span><span class="k-choice__title">Reject and reopen testcase</span><br><span class="k-choice__sub">Later nodes become stale:</span><span class="k-stale-list">testcase → testcase-review → execute-and-evidence → final-review → delivery-drafts → deliverable-ready</span></span></label></div>\n    <label class="k-field"><span class="k-label">Note <span class="k-muted">(optional)</span></span><textarea class="k-textarea" style="min-height:56px">wording NGs: fix the two case texts</textarea></label>\n    <div class="k-command"><div class="k-command__head">' + I('terminal', 'k-icon--sm') + '<span class="k-overline">Will run</span></div><pre class="k-command__body"><span class="t-dim">sdlc</span> gate --ticket TODO-1-2 --node execute-and-evidence --status rejected --actor user --reopen testcase --note "wording NGs: fix the two case texts"</pre></div>\n  </div>\n  <footer class="k-dialog__foot"><span class="k-muted" style="font-size:12px">Logged as actor: user</span><span class="k-spacer"></span><button class="k-btn">Cancel</button><button class="k-btn k-btn--primary">Reject and reopen<span class="k-btn__kbd">Ctrl ↵</span></button></footer>\n</div>\n</div>' }],
    dos: ['Show the evidence and the command in the same dialog.', 'Name the confirm button after the chosen action.'], donts: ['Close the dialog on scrim click while a note is typed.'] });

  add({ id: 'drawer', group: 'Overlays', name: 'Drawer (run policy)', classes: ['k-drawer', 'k-drawer__head', 'k-drawer__body', 'k-setting', 'k-setting__text', 'k-setting__title', 'k-setting__sub'],
    desc: 'Settings for one ticket, beside the content. Each row: what it does, then the control.',
    examples: [{ title: 'Run policy', html:
'<aside class="k-drawer">\n  <header class="k-drawer__head">' + I('sliders') + '<h3 class="k-card__title" style="flex:1">Run policy · TODO-1-2</h3><button class="k-btn k-btn--ghost k-btn--icon k-btn--sm" aria-label="Close">' + I('x', 'k-icon--sm') + '</button></header>\n  <div class="k-drawer__body">\n    <div class="k-setting"><div class="k-setting__text"><div class="k-setting__title">Mode</div></div><div class="k-segmented"><button class="is-selected">Node per session</button><button>Session per run</button></div></div>\n    <div class="k-setting"><div class="k-setting__text"><div class="k-setting__title">Auto-run after a pass</div><div class="k-setting__sub">Start the next node when its gate passes.</div></div><input type="checkbox" class="k-switch" checked aria-label="Auto-run"></div>\n    <div class="k-setting"><div class="k-setting__text"><div class="k-setting__title">Stop on reject or loop-back</div></div><input type="checkbox" class="k-switch" checked aria-label="Stop on reject"></div>\n    <div class="k-setting"><div class="k-setting__text"><div class="k-setting__title">Never ship, commit or push</div><div class="k-setting__sub">' + I('lock', 'k-icon--sm') + ' Locked on in this workspace</div></div><input type="checkbox" class="k-switch" checked disabled aria-label="Never ship"></div>\n    <div class="k-setting"><div class="k-setting__text"><div class="k-setting__title">Loop cap</div><div class="k-setting__sub">needs-changes rounds before G4 asks you</div></div><div class="k-stepper"><button aria-label="Less">' + I('minus', 'k-icon--sm') + '</button><input value="2" aria-label="Loop cap"><button aria-label="More">' + I('plus', 'k-icon--sm') + '</button></div></div>\n  </div>\n</aside>' }],
    dos: ['Explain locked settings in place.'], donts: ['A Save button: settings apply as they change.'] });

  add({ id: 'palette', group: 'Overlays', name: 'Command palette', classes: ['k-palette', 'k-palette__input', 'k-palette__list', 'k-palette__group', 'k-palette__item', 'k-palette__meta', 'k-palette__foot'],
    desc: 'Every action by name, from anywhere (Ctrl K).',
    examples: [{ title: 'Search "re"', html:
'<div class="k-palette">\n  <div class="k-palette__input">' + I('search') + '<input value="re" aria-label="Command"><span class="k-kbd">Esc</span></div>\n  <div class="k-palette__list">\n    <div class="k-palette__group">Ticket TODO-1-2</div>\n    <div class="k-palette__item is-selected">' + I('gate') + 'Review gate<span class="k-palette__meta">execute-and-evidence</span></div>\n    <div class="k-palette__item">' + I('reopen') + 'Reopen a node…</div>\n    <div class="k-palette__group">Sessions</div>\n    <div class="k-palette__item">' + I('stop') + 'Stop session<span class="k-palette__meta">todo12-evidence2</span></div>\n  </div>\n  <div class="k-palette__foot"><span><span class="k-kbd">↑</span><span class="k-kbd">↓</span> move</span><span><span class="k-kbd">↵</span> run</span><span><span class="k-kbd">Esc</span> close</span></div>\n</div>' }],
    dos: ['Group results by what they act on.'], donts: ['Run state changes directly: open their confirm.'] });

  /* ------------------------------------------------------------------ Layout */
  add({ id: 'topbar', group: 'Layout', name: 'Top bar', classes: ['k-topbar', 'k-brand', 'k-brand__mark'],
    desc: 'Brand, workspace, the command trigger, the attention pill and today\'s cost.',
    examples: [{ title: 'Default', wide: true, html:
'<header class="k-topbar" style="height:48px;border:1px solid var(--border);border-radius:12px">\n  <div class="k-brand"><span class="k-brand__mark">' + I('pipeline') + '</span>kaneki</div>\n  <div class="k-breadcrumb">' + I('chevron-right', 'k-icon--sm') + '<b>todo-nestjs-backend</b></div>\n  <span class="k-spacer"></span>\n  <button class="k-cmdk">' + I('search') + '<span class="k-cmdk__text">Search or run a command</span><span class="k-kbd">Ctrl</span><span class="k-kbd">K</span></button>\n  <button class="k-pill k-pill--attention"><span class="k-pill__count">1</span>gate needs you</button>\n  <span class="k-muted" style="font-size:12px">Today <b style="color:var(--ink)">$9.62</b> · 31.8M tokens</span>\n</header>' }],
    dos: ['Keep the attention pill in the same place on every screen.'], donts: ['Put ticket actions in the top bar.'] });

  add({ id: 'session', group: 'Layout', name: 'Session row', classes: ['k-session', 'k-session__name', 'k-session__node', 'k-session__acts'],
    desc: 'One supervised Claude session in the dock: state, name, node, status and actions.',
    examples: [{ title: 'States', wide: true, html:
'<div style="display:grid;gap:2px">\n  <div class="k-session"><span class="k-dot k-dot--attention"></span><span class="k-session__name">todo12-evidence2</span><span class="k-session__node">execute-and-evidence g2</span><span class="k-status k-status--attention">Waiting: your gate decision · 14m</span><span class="k-session__acts"><button class="k-btn k-btn--sm">Focus</button><button class="k-btn k-btn--sm k-btn--ghost">Stop</button></span></div>\n  <div class="k-session is-hover"><span class="k-dot k-dot--run"></span><span class="k-session__name">haise-eb</span><span class="k-session__node">driver / monitor</span><span class="k-status k-status--run">Busy · 2h 41m</span><span class="k-session__acts"><button class="k-btn k-btn--sm">Focus</button><button class="k-btn k-btn--sm k-btn--ghost">Stop</button></span></div>\n  <div class="k-session"><span class="k-dot k-dot--idle"></span><span class="k-session__name">todo12-review2</span><span class="k-session__node">code-review g2</span><span class="k-status">Stopped after gate</span><span class="k-session__acts"><button class="k-btn k-btn--sm">Focus</button><button class="k-btn k-btn--sm k-btn--ghost" disabled>Stop</button></span></div>\n</div>' }],
    dos: ['Say what the session waits for.'], donts: ['Stop without a confirm when the session is busy.'] });

  add({ id: 'inspector', group: 'Layout', name: 'Inspector', classes: ['k-inspector', 'k-inspector__head', 'k-inspector__body'],
    desc: 'The right column: a preview of the file chosen in the node card.',
    examples: [{ title: 'Default', html:
'<aside class="k-inspector" style="border:1px solid var(--border);border-radius:12px;max-width:380px">\n  <header class="k-inspector__head">' + I('file') + '<b>run-report.md</b><span class="k-badge k-badge--outline">g2</span><span class="k-spacer"></span><button class="k-btn k-btn--sm">' + I('external', 'k-icon--sm') + 'Open in Zed</button></header>\n  <div class="k-inspector__body"><pre class="k-code" style="border:0;padding:0;background:none"><span class="t-h">## Summary</span>\nPlanned 139 · <span class="t-ok">OK 136</span> · <span class="t-bad">NG 2</span> · N/A 1</pre></div>\n</aside>' }],
    dos: ['Show the generation of the file.'], donts: ['Editing.'] });

  window.KanekiGallery = C;
})();
