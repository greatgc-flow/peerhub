```python
def search(pattern: str, text: str):
    ESC_LIT = set('\\.|*+?()[]{}^$-')
    SPECIAL = set('\\.|*+?()[]{}^$')

    def _digit(ch):
        return '0' <= ch <= '9'

    def _word(ch):
        return ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or ch == '_'

    def _space(ch):
        return ch == ' ' or ch == '\t' or ch == '\n'

    def _not(f):
        return lambda ch: not f(ch)

    def _lit(c):
        return lambda ch: ch == c

    def _nl(ch):
        return ch != '\n'

    SETS = {'d': _digit, 'D': _not(_digit), 'w': _word, 'W': _not(_word),
            's': _space, 'S': _not(_space)}

    class _Bad(Exception):
        pass

    p = pattern
    n_p = len(p)
    pos = 0
    ngroups = 0
    refs = []

    def bad():
        raise _Bad()

    def read_digits():
        nonlocal pos
        start = pos
        while pos < n_p and '0' <= p[pos] <= '9':
            pos += 1
        if pos == start:
            return None
        return int(p[start:pos])

    def parse_quant():
        nonlocal pos
        c = p[pos]
        if c == '*':
            pos += 1
            return 0, None
        if c == '+':
            pos += 1
            return 1, None
        if c == '?':
            pos += 1
            return 0, 1
        pos += 1  # '{'
        m = read_digits()
        if m is None:
            bad()
        if pos < n_p and p[pos] == '}':
            pos += 1
            mn, mx = m, m
        elif pos < n_p and p[pos] == ',':
            pos += 1
            if pos < n_p and p[pos] == '}':
                pos += 1
                mn, mx = m, None
            else:
                nn = read_digits()
                if nn is None or pos >= n_p or p[pos] != '}':
                    bad()
                pos += 1
                mn, mx = m, nn
        else:
            bad()
        if mn > 1000 or (mx is not None and (mx > 1000 or mn > mx)):
            bad()
        return mn, mx

    def class_atom():
        nonlocal pos
        if p[pos] != '\\':
            pos += 1
            return 'c', p[pos - 1]
        pos += 1
        if pos >= n_p:
            bad()
        e = p[pos]
        pos += 1
        if e in ESC_LIT:
            return 'c', e
        if e == 'n':
            return 'c', '\n'
        if e == 't':
            return 'c', '\t'
        if e in SETS:
            return 'p', SETS[e]
        bad()

    def parse_class():
        nonlocal pos
        pos += 1  # '['
        neg = False
        if pos < n_p and p[pos] == '^':
            neg = True
            pos += 1
        ranges = []
        preds = []
        while True:
            if pos >= n_p:
                bad()
            if p[pos] == ']':
                pos += 1
                break
            kind, val = class_atom()
            if pos + 1 < n_p and p[pos] == '-' and p[pos + 1] != ']':
                if kind == 'p':
                    bad()
                pos += 1
                kind2, val2 = class_atom()
                if kind2 == 'p' or val > val2:
                    bad()
                ranges.append((val, val2))
            elif kind == 'p':
                preds.append(val)
            else:
                ranges.append((val, val))
        rs = tuple(ranges)
        ps = tuple(preds)

        def member(ch):
            for lo, hi in rs:
                if lo <= ch <= hi:
                    return True
            for f in ps:
                if f(ch):
                    return True
            return False

        if neg:
            return ('char', _not(member))
        return ('char', member)

    def parse_escape_atom():
        nonlocal pos
        pos += 1  # backslash
        if pos >= n_p:
            bad()
        e = p[pos]
        pos += 1
        if e in ESC_LIT:
            return ('char', _lit(e))
        if e == 'n':
            return ('char', _lit('\n'))
        if e == 't':
            return ('char', _lit('\t'))
        if e in SETS:
            return ('char', SETS[e])
        if '1' <= e <= '9':
            k = int(e)
            refs.append(k)
            return ('backref', k)
        bad()

    def expect_close():
        nonlocal pos
        if pos < n_p and p[pos] == ')':
            pos += 1
        else:
            bad()

    def parse_atom():
        nonlocal pos, ngroups
        c = p[pos]
        if c == '.':
            pos += 1
            return ('char', _nl)
        if c == '[':
            return parse_class()
        if c == '\\':
            return parse_escape_atom()
        if c == '(':
            if p.startswith('(?:', pos):
                pos += 3
                inner = parse_alt()
                expect_close()
                return inner
            pos += 1
            ngroups += 1
            k = ngroups
            inner = parse_alt()
            expect_close()
            return ('group', k, inner)
        if c in SPECIAL:
            bad()
        pos += 1
        return ('char', _lit(c))

    def parse_item():
        nonlocal pos
        c = p[pos]
        if c == '^':
            pos += 1
            return ('bol',)
        if c == '$':
            pos += 1
            return ('eol',)
        g0 = ngroups
        atom = parse_atom()
        g1 = ngroups
        if pos < n_p and p[pos] in '*+?{':
            mn, mx = parse_quant()
            lazy = False
            if pos < n_p and p[pos] == '?':
                lazy = True
                pos += 1
            return ('rep', atom, mn, mx, lazy, list(range(g0 + 1, g1 + 1)))
        return atom

    def parse_seq():
        items = []
        while pos < n_p and p[pos] not in '|)':
            items.append(parse_item())
        return ('cat', items)

    def parse_alt():
        nonlocal pos
        branches = [parse_seq()]
        while pos < n_p and p[pos] == '|':
            pos += 1
            branches.append(parse_seq())
        if len(branches) == 1:
            return branches[0]
        return ('alt', branches)

    try:
        tree = parse_alt()
        if pos != n_p:
            bad()
    except _Bad:
        return "ERROR"
    if any(k > ngroups for k in refs):
        return "ERROR"

    # ---------- compile to a backtracking program ----------
    G = ngroups
    prog = []
    qcount = [0]

    def emit(ins):
        prog.append(ins)
        return len(prog) - 1

    def comp(node):
        kind = node[0]
        if kind == 'char':
            emit(('char', node[1]))
        elif kind == 'bol':
            emit(('bol',))
        elif kind == 'eol':
            emit(('eol',))
        elif kind == 'backref':
            emit(('backref', node[1]))
        elif kind == 'group':
            emit(('open', node[1]))
            comp(node[2])
            emit(('close', node[1]))
        elif kind == 'cat':
            for item in node[1]:
                comp(item)
        elif kind == 'alt':
            branches = node[1]
            jumps = []
            for i, br in enumerate(branches):
                if i < len(branches) - 1:
                    sp = emit(None)
                    comp(br)
                    jumps.append(emit(None))
                    prog[sp] = ('split', sp + 1, len(prog))
                else:
                    comp(br)
            for j in jumps:
                prog[j] = ('jmp', len(prog))
        else:  # 'rep'
            _, body, mn, mx, lazy, glist = node
            q = qcount[0]
            qcount[0] += 1
            emit(('repinit', q))
            lt = emit(None)
            emit(('iter', q, tuple(glist)))
            comp(body)
            emit(('repend', q, mn, lt))
            prog[lt] = ('reptest', q, mn, mx, lazy, len(prog))

    comp(tree)
    emit(('match',))

    Q = qcount[0]
    OPEN = 2 * (G + 1)          # open-position slots for groups 0..G
    CNT = OPEN + (G + 1)        # iteration counters
    STB = CNT + Q               # iteration start positions
    REGN = STB + Q
    has_ref = any(ins[0] == 'backref' for ins in prog)
    visited = set()
    N = len(text)

    def setv(r, idx, val):
        lst = list(r)
        lst[idx] = val
        return tuple(lst)

    def run(start):
        r0 = [None] * REGN
        r0[0] = start
        stack = [(0, start, tuple(r0))]
        while stack:
            pc, i, r = stack.pop()
            while True:
                ins = prog[pc]
                op = ins[0]
                if op == 'char':
                    if i < N and ins[1](text[i]):
                        i += 1
                        pc += 1
                        continue
                    break
                if op == 'split' or op == 'reptest':
                    if not has_ref:
                        key = (pc, i, r[CNT:])
                        if key in visited:
                            break
                        visited.add(key)
                    if op == 'split':
                        stack.append((ins[2], i, r))
                        pc = ins[1]
                        continue
                    _, q, mn, mx, lazy, X = ins
                    c = r[CNT + q]
                    if c < mn:
                        pc += 1
                    elif mx is not None and c >= mx:
                        pc = X
                    elif not lazy:
                        stack.append((X, i, r))
                        pc += 1
                    else:
                        stack.append((pc + 1, i, r))
                        pc = X
                    continue
                if op == 'jmp':
                    pc = ins[1]
                elif op == 'open':
                    r = setv(r, OPEN + ins[1], i)
                    pc += 1
                elif op == 'close':
                    k = ins[1]
                    lst = list(r)
                    lst[2 * k] = lst[OPEN + k]
                    lst[2 * k + 1] = i
                    r = tuple(lst)
                    pc += 1
                elif op == 'bol':
                    if i != 0:
                        break
                    pc += 1
                elif op == 'eol':
                    if i != N:
                        break
                    pc += 1
                elif op == 'backref':
                    k = ins[1]
                    s = r[2 * k]
                    e = r[2 * k + 1]
                    sub = '' if (s is None or e is None) else text[s:e]
                    if text.startswith(sub, i):
                        i += len(sub)
                        pc += 1
                    else:
                        break
                elif op == 'repinit':
                    r = setv(r, CNT + ins[1], 0)
                    pc += 1
                elif op == 'iter':
                    q, glist = ins[1], ins[2]
                    lst = list(r)
                    lst[STB + q] = i
                    for g in glist:
                        lst[2 * g] = None
                        lst[2 * g + 1] = None
                    r = tuple(lst)
                    pc += 1
                elif op == 'repend':
                    q, mn, lt = ins[1], ins[2], ins[3]
                    c = r[CNT + q]
                    if i == r[STB + q] and c >= mn:
                        break  # empty iteration after minimum reached
                    r = setv(r, CNT + q, c + 1)
                    pc = lt
                elif op == 'match':
                    r = setv(r, 1, i)
                    result = []
                    for k in range(G + 1):
                        s = r[2 * k]
                        e = r[2 * k + 1]
                        result.append(None if (s is None or e is None) else (s, e))
                    return tuple(result)
        return None

    for start in range(N + 1):
        res = run(start)
        if res is not None:
            return res
    return None
```
