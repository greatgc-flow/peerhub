```python
import sys

_META = set("\\.|*+?()[]{}^$")
_ESC_LIT = set("\\.|*+?()[]{}^$-")
_DIG = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = frozenset(" \t\n")
_SETS = {"d": (_DIG, False), "w": (_WORD, False), "s": (_SPACE, False),
         "D": (_DIG, True), "W": (_WORD, True), "S": (_SPACE, True)}
INF = float("inf")


class _Bad(Exception):
    pass


class _Parser:
    def __init__(self, p):
        self.p, self.i, self.ngroups, self.backrefs = p, 0, 0, []

    def peek(self, k=0):
        j = self.i + k
        return self.p[j] if j < len(self.p) else None

    def parse(self):
        node = self.alt()
        if self.i != len(self.p):          # stray ')'
            raise _Bad()
        if any(k > self.ngroups for k in self.backrefs):
            raise _Bad()
        return node

    def alt(self):
        branches = [self.seq()]
        while self.peek() == "|":
            self.i += 1
            branches.append(self.seq())
        return branches[0] if len(branches) == 1 else ("alt", branches)

    def seq(self):
        items = []
        while True:
            c = self.peek()
            if c is None or c in "|)":
                return ("seq", items)
            if c in "^$":
                self.i += 1
                items.append(("bol",) if c == "^" else ("eol",))
                continue
            first, before = self.ngroups + 1, self.ngroups
            atom = self.atom()
            q = self.quant()
            if q:
                lo, hi, greedy = q
                items.append(("rep", atom, lo, hi, greedy,
                              first, self.ngroups - before))
            else:
                items.append(atom)

    def number(self):
        j = self.i
        while self.peek() is not None and self.peek() in _DIG:
            self.i += 1
        if j == self.i:
            raise _Bad()
        v = int(self.p[j:self.i])
        if v > 1000:
            raise _Bad()
        return v

    def quant(self):
        c = self.peek()
        if c is None or c not in "*+?{":
            return None
        self.i += 1
        if c == "*":
            lo, hi = 0, INF
        elif c == "+":
            lo, hi = 1, INF
        elif c == "?":
            lo, hi = 0, 1
        else:
            lo = self.number()
            if self.peek() == "}":
                hi = lo
            elif self.peek() == ",":
                self.i += 1
                hi = INF if self.peek() == "}" else self.number()
            else:
                raise _Bad()
            if self.peek() != "}":
                raise _Bad()
            self.i += 1
            if lo > hi:
                raise _Bad()
        greedy = True
        if self.peek() == "?":
            self.i += 1
            greedy = False
        return lo, hi, greedy

    def atom(self):
        c = self.peek()
        if c == "(":
            self.i += 1
            if self.peek() == "?":
                if self.peek(1) != ":":
                    raise _Bad()
                self.i += 2
                inner = self.alt()
                if self.peek() != ")":
                    raise _Bad()
                self.i += 1
                return ("ncg", inner)
            self.ngroups += 1
            k = self.ngroups
            inner = self.alt()
            if self.peek() != ")":
                raise _Bad()
            self.i += 1
            return ("grp", k, inner)
        if c == ".":
            self.i += 1
            return ("set", frozenset("\n"), True)
        if c == "[":
            return self.cls()
        if c == "\\":
            e = self.escape(False)
            if e[0] == "ref":
                self.backrefs.append(e[1])
            return e
        if c in _META:                     # quantifier with nothing to repeat,
            raise _Bad()                   # stray ] or }, etc.
        self.i += 1
        return ("chr", c)

    def escape(self, in_class):
        self.i += 1
        c = self.peek()
        if c is None:
            raise _Bad()
        self.i += 1
        if c in _ESC_LIT:
            return ("chr", c)
        if c == "n":
            return ("chr", "\n")
        if c == "t":
            return ("chr", "\t")
        if c in _SETS:
            s, neg = _SETS[c]
            return ("set", s, neg)
        if not in_class and c in "123456789":
            return ("ref", int(c))
        raise _Bad()

    def class_item(self):
        c = self.peek()
        if c is None:
            raise _Bad()
        if c == "\\":
            return self.escape(True)
        self.i += 1
        return ("chr", c)

    def cls(self):
        self.i += 1
        neg = False
        if self.peek() == "^":
            neg = True
            self.i += 1
        chars, ranges, sets = set(), [], []
        while True:
            c = self.peek()
            if c is None:
                raise _Bad()
            if c == "]":
                self.i += 1
                break
            x = self.class_item()
            if self.peek() == "-" and self.peek(1) not in (None, "]"):
                self.i += 1
                y = self.class_item()
                if x[0] != "chr" or y[0] != "chr" or x[1] > y[1]:
                    raise _Bad()
                ranges.append((x[1], y[1]))
            elif x[0] == "chr":
                chars.add(x[1])
            else:
                sets.append(x)
        return ("cls", frozenset(chars), tuple(ranges), tuple(sets), neg)


def _in_class(node, ch):
    _, chars, ranges, sets, neg = node
    hit = (ch in chars or any(a <= ch <= b for a, b in ranges)
           or any((ch in s) != n for _, s, n in sets))
    return hit != neg


def _run(tree, text, start, caps0):
    """Continuation-passing backtracker at one start position."""
    n = len(text)

    def m(node, i, caps, k):
        kind = node[0]
        if kind == "chr":
            if i < n and text[i] == node[1]:
                return k(i + 1, caps)
            return None
        if kind == "set":
            if i < n and (text[i] in node[1]) != node[2]:
                return k(i + 1, caps)
            return None
        if kind == "cls":
            if i < n and _in_class(node, text[i]):
                return k(i + 1, caps)
            return None
        if kind == "bol":
            return k(i, caps) if i == 0 else None
        if kind == "eol":
            return k(i, caps) if i == n else None
        if kind == "seq":
            return seq(node[1], 0, i, caps, k)
        if kind == "alt":
            for b in node[1]:
                r = m(b, i, caps, k)
                if r is not None:
                    return r
            return None
        if kind == "ncg":
            return m(node[1], i, caps, k)
        if kind == "grp":
            g = node[1]

            def close(j, c2):
                return k(j, c2[:g] + ((i, j),) + c2[g + 1:])
            return m(node[2], i, caps, close)
        if kind == "ref":
            span = caps[node[1]]
            if span is None:
                return k(i, caps)
            sub = text[span[0]:span[1]]
            if text.startswith(sub, i):
                return k(i + len(sub), caps)
            return None
        if kind == "rep":
            _, body, lo, hi, greedy, first, cnt = node
            return rep(body, lo, hi, greedy, first, cnt, i, caps, k)
        raise AssertionError(kind)

    def seq(items, idx, i, caps, k):
        if idx == len(items):
            return k(i, caps)
        return m(items[idx], i, caps,
                 lambda j, c2: seq(items, idx + 1, j, c2, k))

    def rep(body, lo, hi, greedy, first, cnt, i, caps, k):
        if hi == 0:
            return k(i, caps)

        def after(j, c2):
            if lo == 0 and j == i:         # empty iteration past the minimum
                return None
            return rep(body, max(lo - 1, 0), hi - 1, greedy, first, cnt,
                       j, c2, k)

        reset = caps
        if cnt:                            # groups inside the atom start unset
            reset = caps[:first] + (None,) * cnt + caps[first + cnt:]
        if lo > 0:
            return m(body, i, reset, after)
        if not greedy:
            r = k(i, caps)
            if r is not None:
                return r
            return m(body, i, reset, after)
        r = m(body, i, reset, after)
        if r is not None:
            return r
        return k(i, caps)

    def finish(j, caps):
        return ((start, j),) + caps[1:]

    return m(tree, start, caps0, finish)


def search(pattern, text):
    if not isinstance(pattern, str) or not isinstance(text, str):
        return "ERROR"
    p = _Parser(pattern)
    try:
        tree = p.parse()
    except _Bad:
        return "ERROR"
    # About 3 Python frames per consumed character; texts are <= 1,000 chars.
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 100000))
    try:
        caps0 = (None,) * (p.ngroups + 1)
        for start in range(len(text) + 1):
            r = _run(tree, text, start, caps0)
            if r is not None:
                return r
        return None
    finally:
        sys.setrecursionlimit(old)
```
