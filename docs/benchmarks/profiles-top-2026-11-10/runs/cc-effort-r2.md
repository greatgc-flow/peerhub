```python
import sys
import threading


class _E(Exception):
    pass


def _pred(name, ch):
    if name == 'd':
        return '0' <= ch <= '9'
    if name == 'w':
        return ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or ch == '_'
    if name == 's':
        return ch in ' \t\n'
    if name == 'D':
        return not ('0' <= ch <= '9')
    if name == 'W':
        return not _pred('w', ch)
    return ch not in ' \t\n'  # 'S'


def _parse(p):
    L = len(p)
    st = {'pos': 0, 'ng': 0, 'maxref': 0}

    def err():
        raise _E()

    def esc(inclass):
        st['pos'] += 1
        pos = st['pos']
        if pos >= L:
            err()
        c = p[pos]
        st['pos'] += 1
        if c in '\\.|*+?()[]{}^$-':
            return ('c', c)
        if c == 'n':
            return ('c', '\n')
        if c == 't':
            return ('c', '\t')
        if c in 'dwsDWS':
            return ('s', c)
        if c in '123456789':
            if inclass:
                err()
            n = int(c)
            if n > st['maxref']:
                st['maxref'] = n
            return ('r', n)
        err()

    def citem():
        pos = st['pos']
        if pos >= L:
            err()
        c = p[pos]
        if c == '\\':
            return esc(True)
        st['pos'] += 1
        return ('c', c)

    def cls():
        st['pos'] += 1
        neg = False
        if st['pos'] < L and p[st['pos']] == '^':
            neg = True
            st['pos'] += 1
        chars = set()
        ranges = []
        preds = []
        while True:
            pos = st['pos']
            if pos >= L:
                err()
            if p[pos] == ']':
                st['pos'] += 1
                break
            x = citem()
            pos = st['pos']
            if pos + 1 < L and p[pos] == '-' and p[pos + 1] != ']':
                st['pos'] += 1
                y = citem()
                if x[0] != 'c' or y[0] != 'c' or x[1] > y[1]:
                    err()
                ranges.append((x[1], y[1]))
            else:
                if x[0] == 'c':
                    chars.add(x[1])
                else:
                    preds.append(x[1])
        return ('class', neg, chars, ranges, preds)

    def expect_close():
        if st['pos'] >= L or p[st['pos']] != ')':
            err()
        st['pos'] += 1

    def atom():
        pos = st['pos']
        c = p[pos]
        if c == '(':
            if p.startswith('(?:', pos):
                st['pos'] += 3
                inner = alt()
                expect_close()
                return inner
            st['ng'] += 1
            idx = st['ng']
            st['pos'] += 1
            inner = alt()
            expect_close()
            return ('group', idx, inner)
        if c == '[':
            return cls()
        if c == '.':
            st['pos'] += 1
            return ('any',)
        if c == '\\':
            e = esc(False)
            if e[0] == 'c':
                return ('char', e[1])
            if e[0] == 's':
                return ('class', False, set(), [], [e[1]])
            return ('ref', e[1])
        if c in '|*+?)]{}^$':
            err()
        st['pos'] += 1
        return ('char', c)

    def num():
        s = st['pos']
        while st['pos'] < L and p[st['pos']] in '0123456789':
            st['pos'] += 1
        if st['pos'] > s:
            return int(p[s:st['pos']])
        return None

    def item():
        c = p[st['pos']]
        if c == '^':
            st['pos'] += 1
            return ('bol',)
        if c == '$':
            st['pos'] += 1
            return ('eol',)
        g0 = st['ng']
        a = atom()
        g1 = st['ng']
        if st['pos'] < L and p[st['pos']] in '*+?{':
            q = p[st['pos']]
            st['pos'] += 1
            if q == '*':
                mn, mx = 0, None
            elif q == '+':
                mn, mx = 1, None
            elif q == '?':
                mn, mx = 0, 1
            else:
                m = num()
                if m is None or st['pos'] >= L:
                    err()
                if p[st['pos']] == '}':
                    st['pos'] += 1
                    mn = mx = m
                elif p[st['pos']] == ',':
                    st['pos'] += 1
                    n2 = num()
                    if st['pos'] >= L or p[st['pos']] != '}':
                        err()
                    st['pos'] += 1
                    mn, mx = m, n2
                else:
                    err()
                if m > 1000 or (mx is not None and (mx > 1000 or m > mx)):
                    err()
            lazy = False
            if st['pos'] < L and p[st['pos']] == '?':
                lazy = True
                st['pos'] += 1
            return ('quant', a, mn, mx, lazy, list(range(g0 + 1, g1 + 1)))
        return a

    def seq():
        items = []
        while st['pos'] < L and p[st['pos']] not in '|)':
            items.append(item())
        return ('seq', items)

    def alt():
        alts = [seq()]
        while st['pos'] < L and p[st['pos']] == '|':
            st['pos'] += 1
            alts.append(seq())
        return ('alt', alts)

    node = alt()
    if st['pos'] < L:
        err()
    if st['maxref'] > st['ng']:
        err()
    return node, st['ng']


def _match(ast, ng, text):
    n = len(text)
    spans = [None] * (ng + 1)

    def single(node):
        t = node[0]
        if t == 'char':
            c = node[1]
            return lambda ch: ch == c
        if t == 'any':
            return lambda ch: ch != '\n'
        if t == 'class':
            _, neg, chars, ranges, preds = node

            def cm(ch):
                r = ch in chars
                if not r:
                    for a, b in ranges:
                        if a <= ch <= b:
                            r = True
                            break
                if not r:
                    for pn in preds:
                        if _pred(pn, ch):
                            r = True
                            break
                return r != neg
            return cm
        return None

    def comp(node):
        t = node[0]
        sp = single(node)
        if sp is not None:
            def f(i, k):
                return i < n and sp(text[i]) and k(i + 1)
            return f
        if t == 'bol':
            return lambda i, k: i == 0 and k(i)
        if t == 'eol':
            return lambda i, k: i == n and k(i)
        if t == 'ref':
            idx = node[1]

            def f(i, k):
                s = spans[idx]
                if s is None:
                    return k(i)
                a, b = s
                ln = b - a
                if i + ln <= n and text[a:b] == text[i:i + ln]:
                    return k(i + ln)
                return False
            return f
        if t == 'group':
            idx = node[1]
            inner = comp(node[2])

            def f(i, k):
                def after(j):
                    old = spans[idx]
                    spans[idx] = (i, j)
                    if k(j):
                        return True
                    spans[idx] = old
                    return False
                return inner(i, after)
            return f
        if t == 'seq':
            fs = [comp(x) for x in node[1]]
            L = len(fs)
            if L == 0:
                return lambda i, k: k(i)
            if L == 1:
                return fs[0]

            def step(idx, i, k):
                if idx == L - 1:
                    return fs[idx](i, k)
                return fs[idx](i, lambda j: step(idx + 1, j, k))

            return lambda i, k: step(0, i, k)
        if t == 'alt':
            fs = [comp(x) for x in node[1]]
            if len(fs) == 1:
                return fs[0]

            def f(i, k):
                for a in fs:
                    if a(i, k):
                        return True
                return False
            return f
        # quant
        _, atom, mn, mx, lazy, gl = node
        sp = single(atom)
        if sp is not None:
            def f(i, k):
                limit = n if mx is None else min(n, i + mx)
                if lazy:
                    j = i
                    while j < i + mn:
                        if j < n and sp(text[j]):
                            j += 1
                        else:
                            return False
                    while True:
                        if k(j):
                            return True
                        if j < limit and sp(text[j]):
                            j += 1
                        else:
                            return False
                j = i
                while j < limit and sp(text[j]):
                    j += 1
                for e in range(j, i + mn - 1, -1):
                    if k(e):
                        return True
                return False
            return f
        af = comp(atom)

        def loop(i, count, k):
            can_iter = mx is None or count < mx

            def try_iter():
                saved = [spans[g] for g in gl]
                for g in gl:
                    spans[g] = None

                def after(j):
                    if j == i and count >= mn:
                        return False
                    return loop(j, count + 1, k)
                if af(i, after):
                    return True
                for g, s in zip(gl, saved):
                    spans[g] = s
                return False

            if lazy:
                if count >= mn and k(i):
                    return True
                return can_iter and try_iter()
            if can_iter and try_iter():
                return True
            return count >= mn and k(i)

        return lambda i, k: loop(i, 0, k)

    top = comp(ast)
    endbox = [0]

    def fin(j):
        endbox[0] = j
        return True

    for start in range(n + 1):
        for g in range(ng + 1):
            spans[g] = None
        if top(start, fin):
            return tuple([(start, endbox[0])] + spans[1:])
    return None


def search(pattern, text):
    try:
        ast, ng = _parse(pattern)
    except BaseException:
        return "ERROR"
    box = [None]

    def w():
        try:
            box[0] = _match(ast, ng, text)
        except BaseException:
            box[0] = None

    try:
        sys.setrecursionlimit(1000000)
        threading.stack_size(256 * 1024 * 1024)
        t = threading.Thread(target=w)
        t.start()
        t.join()
    except BaseException:
        w()
    return box[0]
```
