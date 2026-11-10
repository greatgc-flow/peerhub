```python
import sys
import threading


class _E(Exception):
    pass


_SPECIAL = '\\.|*+?()[]{}^$'
_ESC_LIT = '\\.|*+?()[]{}^$-'
_DIG = '0123456789'


def _esc_test(l, ch):
    if l == 'd':
        return '0' <= ch <= '9'
    if l == 'w':
        return ch.isascii() and (ch.isalnum() or ch == '_')
    if l == 's':
        return ch in ' \t\n'
    return not _esc_test(l.lower(), ch)


def _parse(s):
    n = len(s)
    st = {'i': 0, 'ng': 0, 'mr': 0}

    def alt():
        seqs = [seq()]
        while st['i'] < n and s[st['i']] == '|':
            st['i'] += 1
            seqs.append(seq())
        return seqs[0] if len(seqs) == 1 else ('alt', seqs)

    def seq():
        items = []
        while st['i'] < n and s[st['i']] not in '|)':
            items.append(item())
        return ('seq', items)

    def item():
        c = s[st['i']]
        if c == '^':
            st['i'] += 1
            return ('bol',)
        if c == '$':
            st['i'] += 1
            return ('eol',)
        return quant(atom())

    def quant(a):
        i = st['i']
        if i >= n:
            return a
        c = s[i]
        if c == '*':
            lo, hi = 0, None
            i += 1
        elif c == '+':
            lo, hi = 1, None
            i += 1
        elif c == '?':
            lo, hi = 0, 1
            i += 1
        elif c == '{':
            j = i + 1
            k = j
            while k < n and s[k] in _DIG:
                k += 1
            if k == j or k >= n:
                raise _E()
            lo = int(s[j:k])
            if s[k] == '}':
                hi = lo
                k += 1
            elif s[k] == ',':
                k += 1
                j2 = k
                while k < n and s[k] in _DIG:
                    k += 1
                if k >= n:
                    raise _E()
                hi = int(s[j2:k]) if k > j2 else None
                if s[k] != '}':
                    raise _E()
                k += 1
            else:
                raise _E()
            if lo > 1000 or (hi is not None and (hi > 1000 or hi < lo)):
                raise _E()
            i = k
        else:
            return a
        lazy = False
        if i < n and s[i] == '?':
            lazy = True
            i += 1
        st['i'] = i
        return ('rep', a, lo, hi, lazy)

    def atom():
        i = st['i']
        c = s[i]
        if c == '(':
            i += 1
            cap = True
            if s.startswith('?:', i):
                i += 2
                cap = False
            gi = 0
            if cap:
                st['ng'] += 1
                gi = st['ng']
            st['i'] = i
            node = alt()
            i = st['i']
            if i >= n or s[i] != ')':
                raise _E()
            st['i'] = i + 1
            return ('group', gi, node) if cap else node
        if c == '.':
            st['i'] = i + 1
            return ('one', ('any',))
        if c == '[':
            return cls()
        if c == '\\':
            i += 1
            if i >= n:
                raise _E()
            e = s[i]
            st['i'] = i + 1
            if e in _ESC_LIT:
                return ('one', ('lit', e))
            if e == 'n':
                return ('one', ('lit', '\n'))
            if e == 't':
                return ('one', ('lit', '\t'))
            if e in 'dwsDWS':
                return ('one', ('esc', e))
            if e in '123456789':
                v = int(e)
                st['mr'] = max(st['mr'], v)
                return ('bref', v)
            raise _E()
        if c in _SPECIAL:
            raise _E()
        st['i'] = i + 1
        return ('one', ('lit', c))

    def cls_item():
        i = st['i']
        if i >= n:
            raise _E()
        c = s[i]
        if c != '\\':
            st['i'] = i + 1
            return ('c', c)
        i += 1
        if i >= n:
            raise _E()
        e = s[i]
        st['i'] = i + 1
        if e in _ESC_LIT:
            return ('c', e)
        if e == 'n':
            return ('c', '\n')
        if e == 't':
            return ('c', '\t')
        if e in 'dwsDWS':
            return ('e', e)
        raise _E()

    def cls():
        st['i'] += 1
        neg = False
        if st['i'] < n and s[st['i']] == '^':
            neg = True
            st['i'] += 1
        items = []
        while True:
            i = st['i']
            if i >= n:
                raise _E()
            if s[i] == ']':
                st['i'] = i + 1
                break
            x = cls_item()
            i = st['i']
            if i < n and s[i] == '-' and i + 1 < n and s[i + 1] != ']':
                st['i'] = i + 1
                y = cls_item()
                if x[0] != 'c' or y[0] != 'c' or x[1] > y[1]:
                    raise _E()
                items.append(('r', x[1], y[1]))
            elif i < n and s[i] == '-' and i + 1 >= n:
                raise _E()
            elif x[0] == 'c':
                items.append(('r', x[1], x[1]))
            else:
                items.append(('e', x[1]))
        return ('one', ('cls', items, neg))

    node = alt()
    if st['i'] != n:
        raise _E()
    return node, st['ng'], st['mr']


def _mkpred(sp):
    t = sp[0]
    if t == 'lit':
        ch = sp[1]
        return lambda x: x == ch
    if t == 'any':
        return lambda x: x != '\n'
    if t == 'esc':
        l = sp[1]
        return lambda x: _esc_test(l, x)
    items = sp[1]
    neg = sp[2]

    def p(ch):
        hit = False
        for it in items:
            if it[0] == 'r':
                if it[1] <= ch <= it[2]:
                    hit = True
                    break
            elif _esc_test(it[1], ch):
                hit = True
                break
        return hit != neg
    return p


def _groups_in(node, acc):
    t = node[0]
    if t == 'group':
        acc.append(node[1])
        _groups_in(node[2], acc)
    elif t == 'seq' or t == 'alt':
        for ch in node[1]:
            _groups_in(ch, acc)
    elif t == 'rep':
        _groups_in(node[1], acc)
    return acc


def _run(ast, ng, text, hasref):
    n = len(text)
    caps = [None] * (ng + 1)
    dead = set()
    memo = not hasref
    ids = {(): 0}
    cnt = [0]

    def newid():
        cnt[0] += 1
        return cnt[0]

    def mk(*t):
        v = ids.get(t)
        if v is None:
            v = len(ids)
            ids[t] = v
        return v

    def wrap(nid, f):
        if not memo:
            return f

        def g(pos, k, c):
            key = (nid, pos, c)
            if key in dead:
                return False
            if f(pos, k, c):
                return True
            dead.add(key)
            return False
        return g

    def comp(node):
        t = node[0]
        if t == 'one':
            p = _mkpred(node[1])

            def f(pos, k, c):
                if pos < n and p(text[pos]):
                    return k(pos + 1)
                return False
            return f
        if t == 'bol':
            return lambda pos, k, c: k(pos) if pos == 0 else False
        if t == 'eol':
            return lambda pos, k, c: k(pos) if pos == n else False
        if t == 'bref':
            gi = node[1]

            def f(pos, k, c):
                sp = caps[gi]
                if sp is None:
                    return k(pos)
                sub = text[sp[0]:sp[1]]
                if text.startswith(sub, pos):
                    return k(pos + len(sub))
                return False
            return f
        if t == 'seq':
            fs = [comp(x) for x in node[1]]
            m = len(fs)
            if m == 0:
                return lambda pos, k, c: k(pos)
            if m == 1:
                return fs[0]
            sid = newid()

            def f(pos, k, c):
                def step(i, p0):
                    if i == m - 1:
                        return fs[i](p0, k, c)
                    return fs[i](p0, lambda p: step(i + 1, p),
                                 mk('s', sid, i + 1, c))
                return step(0, pos)
            return wrap(sid, f)
        if t == 'alt':
            fs = [comp(x) for x in node[1]]
            aid = newid()

            def f(pos, k, c):
                for g in fs:
                    if g(pos, k, c):
                        return True
                return False
            return wrap(aid, f)
        if t == 'group':
            gi = node[1]
            g = comp(node[2])
            gid = newid()

            def f(pos, k, c):
                def k2(p):
                    prev = caps[gi]
                    caps[gi] = (pos, p)
                    if k(p):
                        return True
                    caps[gi] = prev
                    return False
                return g(pos, k2, c)
            return wrap(gid, f)
        # rep
        inner_node, mn, mx, lazy = node[1], node[2], node[3], node[4]
        rid = newid()
        if inner_node[0] == 'one':
            pr = _mkpred(inner_node[1])

            def f(pos, k, c):
                key = (rid, pos, c)
                if memo and key in dead:
                    return False
                limit = n - pos if mx is None else min(mx, n - pos)
                if not lazy:
                    r = 0
                    while r < limit and pr(text[pos + r]):
                        r += 1
                    j = r
                    while j >= mn:
                        if k(pos + j):
                            return True
                        j -= 1
                else:
                    j = 0
                    ok = True
                    while j < mn:
                        if j < limit and pr(text[pos + j]):
                            j += 1
                        else:
                            ok = False
                            break
                    if ok:
                        while True:
                            if k(pos + j):
                                return True
                            if j < limit and pr(text[pos + j]):
                                j += 1
                            else:
                                break
                if memo:
                    dead.add(key)
                return False
            return f

        inner = comp(inner_node)
        gids = _groups_in(inner_node, [])

        def iterate(count, pos, k, c, cn):
            saved = [caps[g] for g in gids]
            for g in gids:
                caps[g] = None
            ok = count >= mn

            def k2(p):
                if ok and p == pos:
                    return False
                return attempt(count + 1, p, k, c)
            if inner(pos, k2, mk('r', rid, cn, pos if ok else -1, c)):
                return True
            for g, v in zip(gids, saved):
                caps[g] = v
            return False

        def attempt(count, pos, k, c):
            cn = count if mx is not None else min(count, mn)
            key = (rid, cn, pos, c)
            if memo and key in dead:
                return False
            can = (mx is None or count < mx)
            if not lazy:
                if can and iterate(count, pos, k, c, cn):
                    return True
                if count >= mn and k(pos):
                    return True
            else:
                if count >= mn and k(pos):
                    return True
                if can and iterate(count, pos, k, c, cn):
                    return True
            if memo:
                dead.add(key)
            return False

        def f(pos, k, c):
            return attempt(0, pos, k, c)
        return f

    root = comp(ast)
    for start in range(n + 1):
        for i in range(len(caps)):
            caps[i] = None
        end = [None]

        def kend(p):
            end[0] = p
            return True
        if root(start, kend, 0):
            return ((start, end[0]),) + tuple(caps[1:])
    return None


def search(pattern: str, text: str):
    try:
        ast, ng, mr = _parse(pattern)
    except Exception:
        return "ERROR"
    if mr > ng:
        return "ERROR"
    res = [None]

    def work():
        try:
            res[0] = _run(ast, ng, text, mr > 0)
        except BaseException:
            res[0] = None

    try:
        sys.setrecursionlimit(1000000)
    except Exception:
        pass
    started = False
    old = None
    try:
        old = threading.stack_size()
        threading.stack_size(256 * 1024 * 1024)
        t = threading.Thread(target=work)
        t.start()
        started = True
        t.join()
    except BaseException:
        if not started:
            work()
    finally:
        try:
            if old is not None:
                threading.stack_size(old)
        except Exception:
            pass
    return res[0]
```
