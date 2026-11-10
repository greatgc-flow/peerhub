```python
import sys, time, threading

_SPECIAL = set('\\.|*+?()[]{}^$')


class _Err(Exception):
    pass


class _Timeout(Exception):
    pass


def _isd(c): return '0' <= c <= '9'
def _isw(c): return 'a' <= c <= 'z' or 'A' <= c <= 'Z' or '0' <= c <= '9' or c == '_'
def _iss(c): return c == ' ' or c == '\t' or c == '\n'


_PREDS = {
    'd': _isd, 'w': _isw, 's': _iss,
    'D': lambda c: not _isd(c), 'W': lambda c: not _isw(c), 'S': lambda c: not _iss(c),
}


class _Parser:
    def __init__(self, s):
        self.s = s
        self.i = 0
        self.ngroups = 0
        self.maxref = 0

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else None

    def parse(self):
        node = self.alt()
        if self.i != len(self.s):
            raise _Err()
        if self.maxref > self.ngroups:
            raise _Err()
        return node, self.ngroups

    def alt(self):
        seqs = [self.seq()]
        while self.peek() == '|':
            self.i += 1
            seqs.append(self.seq())
        return ('alt', seqs) if len(seqs) > 1 else seqs[0]

    def seq(self):
        items = []
        while True:
            c = self.peek()
            if c is None or c == '|' or c == ')':
                break
            items.append(self.item())
        return ('seq', items)

    def item(self):
        c = self.peek()
        if c == '^':
            self.i += 1
            return ('bol',)
        if c == '$':
            self.i += 1
            return ('eol',)
        return self.quant(self.atom())

    def atom(self):
        c = self.peek()
        if c is None:
            raise _Err()
        if c == '(':
            self.i += 1
            if self.s.startswith('?:', self.i):
                self.i += 2
                inner = self.alt()
                if self.peek() != ')':
                    raise _Err()
                self.i += 1
                return inner
            self.ngroups += 1
            idx = self.ngroups
            inner = self.alt()
            if self.peek() != ')':
                raise _Err()
            self.i += 1
            return ('group', idx, inner)
        if c == '[':
            return self.cls()
        if c == '\\':
            self.i += 1
            return self.escape(False)
        if c == '.':
            self.i += 1
            return ('any',)
        if c in _SPECIAL:
            raise _Err()
        self.i += 1
        return ('char', c)

    def escape(self, in_class):
        c = self.peek()
        if c is None:
            raise _Err()
        self.i += 1
        if c in '\\.|*+?()[]{}^$-':
            return ('char', c)
        if c == 'n':
            return ('char', '\n')
        if c == 't':
            return ('char', '\t')
        if c in _PREDS:
            return ('pred', _PREDS[c])
        if c in '123456789':
            if in_class:
                raise _Err()
            v = int(c)
            if v > self.maxref:
                self.maxref = v
            return ('ref', v)
        raise _Err()

    def citem(self):
        c = self.peek()
        if c is None:
            raise _Err()
        if c == '\\':
            self.i += 1
            return self.escape(True)
        self.i += 1
        return ('char', c)

    def cls(self):
        self.i += 1
        neg = False
        if self.peek() == '^':
            neg = True
            self.i += 1
        ranges = []
        preds = []
        while True:
            c = self.peek()
            if c is None:
                raise _Err()
            if c == ']':
                self.i += 1
                break
            x = self.citem()
            if self.peek() == '-' and self.i + 1 < len(self.s) and self.s[self.i + 1] != ']':
                self.i += 1
                y = self.citem()
                if x[0] != 'char' or y[0] != 'char' or x[1] > y[1]:
                    raise _Err()
                ranges.append((x[1], y[1]))
            elif x[0] == 'char':
                ranges.append((x[1], x[1]))
            else:
                preds.append(x[1])
        return ('set', neg, ranges, preds)

    def quant(self, atom):
        c = self.peek()
        s = self.s
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
            j = self.i + 1
            a = j
            while j < len(s) and '0' <= s[j] <= '9':
                j += 1
            if j == a:
                raise _Err()
            mn = int(s[a:j])
            if j < len(s) and s[j] == '}':
                mx = mn
                j += 1
            elif j < len(s) and s[j] == ',':
                j += 1
                b = j
                while j < len(s) and '0' <= s[j] <= '9':
                    j += 1
                if j >= len(s) or s[j] != '}':
                    raise _Err()
                mx = int(s[b:j]) if j > b else None
                j += 1
            else:
                raise _Err()
            if mn > 1000 or (mx is not None and (mx > 1000 or mx < mn)):
                raise _Err()
            self.i = j
        else:
            return atom
        lazy = False
        if self.peek() == '?':
            lazy = True
            self.i += 1
        return ('rep', atom, mn, mx, lazy)


def _groups_in(node):
    t = node[0]
    if t == 'group':
        return [node[1]] + _groups_in(node[2])
    if t in ('seq', 'alt'):
        out = []
        for x in node[1]:
            out.extend(_groups_in(x))
        return out
    if t == 'rep':
        return _groups_in(node[1])
    return []


def _run(node, ng, text):
    n = len(text)
    caps = [None] * (ng + 1)
    deadline = time.monotonic() + 0.9
    steps = [0]

    def tick():
        steps[0] += 1
        if (steps[0] & 2047) == 0 and time.monotonic() > deadline:
            raise _Timeout()

    def pred_m(p):
        def m(i, k):
            if i < n and p(text[i]):
                return k(i + 1)
            return None
        return m

    def comp(node):
        t = node[0]
        if t == 'seq':
            fs = [comp(x) for x in node[1]]

            def chain(fs):
                if not fs:
                    return lambda i, k: k(i)
                if len(fs) == 1:
                    return fs[0]
                first = fs[0]
                rest = chain(fs[1:])
                return lambda i, k: first(i, lambda j: rest(j, k))
            return chain(fs)
        if t == 'alt':
            fs = [comp(x) for x in node[1]]

            def m(i, k):
                tick()
                for f in fs:
                    r = f(i, k)
                    if r is not None:
                        return r
                return None
            return m
        if t == 'char':
            ch = node[1]
            return pred_m(lambda c: c == ch)
        if t == 'any':
            return pred_m(lambda c: c != '\n')
        if t == 'pred':
            return pred_m(node[1])
        if t == 'set':
            neg, ranges, preds = node[1], node[2], node[3]

            def p(c):
                hit = False
                for lo, hi in ranges:
                    if lo <= c <= hi:
                        hit = True
                        break
                if not hit:
                    for f in preds:
                        if f(c):
                            hit = True
                            break
                return hit != neg
            return pred_m(p)
        if t == 'bol':
            return lambda i, k: k(i) if i == 0 else None
        if t == 'eol':
            return lambda i, k: k(i) if i == n else None
        if t == 'group':
            idx = node[1]
            inner = comp(node[2])

            def m(i, k):
                def k2(j):
                    old = caps[idx]
                    caps[idx] = (i, j)
                    r = k(j)
                    if r is None:
                        caps[idx] = old
                    return r
                return inner(i, k2)
            return m
        if t == 'ref':
            idx = node[1]

            def m(i, k):
                s = caps[idx]
                if s is None:
                    return k(i)
                sub = text[s[0]:s[1]]
                if text.startswith(sub, i):
                    return k(i + len(sub))
                return None
            return m
        if t == 'rep':
            f = comp(node[1])
            mn, mx, lazy = node[2], node[3], node[4]
            inner_groups = _groups_in(node[1])

            def m(i, k):
                def iterate(count, i):
                    tick()

                    def try_iter():
                        saved = [caps[g] for g in inner_groups]
                        for g in inner_groups:
                            caps[g] = None

                        def k2(j):
                            if j == i and count >= mn:
                                return None
                            return iterate(count + 1, j)
                        r = f(i, k2)
                        if r is None:
                            for g, v in zip(inner_groups, saved):
                                caps[g] = v
                        return r
                    can_more = mx is None or count < mx
                    if lazy:
                        if count >= mn:
                            r = k(i)
                            if r is not None:
                                return r
                        if can_more:
                            return try_iter()
                        return None
                    if can_more:
                        r = try_iter()
                        if r is not None:
                            return r
                    if count >= mn:
                        return k(i)
                    return None
                return iterate(0, i)
            return m
        raise _Err()

    root = comp(node)
    for start in range(n + 1):
        for g in range(ng + 1):
            caps[g] = None

        def fin(j, start=start):
            caps[0] = (start, j)
            return tuple(caps)
        r = root(start, fin)
        if r is not None:
            return r
    return None


def _search(pattern, text):
    try:
        node, ng = _Parser(pattern).parse()
    except Exception:
        return "ERROR"
    try:
        return _run(node, ng, text)
    except Exception:
        return None


def search(pattern, text):
    result = [None]

    def runner():
        try:
            sys.setrecursionlimit(max(sys.getrecursionlimit(), 1000000))
        except Exception:
            pass
        try:
            result[0] = _search(pattern, text)
        except BaseException:
            result[0] = None
    try:
        try:
            threading.stack_size(256 * 1024 * 1024)
        except Exception:
            pass
        t = threading.Thread(target=runner)
        t.start()
        t.join()
    except BaseException:
        try:
            return _search(pattern, text)
        except BaseException:
            return None
    return result[0]
```
