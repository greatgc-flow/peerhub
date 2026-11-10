```python
import sys


class _Bad(Exception):
    pass


_META = set('\\.|*+?()[]{}^$')
_ESC_LIT = '\\.|*+?()[]{}^$-'


def _is_digit(c):
    return '0' <= c <= '9'


def _is_word(c):
    return ('a' <= c <= 'z') or ('A' <= c <= 'Z') or ('0' <= c <= '9') or c == '_'


def _is_space(c):
    return c in ' \t\n'


_PREDS = {
    'd': _is_digit,
    'w': _is_word,
    's': _is_space,
    'D': lambda c: not _is_digit(c),
    'W': lambda c: not _is_word(c),
    'S': lambda c: not _is_space(c),
}


def _make_set(neg, ranges, preds):
    def member(c):
        hit = False
        for lo, hi in ranges:
            if lo <= c <= hi:
                hit = True
                break
        if not hit:
            for pf in preds:
                if pf(c):
                    hit = True
                    break
        return hit != neg
    return member


def _digits(p, j):
    s = j
    while j < len(p) and '0' <= p[j] <= '9':
        j += 1
    return p[s:j], j


class _Parser:
    def __init__(self, p):
        self.p = p
        self.i = 0
        self.ngroups = 0
        self.refs = []

    def parse(self):
        tree = self._alt()
        if self.i != len(self.p):
            raise _Bad()
        if self.refs and max(self.refs) > self.ngroups:
            raise _Bad()
        return tree

    def _alt(self):
        branches = [self._seq()]
        while self.i < len(self.p) and self.p[self.i] == '|':
            self.i += 1
            branches.append(self._seq())
        if len(branches) == 1:
            return branches[0]
        return ('alt', branches)

    def _seq(self):
        p = self.p
        n = len(p)
        items = []
        while self.i < n and p[self.i] not in '|)':
            c = p[self.i]
            if c == '^':
                self.i += 1
                items.append(('bol',))
                continue
            if c == '$':
                self.i += 1
                items.append(('eol',))
                continue
            before = self.ngroups
            atom = self._atom()
            q = self._quant()
            if q is None:
                items.append(atom)
            else:
                mn, mx, greedy = q
                groups = list(range(before + 1, self.ngroups + 1))
                items.append(('rep', atom, mn, mx, greedy, groups))
        return ('seq', items)

    def _atom(self):
        p = self.p
        n = len(p)
        c = p[self.i]
        if c == '(':
            if self.i + 1 < n and p[self.i + 1] == '?':
                if p[self.i + 1:self.i + 3] != '?:':
                    raise _Bad()
                self.i += 3
                node = self._alt()
            else:
                self.ngroups += 1
                idx = self.ngroups
                self.i += 1
                node = ('group', idx, self._alt())
            if self.i < n and p[self.i] == ')':
                self.i += 1
            else:
                raise _Bad()
            return node
        if c == '[':
            return self._class()
        if c == '\\':
            kind, v = self._escape(False)
            if kind == 'c':
                return ('char', v)
            if kind == 'p':
                return ('set', v)
            return ('backref', v)
        if c == '.':
            self.i += 1
            return ('any',)
        if c in _META:
            raise _Bad()
        self.i += 1
        return ('char', c)

    def _escape(self, in_class):
        p = self.p
        if self.i + 1 >= len(p):
            raise _Bad()
        e = p[self.i + 1]
        self.i += 2
        if e in _ESC_LIT:
            return ('c', e)
        if e == 'n':
            return ('c', '\n')
        if e == 't':
            return ('c', '\t')
        if e in _PREDS:
            return ('p', _PREDS[e])
        if not in_class and '1' <= e <= '9':
            v = int(e)
            self.refs.append(v)
            return ('b', v)
        raise _Bad()

    def _class_item(self):
        if self.p[self.i] == '\\':
            return self._escape(True)
        c = self.p[self.i]
        self.i += 1
        return ('c', c)

    def _class(self):
        p = self.p
        n = len(p)
        self.i += 1  # skip '['
        neg = False
        if self.i < n and p[self.i] == '^':
            neg = True
            self.i += 1
        ranges = []
        preds = []
        while True:
            if self.i >= n:
                raise _Bad()
            if p[self.i] == ']':
                self.i += 1
                break
            kind, v = self._class_item()
            if kind == 'p':
                preds.append(v)
                continue
            lo = v
            if self.i + 1 < n and p[self.i] == '-' and p[self.i + 1] != ']':
                self.i += 1
                kind2, v2 = self._class_item()
                if kind2 == 'p':
                    raise _Bad()
                if lo > v2:
                    raise _Bad()
                ranges.append((lo, v2))
            else:
                ranges.append((lo, lo))
        return ('set', _make_set(neg, ranges, preds))

    def _quant(self):
        p = self.p
        n = len(p)
        if self.i >= n:
            return None
        c = p[self.i]
        if c == '*':
            mn, mx = 0, None
            self.i += 1
        elif c == '+':
            mn, mx = 1, None
            self.i += 1
        elif c == '?':
            mn, mx = 0, 1
            self.i += 1
        elif c == '{':
            m, j = _digits(p, self.i + 1)
            if not m:
                raise _Bad()
            if j < n and p[j] == '}':
                mn = mx = int(m)
                j += 1
            elif j < n and p[j] == ',':
                nd, j = _digits(p, j + 1)
                if j >= n or p[j] != '}':
                    raise _Bad()
                j += 1
                mn = int(m)
                mx = int(nd) if nd else None
                if mx is not None and mx < mn:
                    raise _Bad()
            else:
                raise _Bad()
            if mn > 1000 or (mx is not None and mx > 1000):
                raise _Bad()
            self.i = j
        else:
            return None
        greedy = True
        if self.i < n and p[self.i] == '?':
            greedy = False
            self.i += 1
        return mn, mx, greedy


def _match(node, text, n, pos, caps, k):
    t = node[0]
    if t == 'char':
        if pos < n and text[pos] == node[1]:
            return k(pos + 1, caps)
        return None
    if t == 'any':
        if pos < n and text[pos] != '\n':
            return k(pos + 1, caps)
        return None
    if t == 'set':
        if pos < n and node[1](text[pos]):
            return k(pos + 1, caps)
        return None
    if t == 'bol':
        return k(pos, caps) if pos == 0 else None
    if t == 'eol':
        return k(pos, caps) if pos == n else None
    if t == 'seq':
        return _match_seq(node[1], 0, text, n, pos, caps, k)
    if t == 'alt':
        for b in node[1]:
            r = _match(b, text, n, pos, caps, k)
            if r is not None:
                return r
        return None
    if t == 'group':
        idx = node[1]
        start = pos

        def close(p2, c2):
            return k(p2, c2[:idx] + ((start, p2),) + c2[idx + 1:])

        return _match(node[2], text, n, pos, caps, close)
    if t == 'backref':
        g = caps[node[1]]
        if g is None:
            return k(pos, caps)
        s, e = g
        if text.startswith(text[s:e], pos):
            return k(pos + (e - s), caps)
        return None
    # 'rep'
    return _match_rep(node, 0, text, n, pos, caps, k)


def _match_seq(items, i, text, n, pos, caps, k):
    if i == len(items):
        return k(pos, caps)
    return _match(items[i], text, n, pos, caps,
                  lambda p2, c2: _match_seq(items, i + 1, text, n, p2, c2, k))


def _match_rep(node, count, text, n, pos, caps, k):
    atom, mn, mx, greedy, groups = node[1], node[2], node[3], node[4], node[5]

    def iterate():
        if mx is not None and count >= mx:
            return None
        c = caps
        for g in groups:
            c = c[:g] + (None,) + c[g + 1:]

        def after(p2, c2):
            if p2 == pos and count >= mn:
                return None
            return _match_rep(node, count + 1, text, n, p2, c2, k)

        return _match(atom, text, n, pos, c, after)

    if count < mn:
        return iterate()
    if greedy:
        r = iterate()
        if r is not None:
            return r
        return k(pos, caps)
    r = k(pos, caps)
    if r is not None:
        return r
    return iterate()


def search(pattern, text):
    parser = _Parser(pattern)
    try:
        tree = parser.parse()
    except _Bad:
        return "ERROR"
    ngroups = parser.ngroups
    n = len(text)
    if sys.getrecursionlimit() < 100000:
        sys.setrecursionlimit(100000)

    def final(p, c):
        return (p, c)

    for start in range(n + 1):
        r = _match(tree, text, n, start, (None,) * (ngroups + 1), final)
        if r is not None:
            end, caps = r
            return ((start, end),) + tuple(caps[1:])
    return None
```
