```python
def search(pattern, text):
    try:
        prog = _compile(pattern)
    except Exception:
        return "ERROR"
    try:
        return _execute(prog, text)
    except Exception:
        return None


class _Err(Exception):
    pass


_SPECIAL = set('\\.|*+?()[]{}^$')
_ESC_LIT = set('\\.|*+?()[]{}^$-')
_DSET = frozenset('0123456789')
_WSET = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
_SSET = frozenset(' \t\n')
_ESC_SETS = {
    'd': lambda ch: ch in _DSET, 'D': lambda ch: ch not in _DSET,
    'w': lambda ch: ch in _WSET, 'W': lambda ch: ch not in _WSET,
    's': lambda ch: ch in _SSET, 'S': lambda ch: ch not in _SSET,
}

(LIT, SET, SPLIT, JMP, BOL, EOL, OPEN, CLOSE, BREF, REP,
 LINIT, LDEC, LITER, LEND, LEXIT, MATCH) = range(16)


def _parse(p):
    n = len(p)
    st = {'i': 0, 'ng': 0}
    brefs = []

    def peek():
        i = st['i']
        return p[i] if i < n else None

    def p_alt():
        alts = [p_seq()]
        while peek() == '|':
            st['i'] += 1
            alts.append(p_seq())
        return alts[0] if len(alts) == 1 else ('alt', alts)

    def p_seq():
        items = []
        while st['i'] < n and p[st['i']] not in '|)':
            items.append(p_item())
        return ('seq', items)

    def digits():
        j = st['i']
        while st['i'] < n and p[st['i']] in '0123456789':
            st['i'] += 1
        if j == st['i']:
            raise _Err()
        v = int(p[j:st['i']])
        if v > 1000:
            raise _Err()
        return v

    def p_item():
        c = p[st['i']]
        if c == '^':
            st['i'] += 1
            return ('bol',)
        if c == '$':
            st['i'] += 1
            return ('eol',)
        a = p_atom()
        c = peek()
        if c == '*':
            m, M = 0, None
            st['i'] += 1
        elif c == '+':
            m, M = 1, None
            st['i'] += 1
        elif c == '?':
            m, M = 0, 1
            st['i'] += 1
        elif c == '{':
            st['i'] += 1
            m = digits()
            c2 = peek()
            if c2 == '}':
                M = m
                st['i'] += 1
            elif c2 == ',':
                st['i'] += 1
                if peek() == '}':
                    M = None
                    st['i'] += 1
                else:
                    M = digits()
                    if peek() != '}':
                        raise _Err()
                    st['i'] += 1
            else:
                raise _Err()
            if M is not None and m > M:
                raise _Err()
        else:
            return a
        lazy = False
        if peek() == '?':
            lazy = True
            st['i'] += 1
        return ('quant', a, m, M, lazy)

    def p_escape(in_class):
        st['i'] += 1
        if st['i'] >= n:
            raise _Err()
        c = p[st['i']]
        st['i'] += 1
        if c in _ESC_LIT:
            return ('ch', c)
        if c == 'n':
            return ('ch', '\n')
        if c == 't':
            return ('ch', '\t')
        if c in _ESC_SETS:
            return ('set', _ESC_SETS[c])
        if c in '123456789':
            if in_class:
                raise _Err()
            return ('bref', int(c))
        raise _Err()

    def p_citem():
        if p[st['i']] == '\\':
            return p_escape(True)
        c = p[st['i']]
        st['i'] += 1
        return ('ch', c)

    def p_class():
        st['i'] += 1
        neg = False
        if peek() == '^':
            neg = True
            st['i'] += 1
        chars = set()
        ranges = []
        fns = []
        while True:
            if st['i'] >= n:
                raise _Err()
            if p[st['i']] == ']':
                st['i'] += 1
                break
            x = p_citem()
            i = st['i']
            if i < n and p[i] == '-' and i + 1 < n and p[i + 1] != ']':
                st['i'] += 1
                y = p_citem()
                if x[0] != 'ch' or y[0] != 'ch' or x[1] > y[1]:
                    raise _Err()
                ranges.append((x[1], y[1]))
            else:
                if x[0] == 'ch':
                    chars.add(x[1])
                else:
                    fns.append(x[1])
        chars = frozenset(chars)
        ranges = tuple(ranges)
        fns = tuple(fns)
        if not ranges and not fns:
            if neg:
                test = lambda ch: ch not in chars
            else:
                test = lambda ch: ch in chars
        else:
            def test(ch):
                r = ch in chars
                if not r:
                    for lo, hi in ranges:
                        if lo <= ch <= hi:
                            r = True
                            break
                if not r:
                    for f in fns:
                        if f(ch):
                            r = True
                            break
                return r != neg
        return ('set', test)

    def p_atom():
        c = p[st['i']]
        if c == '.':
            st['i'] += 1
            return ('set', lambda ch: ch != '\n')
        if c == '[':
            return p_class()
        if c == '\\':
            r = p_escape(False)
            if r[0] == 'bref':
                brefs.append(r[1])
            return r
        if c == '(':
            if p.startswith('(?:', st['i']):
                st['i'] += 3
                inner = p_alt()
                if peek() != ')':
                    raise _Err()
                st['i'] += 1
                return ('nc', inner)
            st['i'] += 1
            st['ng'] += 1
            k = st['ng']
            inner = p_alt()
            if peek() != ')':
                raise _Err()
            st['i'] += 1
            return ('group', k, inner)
        if c in _SPECIAL:
            raise _Err()
        st['i'] += 1
        return ('ch', c)

    tree = p_alt()
    if st['i'] != n:
        raise _Err()
    ng = st['ng']
    for b in brefs:
        if b > ng:
            raise _Err()
    return tree, ng, bool(brefs)


def _groups_in(node, out):
    t = node[0]
    if t == 'seq' or t == 'alt':
        for x in node[1]:
            _groups_in(x, out)
    elif t == 'group':
        out.append(node[1])
        _groups_in(node[2], out)
    elif t == 'nc':
        _groups_in(node[1], out)
    elif t == 'quant':
        _groups_in(node[1], out)


def _compile(pattern):
    if not isinstance(pattern, str):
        raise _Err()
    tree, ng, hb = _parse(pattern)
    code = []
    nloops = [0]

    def emit(ins):
        code.append(ins)
        return len(code) - 1

    def comp(node):
        t = node[0]
        if t == 'seq':
            for x in node[1]:
                comp(x)
        elif t == 'alt':
            alts = node[1]
            jmps = []
            for a in alts[:-1]:
                sp = emit([SPLIT, None, None])
                code[sp][1] = len(code)
                comp(a)
                jmps.append(emit([JMP, None]))
                code[sp][2] = len(code)
            comp(alts[-1])
            for j in jmps:
                code[j][1] = len(code)
        elif t == 'ch':
            emit((LIT, node[1]))
        elif t == 'set':
            emit((SET, node[1]))
        elif t == 'bol':
            emit((BOL,))
        elif t == 'eol':
            emit((EOL,))
        elif t == 'bref':
            emit((BREF, 2 * (node[1] - 1) + 1))
        elif t == 'group':
            k = node[1]
            emit((OPEN, 2 * (k - 1)))
            comp(node[2])
            emit((CLOSE, 2 * (k - 1)))
        elif t == 'nc':
            comp(node[1])
        elif t == 'quant':
            a, m, M, lazy = node[1], node[2], node[3], node[4]
            if a[0] == 'ch':
                c = a[1]
                emit((REP, (lambda ch, c=c: ch == c), m, M, lazy))
            elif a[0] == 'set':
                emit((REP, a[1], m, M, lazy))
            else:
                r = nloops[0]
                nloops[0] += 1
                gs = []
                _groups_in(a, gs)
                gidx = tuple(2 * (k - 1) for k in gs)
                emit((LINIT, 2 * r))
                head = emit(None)
                emit((LITER, 2 * r, gidx))
                comp(a)
                emit((LEND, 2 * r, m, M, head))
                ex = emit((LEXIT, 2 * r))
                code[head] = (LDEC, 2 * r, m, M, lazy, head + 1, ex)
        else:
            raise _Err()

    comp(tree)
    emit((MATCH,))
    code = [tuple(c) for c in code]
    return code, ng, nloops[0], hb


def _execute(prog, text):
    code, G, nloops, hb = prog
    if not isinstance(text, str):
        return None
    n = len(text)
    R = 2 * nloops
    visited = set()
    vadd = visited.add
    regs0 = (None,) * R
    cap0 = (None,) * (2 * G)
    runs = [None] * len(code)

    def akey(pc, pos, regs, cap):
        if R:
            l = list(regs)
            for i in range(1, R, 2):
                s = l[i]
                if s is not None:
                    l[i] = (s == pos)
            t = (pc, pos, tuple(l))
        else:
            t = (pc, pos)
        if hb:
            t = t + (cap,)
        return t

    def getrun(pc, test):
        arr = runs[pc]
        if arr is None:
            arr = [0] * (n + 1)
            for j in range(n - 1, -1, -1):
                if test(text[j]):
                    arr[j] = arr[j + 1] + 1
            runs[pc] = arr
        return arr

    for start in range(n + 1):
        stack = [(0, start, regs0, cap0, None)]
        while stack:
            pc, pos, regs, cap, ext = stack.pop()
            if ext is not None:
                cur, stop, step = ext
                if cur != stop:
                    stack.append((pc, pos, regs, cap, (cur + step, stop, step)))
                pos += cur
                pc += 1
                if akey(pc, pos, regs, cap) in visited:
                    continue
            while True:
                ins = code[pc]
                op = ins[0]
                if op == LIT:
                    if pos < n and text[pos] == ins[1]:
                        pos += 1
                        pc += 1
                        continue
                    break
                elif op == SET:
                    if pos < n and ins[1](text[pos]):
                        pos += 1
                        pc += 1
                        continue
                    break
                elif op == SPLIT:
                    k = akey(pc, pos, regs, cap)
                    if k in visited:
                        break
                    vadd(k)
                    stack.append((ins[2], pos, regs, cap, None))
                    pc = ins[1]
                elif op == JMP:
                    pc = ins[1]
                elif op == BOL:
                    if pos != 0:
                        break
                    pc += 1
                elif op == EOL:
                    if pos != n:
                        break
                    pc += 1
                elif op == OPEN:
                    c = list(cap)
                    c[ins[1]] = pos
                    cap = tuple(c)
                    pc += 1
                elif op == CLOSE:
                    c = list(cap)
                    c[ins[1] + 1] = (cap[ins[1]], pos)
                    cap = tuple(c)
                    pc += 1
                elif op == BREF:
                    sp = cap[ins[1]]
                    if sp is None:
                        pc += 1
                    else:
                        sub = text[sp[0]:sp[1]]
                        if text.startswith(sub, pos):
                            pos += len(sub)
                            pc += 1
                        else:
                            break
                elif op == REP:
                    k = akey(pc, pos, regs, cap)
                    if k in visited:
                        break
                    vadd(k)
                    m = ins[2]
                    M = ins[3]
                    cnt = getrun(pc, ins[1])[pos]
                    if M is not None and cnt > M:
                        cnt = M
                    if cnt < m:
                        break
                    if ins[4]:
                        if cnt > m:
                            stack.append((pc, pos, regs, cap, (m + 1, cnt, 1)))
                        pos += m
                    else:
                        if cnt > m:
                            stack.append((pc, pos, regs, cap, (cnt - 1, m, -1)))
                        pos += cnt
                    pc += 1
                elif op == LINIT:
                    l = list(regs)
                    l[ins[1]] = 0
                    regs = tuple(l)
                    pc += 1
                elif op == LDEC:
                    k = akey(pc, pos, regs, cap)
                    if k in visited:
                        break
                    vadd(k)
                    _, r, m, M, lazy, it, ex = ins
                    c = regs[r]
                    if c < m:
                        pc = it
                    elif M is not None and c >= M:
                        pc = ex
                    elif lazy:
                        stack.append((it, pos, regs, cap, None))
                        pc = ex
                    else:
                        stack.append((ex, pos, regs, cap, None))
                        pc = it
                elif op == LITER:
                    l = list(regs)
                    l[ins[1] + 1] = pos
                    regs = tuple(l)
                    if ins[2]:
                        c = list(cap)
                        for g in ins[2]:
                            c[g] = None
                            c[g + 1] = None
                        cap = tuple(c)
                    pc += 1
                elif op == LEND:
                    _, r, m, M, head = ins
                    c = regs[r]
                    s = regs[r + 1]
                    if pos == s and c >= m:
                        break
                    c += 1
                    if M is None and c > m:
                        c = m
                    l = list(regs)
                    l[r] = c
                    l[r + 1] = None
                    regs = tuple(l)
                    pc = head
                elif op == LEXIT:
                    l = list(regs)
                    l[ins[1]] = None
                    l[ins[1] + 1] = None
                    regs = tuple(l)
                    pc += 1
                elif op == MATCH:
                    return ((start, pos),) + tuple(cap[2 * i + 1] for i in range(G))
                else:
                    break
    return None
```
