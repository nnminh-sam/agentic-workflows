package engine

// Port of tests/test_contracts.py. The Validation class tests
// contracts.validate / contracts.load, which have no Go equivalent yet
// (built-in contracts are embedded; user overrides are validated by
// internal/config). Those cases are kept and skipped so the gap is visible.

import (
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/defaults"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/config"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/policy"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/verdict"
)

func TestShippedContracts(t *testing.T) {
	cs, err := config.LoadContracts(defaults.FS)
	if err != nil {
		t.Fatal(err)
	}
	t.Run("test_shipped_contracts_are_valid", func(t *testing.T) {
		if s := cs[policy.Commit].Rule("CM003").Severity; s != verdict.Deny {
			t.Errorf("CM003 severity %s", s)
		}
		want := map[string][]string{
			policy.Identity:  {"ID001", "ID002", "ID003", "ID004", "ID005", "ID006"},
			policy.Branching: {"BR001", "BR002", "BR003", "BR004", "BR005"},
		}
		for name, rids := range want {
			for _, id := range rids {
				if r := cs[name].Rule(id); r.Name == "" {
					t.Errorf("%s: missing %s", name, id)
				}
			}
		}
	})
	t.Run("test_every_rule_has_examples", func(t *testing.T) {
		for name, c := range cs {
			for _, r := range c.Rules {
				if len(r.Examples.Good) == 0 || len(r.Examples.Bad) == 0 {
					t.Errorf("%s %s: missing good/bad examples", name, r.ID)
				}
			}
		}
	})
	t.Run("id006_default_severity_warn", func(t *testing.T) {
		if s := cs[policy.Identity].Rule("ID006").Severity; s != verdict.Warn {
			t.Errorf("ID006 severity %s, want warn (spec: warn in default)", s)
		}
	})
}

func TestContractValidation(t *testing.T) {
	for _, name := range []string{"test_bad_severity", "test_missing_and_unknown_rules", "test_duplicate_id",
		"test_bad_regex", "test_bad_policy_decision", "test_types_required", "test_load_reports_invalid_json"} {
		t.Run(name, func(t *testing.T) {
			t.Skip("NOT PORTED: Go has no equivalent of contracts.validate/load for contract files")
		})
	}
	// Robustness instead: a bad regex in a param must not panic the engine.
	t.Run("bad_regex_param_does_not_panic", func(t *testing.T) {
		pol := testPolicy(t, testProfile(), setParam("ID002", "patterns", []any{"(unclosed"}))
		defer func() {
			if r := recover(); r != nil {
				t.Fatalf("panic: %v", r)
			}
		}()
		run(t, commitX, opts{pol: pol})
	})
}
