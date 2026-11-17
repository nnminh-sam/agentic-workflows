package workflow

import (
	"strings"
	"testing"
)

func TestBranchNewName(t *testing.T) {
	c := compileName(t, builtins(t), "default", CompileOptions{})
	tests := []struct {
		typ, name string
		want      string
		wantErr   string
	}{
		{"feat", "login", "develop/feat/login", ""},
		{"fix", "null-ptr", "develop/fix/null-ptr", ""},
		{"wip", "login", "", "unknown branch type"},
	}
	for _, tt := range tests {
		got, err := c.BranchNewName(tt.typ, tt.name, "", "")
		if tt.wantErr != "" {
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("%s/%s: err %v, want %q", tt.typ, tt.name, err, tt.wantErr)
			}
			continue
		}
		if err != nil || got != tt.want {
			t.Errorf("%s/%s = %q, %v; want %q", tt.typ, tt.name, got, err, tt.want)
		}
	}
}

func TestBranchNewNameNeedsTicket(t *testing.T) {
	c := compileName(t, releaseTrainCatalog(t), "release-train", CompileOptions{})
	if _, err := c.BranchNewName("feat", "login", "", ""); err == nil || !strings.Contains(err.Error(), "{ticket}") {
		t.Fatalf("want missing {ticket} error, got %v", err)
	}
	got, err := c.BranchNewName("feat", "login", "ABC-12", "")
	if err != nil || got != "feat/ABC-12-login" {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestBranchCutFor(t *testing.T) {
	c := compileName(t, releaseTrainCatalog(t), "release-train", CompileOptions{})
	tests := []struct {
		id, value  string
		name, base string
		wantErr    string
	}{
		{"cut", "1.2.0", "release/1.2.0", "develop", ""},
		{"hotfix", "login-crash", "hotfix/login-crash", "main", ""},
		{"cut", "", "", "", "needs a value for {version}"},
		{"merge", "x", "", "", "available: cut, hotfix"},
		{"nope", "x", "", "", "unknown transition"},
	}
	for _, tt := range tests {
		got, err := c.BranchCutFor(tt.id, tt.value)
		if tt.wantErr != "" {
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Errorf("%s %q: err %v, want %q", tt.id, tt.value, err, tt.wantErr)
			}
			continue
		}
		if err != nil || got.Name != tt.name || got.Base != tt.base {
			t.Errorf("%s %q = %+v, %v; want %s from %s", tt.id, tt.value, got, err, tt.name, tt.base)
		}
	}
}

func TestBranchCutDefaultHasNone(t *testing.T) {
	c := compileName(t, builtins(t), "default", CompileOptions{})
	_, err := c.BranchCutFor("release", "1.0.0")
	if err == nil || !strings.Contains(err.Error(), "no branch transitions") {
		t.Fatalf("got %v", err)
	}
}

func TestBranchPrunable(t *testing.T) {
	c := compileName(t, releaseTrainCatalog(t), "release-train", CompileOptions{})
	for name, want := range map[string]bool{
		"feat/ABC-1-x": true, "hotfix/a": true, "develop": false, "main": false, "release/1.0": false, "random": false,
	} {
		if got := c.BranchPrunable(name); got != want {
			t.Errorf("BranchPrunable(%q) = %v, want %v", name, got, want)
		}
	}
}
