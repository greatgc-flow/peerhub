```python
def evaluate(src: str):
    LIMIT = 10**18

    # --- Lexer ---
    tokens = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in ' \t\n':
            i += 1
            continue
        if c.isdigit():
            start = i
            while i < n and src[i].isdigit():
                i += 1
            num_str = src[start:i]
            if len(num_str) > 1 and num_str[0] == '0':
                return "SYNTAX"
            tokens.append(('NUM', num_str))
        elif c.isalpha() or c == '_':
            if not (('a' <= c <= 'z') or c == '_'):
                return "SYNTAX"
            start = i
            while i < n and (('a' <= src[i] <= 'z') or src[i].isdigit() or src[i] == '_'):
                i += 1
            tokens.append(('NAME', src[start:i]))
        elif c in '+-*/%^()=;':
            tokens.append((c, c))
            i += 1
        else:
            return "SYNTAX"

    tokens.append(('EOF', 'EOF'))

    # --- Parser ---
    pos = 0

    def cur():
        return tokens[pos]

    def eat(expected_type):
        nonlocal pos
        t = tokens[pos]
        if t[0] == expected_type:
            pos += 1
            return t
        return None

    def parse_primary():
        t = cur()
        if t[0] == 'NUM':
            pos_adv()
            return ('num', t[1])
        if t[0] == 'NAME':
            pos_adv()
            return ('var', t[1])
        if eat('('):
            e = parse_expr()
            if e is None or not eat(')'):
                return None
            return e
        return None

    def pos_adv():
        nonlocal pos
        pos += 1

    def parse_unary():
        t = cur()
        if t[0] in ('+', '-'):
            op = t[0]
            pos_adv()
            sub = parse_unary()
            if sub is None:
                return None
            return ('unary', op, sub)
        # power := primary ['^' unary]
        prim = parse_primary()
        if prim is None:
            return None
        if cur()[0] == '^':
            pos_adv()
            rhs = parse_unary()
            if rhs is None:
                return None
            return ('binop', '^', prim, rhs)
        return prim

    def parse_term():
        node = parse_unary()
        if node is None:
            return None
        while cur()[0] in ('*', '/', '%'):
            op = cur()[0]
            pos_adv()
            rhs = parse_unary()
            if rhs is None:
                return None
            node = ('binop', op, node, rhs)
        return node

    def parse_expr():
        node = parse_term()
        if node is None:
            return None
        while cur()[0] in ('+', '-'):
            op = cur()[0]
            pos_adv()
            rhs = parse_term()
            if rhs is None:
                return None
            node = ('binop', op, node, rhs)
        return node

    def parse_stmt():
        if cur()[0] == 'NAME' and tokens[pos + 1][0] == '=':
            var_name = cur()[1]
            pos_adv()
            pos_adv()
            e = parse_expr()
            if e is None:
                return None
            return ('assign', var_name, e)
        return parse_expr()

    # Program parser
    if cur()[0] == 'EOF':
        return "SYNTAX"

    stmts = []
    first_stmt = parse_stmt()
    if first_stmt is None:
        return "SYNTAX"
    stmts.append(first_stmt)

    while cur()[0] == ';':
        pos_adv()
        if cur()[0] == 'EOF':
            break
        s = parse_stmt()
        if s is None:
            return "SYNTAX"
        stmts.append(s)

    if cur()[0] != 'EOF':
        return "SYNTAX"

    # --- Evaluator ---
    env = {}

    class EvaluationError(Exception):
        def __init__(self, code):
            self.code = code

    def check_bound(val):
        if abs(val) > LIMIT:
            raise EvaluationError("OVERFLOW")
        return val

    def eval_node(node):
        kind = node[0]
        if kind == 'num':
            val = int(node[1])
            return check_bound(val)
        if kind == 'var':
            name = node[1]
            if name not in env:
                raise EvaluationError("UNDEFINED")
            return env[name]
        if kind == 'unary':
            op, sub = node[1], node[2]
            v = eval_node(sub)
            res = v if op == '+' else -v
            return check_bound(res)
        if kind == 'binop':
            op, left, right = node[1], node[2], node[3]
            lval = eval_node(left)
            rval = eval_node(right)
            if op == '+':
                return check_bound(lval + rval)
            if op == '-':
                return check_bound(lval - rval)
            if op == '*':
                return check_bound(lval * rval)
            if op == '/':
                if rval == 0:
                    raise EvaluationError("DIV_ZERO")
                sign = -1 if (lval < 0) ^ (rval < 0) else 1
                q = sign * (abs(lval) // abs(rval))
                return check_bound(q)
            if op == '%':
                if rval == 0:
                    raise EvaluationError("DIV_ZERO")
                sign = -1 if (lval < 0) ^ (rval < 0) else 1
                q = sign * (abs(lval) // abs(rval))
                rem = lval - rval * q
                return check_bound(rem)
            if op == '^':
                if rval < 0:
                    raise EvaluationError("NEG_EXP")
                if lval == 0:
                    res = 1 if rval == 0 else 0
                    return check_bound(res)
                if abs(lval) == 1:
                    res = 1 if lval == 1 else (-1 if (rval % 2 == 1) else 1)
                    return check_bound(res)
                if rval > 60:
                    raise EvaluationError("OVERFLOW")
                res = lval ** rval
                return check_bound(res)
        if kind == 'assign':
            val = eval_node(node[2])
            env[node[1]] = val
            return val
        raise EvaluationError("SYNTAX")

    last_val = None
    try:
        for stmt in stmts:
            last_val = eval_node(stmt)
    except EvaluationError as e:
        return e.code
    except Exception:
        return "SYNTAX"

    return last_val
```
