```python
import sys

def evaluate(src):
    LIMIT = 10 ** 18

    class Syn(Exception):
        pass

    class RT(Exception):
        pass

    try:
        if sys.getrecursionlimit() < 20000:
            sys.setrecursionlimit(20000)
    except Exception:
        pass

    def lex(s):
        toks = []
        i, n = 0, len(s)
        while i < n:
            c = s[i]
            if c in ' \t\n':
                i += 1
            elif '0' <= c <= '9':
                j = i
                while j < n and '0' <= s[j] <= '9':
                    j += 1
                t = s[i:j]
                if len(t) > 1 and t[0] == '0':
                    raise Syn()
                toks.append(('num', t))
                i = j
            elif ('a' <= c <= 'z') or c == '_':
                j = i
                while j < n and (('a' <= s[j] <= 'z') or ('0' <= s[j] <= '9') or s[j] == '_'):
                    j += 1
                toks.append(('name', s[i:j]))
                i = j
            elif c in '+-*/%^()=;':
                toks.append(('sym', c))
                i += 1
            else:
                raise Syn()
        toks.append(('eof', ''))
        return toks

    pos = [0]
    toks = []

    def peek():
        return toks[pos[0]]

    def is_sym(c):
        t = toks[pos[0]]
        return t[0] == 'sym' and t[1] == c

    def advance():
        pos[0] += 1

    def parse_program():
        stmts = [parse_stmt()]
        while is_sym(';'):
            advance()
            if peek()[0] == 'eof':
                break
            stmts.append(parse_stmt())
        if peek()[0] != 'eof':
            raise Syn()
        return stmts

    def parse_stmt():
        t = peek()
        if t[0] == 'name' and toks[pos[0] + 1] == ('sym', '='):
            advance()
            advance()
            return ('assign', t[1], parse_expr())
        return parse_expr()

    def parse_expr():
        node = parse_term()
        while is_sym('+') or is_sym('-'):
            op = peek()[1]
            advance()
            node = ('bin', op, node, parse_term())
        return node

    def parse_term():
        node = parse_unary()
        while is_sym('*') or is_sym('/') or is_sym('%'):
            op = peek()[1]
            advance()
            node = ('bin', op, node, parse_unary())
        return node

    def parse_unary():
        if is_sym('+') or is_sym('-'):
            op = peek()[1]
            advance()
            return ('un', op, parse_unary())
        return parse_power()

    def parse_power():
        node = parse_primary()
        if is_sym('^'):
            advance()
            node = ('pow', node, parse_unary())
        return node

    def parse_primary():
        t = peek()
        if t[0] == 'num':
            advance()
            return ('num', t[1])
        if t[0] == 'name':
            advance()
            return ('var', t[1])
        if is_sym('('):
            advance()
            e = parse_expr()
            if not is_sym(')'):
                raise Syn()
            advance()
            return e
        raise Syn()

    env = {}

    def chk(v):
        if v > LIMIT or v < -LIMIT:
            raise RT('OVERFLOW')
        return v

    def ev(node):
        k = node[0]
        if k == 'num':
            t = node[1]
            if len(t) > 19:
                raise RT('OVERFLOW')
            return chk(int(t))
        if k == 'var':
            if node[1] not in env:
                raise RT('UNDEFINED')
            return env[node[1]]
        if k == 'assign':
            v = ev(node[2])
            env[node[1]] = v
            return v
        if k == 'un':
            v = ev(node[2])
            return chk(-v if node[1] == '-' else v)
        if k == 'bin':
            a = ev(node[2])
            b = ev(node[3])
            op = node[1]
            if op == '+':
                return chk(a + b)
            if op == '-':
                return chk(a - b)
            if op == '*':
                return chk(a * b)
            if b == 0:
                raise RT('DIV_ZERO')
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            if op == '/':
                return chk(q)
            return chk(a - b * q)
        # pow
        a = ev(node[1])
        b = ev(node[2])
        if b < 0:
            raise RT('NEG_EXP')
        if a == 0:
            return 1 if b == 0 else 0
        if a == 1:
            return 1
        if a == -1:
            return -1 if b % 2 else 1
        if b > 62:
            raise RT('OVERFLOW')
        return chk(a ** b)

    try:
        try:
            toks = lex(src)
            stmts = parse_program()
        except Syn:
            return "SYNTAX"
        try:
            result = 0
            for s in stmts:
                result = ev(s)
            return result
        except RT as e:
            return e.args[0]
    except Exception:
        return "SYNTAX"
```
