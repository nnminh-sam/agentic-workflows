package verdict

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestSeverityParse(t *testing.T) {
	tests := []struct {
		in      string
		want    Severity
		wantErr bool
	}{
		{"off", Off, false},
		{"warn", Warn, false},
		{"ask", Ask, false},
		{"deny", Deny, false},
		{"DENY", Deny, false},
		{"  Warn ", Warn, false},
		{"", Off, true},
		{"block", Off, true},
		{"error", Off, true},
	}
	for _, tt := range tests {
		got, err := ParseSeverity(tt.in)
		if (err != nil) != tt.wantErr || got != tt.want {
			t.Errorf("ParseSeverity(%q) = %v, %v; want %v, err=%v", tt.in, got, err, tt.want, tt.wantErr)
		}
	}
}

func TestSeverityOrderAndString(t *testing.T) {
	if !(Off < Warn && Warn < Ask && Ask < Deny) {
		t.Fatal("severity order must be off < warn < ask < deny")
	}
	for s, want := range map[Severity]string{Off: "off", Warn: "warn", Ask: "ask", Deny: "deny", 9: "Severity(9)", -1: "Severity(-1)"} {
		if got := s.String(); got != want {
			t.Errorf("Severity(%d).String() = %q, want %q", int(s), got, want)
		}
	}
}

func TestSeverityJSON(t *testing.T) {
	type wrap struct {
		S Severity `json:"s"`
	}
	for _, s := range []Severity{Off, Warn, Ask, Deny} {
		b, err := json.Marshal(wrap{s})
		if err != nil {
			t.Fatal(err)
		}
		if want := `{"s":"` + s.String() + `"}`; string(b) != want {
			t.Errorf("marshal = %s, want %s", b, want)
		}
		var w wrap
		if err := json.Unmarshal(b, &w); err != nil || w.S != s {
			t.Errorf("round trip %v: got %v, %v", s, w.S, err)
		}
	}
	var w wrap
	if err := json.Unmarshal([]byte(`{"s":"nope"}`), &w); err == nil {
		t.Error("unmarshal of unknown severity should fail")
	}
	if err := json.Unmarshal([]byte(`{"s":"Ask"}`), &w); err != nil || w.S != Ask {
		t.Errorf("case-insensitive unmarshal: %v %v", w.S, err)
	}
}

func TestDecisionExitCode(t *testing.T) {
	for d, want := range map[Decision]int{Allow: 0, DecWarn: 0, DecAsk: 1, DecDeny: 2, "": 0} {
		if got := d.ExitCode(); got != want {
			t.Errorf("%q.ExitCode() = %d, want %d", d, got, want)
		}
	}
}

func TestVerdictDecision(t *testing.T) {
	tests := []struct {
		name string
		sevs []Severity
		want Decision
	}{
		{"empty", nil, Allow},
		{"off only", []Severity{Off}, Allow},
		{"warn", []Severity{Warn}, DecWarn},
		{"warn+ask", []Severity{Warn, Ask, Warn}, DecAsk},
		{"deny wins", []Severity{Ask, Deny, Warn}, DecDeny},
		{"deny first", []Severity{Deny, Warn}, DecDeny},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var v Verdict
			for i, s := range tt.sevs {
				v.Add("R"+string(rune('0'+i)), s, "m")
			}
			if got := v.Decision(); got != tt.want {
				t.Errorf("Decision = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestVerdictAddSkipsOff(t *testing.T) {
	var v Verdict
	v.Add("A", Off, "x")
	v.Addf("B", Warn, "n=%d", 3)
	if len(v.Findings) != 1 || v.Findings[0] != (Finding{RuleID: "B", Severity: Warn, Message: "n=3"}) {
		t.Errorf("Findings = %+v", v.Findings)
	}
	var w Verdict
	w.Add("C", Deny, "d")
	v.Extend(w)
	v.Extend(Verdict{})
	if len(v.Findings) != 2 || v.Decision() != DecDeny {
		t.Errorf("after Extend: %+v", v.Findings)
	}
}

func TestVerdictOf(t *testing.T) {
	var v Verdict
	v.Add("A", Warn, "a")
	v.Add("B", Deny, "b")
	v.Add("C", Warn, "c")
	got := v.Of(Warn)
	if len(got) != 2 || got[0].RuleID != "A" || got[1].RuleID != "C" {
		t.Errorf("Of(Warn) = %+v", got)
	}
	if v.Of(Ask) != nil {
		t.Error("Of(Ask) should be nil")
	}
}

func TestVerdictReason(t *testing.T) {
	tests := []struct {
		name string
		add  []Finding
		want string
	}{
		{"deny only", []Finding{{"CM003", Deny, "header too long"}}, "mygit: [CM003] header too long"},
		{"blocking first, then warnings", []Finding{
			{"W1", Warn, "w one"},
			{"D1", Deny, "d one"},
			{"A1", Ask, "a one"},
			{"W2", Warn, "w two"},
		}, "mygit: [D1] d one\n[A1] a one\nWarnings: [W1] w one; [W2] w two"},
		{"warnings only", []Finding{{"W1", Warn, "x"}}, "mygit: Warnings: [W1] x"},
		{"empty", nil, "mygit: "},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			v := Verdict{Findings: tt.add}
			if got := v.Reason(); got != tt.want {
				t.Errorf("Reason =\n%q\nwant\n%q", got, tt.want)
			}
		})
	}
}

func TestFindingJSON(t *testing.T) {
	v := Verdict{}
	v.Add("ID001", Deny, "no name")
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"findings":[{"rule":"ID001","severity":"deny","message":"no name"}]}`
	if string(b) != want {
		t.Errorf("json = %s, want %s", b, want)
	}
	var back Verdict
	if err := json.Unmarshal(b, &back); err != nil || !reflect.DeepEqual(back, v) {
		t.Errorf("round trip = %+v, %v", back, err)
	}
	if got := (Finding{RuleID: "X", Message: "y"}).String(); got != "[X] y" {
		t.Errorf("Finding.String = %q", got)
	}
}
