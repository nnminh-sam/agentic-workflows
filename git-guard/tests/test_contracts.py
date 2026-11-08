import copy
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from gitguard import contracts as c


class ShippedContracts(unittest.TestCase):
    def test_shipped_contracts_are_valid(self):
        loaded = c.load()
        self.assertEqual(loaded.commit.rule("CM003").severity, "deny")

    def test_every_rule_has_examples(self):
        for name, data in c.load_raw().items():
            for r in data["rules"]:
                self.assertTrue(r.get("examples", {}).get("good") and r["examples"].get("bad"), f"{name} {r['id']}")


class Validation(unittest.TestCase):
    def setUp(self):
        self.raw = copy.deepcopy(c.load_raw())

    def problems(self, name):
        return c.validate(name, self.raw[name])

    def test_bad_severity(self):
        self.raw["commit-message"]["rules"][0]["severity"] = "error"
        self.assertTrue(any("severity" in p for p in self.problems("commit-message")))

    def test_missing_and_unknown_rules(self):
        self.raw["identity"]["rules"].pop()
        self.raw["identity"]["rules"].append({**self.raw["identity"]["rules"][0], "id": "ID999"})
        probs = self.problems("identity")
        self.assertTrue(any("missing rules ['ID005']" in p for p in probs), probs)
        self.assertTrue(any("unknown rule ids ['ID999']" in p for p in probs), probs)

    def test_duplicate_id(self):
        self.raw["github-cli"]["rules"].append(copy.deepcopy(self.raw["github-cli"]["rules"][0]))
        self.assertTrue(any("duplicate" in p for p in self.problems("github-cli")))

    def test_bad_regex(self):
        for r in self.raw["identity"]["rules"]:
            if r["id"] == "ID002":
                r["params"]["patterns"] = ["(unclosed"]
        self.assertTrue(any("bad regex" in p for p in self.problems("identity")))

    def test_bad_policy_decision(self):
        for r in self.raw["github-cli"]["rules"]:
            if r["id"] == "GH002":
                r["params"]["commands"][0]["decision"] = "block"
        self.assertTrue(any("decision must be" in p for p in self.problems("github-cli")))

    def test_types_required(self):
        self.raw["commit-message"]["settings"]["types"] = []
        self.assertTrue(any("settings.types" in p for p in self.problems("commit-message")))

    def test_load_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, fname in c.CONTRACT_FILES.items():
                Path(tmp, fname).write_text(json.dumps(self.raw[name]), encoding="utf-8")
            c.load(Path(tmp))  # valid copy loads
            Path(tmp, "identity.json").write_text("{ not json", encoding="utf-8")
            with self.assertRaises(c.ContractError):
                c.load(Path(tmp))


if __name__ == "__main__":
    unittest.main()
