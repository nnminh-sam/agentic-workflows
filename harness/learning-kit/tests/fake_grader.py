"""Stand-in for `claude -p` in tests: full credit when the answer contains 'correct'."""
import json
import sys

prompt = sys.stdin.read()
items = json.loads(prompt.split("Items (JSON):\n", 1)[1])
schema_arg = "--json-schema" in sys.argv
grades = []
for it in items:
    ok = "correct" in (it.get("learner_answer") or "").lower()
    half = "partial" in (it.get("learner_answer") or "").lower()
    pts = [1 if ok else (0.5 if half else 0) for _ in it["rubric"]]
    grades.append({"id": it["id"], "points": pts, "feedback": "fake", "missed": [] if ok else ["all"],
                   "wrong_claims": []})
if schema_arg:
    print(json.dumps({"type": "result", "is_error": False, "structured_output": {"grades": grades}}))
else:  # like a CLI without --json-schema: the JSON arrives as text, maybe fenced, in `result`
    reply = "Here you go:\n```json\n" + json.dumps({"grades": grades}) + "\n```"
    print(json.dumps({"type": "result", "is_error": False, "result": reply}))
