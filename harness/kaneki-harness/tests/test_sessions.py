"""`sdlc transcripts` and harness/sessions.py: the agents' session logs matched to ticket nodes (M2 step 1).

Every log here is a SYNTHETIC fixture shaped like the real formats (field names only); no real conversation text."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402

from harness import sessions as S  # noqa: E402

TS = "2026-11-23T10:00:00.000Z"


def write_jsonl(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows), encoding="utf-8")
    return path


def age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def claude_rows(session: str, cwd: str, first_user: str, *, tool_cmd: str | None = None, agent_id: str | None = None,
                title: str | None = None) -> list:
    base = {"sessionId": session, "cwd": cwd, "timestamp": TS, "isSidechain": bool(agent_id)}
    if agent_id:
        base["agentId"] = agent_id
    rows: list = [{"type": "permission-mode", "permissionMode": "default", "sessionId": session},
                  {**base, "type": "user", "uuid": "u1", "message": {"role": "user", "content": first_user}},
                  {**base, "type": "user", "uuid": "u0", "isMeta": True, "message": {"role": "user", "content": "meta"}}]
    # one API message split over two rows that share message.id, plus a repeated block
    rows.append({**base, "type": "assistant", "uuid": "a1",
                 "message": {"id": "msg_1", "role": "assistant", "content": [
                     {"type": "thinking", "thinking": "hidden", "signature": "s"},
                     {"type": "text", "text": "Working on it."}]}})
    if tool_cmd:
        rows.append({**base, "type": "assistant", "uuid": "a2",
                     "message": {"id": "msg_1", "role": "assistant", "content": [
                         {"type": "text", "text": "Working on it."},
                         {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": tool_cmd}}]}})
        rows.append({**base, "type": "user", "uuid": "u2", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": '{"ok": true}'}]}]}})
    if title:
        rows.append({"type": "ai-title", "aiTitle": title, "sessionId": session})
    rows.append("{not json")
    return rows


class TranscriptsTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.t = "TX-1"
        code, res = run("init", "--ticket", self.t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        code, plan = run("plan", "--ticket", self.t, "--node", "prepare", *self.p)
        self.assertEqual(code, 0, plan)
        self.node, self.unit = "prepare", plan["units"][0]["id"]
        code, pk = run("packet", "--ticket", self.t, "--node", self.node, "--unit", self.unit, *self.p)
        self.assertEqual(code, 0, pk)
        self.pk = pk
        Path(pk["resultPath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(pk["resultPath"]).write_text("status: ok\n")
        code, rec = run("record", "--ticket", self.t, "--node", self.node, "--unit", self.unit, "--result",
                        pk["resultPath"], "--model", pk["model"], "--tokens", "100", *self.p)
        self.assertEqual(code, 0, rec)
        self.packet_line = f"Read the packet at {pk['path']} (sha256 {pk['packetDigest']}) and execute it."
        self.ws = str(self.profile.parent.resolve())
        self.homes = self.tmp / "homes"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.homes / "claude"), "CODEX_HOME": str(self.homes / "codex"),
                    "ANTIGRAVITY_CLI_ROOT": str(self.homes / "agy")}
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers ---------------------------------------------------------------------------------------
    def tdir(self, ticket: str | None = None) -> Path:  # type: ignore[override]
        return self.tmp / "tickets" / (ticket or self.t)

    def snapshot(self) -> dict[str, str]:
        return {str(p.relative_to(self.tmp)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.tmp.rglob("*")) if p.is_file()}

    def transcripts(self, *extra: str) -> dict:
        before = self.snapshot()
        code, res = run("transcripts", "--ticket", self.t, *extra, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.snapshot(), before, "transcripts wrote a file")
        return res

    def claude_dir(self, cwd: str | None = None) -> Path:
        return self.homes / "claude" / "projects" / S.claude_slug(cwd or self.ws)

    def gate_cmd(self, command: str = "gate") -> str:
        return f"sh .sdlc/bin/sdlc {command} --ticket {self.t} --node {self.node} --status approved"

    # --- Claude Code ---------------------------------------------------------------------------------
    def test_claude_exact_unit_subagent_and_heuristic_controller(self) -> None:
        proj = self.claude_dir()
        write_jsonl(proj / "ctl-1.jsonl", claude_rows("ctl-1", self.ws, "drive the ticket", tool_cmd=self.gate_cmd(),
                                                    title="Controller"))
        sub = write_jsonl(proj / "ctl-1" / "subagents" / "agent-abc.jsonl",
                          claude_rows("ctl-1", self.ws, self.packet_line, agent_id="abc"))
        (sub.parent / "agent-abc.meta.json").write_text(json.dumps(
            {"agentType": "kaneki-worker", "description": "prepare worker", "toolUseId": "tu_9"}), encoding="utf-8")
        write_jsonl(proj / "other.jsonl", claude_rows("other", self.ws, "unrelated", tool_cmd="git status"))
        res = self.transcripts()
        node = res["nodes"][self.node]
        self.assertEqual(len(node["units"]), 1)
        u = node["units"][0]
        self.assertEqual((u["agent"], u["match"], u["node"], u["unit"], u["generation"], u["round"]),
                         ("claude", "exact", self.node, self.unit, 1, 1))
        self.assertEqual(u["dispatchId"], self.pk["dispatchId"])
        self.assertEqual(u["packetDigest"], self.pk["packetDigest"])
        self.assertEqual(u["parent"], {"agent": "claude", "sessionId": "ctl-1", "toolUseId": "tu_9"})
        self.assertEqual(u["title"], "prepare worker")
        self.assertEqual(u["record"]["tokens"], 100)
        self.assertNotIn("steps", u)
        self.assertEqual([c["sessionId"] for c in node["controller"]], ["ctl-1"])
        self.assertEqual(node["controller"][0]["match"], "heuristic")
        self.assertEqual(node["controller"][0]["sdlcCommands"], ["gate"])
        self.assertEqual(node["controller"][0]["title"], "Controller")
        for n, entry in res["nodes"].items():
            if n != self.node:
                self.assertEqual(entry, {"units": [], "controller": []})

    def test_claude_steps_with_node(self) -> None:
        write_jsonl(self.claude_dir() / "ctl-1.jsonl", claude_rows("ctl-1", self.ws, "go", tool_cmd=self.gate_cmd()))
        res = self.transcripts("--node", self.node)
        self.assertEqual(list(res["nodes"]), [self.node])
        steps = res["nodes"][self.node]["controller"][0]["steps"]
        self.assertEqual([s["role"] for s in steps], ["user", "agent", "tool"])  # meta, thinking, repeat dropped
        self.assertEqual(steps[2]["tool"], {"name": "Bash", "input": {"command": self.gate_cmd()}, "output": '{"ok": true}'})
        self.assertEqual(steps[0]["at"], TS)

    def test_worktree_folder_is_scanned(self) -> None:
        state_path = self.tdir() / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        wt = self.tmp / "worktrees" / f"app-{self.t}"
        state["context"]["repositories"][0]["worktreeRel"] = os.path.relpath(wt, self.profile.parent)
        self.assertIn(str(wt.resolve()), S.workspace_dirs(self.profile.parent, state))
        write_jsonl(self.claude_dir(str(wt.resolve())) / "w.jsonl", claude_rows("w", str(wt), "x", tool_cmd=self.gate_cmd()))
        sessions = S.collect(self.tdir(), S.workspace_dirs(self.profile.parent, state))
        self.assertEqual([s["sessionId"] for s in sessions], ["w"])

    def test_slug(self) -> None:
        self.assertEqual(S.claude_slug(r"D:\workspace\HAISE\wp-agentic-workflows\agentic-workflows"),
                         "D--workspace-HAISE-wp-agentic-workflows-agentic-workflows")
        self.assertEqual(S.claude_slug("/home/u/my_proj.v2"), "-home-u-my-proj-v2")

    def test_running_flag_and_step_cap(self) -> None:
        rows = claude_rows("ctl-1", self.ws, "go", tool_cmd=self.gate_cmd())
        rows += [{"type": "assistant", "sessionId": "ctl-1", "timestamp": TS,
                  "message": {"id": f"m{i}", "content": [{"type": "text", "text": f"step {i}"}]}} for i in range(10)]
        live = write_jsonl(self.claude_dir() / "ctl-1.jsonl", rows)
        s = S.read_claude(live, max_steps=4)
        self.assertTrue(s["running"])
        self.assertEqual(s["stepCount"], 13)
        self.assertEqual(len(s["steps"]), 4)
        self.assertEqual(s["truncated"], 9)
        self.assertEqual(s["steps"][0]["role"], "user")  # the first step is always kept
        self.assertEqual(s["steps"][-1]["text"], "step 9")
        age(live, 600)
        self.assertFalse(S.read_claude(live)["running"])
        res = self.transcripts("--node", self.node, "--max-steps", "2")
        self.assertEqual(len(res["nodes"][self.node]["controller"][0]["steps"]), 2)

    # --- Codex -----------------------------------------------------------------------------------------
    def codex_file(self, name: str, meta: dict, items: list) -> Path:
        rows = [{"timestamp": TS, "type": "session_meta", "payload": {"timestamp": TS, "cwd": self.ws, **meta}},
                {"timestamp": TS, "type": "turn_context", "payload": {"cwd": self.ws, "model": "m"}},
                {"timestamp": TS, "type": "response_item", "payload": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}}]
        rows += [{"timestamp": TS, "type": t, "payload": p} for t, p in items]
        return write_jsonl(self.homes / "codex" / "sessions" / "2026" / "11" / "23" / f"rollout-{name}.jsonl", rows)

    def test_codex_exact_heuristic_and_parent(self) -> None:
        self.codex_file("a", {"id": "cx-unit", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "cx-ctl"}}}}, [
            ("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": self.packet_line}]}),
            ("event_msg", {"type": "user_message", "message": self.packet_line}),
            ("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]})])
        self.codex_file("b", {"id": "cx-ctl", "source": "cli"}, [
            ("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "drive"}]}),
            ("response_item", {"type": "function_call", "name": "exec_command", "call_id": "c1",
                               "arguments": json.dumps({"cmd": self.gate_cmd("record")})}),
            ("response_item", {"type": "function_call_output", "call_id": "c1", "output": "ok"}),
            ("response_item", {"type": "function_call", "name": "shell", "call_id": "c2",
                               "arguments": json.dumps({"command": ["bash", "-lc", self.gate_cmd()]})}),
            ("response_item", {"type": "function_call_output", "call_id": "c2",
                               "output": json.dumps({"output": "gated", "metadata": {"exit_code": 0}})}),
            ("event_msg", {"type": "token_count", "info": None})])
        elsewhere = self.codex_file("c", {"id": "cx-else"}, [
            ("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": self.packet_line}]})])
        lines = elsewhere.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace(json.dumps(self.ws)[1:-1], "/somewhere/else")
        elsewhere.write_text("\n".join(lines) + "\n", encoding="utf-8")

        res = self.transcripts("--node", self.node)
        node = res["nodes"][self.node]
        self.assertEqual([(u["agent"], u["sessionId"], u["match"]) for u in node["units"]], [("codex", "cx-unit", "exact")])
        self.assertEqual(node["units"][0]["parent"], {"agent": "codex", "sessionId": "cx-ctl"})
        self.assertEqual([s["role"] for s in node["units"][0]["steps"]], ["user", "agent"])  # event_msg not doubled
        ctl = node["controller"][0]
        self.assertEqual((ctl["sessionId"], ctl["sdlcCommands"]), ("cx-ctl", ["record", "gate"]))
        self.assertEqual([s["tool"]["output"] for s in ctl["steps"] if s["tool"]], ["ok", "gated"])

    def test_codex_legacy_format(self) -> None:
        f = write_jsonl(self.homes / "codex" / "sessions" / "rollout-old.jsonl", [
            {"id": "old-1", "timestamp": TS, "instructions": None},
            {"record_type": "state"},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "local_shell_call", "call_id": "l1", "action": {"type": "exec", "command": ["sdlc", "plan"]}}])
        s = S.read_codex(f)
        self.assertEqual(s["sessionId"], "old-1")
        self.assertEqual([x["role"] for x in s["steps"]], ["user", "tool"])

    # --- Antigravity -----------------------------------------------------------------------------------
    def agy(self, cid: str, rows: list, *, full: bool = True) -> Path:
        name = "transcript_full.jsonl" if full else "transcript.jsonl"
        return write_jsonl(self.homes / "agy" / "brain" / cid / ".system_generated" / "logs" / name, rows)

    def agy_db(self, rows: list[tuple]) -> None:
        db = self.homes / "agy" / "conversation_summaries.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, "
                    "workspace_uris TEXT, parent_conversation_id TEXT, agent_name TEXT, status TEXT, extra BLOB)")
        con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,NULL)", rows)
        con.commit()
        con.close()

    def test_antigravity_exact_heuristic_parent_and_filter(self) -> None:
        uri = Path(self.ws).as_uri()
        self.agy_db([("ag-ctl", "Drive TX-1", json.dumps([uri]), "", None, "CASCADE_RUN_STATUS_IDLE"),
                     ("ag-unit", "Unit", "[]", "ag-ctl", "worker", "CASCADE_RUN_STATUS_IDLE"),
                     ("ag-else", "Else", json.dumps(["file:///nowhere"]), "", None, "x")])
        self.agy("ag-ctl", [
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": TS,
             "content": "Gating.", "tool_calls": [{"name": "run_command",
                                                     "args": {"CommandLine": json.dumps(self.gate_cmd()),
                                                              "WaitMsBeforeAsync": "\"5000\""}}]},
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "created_at": TS,
             "content": "<USER_REQUEST>drive it</USER_REQUEST>"},
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE", "created_at": TS, "content": "gated"},
            {"step_index": 3, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": TS, "content": "s"}])
        self.agy("ag-unit", [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": TS,
                              "content": f"<USER_REQUEST>{self.packet_line}</USER_REQUEST>"},
                             {"step_index": 1, "source": "MODEL", "type": "GENERIC", "status": "ERROR", "error": "boom"}],
                 full=False)
        self.agy("ag-else", [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": self.packet_line}])

        res = self.transcripts("--node", self.node)
        node = res["nodes"][self.node]
        self.assertEqual([(u["agent"], u["sessionId"]) for u in node["units"]], [("antigravity", "ag-unit")])
        self.assertEqual(node["units"][0]["parent"], {"agent": "antigravity", "sessionId": "ag-ctl"})
        self.assertTrue(node["units"][0]["source"].endswith("transcript.jsonl"))
        ctl = node["controller"][0]
        self.assertEqual((ctl["sessionId"], ctl["title"], ctl["cwd"]), ("ag-ctl", "Drive TX-1", self.ws))
        self.assertEqual([s["role"] for s in ctl["steps"]], ["user", "agent", "tool"])
        self.assertEqual(ctl["steps"][0]["text"], "drive it")
        self.assertEqual(ctl["steps"][2]["tool"]["input"]["CommandLine"], self.gate_cmd())
        self.assertEqual(ctl["steps"][2]["tool"]["output"], "gated")

    def test_antigravity_without_database_is_best_effort(self) -> None:
        self.agy("ag-1", [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": self.packet_line}])
        self.agy("ag-2", ["garbage", "[1, 2]"])
        (self.homes / "agy" / "conversation_summaries.db").write_bytes(b"not a database")
        self.assertEqual(S.antigravity_summaries(self.homes / "agy"), {})
        res = self.transcripts()
        self.assertEqual([u["sessionId"] for u in res["nodes"][self.node]["units"]], ["ag-1"])

    # --- dispatch logs ---------------------------------------------------------------------------------
    def test_dispatch_log(self) -> None:
        logs = self.tdir() / "logs"
        (logs / f"{self.pk['dispatchId']}.dispatch.log").write_text(
            f"$ codex exec 'Read the packet'\nexit=0\n\n--- STDOUT ---\nunit output\n\n--- STDERR ---\nwarn\n",
            encoding="utf-8")
        (logs / "junk.dispatch.log").write_text("no header", encoding="utf-8")
        res = self.transcripts("--node", self.node)
        u = res["nodes"][self.node]["units"][0]
        self.assertEqual((u["agent"], u["match"], u["exitCode"], u["runner"], u["running"]),
                         ("dispatch", "exact", 0, "codex", False))
        self.assertEqual([s["role"] for s in u["steps"]], ["tool", "agent", "tool"])
        self.assertEqual(u["steps"][1]["text"], "unit output")
        self.assertEqual(u["steps"][2]["tool"]["output"], "warn")
        self.assertEqual(u["record"]["status"], "ok")

    # --- robustness ------------------------------------------------------------------------------------
    def test_parse_failures_never_crash(self) -> None:
        proj = self.claude_dir()
        write_jsonl(proj / "bad.jsonl", ["{{{", "[]", '{"type": "user", "message": 5}'])
        (proj / "bin.jsonl").write_bytes(b"\xff\xfe\x00garbage\n")
        write_jsonl(self.homes / "codex" / "sessions" / "2026" / "11" / "23" / "rollout-bad.jsonl", ["nope"])
        self.assertIsNone(S.read_claude(proj / "bad.jsonl"))
        self.assertIsNone(S.read_claude(proj / "missing.jsonl"))
        self.assertIsNone(S.read_codex(proj / "missing.jsonl"))
        self.assertIsNone(S.read_antigravity(proj / "missing.jsonl", conversation_id="x"))
        res = self.transcripts()
        self.assertTrue(res["ok"])
        self.assertEqual(res["nodes"][self.node], {"units": [], "controller": []})

    def test_packet_of_another_ticket_is_not_exact(self) -> None:
        other = self.packet_line.replace(str(self.tdir()), str(self.tmp / "tickets" / "OTHER"))
        write_jsonl(self.claude_dir() / "x" / "subagents" / "agent-1.jsonl", claude_rows("x", self.ws, other, agent_id="1"))
        self.assertEqual(self.transcripts()["nodes"][self.node]["units"], [])

    def test_old_logs_are_skipped_and_unknown_node_is_an_error(self) -> None:
        f = write_jsonl(self.claude_dir() / "old.jsonl", claude_rows("old", self.ws, "go", tool_cmd=self.gate_cmd()))
        age(f, 10 * 86400)
        self.assertEqual(self.transcripts()["nodes"][self.node]["controller"], [])
        code, res = run("transcripts", "--ticket", self.t, "--node", "nope", *self.p)
        self.assertEqual(code, 2)
        self.assertIn("nope", res["error"])

    def test_sdlc_call_parsing(self) -> None:
        b = S._Builder("claude", self.profile, 10, 120, time.time())
        b.add("tool", "", TS, {"name": "PowerShell", "input": {"command":
              f'& .sdlc\\bin\\sdlc.cmd packet --ticket "{self.t}" --node={self.node} --unit worker; sdlc status --ticket {self.t}'}})
        b.add("tool", "", TS, {"name": "x", "input": {"argv": ["python", "-m", "harness.cli", "record", "--ticket", "T2", "--node", "n"]}})
        self.assertEqual([(c["command"], c["ticket"], c["node"], c["unit"]) for c in b.sdlc],
                         [("packet", self.t, self.node, "worker"), ("record", "T2", "n", None)])

    def test_split_dispatch(self) -> None:
        self.assertEqual(S.split_dispatch("code-review.g2.semantic-review.r3", ["code-review"]),
                         {"node": "code-review", "generation": 2, "unit": "semantic-review", "round": 3})
        self.assertEqual(S.split_dispatch("a.g1.b.g1.worker.r1", ["a.g1.b"])["node"], "a.g1.b")
        self.assertIsNone(S.split_dispatch("not-a-dispatch"))


if __name__ == "__main__":
    unittest.main()
