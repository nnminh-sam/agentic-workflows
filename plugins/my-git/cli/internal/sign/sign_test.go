package sign

import (
	"context"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/execx"
	"github.com/nnminh-sam/agentic-workflows/plugins/my-git/cli/internal/gitx"
)

func TestFormatsAndHelpers(t *testing.T) {
	want := []string{SSH, OpenPGP, X509, Default, None}
	if got := Formats(); !reflect.DeepEqual(got, want) {
		t.Errorf("Formats() = %v, want %v", got, want)
	}
	if got := AllowedSignersPath("/cfg"); got != filepath.Join("/cfg", "allowed_signers") {
		t.Errorf("AllowedSignersPath = %q", got)
	}
	if cmd := GPGExportCmd("ABC123"); cmd.Name != "gpg" || !reflect.DeepEqual(cmd.Args, []string{"--armor", "--export", "ABC123"}) {
		t.Errorf("GPGExportCmd = %+v", cmd)
	}
	if got := ExpandHome("~", "/h"); got != "/h" {
		t.Errorf("ExpandHome(~) = %q", got)
	}
	if got := ExpandHome("~/.ssh/id", "/h"); got != filepath.Join("/h", ".ssh", "id") {
		t.Errorf("ExpandHome(~/...) = %q", got)
	}
	if got := ExpandHome(`~\.ssh\id`, "/h"); got != filepath.Join("/h", `.ssh\id`) {
		t.Errorf("ExpandHome(~\\...) = %q", got)
	}
	if got := ExpandHome("/abs/k", "/h"); got != "/abs/k" {
		t.Errorf("ExpandHome(/abs/k) = %q", got)
	}
	if got := PublicKeyPath("/k.pub", nil); got != "/k.pub" {
		t.Errorf("PublicKeyPath(.pub) = %q", got)
	}
	if got := PublicKeyPath("/k", func(p string) bool { return p == "/k.pub" }); got != "/k.pub" {
		t.Errorf("PublicKeyPath(exists) = %q", got)
	}
	if got := PublicKeyPath("/k", func(string) bool { return false }); got != "/k" {
		t.Errorf("PublicKeyPath(missing) = %q", got)
	}
}

func TestMergeAllowedSigners(t *testing.T) {
	pub := "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI comment here\n"
	existing := "# comment\nother@x.io namespaces=\"git\" ssh-rsa BBBB\nocto@x.io namespaces=\"git\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI\n\n"
	got, err := MergeAllowedSigners([]byte(existing), []string{"Octo@x.io", "two@x.io"}, pub)
	if err != nil {
		t.Fatal(err)
	}
	wantLines := []string{
		"# comment",
		`other@x.io namespaces="git" ssh-rsa BBBB`,
		`Octo@x.io namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI`,
		`two@x.io namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI`,
	}
	if got != strings.Join(wantLines, "\n")+"\n" {
		t.Errorf("got:\n%s\nwant:\n%s", got, strings.Join(wantLines, "\n")+"\n")
	}
	for _, bad := range []string{"", "ssh-ed25519", "rsa-key AAAA"} {
		if _, err := MergeAllowedSigners(nil, []string{"a@b.c"}, bad); err == nil {
			t.Errorf("MergeAllowedSigners(%q) should fail", bad)
		}
	}
	for _, prefix := range []string{"ecdsa-sha2-nistp256 AAAA", "sk-ssh-ed25519@openssh.com AAAA"} {
		if _, err := MergeAllowedSigners(nil, []string{"a@b.c"}, prefix); err != nil {
			t.Errorf("MergeAllowedSigners(%q) failed: %v", prefix, err)
		}
	}
}

func TestSetupSteps(t *testing.T) {
	t.Run("none and default", func(t *testing.T) {
		steps, err := SetupSteps(SetupOptions{Format: None})
		if err != nil || len(steps) != 1 || !reflect.DeepEqual(steps[0].Args, []string{"config", "--local", "commit.gpgsign", "false"}) {
			t.Fatalf("none = %+v, %v", steps, err)
		}
		steps, err = SetupSteps(SetupOptions{Format: Default, Global: true})
		if err != nil || len(steps) != 1 || !reflect.DeepEqual(steps[0].Args, []string{"config", "--global", "commit.gpgsign", "true"}) {
			t.Fatalf("default = %+v, %v", steps, err)
		}
	})

	t.Run("ssh with upload", func(t *testing.T) {
		steps, err := SetupSteps(SetupOptions{
			Format:             SSH,
			Key:                "/h/.ssh/id.pub",
			PublicKey:          "ssh-ed25519 AAAA comment",
			Emails:             []string{"o@x.io"},
			AllowedSignersPath: "/cfg/allowed_signers",
			Upload:             true,
		})
		if err != nil {
			t.Fatal(err)
		}
		if len(steps) != 6 {
			t.Fatalf("steps = %+v", steps)
		}
		if steps[3].Kind != KindWriteFile || steps[3].Args[0] != "/cfg/allowed_signers" {
			t.Errorf("write step = %+v", steps[3])
		}
		if steps[5].Kind != KindGH || steps[5].Args[0] != "ssh-key" {
			t.Errorf("gh step = %+v", steps[5])
		}
	})

	t.Run("openpgp with program and upload", func(t *testing.T) {
		steps, err := SetupSteps(SetupOptions{
			Format:         OpenPGP,
			Key:            "KEYID",
			Program:        "/usr/local/bin/gpg",
			Upload:         true,
			ArmoredKeyPath: "/tmp/key.asc",
		})
		if err != nil || len(steps) != 5 {
			t.Fatalf("openpgp = %+v, %v", steps, err)
		}
		if steps[2].Args[2] != "gpg.program" || steps[4].Kind != KindGH || steps[4].Args[0] != "gpg-key" {
			t.Errorf("steps = %+v", steps)
		}
	})

	t.Run("x509 without key and with program", func(t *testing.T) {
		steps, err := SetupSteps(SetupOptions{Format: X509, Program: "gpgsm"})
		if err != nil || len(steps) != 3 || steps[1].Args[2] != "gpg.x509.program" {
			t.Fatalf("x509 = %+v, %v", steps, err)
		}
	})

	t.Run("errors", func(t *testing.T) {
		bad := []SetupOptions{
			{Format: "bogus"},
			{Format: SSH},
			{Format: OpenPGP},
			{Format: SSH, Key: "/k"},
			{Format: SSH, Key: "/k", AllowedSignersPath: "/a"},
			{Format: SSH, Key: "/k", AllowedSignersPath: "/a", Emails: []string{"a@b.c"}, PublicKey: "bad"},
			{Format: OpenPGP, Key: "K", Upload: true},
			{Format: X509, Upload: true},
		}
		for i, o := range bad {
			if _, err := SetupSteps(o); err == nil {
				t.Errorf("case %d (%+v): expected error", i, o)
			}
		}
	})
}

func TestReadStatus(t *testing.T) {
	f := execx.NewFake()
	f.Default = &execx.Result{ExitCode: 1}
	f.OnOut("git config --get gpg.format", "x509\n")
	f.OnOut("git config --get user.signingkey", "CERT\n")
	f.OnOut("git config --get commit.gpgsign", "yes\n")
	f.OnOut("git config --get gpg.ssh.allowedSignersFile", "/a/s\n")
	f.OnOut("git config --get gpg.x509.program", "gpgsm\n")

	st, err := ReadStatus(context.Background(), gitx.New(f, "/repo"))
	if err != nil {
		t.Fatal(err)
	}
	want := Status{Format: "x509", Key: "CERT", GPGSign: true, AllowedSignersFile: "/a/s", Program: "gpgsm"}
	if st != want {
		t.Errorf("got %+v, want %+v", st, want)
	}
}

func TestCommitVerifyAndCounts(t *testing.T) {
	for _, code := range []string{"G", "U", "X", "Y"} {
		c := Commit{Status: code}
		if !c.OK() || Describe(code) == "" || strings.HasPrefix(Describe(code), "unknown") {
			t.Errorf("status %s: OK=%v Describe=%q", code, c.OK(), Describe(code))
		}
	}
	for _, code := range []string{"R", "E", "B", "N", "Z"} {
		c := Commit{Status: code}
		if c.OK() || Describe(code) == "" {
			t.Errorf("status %s: OK=%v Describe=%q", code, c.OK(), Describe(code))
		}
	}
	if got := LogArgs("main..HEAD"); len(got) != 3 || got[2] != "main..HEAD" {
		t.Errorf("LogArgs = %v", got)
	}
	raw := "aaa\x1fG\x1fOcto\x1ffeat: one\r\n\nbbb\x1fN\x1f\x1ffix: two\nshort\x1fB\n"
	cs := ParseLog(raw)
	if len(cs) != 3 || cs[0].Signer != "Octo" || cs[1].Status != "N" || cs[2].Status != "B" {
		t.Fatalf("ParseLog = %+v", cs)
	}
	probs := Problems(cs)
	if len(probs) != 2 || probs[0].SHA != "bbb" || probs[1].SHA != "short" {
		t.Errorf("Problems = %+v", probs)
	}
	if got := Counts(cs); got != "B=1 G=1 N=1" {
		t.Errorf("Counts = %q", got)
	}

	f := execx.NewFake()
	f.OnOut("git log --format=%H%x1f%G?%x1f%GS%x1f%s main..HEAD", raw)
	got, err := Verify(context.Background(), gitx.New(f, "/repo"), "main..HEAD")
	if err != nil || len(got) != 3 {
		t.Errorf("Verify = %v, %v", got, err)
	}
}
