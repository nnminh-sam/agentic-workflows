"""`sdlc cost`, harness/cost.py, `/api/cost` and the measured STATUS field (M3 step 1).

Every log here is a SYNTHETIC fixture shaped like the real formats (field names only); no real conversation text."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_lifecycle import Base, run  # noqa: E402
from test_sessions import write_jsonl  # noqa: E402

from harness import cost as C  # noqa: E402
from harness import sessions as S  # noqa: E402
from harness import web  # noqa: E402
from harness.cli import main  # noqa: E402


def local_ts(day: date, hour: int, minute: int = 0) -> str:
    """An ISO timestamp at a local wall-clock time, so local-day bucketing is TZ independent."""
    return datetime(day.year, day.month, day.day, hour, minute).astimezone().isoformat()


D1 = date(2026, 11, 20)
D2 = date(2026, 11, 21)


def usage(i: int, o: int, cr: int = 0, cw: int = 0) -> dict:
    return {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
            "service_tier": "standard"}


class CostBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.t = "CO-1"
        code, res = run("init", "--ticket", self.t, "--kind", "bug", *self.p)
        self.assertEqual(code, 0, res)
        state = json.loads((self.tdir(self.t) / "state.json").read_text(encoding="utf-8"))
        self.nodes = list(state["nodes"].keys())
        self.node, self.node2 = self.nodes[0], self.nodes[1]
        code, plan = run("plan", "--ticket", self.t, "--node", self.node, *self.p)
        self.unit = plan["units"][0]["id"]
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
        self.proj = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / S.claude_slug(self.ws)

    # --- fixtures --------------------------------------------------------------------------------------
    def gate(self, node: str, ticket: str | None = None) -> str:
        return f"sh .sdlc/bin/sdlc gate --ticket {ticket or self.t} --node {node} --status approved"

    def user(self, session: str, text: str, ts: str) -> dict:
        return {"type": "user", "sessionId": session, "cwd": self.ws, "timestamp": ts, "uuid": "u-" + ts,
                "message": {"role": "user", "content": text}}

    def asst(self, session: str, mid: str, model: str, u: dict, ts: str, tool_cmd: str | None = None) -> dict:
        content: list = [{"type": "text", "text": "synthetic"}]
        if tool_cmd:
            content.append({"type": "tool_use", "id": "tu-" + mid, "name": "Bash", "input": {"command": tool_cmd}})
        return {"type": "assistant", "sessionId": session, "cwd": self.ws, "timestamp": ts, "uuid": "a-" + mid + ts,
                "message": {"id": mid, "role": "assistant", "model": model, "content": content, "usage": u}}

    def cost_state(self, session: str, total: float, models: dict) -> dict:
        return {"type": "cost-state", "sessionId": session, "totalCostUSD": total, "hasUnknownModelCost": False,
                "modelUsage": {m: {"inputTokens": v[1], "outputTokens": v[2], "cacheReadInputTokens": 0,
                                   "cacheCreationInputTokens": 0, "costUSD": v[0]} for m, v in models.items()}}

    def prices(self, models: dict) -> Path:
        path = Path(os.environ["KANEKI_HOME"]) / "prices.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "models": models}), encoding="utf-8")
        return path

    def report(self, *extra: str) -> dict:
        before = self.snapshot()
        code, res = run("cost", "--ticket", self.t, "--json", *extra, *self.p)
        self.assertEqual(code, 0, res)
        self.assertEqual(self.snapshot(), before, "cost wrote into the workspace")
        return res

    def snapshot(self) -> dict[str, str]:
        return {str(p.relative_to(self.tmp)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.tmp.rglob("*")) if p.is_file()}

    def node_of(self, rep: dict, node: str) -> dict:
        return next(n for n in rep["nodes"] if n["node"] == node)

    def controller_with_subagent(self) -> None:
        ts = local_ts(D1, 10)
        write_jsonl(self.proj / "ctl.jsonl", [
            self.user("ctl", "drive the ticket", ts),
            # one API message over two rows sharing message.id: counted once, the last row's usage wins
            self.asst("ctl", "m1", "claude-opus-5-5", usage(1, 5, 100, 10), ts, tool_cmd=self.gate(self.node)),
            self.asst("ctl", "m1", "claude-opus-5-5", usage(1, 9, 100, 10), ts),
            self.asst("ctl", "m2", "claude-opus-5-5", usage(2, 20, 200, 0), local_ts(D1, 10, 5)),
            {"type": "summary", "summary": "x"}, "{not json"])
        write_jsonl(self.proj / "ctl" / "subagents" / "agent-w1.jsonl", [
            self.user("ctl", self.packet_line, ts),
            self.asst("ctl", "s1", "claude-sonnet-5-5", usage(3, 30, 300, 30), local_ts(D1, 10, 2))])


class MeasureTests(CostBase):
    def test_message_id_counted_once_and_subagent_included(self) -> None:
        self.controller_with_subagent()
        rep = self.report()
        n = self.node_of(rep, self.node)
        self.assertEqual(n["controller"]["tokens"], (1 + 9 + 100 + 10) + (2 + 20 + 200))
        self.assertEqual(n["controller"]["messages"], 2)
        self.assertEqual(n["units"]["tokens"], 3 + 30 + 300 + 30, "the subagent with the packet is the unit")
        self.assertEqual(rep["total"]["messages"], 3)
        self.assertEqual(n["reported"]["tokens"], 100)
        self.assertEqual(n["ratio"], round(363 / 100, 3))
        self.assertEqual(rep["total"]["source"], "unknown")
        self.assertIsNone(rep["total"]["usd"], "no reported cost and no price: USD unknown, tokens still shown")
        self.assertEqual(rep["total"]["unpricedTokens"], rep["total"]["tokens"])
        models = {m["model"]: m for m in rep["byModel"]}
        self.assertEqual(models["claude-sonnet-5-5"]["cacheWrite"], 30)
        self.assertEqual(models["claude-opus-5-5"]["output"], 29)

    def test_same_message_id_in_two_logs_counts_once(self) -> None:
        self.controller_with_subagent()
        ts = local_ts(D1, 11)
        write_jsonl(self.proj / "resumed.jsonl", [  # a resumed copy repeats m2
            self.asst("resumed", "x0", "claude-opus-5-5", usage(0, 1), ts, tool_cmd=self.gate(self.node)),
            self.asst("resumed", "m2", "claude-opus-5-5", usage(2, 20, 200, 0), ts)])
        rep = self.report()
        self.assertEqual(rep["total"]["messages"], 4)

    def test_cost_state_is_reported_and_spread_not_added(self) -> None:
        self.controller_with_subagent()
        rows = (self.proj / "ctl.jsonl").read_text(encoding="utf-8")
        cs = self.cost_state("ctl", 1.51, {"claude-opus-5-5": (1.0, 5, 50), "claude-sonnet-5-5": (0.5, 3, 30),
                                           "claude-haiku-5-5": (0.01, 7, 1)})
        (self.proj / "ctl.jsonl").write_text(rows + json.dumps(cs) + "\n", encoding="utf-8")
        self.prices({"claude-opus-5-5": {"input": 1, "output": 1, "cacheRead": 1, "cacheWrite": 1}})
        rep = self.report()
        self.assertAlmostEqual(rep["total"]["usd"], 1.51, places=6)
        self.assertEqual(rep["total"]["source"], "reported", "the agent's own cost wins over the price table")
        n = self.node_of(rep, self.node)
        self.assertAlmostEqual(n["units"]["usd"], 0.5, places=6)
        self.assertAlmostEqual(n["controller"]["usd"], 1.01, places=6)
        side = next(m for m in rep["byModel"] if m["model"] == "claude-haiku-5-5")
        self.assertEqual((side["messages"], side["tokens"]), (0, 8), "a model with no logged message is a side record")

    def test_cost_state_shared_with_work_outside_the_ticket(self) -> None:
        ts1, ts2 = local_ts(D1, 9), local_ts(D1, 9, 30)
        write_jsonl(self.proj / "two.jsonl", [
            self.asst("two", "a1", "claude-opus-5-5", usage(0, 10), ts1, tool_cmd=self.gate(self.node)),
            self.asst("two", "a2", "claude-opus-5-5", usage(0, 30), ts2, tool_cmd=self.gate("x", ticket="OTHER-9")),
            self.asst("two", "a3", "claude-opus-5-5", usage(0, 60), ts2),
            self.cost_state("two", 1.0, {"claude-opus-5-5": (1.0, 0, 100)})])
        rep = self.report()
        self.assertEqual(rep["total"]["tokens"], 40, "messages after the call for another ticket belong to it")
        self.assertAlmostEqual(rep["total"]["usd"], 0.4, places=6, msg="only this ticket's share of the session")

    def test_priced_unknown_and_unverified(self) -> None:
        self.controller_with_subagent()
        self.prices({"claude-opus": {"input": 10, "output": 20, "cacheRead": 1, "cacheWrite": 5},
                     "claude-sonnet-5-5": {"input": 3, "output": None, "cacheRead": 1, "cacheWrite": 1}})
        rep = self.report()
        n = self.node_of(rep, self.node)
        want = ((1 + 2) * 10 + (9 + 20) * 20 + 300 * 1 + 10 * 5) / 1e6
        self.assertAlmostEqual(n["controller"]["usd"], want, places=9)
        self.assertEqual(n["controller"]["source"], "priced")
        self.assertIsNone(n["units"]["usd"], "a missing output price leaves that model's USD unknown")
        self.assertEqual(n["units"]["source"], "unknown")
        self.assertEqual(rep["total"]["source"], "mixed")
        self.assertEqual(rep["total"]["sources"], ["priced", "unknown"])
        self.assertEqual(rep["total"]["unpricedTokens"], 363)
        p = rep["prices"]
        self.assertEqual([(u["model"], u["entry"], u["verified"]) for u in p["used"]],
                         [("claude-opus-5-5", "claude-opus", False), ("claude-sonnet-5-5", "claude-sonnet-5-5", False)])
        self.assertFalse(p["allVerified"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["cost", "--ticket", self.t, *self.p]), 0)
        text = buf.getvalue()
        self.assertIn("UNVERIFIED", text)
        self.assertIn("priced, unverified", text)
        self.prices({"claude-opus-5-5": {"input": 10, "output": 20, "cacheRead": 1, "cacheWrite": 5, "verified": True}})
        rep = self.report()
        self.assertTrue(rep["prices"]["used"][0]["verified"], "a price-file change refreshes the cached answer")

    def test_per_node_attribution_by_time(self) -> None:
        write_jsonl(self.proj / "ctl.jsonl", [
            self.asst("ctl", "b0", "claude-opus-5-5", usage(0, 1), local_ts(D1, 8)),  # before any call: first node
            self.asst("ctl", "b1", "claude-opus-5-5", usage(0, 10), local_ts(D1, 9), tool_cmd=self.gate(self.node)),
            self.asst("ctl", "b2", "claude-opus-5-5", usage(0, 100), local_ts(D1, 9, 10)),
            self.asst("ctl", "b3", "claude-opus-5-5", usage(0, 1000), local_ts(D1, 9, 20), tool_cmd=self.gate(self.node2)),
            self.asst("ctl", "b4", "claude-opus-5-5", usage(0, 10000), local_ts(D1, 9, 30)),
            self.asst("ctl", "b5", "claude-opus-5-5", usage(0, 100000), local_ts(D1, 9, 40),
                      tool_cmd=f"sdlc plan --ticket {self.t}")])  # no --node: stays on the last node
        write_jsonl(self.proj / "ctl" / "subagents" / "agent-h.jsonl", [  # no packet, no calls: follows the parent
            self.user("ctl", "help", local_ts(D1, 9, 35)),
            self.asst("ctl", "h1", "claude-haiku-5-5", usage(0, 7), local_ts(D1, 9, 35))])
        rep = self.report()
        self.assertEqual(self.node_of(rep, self.node)["controller"]["tokens"], 1 + 10 + 100 + 1000)
        self.assertEqual(self.node_of(rep, self.node2)["controller"]["tokens"], 10000 + 100000 + 7)
        one = self.report("--node", self.node2)
        self.assertEqual([n["node"] for n in one["nodes"]], [self.node2])
        self.assertEqual(one["total"]["tokens"], 110007)
        code, bad = run("cost", "--ticket", self.t, "--node", "nope", "--json", *self.p)
        self.assertEqual(code, 2, bad)

    def test_since_range(self) -> None:
        write_jsonl(self.proj / "ctl.jsonl", [
            self.asst("ctl", "d1", "claude-opus-5-5", usage(0, 10), local_ts(D1, 23, 30), tool_cmd=self.gate(self.node)),
            self.asst("ctl", "d2", "claude-opus-5-5", usage(0, 200), local_ts(D2, 0, 30))])
        code, day2 = run("cost", "--since", D2.isoformat(), "--json", *self.p)
        self.assertEqual(code, 0, day2)
        self.assertEqual(day2["total"]["tokens"], 200)
        self.assertEqual([(t["ticket"], t["tokens"]) for t in day2["tickets"]], [(self.t, 200)])
        code, day1 = run("cost", "--since", D1.isoformat(), "--until", D1.isoformat(), "--json", *self.p)
        self.assertEqual(day1["total"]["tokens"], 10)
        code, both = run("cost", "--since", D1.isoformat(), "--json", *self.p)
        self.assertEqual([(d["day"], d["tokens"]) for d in both["byDay"]], [(D1.isoformat(), 10), (D2.isoformat(), 200)])
        self.assertEqual(self.report("--since", D2.isoformat())["total"]["tokens"], 200)
        self.assertEqual(C.measured(self.profile, self.t, self.node, D2)["tokens"], 200)
        self.assertEqual(C.measured(self.profile, None, None, D1.isoformat())["tokens"], 10)
        code, today = run("cost", "--since", "today", "--json", *self.p)
        self.assertEqual((code, today["window"]["since"]), (0, date.today().isoformat()))
        self.assertEqual(C.parse_day("yesterday", D2), D1)
        for argv in (["--since", "21/11/2026"], ["--until", D1.isoformat()], []):
            code, err = run("cost", "--json", *argv, *self.p)
            self.assertEqual(code, 2, (argv, err))

    def test_codex_rollout_unverified_format(self) -> None:
        root = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "11" / "20"
        ts = local_ts(D1, 12)

        def tc(i: int, c: int, o: int) -> dict:
            return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o,
                                      "reasoning_output_tokens": 0, "total_tokens": i + o}}}}
        write_jsonl(root / "rollout-2026-11-20T12-00-00-cx1.jsonl", [
            {"timestamp": ts, "type": "session_meta", "payload": {"id": "cx1", "cwd": self.ws, "timestamp": ts}},
            {"timestamp": ts, "type": "turn_context", "payload": {"cwd": self.ws, "model": "gpt-test"}},
            {"timestamp": ts, "type": "response_item", "payload": {"type": "message", "role": "user",
                                                                     "content": [{"type": "input_text", "text": self.packet_line}]}},
            tc(100, 40, 10), tc(100, 40, 10),  # a repeated total (rate-limit update) is not new usage
            tc(250, 140, 30)])
        rep = self.report()
        m = next(x for x in rep["byModel"] if x["agent"] == "codex")
        self.assertEqual((m["model"], m["input"], m["cacheRead"], m["output"], m["messages"]), ("gpt-test", 110, 140, 30, 2))
        self.assertEqual(self.node_of(rep, self.node)["units"]["tokens"], 280)

    def test_antigravity_without_usage_is_unknown_not_guessed(self) -> None:
        logs = Path(os.environ["ANTIGRAVITY_CLI_ROOT"]) / "brain" / "conv-1" / ".system_generated" / "logs"
        write_jsonl(logs / "transcript.jsonl", [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": self.packet_line},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "synthetic",
             "tool_calls": [{"name": "run_command", "args": {"CommandLine": "\"ls\""}}]},
            {"step_index": 2, "source": "MODEL", "type": "RUN_COMMAND", "content": "ok"}])
        rep = self.report()
        n = self.node_of(rep, self.node)
        self.assertEqual((n["units"]["tokens"], n["units"]["unknownSteps"]), (0, 1))
        self.assertEqual(rep["total"]["source"], "unknown")
        write_jsonl(logs / "transcript.jsonl", [
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": self.packet_line},
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "synthetic", "model": "gem-test",
             "usage_metadata": {"prompt_token_count": 50, "cached_content_token_count": 20, "candidates_token_count": 5,
                                "thoughts_token_count": 2}}])
        m = next(x for x in self.report()["byModel"] if x["agent"] == "antigravity")
        self.assertEqual((m["model"], m["input"], m["cacheRead"], m["output"]), ("gem-test", 30, 20, 7))

    def test_dispatch_log_reported_and_not_double_counted(self) -> None:
        logs = self.tdir(self.t) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        result = {"type": "result", "total_cost_usd": 0.25, "usage": usage(4, 40, 400, 4),
                  "modelUsage": {"claude-sonnet-5-5": {"costUSD": 0.25}}}
        (logs / f"{self.pk['dispatchId']}.dispatch.log").write_text(
            f"$ claude -p x\nexit=0\n\n--- STDOUT ---\n{json.dumps(result)}\n\n--- STDERR ---\n\n", encoding="utf-8")
        rep = self.report()
        n = self.node_of(rep, self.node)
        self.assertEqual((n["units"]["tokens"], n["units"]["usd"], n["units"]["source"]), (448, 0.25, "reported"))
        self.assertEqual(n["agents"][0]["kind"], "dispatch")
        write_jsonl(self.proj / "w.jsonl", [  # the same dispatch's own session log: it wins, counted once
            self.user("w", self.packet_line, local_ts(D1, 10)),
            self.asst("w", "w1", "claude-sonnet-5-5", usage(4, 40, 400, 4), local_ts(D1, 10))])
        n = self.node_of(self.report(), self.node)
        self.assertEqual((n["units"]["tokens"], [a["kind"] for a in n["agents"]]), (448, ["unit"]))

    def test_unrelated_sessions_are_ignored(self) -> None:
        write_jsonl(self.proj / "other.jsonl", [self.asst("other", "o1", "claude-opus-5-5", usage(0, 9),
                                                          local_ts(D1, 9), tool_cmd="git status")])
        self.assertEqual(self.report()["total"]["tokens"], 0)


class CacheTests(CostBase):
    def test_cached_by_fingerprint_and_incremental(self) -> None:
        self.controller_with_subagent()
        with mock.patch.object(C, "scan_claude", wraps=C.scan_claude) as scan:
            first = C.ticket_report(self.profile, self.t)
            self.assertEqual(scan.call_count, 2)
            with mock.patch.object(C, "_records", wraps=C._records) as recs:
                self.assertEqual(C.ticket_report(self.profile, self.t)["total"], first["total"])
                self.assertEqual(recs.call_count, 0, "nothing changed: served from the scope cache")
            log = self.proj / "ctl.jsonl"
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(self.asst("ctl", "m3", "claude-opus-5-5", usage(0, 1000), local_ts(D1, 11))) + "\n")
            again = C.ticket_report(self.profile, self.t)
            self.assertEqual(again["total"]["tokens"], first["total"]["tokens"] + 1000)
            self.assertEqual(scan.call_count, 3, "only the changed log is read again")
            prev = scan.call_args[0][1]
            self.assertIsNotNone(prev, "a grown log continues from the last read")
            self.assertGreater(prev["offset"], 0)
        # a half-written last line is left for the next read
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.asst("ctl", "m4", "claude-opus-5-5", usage(0, 5), local_ts(D1, 12)))[:40])
        self.assertEqual(C.ticket_report(self.profile, self.t)["total"]["tokens"], again["total"]["tokens"])

    def test_cache_lives_in_the_user_config_dir(self) -> None:
        self.controller_with_subagent()
        C.ticket_report(self.profile, self.t)
        self.assertTrue(list((Path(os.environ["KANEKI_HOME"]) / "cache" / "cost" / "files").glob("*.json")))
        self.assertTrue(list((Path(os.environ["KANEKI_HOME"]) / "cache" / "cost" / "scopes").glob("*.json")))

    def test_price_file_location_and_template(self) -> None:
        self.assertEqual(C.price_path(), Path(os.environ["KANEKI_HOME"]) / "prices.json")
        with mock.patch.dict(os.environ, {"KANEKI_PRICES": str(self.user_home / "elsewhere.json")}):
            self.assertEqual(C.price_path(), self.user_home / "elsewhere.json")
        code, res = run("cost", "--init-prices", *self.p)
        self.assertEqual((code, res["written"]), (0, True))
        tpl = json.loads(Path(res["path"]).read_text(encoding="utf-8"))
        self.assertTrue(all(all(e[k] is None for k in C.PRICE_KEYS) and e["verified"] is False
                            for e in tpl["models"].values()), "placeholders only: no invented prices")
        code, res = run("cost", "--init-prices", *self.p)
        self.assertEqual((code, res["written"]), (0, False))
        Path(res["path"]).write_text("{bad", encoding="utf-8")
        self.assertIn("cannot read price file", self.report()["prices"]["error"])


class StatusFieldTests(CostBase):
    def test_status_md_and_tickets_carry_measured(self) -> None:
        self.controller_with_subagent()
        code, res = run("lang", "--ticket", self.t, "--set", "delivery=en", "--reason", "test", *self.p)
        self.assertEqual(code, 0, res)  # any change refreshes STATUS.md
        status = (self.tdir(self.t) / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("**Measured**", status)
        self.assertIn("705 tokens", status)
        self.assertIn("USD unknown", status)
        code, res = run("tickets", *self.p)
        self.assertEqual(code, 0, res)
        m = res["tickets"][0]["measured"]
        self.assertEqual((m["tokens"], m["usd"], m["source"], m["reportedTokens"]), (705, None, "unknown", 100))
        self.assertEqual(res["contractVersion"], 1)


class CostApiTests(CostBase):
    def setUp(self) -> None:
        super().setUp()
        self.board = web.Board(self.profile)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.board))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def get(self, path: str, **query: str) -> tuple[int, dict]:
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_api_cost_ticket_today_errors_and_recheck(self) -> None:
        self.controller_with_subagent()
        code, res = self.get("/api/cost", id=self.t)
        self.assertEqual(code, 200, res)
        self.assertEqual(res["total"]["tokens"], 705)
        code, one = self.get("/api/cost", id=self.t, node=self.node)
        self.assertEqual([n["node"] for n in one["nodes"]], [self.node])
        code, today = self.get("/api/cost", since="today")
        self.assertEqual((code, today["window"]["since"]), (200, date.today().isoformat()))
        code, day = self.get("/api/cost", since=D1.isoformat(), until=D1.isoformat())
        self.assertEqual(day["total"]["tokens"], 705)
        for bad in ({}, {"id": "../x"}, {"id": "NOPE"}, {"since": "soon"}, {"node": self.node, "since": "today"},
                    {"id": self.t, "node": "nope"}):
            self.assertEqual(self.get("/api/cost", **bad)[0], 400, bad)
        with mock.patch.object(C, "ticket_report", wraps=C.ticket_report) as rep:
            self.board._cost_cache.clear()
            self.get("/api/cost", id=self.t)
            self.get("/api/cost", id=self.t)
            self.assertEqual(rep.call_count, 1, "polls within COST_RECHECK_SECONDS reuse the answer")
            with mock.patch.object(web.time, "time", return_value=time.time() + web.COST_RECHECK_SECONDS + 1):
                self.get("/api/cost", id=self.t)
            self.assertEqual(rep.call_count, 2)

    def test_page_has_cost_tab_and_today(self) -> None:
        with urllib.request.urlopen(self.base + "/") as r:
            html = r.read().decode("utf-8")
        for needle in ("function costView", "/api/cost?since=today", "/api/cost?id=", 'id="today"',
                       "priced · unverified", "USD unknown"):
            self.assertIn(needle, html)
        self.assertNotIn("arrives with", html, "the M3 placeholder is gone")
        self.assertNotIn("<script src", html, "the page stays offline")


if __name__ == "__main__":  # pragma: no cover
    import unittest
    unittest.main()
