"""A small, forgiving lexer for the bash and PowerShell commands Claude runs.

It is not a full shell parser. It only needs to:
  * split a command line into simple commands (on && || ; | & newline and parens),
  * turn each simple command into words with quotes removed,
  * resolve the common message forms statically: "...", '...', $'...',
    "$(cat <<'EOF' ... EOF)", PowerShell here-strings @'...'@, and
    heredocs fed to stdin (git commit -F - <<'EOF'),
  * mark words that depend on runtime expansion ($VAR, $(cmd), `cmd`) as dynamic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BASH = "bash"
POWERSHELL = "powershell"

_CAT_HEREDOC = re.compile(
    r"^\$\(\s*cat\s*<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[ \t]*\r?\n(.*?)\r?\n[ \t]*\2[ \t]*\r?\n?\s*\)$",
    re.DOTALL,
)
_REDIRECT = re.compile(r"(\d*|&)(>>|>\||>&|>|<&|<)")
_VAR_START = re.compile(r"[A-Za-z_{@*#?$!0-9]")


@dataclass
class Word:
    value: str
    raw: str
    dynamic: bool = False


@dataclass
class Segment:
    words: list[Word] = field(default_factory=list)
    stdin: str | None = None  # heredoc / here-string body redirected to stdin

    @property
    def values(self) -> list[str]:
        return [w.value for w in self.words]


class _Lexer:
    def __init__(self, text: str, dialect: str):
        self.t = text
        self.n = len(text)
        self.ps = dialect == POWERSHELL
        self.esc = "`" if self.ps else "\\"

    # ---------- skipping helpers (used inside $( ... )) ----------
    def skip_sq(self, i: int) -> int:
        """i at opening quote; return index after the closing quote."""
        i += 1
        while i < self.n:
            if self.t[i] == "'":
                if self.ps and i + 1 < self.n and self.t[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return self.n

    def skip_dq(self, i: int) -> int:
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == self.esc:
                i += 2
                continue
            if self.t.startswith("$(", i):
                i = self.skip_subst(i + 2)
                continue
            if c == '"':
                if self.ps and i + 1 < self.n and self.t[i + 1] == '"':
                    i += 2
                    continue
                return i + 1
            i += 1
        return self.n

    def read_delim(self, i: int) -> tuple[str, int]:
        """Read a heredoc delimiter word starting at i (after '<<' / '<<-' and spaces)."""
        out = []
        while i < self.n and self.t[i] not in " \t\r\n;&|()<>":
            c = self.t[i]
            if c in "'\"":
                j = self.t.find(c, i + 1)
                j = self.n if j < 0 else j
                out.append(self.t[i + 1:j])
                i = j + 1
            elif c == "\\":
                out.append(self.t[i + 1:i + 2])
                i += 2
            else:
                out.append(c)
                i += 1
        return "".join(out), i

    def read_heredoc_bodies(self, i: int, pending: list[tuple[str, bool]]) -> tuple[list[str], int]:
        """i at the first char after the newline; consume each pending heredoc body."""
        bodies = []
        for delim, strip_tabs in pending:
            lines = []
            while i < self.n:
                j = self.t.find("\n", i)
                end = self.n if j < 0 else j
                line = self.t[i:end].rstrip("\r")
                i = end + 1 if j >= 0 else self.n
                check = line.lstrip("\t") if strip_tabs else line
                if check == delim:
                    break
                lines.append(line.lstrip("\t") if strip_tabs else line)
            bodies.append("\n".join(lines))
        return bodies, i

    def heredoc_start(self, i: int) -> tuple[str, bool, int] | None:
        """If a bash heredoc operator starts at i, return (delim, strip_tabs, index after)."""
        if self.ps or not self.t.startswith("<<", i) or self.t.startswith("<<<", i):
            return None
        j = i + 2
        strip = j < self.n and self.t[j] == "-"
        if strip:
            j += 1
        while j < self.n and self.t[j] in " \t":
            j += 1
        delim, j = self.read_delim(j)
        if not delim:
            return None
        return delim, strip, j

    def skip_subst(self, i: int) -> int:
        """i just after '$(' (or '('); return index after the matching ')'."""
        depth = 1
        pending: list[tuple[str, bool]] = []
        while i < self.n:
            c = self.t[i]
            if c == self.esc:
                i += 2
                continue
            if c == "'":
                i = self.skip_sq(i)
                continue
            if c == '"':
                i = self.skip_dq(i)
                continue
            hd = self.heredoc_start(i)
            if hd:
                pending.append((hd[0], hd[1]))
                i = hd[2]
                continue
            if c == "\n" and pending:
                _, i = self.read_heredoc_bodies(i + 1, pending)
                pending = []
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return self.n

    # ---------- word pieces ----------
    def subst_piece(self, i: int) -> tuple[str, bool, int]:
        """i at '$('; return (text, dynamic, next index). Resolves $(cat <<EOF ... EOF)."""
        end = self.skip_subst(i + 2)
        raw = self.t[i:end]
        m = _CAT_HEREDOC.match(raw)
        if m and not self.ps:
            return m.group(3), False, end
        return raw, True, end

    def var_piece(self, i: int) -> tuple[str, int]:
        """i at '$' followed by a variable start; return (raw text, next index)."""
        j = i + 1
        if j < self.n and self.t[j] == "{":
            k = self.t.find("}", j)
            k = self.n - 1 if k < 0 else k
            return self.t[i:k + 1], k + 1
        while j < self.n and (self.t[j].isalnum() or self.t[j] in "_:" or (j == i + 1 and self.t[j] in "@*#?$!")):
            j += 1
            if j == i + 2 and self.t[i + 1] in "@*#?$!0123456789":
                break
        return self.t[i:j], j

    def dq_piece(self, i: int) -> tuple[str, bool, int]:
        """i at '"'; return (value, dynamic, next index)."""
        out, dyn = [], False
        i += 1
        while i < self.n:
            c = self.t[i]
            if c == self.esc and i + 1 < self.n:
                nxt = self.t[i + 1]
                if self.ps:
                    out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(nxt, nxt))
                elif nxt in '"\\$`':
                    out.append(nxt)
                elif nxt == "\n":
                    pass
                else:
                    out.append(c + nxt)
                i += 2
                continue
            if c == '"':
                if self.ps and i + 1 < self.n and self.t[i + 1] == '"':
                    out.append('"')
                    i += 2
                    continue
                return "".join(out), dyn, i + 1
            if self.t.startswith("$(", i):
                text, d, i = self.subst_piece(i)
                out.append(text)
                dyn = dyn or d
                continue
            if c == "$" and i + 1 < self.n and _VAR_START.match(self.t[i + 1]):
                text, i = self.var_piece(i)
                out.append(text)
                dyn = True
                continue
            if c == "`" and not self.ps:
                j = self.t.find("`", i + 1)
                j = self.n - 1 if j < 0 else j
                out.append(self.t[i:j + 1])
                dyn = True
                i = j + 1
                continue
            out.append(c)
            i += 1
        return "".join(out), dyn, self.n

    def sq_piece(self, i: int) -> tuple[str, int]:
        end = self.skip_sq(i)
        body = self.t[i + 1:end - 1] if end <= self.n and self.t[end - 1:end] == "'" else self.t[i + 1:end]
        if self.ps:
            body = body.replace("''", "'")
        return body, end

    def ansi_c_piece(self, i: int) -> tuple[str, int]:
        """i at "$'" (bash ANSI-C quoting)."""
        out = []
        i += 2
        table = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "a": "\a", "e": "\x1b"}
        while i < self.n:
            c = self.t[i]
            if c == "\\" and i + 1 < self.n:
                out.append(table.get(self.t[i + 1], "\\" + self.t[i + 1]))
                i += 2
                continue
            if c == "'":
                return "".join(out), i + 1
            out.append(c)
            i += 1
        return "".join(out), self.n

    def here_string_piece(self, i: int) -> tuple[str, bool, int] | None:
        """PowerShell @'\\n...\\n'@ or @"\\n...\\n"@ starting at i."""
        if not self.ps or not (self.t.startswith("@'", i) or self.t.startswith('@"', i)):
            return None
        q = self.t[i + 1]
        j = i + 2
        while j < self.n and self.t[j] in " \t\r":
            j += 1
        if j >= self.n or self.t[j] != "\n":
            return None
        start = j + 1
        m = re.compile(r"\r?\n" + re.escape(q) + "@").search(self.t, start - 1)
        if not m:
            return self.t[start:], q == '"' and "$" in self.t[start:], self.n
        body = self.t[start:m.start()] if m.start() >= start else ""
        return body, (q == '"' and bool(re.search(r"\$[A-Za-z_({]", body))), m.end()

    # ---------- main loop ----------
    def lex(self) -> list[Segment]:
        segs: list[Segment] = []
        seg = Segment()
        word: list[str] | None = None
        raw_start = 0
        dyn = False
        pending: list[tuple[str, bool]] = []
        herestring_next = False

        def end_word(i: int):
            nonlocal word, dyn, herestring_next
            if word is not None:
                w = Word("".join(word), self.t[raw_start:i], dyn)
                if herestring_next:
                    seg.stdin = w.value + "\n"
                    herestring_next = False
                else:
                    seg.words.append(w)
            word, dyn = None, False

        def end_seg():
            nonlocal seg
            if seg.words or seg.stdin is not None:
                segs.append(seg)
            seg = Segment()

        def start(i: int):
            nonlocal word, raw_start
            if word is None:
                word, raw_start = [], i

        i = 0
        while i < self.n:
            c = self.t[i]
            if c in " \t\r":
                end_word(i)
                i += 1
                continue
            if c == "\n":
                end_word(i)
                if pending:
                    bodies, i = self.read_heredoc_bodies(i + 1, pending)
                    seg.stdin = bodies[-1] + "\n" if bodies else seg.stdin
                    pending = []
                else:
                    i += 1
                end_seg()
                continue
            if c == "#" and word is None:
                j = self.t.find("\n", i)
                i = self.n if j < 0 else j
                continue
            if c == self.esc:
                if i + 1 < self.n and self.t[i + 1] in "\r\n":
                    i += 2 if self.t[i + 1] == "\n" else 3
                    continue
                start(i)
                word.append(self.t[i + 1:i + 2])
                i += 2
                continue
            # operators
            two = self.t[i:i + 2]
            if two in ("&&", "||"):
                end_word(i)
                end_seg()
                i += 2
                continue
            if c in ";|" or (c == "&" and not self.ps and not self.t.startswith("&>", i)) or (c in "()" and not (self.ps and word is not None)):
                if c == "(" and self.ps:
                    # PowerShell (expr) used as an argument: keep as a dynamic word
                    end_word(i)
                    end = self.skip_subst(i + 1)
                    start(i)
                    word.append(self.t[i:end])
                    dyn = True
                    end_word(end)
                    i = end
                    continue
                end_word(i)
                end_seg()
                i += 1
                continue
            hd = self.heredoc_start(i)
            if hd:
                end_word(i)
                pending.append((hd[0], hd[1]))
                i = hd[2]
                continue
            if not self.ps and self.t.startswith("<<<", i):
                end_word(i)
                herestring_next = True
                i += 3
                while i < self.n and self.t[i] in " \t":
                    i += 1
                continue
            if word is None:
                m = _REDIRECT.match(self.t, i)
                if m and (m.group(1) or m.group(2)):
                    i = m.end()
                    if m.group(2).endswith("&") and i < self.n and (self.t[i].isdigit() or self.t[i] == "-"):
                        i += 1
                        continue
                    while i < self.n and self.t[i] in " \t":
                        i += 1
                    # drop the redirection target word
                    sub = _Lexer(self.t[i:], POWERSHELL if self.ps else BASH)
                    j = sub.word_end(0)
                    i += j
                    continue
            hs = self.here_string_piece(i)
            if hs:
                start(i)
                word.append(hs[0])
                dyn = dyn or hs[1]
                i = hs[2]
                continue
            if c == "'":
                start(i)
                text, i = self.sq_piece(i)
                word.append(text)
                continue
            if c == '"':
                start(i)
                text, d, i = self.dq_piece(i)
                word.append(text)
                dyn = dyn or d
                continue
            if self.t.startswith("$'", i) and not self.ps:
                start(i)
                text, i = self.ansi_c_piece(i)
                word.append(text)
                continue
            if self.t.startswith("$(", i):
                start(i)
                text, d, i = self.subst_piece(i)
                word.append(text)
                dyn = dyn or d
                continue
            if c == "$" and i + 1 < self.n and _VAR_START.match(self.t[i + 1]):
                start(i)
                text, i = self.var_piece(i)
                word.append(text)
                dyn = True
                continue
            if c == "`" and not self.ps:
                start(i)
                j = self.t.find("`", i + 1)
                j = self.n - 1 if j < 0 else j
                word.append(self.t[i:j + 1])
                dyn = True
                i = j + 1
                continue
            start(i)
            word.append(c)
            i += 1
        end_word(self.n)
        end_seg()
        return segs

    def word_end(self, i: int) -> int:
        """Index just past the word starting at i (quotes respected)."""
        while i < self.n:
            c = self.t[i]
            if c in " \t\r\n;|&()<>":
                return i
            if c == self.esc:
                i += 2
            elif c == "'":
                i = self.skip_sq(i)
            elif c == '"':
                i = self.skip_dq(i)
            elif self.t.startswith("$(", i):
                i = self.skip_subst(i + 2)
            else:
                i += 1
        return self.n


def lex(text: str, dialect: str = BASH) -> list[Segment]:
    """Split a command line into simple-command segments of words."""
    return _Lexer(text, dialect).lex()
