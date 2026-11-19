/* generated from tokens.json by build_tokens.py; do not edit by hand */
window.KanekiTokens = {
 "name": "Kaneki Desktop",
 "version": 2,
 "meta": {
  "note": "v2 (2026-11-18): premium redesign of the v1 tokens taken from kaneki-harness/docs/mock/desktop-ui.html @ 686a5c7. Same roles (warn = waiting on you, ok, bad, run, model colours), refined values, every text pair checked by build_tokens.py --contrast."
 },
 "color": {
  "themes": [
   {
    "id": "light",
    "name": "Light"
   },
   {
    "id": "dark",
    "name": "Dark"
   }
  ],
  "tokens": [
   {
    "name": "canvas",
    "value": {
     "light": "#f5f6f8",
     "dark": "#0b0d11"
    },
    "usage": "Window ground behind panels; the main column."
   },
   {
    "name": "surface",
    "value": {
     "light": "#ffffff",
     "dark": "#121519"
    },
    "usage": "Panels, cards, inputs, menus, dialogs."
   },
   {
    "name": "surface-2",
    "value": {
     "light": "#fafbfc",
     "dark": "#161a20"
    },
    "usage": "Subtle fill inside a surface: table header, card footer, code block."
   },
   {
    "name": "surface-3",
    "value": {
     "light": "#f0f2f5",
     "dark": "#1c2129"
    },
    "usage": "Hover and pressed fill of rows and ghost buttons; sunken wells; tracks."
   },
   {
    "name": "overlay",
    "value": {
     "light": "#ffffff",
     "dark": "#181c23"
    },
    "usage": "Floating layers: dialog, drawer, palette, menu, toast, tooltip body (dark lifts above surface)."
   },
   {
    "name": "border",
    "value": {
     "light": "#e4e7ec",
     "dark": "#252a33"
    },
    "usage": "Hairlines between regions, card and table borders."
   },
   {
    "name": "border-strong",
    "value": {
     "light": "#cfd4dc",
     "dark": "#353c47"
    },
    "usage": "Control borders (inputs, secondary buttons, checkboxes): at least 1.5:1 so a field is findable."
   },
   {
    "name": "ink",
    "value": {
     "light": "#101828",
     "dark": "#eef1f5"
    },
    "usage": "Primary text on canvas, surface, surface-2, surface-3 and overlay (15:1 or more)."
   },
   {
    "name": "ink-2",
    "value": {
     "light": "#475467",
     "dark": "#b3bac6"
    },
    "usage": "Secondary text: subtitles, table cells that are not the key, descriptions (7:1 or more)."
   },
   {
    "name": "ink-3",
    "value": {
     "light": "#5d6879",
     "dark": "#8f98a6"
    },
    "usage": "Tertiary text: labels, timestamps, hints, placeholders on surface and canvas (4.8:1 or more)."
   },
   {
    "name": "ink-disabled",
    "value": {
     "light": "#a3abb8",
     "dark": "#565e6b"
    },
    "usage": "Disabled labels and pending node numerals only; never for information that must be read."
   },
   {
    "name": "accent",
    "value": {
     "light": "#2f54d0",
     "dark": "#7591ff"
    },
    "usage": "Primary button fill, selection, active tab bar, links, focus ring, running state."
   },
   {
    "name": "accent-hover",
    "value": {
     "light": "#2445b8",
     "dark": "#8aa2ff"
    },
    "usage": "Primary button hover."
   },
   {
    "name": "accent-soft",
    "value": {
     "light": "#edf1ff",
     "dark": "#18203d"
    },
    "usage": "Selected row, selected choice, highlighted palette item, running node fill."
   },
   {
    "name": "accent-text",
    "value": {
     "light": "#2945b5",
     "dark": "#a3b5ff"
    },
    "usage": "Accent-coloured text on surface or accent-soft (links, record tags, selected labels)."
   },
   {
    "name": "on-accent",
    "value": {
     "light": "#ffffff",
     "dark": "#0b0d11"
    },
    "usage": "Text and icons on an accent fill: white in light, near-black in dark."
   },
   {
    "name": "ok",
    "value": {
     "light": "#14855e",
     "dark": "#3cc796"
    },
    "usage": "Done/passed marks: dots, node rings, progress segments, success icons."
   },
   {
    "name": "ok-soft",
    "value": {
     "light": "#e8f6f0",
     "dark": "#0f2a20"
    },
    "usage": "Fill behind ok text: done node, approved banner, gate tag."
   },
   {
    "name": "ok-text",
    "value": {
     "light": "#0c6b4a",
     "dark": "#62d9ad"
    },
    "usage": "ok-coloured text on surface or ok-soft (PASS, approved)."
   },
   {
    "name": "warn",
    "value": {
     "light": "#c27409",
     "dark": "#f2a531"
    },
    "usage": "Waiting on you, and only that: attention dot, gate banner edge, pending-decision node ring."
   },
   {
    "name": "warn-soft",
    "value": {
     "light": "#fdf3e2",
     "dark": "#2e220c"
    },
    "usage": "Fill behind warn text: gate banner, attention pill, ask banner."
   },
   {
    "name": "warn-text",
    "value": {
     "light": "#8a4f00",
     "dark": "#f6bb5c"
    },
    "usage": "Attention text on surface or warn-soft."
   },
   {
    "name": "on-warn",
    "value": {
     "light": "#1c1200",
     "dark": "#1c1200"
    },
    "usage": "Text on a warn fill (Review gate button)."
   },
   {
    "name": "bad",
    "value": {
     "light": "#d0343e",
     "dark": "#f26b6b"
    },
    "usage": "Failed/rejected/stale marks: dots, icons, destructive button fill."
   },
   {
    "name": "bad-soft",
    "value": {
     "light": "#fdeeee",
     "dark": "#2e1517"
    },
    "usage": "Fill behind bad text: FAIL tag, invalidate tag, error field."
   },
   {
    "name": "bad-text",
    "value": {
     "light": "#b0222c",
     "dark": "#ff8f8f"
    },
    "usage": "Error text on surface or bad-soft (FAIL, NG ids, field errors)."
   },
   {
    "name": "on-bad",
    "value": {
     "light": "#ffffff",
     "dark": "#1a0a0b"
    },
    "usage": "Text on a bad fill (destructive button)."
   },
   {
    "name": "run",
    "value": "{accent}",
    "usage": "Work in progress: running node ring, busy dot, live progress. Alias of accent so they can diverge later."
   },
   {
    "name": "haiku",
    "value": {
     "light": "#0e8a7b",
     "dark": "#3fd0bd"
    },
    "usage": "haiku model mark: cost bars, tier dot."
   },
   {
    "name": "haiku-soft",
    "value": {
     "light": "#e6f6f3",
     "dark": "#0f2724"
    },
    "usage": "Tier badge fill for haiku."
   },
   {
    "name": "haiku-text",
    "value": {
     "light": "#0a6b60",
     "dark": "#5fe0cf"
    },
    "usage": "Tier badge text for haiku."
   },
   {
    "name": "sonnet",
    "value": {
     "light": "#7a52db",
     "dark": "#b39cff"
    },
    "usage": "sonnet model mark: cost bars, tier dot."
   },
   {
    "name": "sonnet-soft",
    "value": {
     "light": "#f2eefe",
     "dark": "#1f1936"
    },
    "usage": "Tier badge fill for sonnet."
   },
   {
    "name": "sonnet-text",
    "value": {
     "light": "#5b37b8",
     "dark": "#c4b2ff"
    },
    "usage": "Tier badge text for sonnet."
   },
   {
    "name": "opus",
    "value": {
     "light": "#d1621c",
     "dark": "#ff9d5c"
    },
    "usage": "opus model mark: cost bars, tier dot."
   },
   {
    "name": "opus-soft",
    "value": {
     "light": "#fdf0e7",
     "dark": "#2d1c10"
    },
    "usage": "Tier badge fill for opus."
   },
   {
    "name": "opus-text",
    "value": {
     "light": "#9c4410",
     "dark": "#ffb27f"
    },
    "usage": "Tier badge text for opus."
   },
   {
    "name": "scrim",
    "value": {
     "light": "rgba(16,24,40,0.36)",
     "dark": "rgba(0,0,0,0.6)"
    },
    "usage": "Dimmed window behind a dialog."
   },
   {
    "name": "knob",
    "value": {
     "light": "#ffffff",
     "dark": "#f4f6f9"
    },
    "usage": "Switch thumb."
   },
   {
    "name": "tooltip",
    "value": {
     "light": "#101828",
     "dark": "#eef1f5"
    },
    "usage": "Tooltip fill (inverted)."
   },
   {
    "name": "on-tooltip",
    "value": {
     "light": "#ffffff",
     "dark": "#101828"
    },
    "usage": "Tooltip text."
   }
  ]
 },
 "type": {
  "fonts": [],
  "families": {
   "ui": "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, sans-serif",
   "display": "\"Segoe UI Variable Display\", \"Segoe UI\", system-ui, -apple-system, sans-serif",
   "mono": "\"Cascadia Mono\", \"Cascadia Code\", Consolas, ui-monospace, monospace"
  },
  "groups": [
   {
    "name": "Display",
    "family": "display",
    "styles": [
     {
      "name": "display",
      "fontSize": "28px",
      "lineHeight": 1.15,
      "fontWeight": 600,
      "letterSpacing": "-0.4px",
      "sample": "$4.81",
      "usage": "Headline numbers in stat tiles."
     },
     {
      "name": "heading",
      "fontSize": "20px",
      "lineHeight": 1.25,
      "fontWeight": 600,
      "letterSpacing": "-0.2px",
      "sample": "TODO-1-2 · Foundation",
      "usage": "Page and ticket titles."
     },
     {
      "name": "title",
      "fontSize": "15px",
      "lineHeight": 1.35,
      "fontWeight": 600,
      "sample": "Gate · execute-and-evidence",
      "usage": "Dialog, drawer and card titles."
     }
    ]
   },
   {
    "name": "Text",
    "family": "ui",
    "styles": [
     {
      "name": "body-lg",
      "fontSize": "14px",
      "lineHeight": 1.5,
      "fontWeight": 400,
      "sample": "Accept the 2 wording NGs and continue.",
      "usage": "Dialog body, empty states."
     },
     {
      "name": "body",
      "fontSize": "13px",
      "lineHeight": 1.5,
      "fontWeight": 400,
      "sample": "Reviewer PASS · worker rounds 2",
      "usage": "Default window text."
     },
     {
      "name": "body-strong",
      "fontSize": "13px",
      "lineHeight": 1.5,
      "fontWeight": 600,
      "sample": "TODO-1-2",
      "usage": "Ids, names, emphasised values."
     },
     {
      "name": "small",
      "fontSize": "12px",
      "lineHeight": 1.45,
      "fontWeight": 400,
      "sample": "Foundation (tiered redo)",
      "usage": "Secondary lines, hints, table meta, toasts."
     },
     {
      "name": "caption",
      "fontSize": "11px",
      "lineHeight": 1.4,
      "fontWeight": 500,
      "sample": "generation 2",
      "usage": "Badges, tags, kbd."
     },
     {
      "name": "overline",
      "fontSize": "11px",
      "lineHeight": 1.4,
      "fontWeight": 600,
      "letterSpacing": "0.6px",
      "sample": "TICKETS",
      "usage": "Section labels and table headers, uppercase, ink-3."
     }
    ]
   },
   {
    "name": "Code",
    "family": "mono",
    "styles": [
     {
      "name": "code",
      "fontSize": "12px",
      "lineHeight": 1.6,
      "fontWeight": 400,
      "sample": "sdlc gate --ticket TODO-1-2 --status approved",
      "usage": "Commands, file previews, log lines, case ids, timestamps."
     }
    ]
   }
  ]
 },
 "spacing": {
  "note": "4 px grid, with 2/6/10 half-steps for tight controls.",
  "tokens": [
   {
    "name": "space-0-5",
    "value": "2px",
    "usage": "Hairline gaps: progress segments, stacked badges."
   },
   {
    "name": "space-1",
    "value": "4px",
    "usage": "Icon to text in small controls; gaps in tag groups."
   },
   {
    "name": "space-1-5",
    "value": "6px",
    "usage": "Icon to text in buttons; chip padding."
   },
   {
    "name": "space-2",
    "value": "8px",
    "usage": "Gap between controls in a toolbar; list item padding."
   },
   {
    "name": "space-2-5",
    "value": "10px",
    "usage": "Control side padding (md)."
   },
   {
    "name": "space-3",
    "value": "12px",
    "usage": "Row padding; gaps inside cards."
   },
   {
    "name": "space-4",
    "value": "16px",
    "usage": "Card padding; panel side padding."
   },
   {
    "name": "space-5",
    "value": "20px",
    "usage": "Dialog padding; section gaps inside a view."
   },
   {
    "name": "space-6",
    "value": "24px",
    "usage": "Gap between cards; view padding."
   },
   {
    "name": "space-8",
    "value": "32px",
    "usage": "Gap between major sections; empty state padding."
   }
  ]
 },
 "radius": {
  "tokens": [
   {
    "name": "radius-xs",
    "value": "4px",
    "usage": "Badges, tags, kbd, checkbox."
   },
   {
    "name": "radius-sm",
    "value": "6px",
    "usage": "Buttons, inputs, menu items, segmented control."
   },
   {
    "name": "radius-md",
    "value": "8px",
    "usage": "List rows, banners, code blocks, toasts."
   },
   {
    "name": "radius-lg",
    "value": "12px",
    "usage": "Cards, stat tiles, menus."
   },
   {
    "name": "radius-xl",
    "value": "14px",
    "usage": "Dialogs, drawers, the command palette."
   },
   {
    "name": "radius-full",
    "value": "999px",
    "usage": "Pills, switches, dots, avatars."
   }
  ]
 },
 "shadow": {
  "tokens": [
   {
    "name": "shadow-xs",
    "value": {
     "light": "0 1px 2px rgba(16,24,40,0.05)",
     "dark": "0 1px 2px rgba(0,0,0,0.4)"
    },
    "usage": "Buttons and inputs at rest."
   },
   {
    "name": "shadow-sm",
    "value": {
     "light": "0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.08)",
     "dark": "0 1px 2px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)"
    },
    "usage": "Cards and stat tiles."
   },
   {
    "name": "shadow-md",
    "value": {
     "light": "0 4px 8px -2px rgba(16,24,40,0.08), 0 2px 4px -2px rgba(16,24,40,0.05)",
     "dark": "0 4px 12px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.05)"
    },
    "usage": "Menus, tooltips, toasts, selected cards."
   },
   {
    "name": "shadow-lg",
    "value": {
     "light": "0 20px 40px -8px rgba(16,24,40,0.18), 0 6px 12px -4px rgba(16,24,40,0.08)",
     "dark": "0 24px 48px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.06)"
    },
    "usage": "Dialogs, drawers, the command palette."
   },
   {
    "name": "focus-ring",
    "value": {
     "light": "0 0 0 2px #ffffff, 0 0 0 4px #2f54d0",
     "dark": "0 0 0 2px #0b0d11, 0 0 0 4px #7591ff"
    },
    "usage": "Keyboard focus on every interactive element: a 2px gap, then a 2px accent ring (3:1 or more on every surface)."
   }
  ]
 },
 "size": {
  "tokens": [
   {
    "name": "control-sm",
    "value": "26px",
    "usage": "Small buttons and inputs (row actions)."
   },
   {
    "name": "control-md",
    "value": "32px",
    "usage": "Default buttons, inputs, selects."
   },
   {
    "name": "control-lg",
    "value": "38px",
    "usage": "Dialog primary actions, palette input."
   },
   {
    "name": "icon-sm",
    "value": "14px",
    "usage": "Icons in small controls and badges."
   },
   {
    "name": "icon-md",
    "value": "16px",
    "usage": "Default icon size."
   },
   {
    "name": "titlebar-height",
    "value": "48px",
    "usage": "Top bar."
   },
   {
    "name": "rail-width",
    "value": "260px",
    "usage": "Ticket rail."
   },
   {
    "name": "inspector-width",
    "value": "360px",
    "usage": "Inspector."
   },
   {
    "name": "dock-height",
    "value": "188px",
    "usage": "Sessions and live log dock."
   },
   {
    "name": "node-size",
    "value": "32px",
    "usage": "Pipeline node."
   },
   {
    "name": "drawer-width",
    "value": "400px",
    "usage": "Run policy drawer."
   },
   {
    "name": "dialog-width",
    "value": "720px",
    "usage": "Gate dialog (max)."
   },
   {
    "name": "palette-width",
    "value": "600px",
    "usage": "Command palette (max)."
   },
   {
    "name": "toast-width",
    "value": "360px",
    "usage": "Toasts."
   }
  ]
 },
 "motion": {
  "tokens": [
   {
    "name": "ease-out",
    "value": "cubic-bezier(0.2,0.8,0.2,1)",
    "usage": "Everything that enters or responds to a pointer."
   },
   {
    "name": "duration-fast",
    "value": "120ms",
    "usage": "Hover, press, toggle."
   },
   {
    "name": "duration-base",
    "value": "180ms",
    "usage": "Menus, tooltips, toasts entering."
   },
   {
    "name": "duration-slow",
    "value": "260ms",
    "usage": "Dialogs and drawers entering."
   }
  ]
 },
 "contrast": {
  "note": "Pairs build_tokens.py --contrast checks in every theme: [foreground, background, minimum ratio].",
  "pairs": [
   [
    "ink",
    "canvas",
    4.5
   ],
   [
    "ink",
    "surface",
    4.5
   ],
   [
    "ink",
    "surface-3",
    4.5
   ],
   [
    "ink",
    "overlay",
    4.5
   ],
   [
    "ink",
    "accent-soft",
    4.5
   ],
   [
    "ink-2",
    "surface",
    4.5
   ],
   [
    "ink-2",
    "canvas",
    4.5
   ],
   [
    "ink-2",
    "surface-3",
    4.5
   ],
   [
    "ink-3",
    "surface",
    4.5
   ],
   [
    "ink-3",
    "canvas",
    4.5
   ],
   [
    "ink-3",
    "surface-2",
    4.5
   ],
   [
    "ink-3",
    "overlay",
    4.5
   ],
   [
    "accent-text",
    "surface",
    4.5
   ],
   [
    "accent-text",
    "accent-soft",
    4.5
   ],
   [
    "on-accent",
    "accent",
    4.5
   ],
   [
    "on-accent",
    "accent-hover",
    4.5
   ],
   [
    "ok-text",
    "surface",
    4.5
   ],
   [
    "ok-text",
    "ok-soft",
    4.5
   ],
   [
    "warn-text",
    "surface",
    4.5
   ],
   [
    "warn-text",
    "warn-soft",
    4.5
   ],
   [
    "on-warn",
    "warn",
    4.5
   ],
   [
    "bad-text",
    "surface",
    4.5
   ],
   [
    "bad-text",
    "bad-soft",
    4.5
   ],
   [
    "on-bad",
    "bad",
    4.5
   ],
   [
    "haiku-text",
    "haiku-soft",
    4.5
   ],
   [
    "sonnet-text",
    "sonnet-soft",
    4.5
   ],
   [
    "opus-text",
    "opus-soft",
    4.5
   ],
   [
    "on-tooltip",
    "tooltip",
    4.5
   ],
   [
    "accent",
    "surface",
    3
   ],
   [
    "accent",
    "canvas",
    3
   ],
   [
    "ok",
    "surface",
    3
   ],
   [
    "warn",
    "surface",
    3
   ],
   [
    "bad",
    "surface",
    3
   ],
   [
    "haiku",
    "surface",
    3
   ],
   [
    "sonnet",
    "surface",
    3
   ],
   [
    "opus",
    "surface",
    3
   ],
   [
    "border-strong",
    "surface",
    1.4
   ]
  ]
 }
};
