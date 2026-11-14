package profile

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func envOf(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func TestSelectOrder(t *testing.T) {
	match := []Match{
		{Path: "~/work/**", Profile: "bypath"},
		{RemoteOwner: "acme", Profile: "byowner"},
	}
	where := Where{RemoteOwner: "acme", Dir: "/h/work/api", Home: "/h", GOOS: "linux"}
	tests := []struct {
		name      string
		in        SelectInput
		want, why string
	}{
		{"flag wins", SelectInput{Flag: "f", Getenv: envOf(map[string]string{"MYGIT_PROFILE": "e"}), Local: "l", Match: match, Default: "d", Where: where}, "f", "--profile flag"},
		{"env next", SelectInput{Getenv: envOf(map[string]string{"MYGIT_PROFILE": " e "}), Local: "l", Match: match, Default: "d", Where: where}, "e", "env MYGIT_PROFILE"},
		{"local next", SelectInput{Local: "l", Match: match, Default: "d", Where: where}, "l", "local .mygit.local.json"},
		{"owner before path even if listed later", SelectInput{Match: match, Default: "d", Where: where}, "byowner", "match remoteOwner=acme"},
		{"owner case-insensitive", SelectInput{Match: match, Where: Where{RemoteOwner: "ACME", Dir: "/x", Home: "/h"}}, "byowner", "match remoteOwner=acme"},
		{"path glob", SelectInput{Match: match, Default: "d", Where: Where{RemoteOwner: "other", Dir: "/h/work/a/b", Home: "/h"}}, "bypath", "match path=~/work/**"},
		{"default", SelectInput{Match: match, Default: "d", Where: Where{RemoteOwner: "other", Dir: "/elsewhere", Home: "/h"}}, "d", "defaultProfile"},
		{"none", SelectInput{Where: where}, "", "no profile selected"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, why := Select(tt.in)
			if got != tt.want || why != tt.why {
				t.Errorf("Select = %q (%s), want %q (%s)", got, why, tt.want, tt.why)
			}
		})
	}
}

func TestFindMatchCombinedAndPick(t *testing.T) {
	rules := []Match{
		{RemoteOwner: "acme", Path: "~/oss/**", Profile: "both"},
		{RemoteOwner: "acme", Ruleset: "strict"},
		{}, // no condition never matches
		{Path: "/x/**", Workflow: "wf"},
	}
	w := Where{RemoteOwner: "acme", Dir: "/h/work", Home: "/h"}
	// combined owner+path rule needs both.
	if v, _ := FindMatch(rules, w, func(m Match) string { return m.Profile }); v != "" {
		t.Errorf("combined rule matched without path: %q", v)
	}
	w.Dir = "/h/oss/p"
	if v, r := FindMatch(rules, w, func(m Match) string { return m.Profile }); v != "both" || r != "match remoteOwner=acme path=~/oss/**" {
		t.Errorf("combined = %q %q", v, r)
	}
	// rules without the picked field are skipped.
	if v, _ := FindMatch(rules, w, func(m Match) string { return m.Ruleset }); v != "strict" {
		t.Errorf("ruleset pick = %q", v)
	}
	if v, _ := FindMatch(rules, Where{Dir: "/x/y"}, func(m Match) string { return m.Workflow }); v != "wf" {
		t.Errorf("workflow pick = %q", v)
	}
}

func TestPathMatch(t *testing.T) {
	tests := []struct {
		glob, dir, home, goos string
		want                  bool
	}{
		{"~/work/**", "/h/work", "/h", "linux", true},
		{"~/work/**", "/h/work/", "/h", "linux", true},
		{"~/work/**", "/h/work/a/b", "/h", "linux", true},
		{"~/work/**", "/h/workx", "/h", "linux", false},
		{"~/work/*", "/h/work/a", "/h", "linux", true},
		{"~/work/*", "/h/work/a/b", "/h", "linux", false},
		{"~/work/**/api", "/h/work/x/y/api", "/h", "linux", true},
		{"~/work/**/api", "/h/work/api", "/h", "linux", true},
		{"~/w?rk", "/h/work", "/h", "linux", true},
		{"~", "/h", "/h", "linux", true},
		{"/abs/**", "/abs/q", "/h", "darwin", true},
		{"/a/**b", "/a/x/yb", "/h", "linux", true},
		{"/a.b/*", "/aXb/c", "/h", "linux", false}, // dots are literal
		{"~/Work/**", "/h/work/x", "/h", "linux", false},
		{`~\Work\**`, `C:\Users\u\work\x`, `C:\Users\u`, "windows", true},
		{`C:\src\*`, `c:\SRC\api`, `C:\Users\u`, "windows", true},
		{"~/work/**", "", "/h", "linux", false},
	}
	for _, tt := range tests {
		if got := PathMatch(tt.glob, tt.dir, tt.home, tt.goos); got != tt.want {
			t.Errorf("PathMatch(%q, %q, %s) = %v, want %v", tt.glob, tt.dir, tt.goos, got, tt.want)
		}
	}
}

func TestFirst(t *testing.T) {
	if _, ok := First(Choice{}, Choice{Reason: "x"}); ok {
		t.Error("empty values must not hit")
	}
	if c, ok := First(Choice{}, Choice{"v", "r"}, Choice{"w", "s"}); !ok || c.Value != "v" {
		t.Error(c)
	}
}

func TestLookup(t *testing.T) {
	ps := map[string]Profile{"work": {GitHub: GitHub{User: "w"}}, "a": {}}
	if p, err := Lookup(ps, "", "x"); err != nil || !p.IsZero() {
		t.Error("empty name")
	}
	if p, err := Lookup(ps, "work", "x"); err != nil || p.GitHub.User != "w" {
		t.Error("found")
	}
	_, err := Lookup(ps, "nope", "env MYGIT_PROFILE")
	if err == nil || !strings.Contains(err.Error(), `"nope" (env MYGIT_PROFILE)`) || !strings.Contains(err.Error(), "a, work") {
		t.Errorf("err = %v", err)
	}
}

func TestVarsNamesAndAliases(t *testing.T) {
	p := Profile{
		Git:    Git{Name: " Octo ", Email: StringList{"a@x", " ", "b@x"}, Signing: Signing{Format: "ssh", Key: "~/.ssh/s.pub"}},
		GitHub: GitHub{User: "octo", Orgs: StringList{"acme"}, Transport: "ssh", SSHKey: "~/.ssh/k"},
	}
	got := Vars(p)
	want := map[string]any{
		"github_user": "octo", "github.user": "octo",
		"github_orgs": []string{"acme"}, "github.orgs": []string{"acme"},
		"git_name": "Octo", "git.name": "Octo",
		"git_email": []string{"a@x", "b@x"}, "git.email": []string{"a@x", "b@x"},
		"git_transport": "ssh", "github.transport": "ssh",
		"ssh_key": "~/.ssh/k", "github.sshKey": "~/.ssh/k",
		"signing_format": "ssh", "git.signing.format": "ssh",
		"signing_key": "~/.ssh/s.pub", "git.signing.key": "~/.ssh/s.pub",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Vars =\n%#v\nwant\n%#v", got, want)
	}
	if v := Vars(Profile{}); len(v) != 0 {
		t.Errorf("zero profile vars = %v", v)
	}
	// signing_key follows the Python semantics.
	for _, c := range []struct {
		s    Signing
		want any
	}{
		{Signing{Format: "default"}, "default"},
		{Signing{Format: "none", Key: "k"}, nil},
		{Signing{Format: "openpgp", Key: "ABC"}, "ABC"},
		{Signing{Format: "x509"}, nil},
	} {
		if got := Vars(Profile{Git: Git{Signing: c.s}})["signing_key"]; got != c.want {
			t.Errorf("signing_key(%+v) = %v, want %v", c.s, got, c.want)
		}
	}
}

func TestStringListJSON(t *testing.T) {
	var p Profile
	if err := json.Unmarshal([]byte(`{"git":{"email":"a@x"},"github":{"orgs":["o1","o2"]}}`), &p); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual([]string(p.Git.Email), []string{"a@x"}) || len(p.GitHub.Orgs) != 2 {
		t.Errorf("%+v", p)
	}
	var l StringList
	if err := json.Unmarshal([]byte(`""`), &l); err != nil || l != nil {
		t.Errorf("empty string = %v %v", l, err)
	}
	if err := json.Unmarshal([]byte(`5`), &l); err == nil {
		t.Error("number accepted")
	}
}

func TestValidate(t *testing.T) {
	if err := (Profile{Git: Git{Signing: Signing{Format: "pgp"}}}).Validate(); err == nil || !strings.Contains(err.Error(), "git.signing.format") {
		t.Errorf("format: %v", err)
	}
	if err := (Profile{GitHub: GitHub{Transport: "https"}}).Validate(); err == nil || !strings.Contains(err.Error(), "github.transport") {
		t.Errorf("transport: %v", err)
	}
	for _, f := range []string{"", "ssh", "openpgp", "x509", "default", "none"} {
		if err := (Profile{Git: Git{Signing: Signing{Format: f}}}).Validate(); err != nil {
			t.Errorf("%s: %v", f, err)
		}
	}
}

func TestApplyEnv(t *testing.T) {
	base := Profile{Git: Git{Name: "n", Signing: Signing{Format: "none"}}, GitHub: GitHub{User: "file-user"}}
	env := map[string]string{
		"MYGIT_GITHUB_USER":     "env-user",
		"GIT_GUARD_GITHUB_USER": "legacy-user", // MYGIT_* wins
		"GIT_GUARD_GITHUB_ORGS": "a, b,",
		"MYGIT_GIT_EMAIL":       "e1@x,e2@x",
		"MYGIT_GIT_NAME":        "  ",           // blank ignored
		"MYGIT_SIGNING_KEY":     "~/.ssh/s.pub", // implies ssh format
		"MYGIT_GIT_TRANSPORT":   "gh",
		"MYGIT_SSH_KEY":         "~/.ssh/k",
	}
	p, origins, err := ApplyEnv(base, envOf(env))
	if err != nil {
		t.Fatal(err)
	}
	if p.GitHub.User != "env-user" || !reflect.DeepEqual([]string(p.GitHub.Orgs), []string{"a", "b"}) ||
		!reflect.DeepEqual([]string(p.Git.Email), []string{"e1@x", "e2@x"}) || p.Git.Name != "n" ||
		p.Git.Signing != (Signing{Format: "ssh", Key: "~/.ssh/s.pub"}) || p.GitHub.Transport != "gh" || p.GitHub.SSHKey != "~/.ssh/k" {
		t.Errorf("profile = %+v", p)
	}
	wantO := map[string]string{
		"github.user": "env MYGIT_GITHUB_USER", "github.orgs": "env GIT_GUARD_GITHUB_ORGS",
		"git.email": "env MYGIT_GIT_EMAIL", "git.signing.key": "env MYGIT_SIGNING_KEY",
		"github.transport": "env MYGIT_GIT_TRANSPORT", "github.sshKey": "env MYGIT_SSH_KEY",
	}
	if !reflect.DeepEqual(origins, wantO) {
		t.Errorf("origins = %v", origins)
	}
	// signing key "default" → git's own setup.
	p, _, _ = ApplyEnv(Profile{Git: Git{Signing: Signing{Format: "ssh", Key: "k"}}}, envOf(map[string]string{"MYGIT_SIGNING_KEY": "default"}))
	if p.Git.Signing != (Signing{Format: "default"}) {
		t.Errorf("default signing = %+v", p.Git.Signing)
	}
	// key with an explicit openpgp format keeps the format.
	p, _, _ = ApplyEnv(Profile{Git: Git{Signing: Signing{Format: "openpgp"}}}, envOf(map[string]string{"MYGIT_SIGNING_KEY": "ABC"}))
	if p.Git.Signing != (Signing{Format: "openpgp", Key: "ABC"}) {
		t.Errorf("openpgp signing = %+v", p.Git.Signing)
	}
	// MYGIT_SIGNING_FORMAT
	p, _, _ = ApplyEnv(Profile{}, envOf(map[string]string{"MYGIT_SIGNING_FORMAT": "x509"}))
	if p.Git.Signing.Format != "x509" {
		t.Errorf("format = %+v", p.Git.Signing)
	}
	// invalid values are errors.
	if _, _, err := ApplyEnv(Profile{}, envOf(map[string]string{"MYGIT_GIT_TRANSPORT": "https"})); err == nil {
		t.Error("bad transport accepted")
	}
	// nil getenv
	if q, o, err := ApplyEnv(base, nil); err != nil || len(o) != 0 || q.GitHub.User != "file-user" {
		t.Error("nil getenv")
	}
	if len(EnvVars()) != 8 {
		t.Errorf("EnvVars = %d", len(EnvVars()))
	}
}

func args(steps []Step) []string {
	var out []string
	for _, s := range steps {
		out = append(out, s.Kind+" "+strings.Join(s.Args, " "))
	}
	return out
}

func TestApplyStepsTransportSSH(t *testing.T) {
	p := Profile{
		Git:    Git{Name: "Octo Cat", Email: StringList{"octo@acme.example", "alt@x"}, Signing: Signing{Format: "ssh", Key: "~/.ssh/id_sign.pub"}},
		GitHub: GitHub{User: "octo-acme", Transport: "ssh", SSHKey: "~/.ssh/id_acme"},
	}
	steps, err := ApplySteps(p, ApplyOptions{Name: "work", LocalConfigPath: "/r/.mygit.local.json", ExistingLocal: []byte(`{"ruleset":"x"}`)})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"git-config config --local user.name Octo Cat",
		"git-config config --local user.email octo@acme.example",
		"git-config config --local core.sshCommand ssh -i ~/.ssh/id_acme -o IdentitiesOnly=yes",
		"git-config config --local commit.gpgsign true",
		"git-config config --local gpg.format ssh",
		"git-config config --local user.signingkey ~/.ssh/id_sign.pub",
		"gh auth switch --user octo-acme",
	}
	got := args(steps)
	if !reflect.DeepEqual(got[:len(got)-1], want) {
		t.Errorf("steps =\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	last := steps[len(steps)-1]
	if last.Kind != KindWriteFile || last.Args[0] != "/r/.mygit.local.json" {
		t.Fatalf("last step = %+v", last)
	}
	var doc map[string]any
	if err := json.Unmarshal([]byte(last.Args[1]), &doc); err != nil || doc["profile"] != "work" || doc["ruleset"] != "x" {
		t.Errorf("local config = %s (%v)", last.Args[1], err)
	}
}

func TestApplyStepsTransportGH(t *testing.T) {
	p := Profile{GitHub: GitHub{User: "octocat", Transport: "gh", SSHKey: "~/.ssh/ignored"}}
	steps, err := ApplySteps(p, ApplyOptions{})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"gh auth switch --user octocat", "gh auth setup-git"}
	if got := args(steps); !reflect.DeepEqual(got, want) {
		t.Errorf("steps = %v", got)
	}
}

func TestApplyStepsGlobal(t *testing.T) {
	p := Profile{Git: Git{Name: "n"}}
	steps, err := ApplySteps(p, ApplyOptions{Global: true, Name: "w", LocalConfigPath: "/r/.mygit.local.json"})
	if err != nil {
		t.Fatal(err)
	}
	if got := args(steps); !reflect.DeepEqual(got, []string{"git-config config --global user.name n"}) {
		t.Errorf("global steps = %v (local file must be skipped)", got)
	}
}

func TestApplyStepsErrors(t *testing.T) {
	if _, err := ApplySteps(Profile{GitHub: GitHub{Transport: "x"}}, ApplyOptions{}); err == nil {
		t.Error("invalid profile accepted")
	}
	if _, err := ApplySteps(Profile{}, ApplyOptions{Name: "w", LocalConfigPath: "/l", ExistingLocal: []byte("{oops")}); err == nil ||
		!strings.Contains(err.Error(), "/l") {
		t.Errorf("bad local json: %v", err)
	}
	steps, err := ApplySteps(Profile{}, ApplyOptions{Name: "w", LocalConfigPath: "/l", ExistingLocal: []byte("null")})
	if err != nil || len(steps) != 1 || !strings.Contains(steps[0].Args[1], `"profile": "w"`) {
		t.Errorf("null local: %v %v", steps, err)
	}
}

func TestSigningConfig(t *testing.T) {
	tests := []struct {
		s    Signing
		want [][2]string
	}{
		{Signing{}, nil},
		{Signing{Format: "none"}, [][2]string{{"commit.gpgsign", "false"}}},
		{Signing{Format: "default", Key: "ignored"}, [][2]string{{"commit.gpgsign", "true"}}},
		{Signing{Format: "ssh", Key: "k.pub"}, [][2]string{{"commit.gpgsign", "true"}, {"gpg.format", "ssh"}, {"user.signingkey", "k.pub"}}},
		{Signing{Format: "openpgp", Key: "ABC", Program: "gpg2"}, [][2]string{{"commit.gpgsign", "true"}, {"gpg.format", "openpgp"}, {"user.signingkey", "ABC"}, {"gpg.program", "gpg2"}}},
		{Signing{Format: "x509", Program: "smimesign"}, [][2]string{{"commit.gpgsign", "true"}, {"gpg.format", "x509"}, {"gpg.x509.program", "smimesign"}}},
		{Signing{Format: "ssh", Program: "ignored"}, [][2]string{{"commit.gpgsign", "true"}, {"gpg.format", "ssh"}}},
	}
	for _, tt := range tests {
		if got := SigningConfig(tt.s); !reflect.DeepEqual(got, tt.want) {
			t.Errorf("SigningConfig(%+v) = %v, want %v", tt.s, got, tt.want)
		}
	}
}

func TestIsZero(t *testing.T) {
	if !(Profile{}).IsZero() {
		t.Error("zero")
	}
	if (Profile{GitHub: GitHub{SSHKey: "k"}}).IsZero() || (Profile{Git: Git{Signing: Signing{Program: "p"}}}).IsZero() {
		t.Error("non-zero")
	}
}

func TestApplyStepsGHRewritesSSHRemote(t *testing.T) {
	p := Profile{GitHub: GitHub{User: "octocat", Transport: TransportGH}}
	cases := []struct {
		name string
		opts ApplyOptions
		want []string // nil = no set-url step
	}{
		{"scp-like", ApplyOptions{RemoteURL: "git@github.com:acme/api.git"},
			[]string{"remote", "set-url", "origin", "https://github.com/acme/api.git"}},
		{"named remote, no .git", ApplyOptions{RemoteName: "upstream", RemoteURL: "git@github.com:acme/api"},
			[]string{"remote", "set-url", "upstream", "https://github.com/acme/api.git"}},
		{"ssh scheme with port", ApplyOptions{RemoteURL: "ssh://git@ssh.github.com:443/acme/api.git"},
			[]string{"remote", "set-url", "origin", "https://ssh.github.com/acme/api.git"}},
		{"already https", ApplyOptions{RemoteURL: "https://github.com/acme/api.git"}, nil},
		{"no url", ApplyOptions{}, nil},
		{"global", ApplyOptions{Global: true, RemoteURL: "git@github.com:acme/api.git"}, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			steps, err := ApplySteps(p, c.opts)
			if err != nil {
				t.Fatal(err)
			}
			var got []string
			for i, s := range steps {
				if len(s.Args) > 1 && s.Args[0] == "remote" {
					got = s.Args
					if s.Kind != KindGitConfig || i != len(steps)-1 || steps[i-1].Args[1] != "setup-git" {
						t.Errorf("set-url step %+v at %d of %+v", s, i, steps)
					}
				}
			}
			if !reflect.DeepEqual(got, c.want) {
				t.Errorf("set-url args = %v, want %v", got, c.want)
			}
		})
	}
	ssh := Profile{GitHub: GitHub{Transport: TransportSSH, SSHKey: "~/.ssh/k"}}
	steps, _ := ApplySteps(ssh, ApplyOptions{RemoteURL: "git@github.com:acme/api.git"})
	for _, s := range steps {
		if s.Args[0] == "remote" {
			t.Errorf("transport ssh must not rewrite the remote: %+v", s)
		}
	}
}

func TestSSHToHTTPS(t *testing.T) {
	cases := map[string]string{
		"git@github.com:acme/api.git":               "https://github.com/acme/api.git",
		"github.com:acme/api":                       "https://github.com/acme/api.git",
		"ssh://git@github.com/acme/api.git":         "https://github.com/acme/api.git",
		"git+ssh://git@ghe.corp:2222/acme/api.git/": "https://ghe.corp/acme/api.git",
		"https://github.com/acme/api.git":           "",
		"/srv/git/api.git":                          "",
		"./a:b/c":                                   "",
		"git@github.com:api.git":                    "",
		"ssh://github.com":                          "",
		"git@:acme/api":                             "",
	}
	for in, want := range cases {
		got, ok := SSHToHTTPS(in)
		if got != want || ok != (want != "") {
			t.Errorf("SSHToHTTPS(%q) = %q, %v; want %q", in, got, ok, want)
		}
	}
}
