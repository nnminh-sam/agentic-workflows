# P2 · Wails setup and project scaffold (M0)

**Milestone:** M0. **Output:** `parts/P2-stack-spike-scaffold.plan.md`

## Problem
D10 is closed (owner, 2026-11-18): **Go + Wails**. No Rust, no Tauri, no ImGui, no comparison spike. The user has
never built a desktop app. P2 sets up the Wails toolchain, runs a small spike slice and measures it against the plan §6
targets (idle memory < 80 MB incl. WebView2 as a goal, cold start < 1 s, idle CPU ≈ 0, binary size, Vietnamese Telex
input works), and leaves a scaffold the other parts build on.

## Plan must cover
1. **Wails facts**: the current stable version you can verify from local docs or well-known facts
   (mark uncertain versions `ASSUMPTION`), toolchain to install on Windows (sizes), WebView2 use, Go libraries for
   notifications, single-instance, file watching, child processes, updater.
2. **Spike slice spec** (Wails): window + ticket list from `sdlc tickets` + watch
   `decisions.jsonl` + one toast + one button that runs `sdlc gate --help`. Exact steps, a measurement script
   (PowerShell `Get-Process` summing the app + WebView2 children, cold-start timing), and a results table template.
3. **Install plan** for the toolchain (user approves installs; list commands, disk impact, how to uninstall).
4. **Scaffold layout** for `harness/kaneki-harness/app/` (Wails): where the Host source, UI source, design system,
   contracts, fixtures, tests and docs go; how the UI imports `design-system/` (copy at build vs. served as-is);
   dev loop (hot reload), build commands, `.gitignore`.
5. **Frontend tooling decision input**: whether the UI needs a bundler (Vite) or can be plain ES modules; give a
   recommendation but leave the UI framework to P4.
6. A **decision record** template to write the result into `harness/kaneki-harness/docs/desktop-app/plan.md` §6.

## Not in scope
Real screens, real Host features beyond the spike slice, packaging/installer (P8).
