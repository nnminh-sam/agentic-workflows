package plan

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
)

// ErrNotFound means no saved plan has the requested id.
var ErrNotFound = errors.New("plan not found")

// Store saves plans as <Dir>/<id>.json so `--apply <id>`, `plan show` and
// `plan verify` work across processes. Callers choose Dir (e.g.
// $XDG_STATE_HOME/mygit/plans or <git-dir>/mygit/plans).
type Store struct {
	Dir string
}

var idRe = regexp.MustCompile(`^[0-9a-f]{6,64}$`)

func (s Store) path(id string) (string, error) {
	if !idRe.MatchString(id) {
		return "", fmt.Errorf("invalid plan id %q", id)
	}
	return filepath.Join(s.Dir, id+".json"), nil
}

// Save writes p (overwriting a plan with the same id).
func (s Store) Save(p *Plan) error {
	path, err := s.path(p.ID)
	if err != nil {
		return err
	}
	b, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return fmt.Errorf("save plan %s: %w", p.ID, err)
	}
	if err := os.MkdirAll(s.Dir, 0o700); err != nil {
		return fmt.Errorf("save plan %s: %w", p.ID, err)
	}
	if err := os.WriteFile(path, append(b, '\n'), 0o600); err != nil {
		return fmt.Errorf("save plan %s: %w", p.ID, err)
	}
	return nil
}

// Load reads the plan with the given id (ErrNotFound if absent).
func (s Store) Load(id string) (*Plan, error) {
	path, err := s.path(id)
	if err != nil {
		return nil, err
	}
	b, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, fmt.Errorf("plan %s: %w", id, ErrNotFound)
	}
	if err != nil {
		return nil, fmt.Errorf("load plan %s: %w", id, err)
	}
	var p Plan
	if err := json.Unmarshal(b, &p); err != nil {
		return nil, fmt.Errorf("load plan %s: %w", id, err)
	}
	return &p, nil
}

// Delete removes a saved plan; a missing plan is not an error.
func (s Store) Delete(id string) error {
	path, err := s.path(id)
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("delete plan %s: %w", id, err)
	}
	return nil
}
