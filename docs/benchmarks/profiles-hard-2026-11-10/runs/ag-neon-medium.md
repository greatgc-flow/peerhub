```python
def evaluate(src: str):
    LIMIT = 10**18

    tokens = []
    n = len(src)
    i = 0
    while i < n:
        ch = src[i]
        if ch == " " or ch == "\t" or ch == "\n":
            i += 1
        elif "0" <= ch <= "9":
            start = i
            i += 1
            while i < n and "0" <= src[i] <= "9":
                i += 1
            length = i - start
            if length > 1 and src[start] == "0":
                return "SYNTAX"
            if length > 19:
                tokens.append((0, None))
            else:
                val = int(src[start:i])
                tokens.append((0, val if val <= LIMIT else None))
        elif ("a" <= ch <= "z") or ch == "_":
            start = i
            i += 1
            while i < n and (("a" <= src[i] <= "z") or ("0" <= src[i] <= "9") or src[i] == "_"):
                i += 1
            tokens.append((1, src[start:i]))
        elif ch in "+-*/%^()=;":
            tokens.append((2, ch))
            i += 1
        else:
            return "SYNTAX"
    tokens.append((3, ""))

    class SyntaxErr(Exception):
        pass

    class EvalErr(Exception):
        pass

    pos = 0

    def parse_program():
        nonlocal pos
        if tokens[pos][0] == 3:
            raise SyntaxErr()
        stmts = [parse_stmt()]
        while tokens[pos] == (2, ";"):
            pos += 1
            if tokens[pos][0] == 3:
                break
            stmts.append(parse_stmt())
        if tokens[pos][0] != 3:
            raise SyntaxErr()
        return stmts

    def parse_stmt():
        nonlocal pos
        if tokens[pos][0] == 1 and tokens[pos + 1] == (2, "="):
            name = tokens[pos][1]
            pos += 2
            expr = parse_expr()
            return ("assign", name, expr)
        return parse_expr()

    def parse_expr():
        nonlocal pos
        node = parse_term()
        while tokens[pos][0] == 2 and (tokens[pos][1] == "+" or tokens[pos][1] == "-"):
            op = tokens[pos][1]
            pos += 1
            right = parse_term()
            node = ("bin", op, node, right)
        return node

    def parse_term():
        nonlocal pos
        node = parse_unary()
        while tokens[pos][0] == 2 and (
            tokens[pos][1] == "*" or tokens[pos][1] == "/" or tokens[pos][1] == "%"
        ):
            op = tokens[pos][1]
            pos += 1
            right = parse_unary()
            node = ("bin", op, node, right)
        return node

    def parse_unary():
        nonlocal pos
        neg = False
        while tokens[pos][0] == 2 and (tokens[pos][1] == "+" or tokens[pos][1] == "-"):
            if tokens[pos][1] == "-":
                neg = not neg
            pos += 1
        node = parse_power()
        if neg:
            node = ("neg", node)
        return node

    def parse_power():
        nonlocal pos
        bases = [parse_primary()]
        negs = []
        while tokens[pos] == (2, "^"):
            pos += 1
            neg = False
            while tokens[pos][0] == 2 and (tokens[pos][1] == "+" or tokens[pos][1] == "-"):
                if tokens[pos][1] == "-":
                    neg = not neg
                pos += 1
            negs.append(neg)
            bases.append(parse_primary())
        node = bases[-1]
        for idx in range(len(negs) - 1, -1, -1):
            if negs[idx]:
                node = ("neg", node)
            node = ("bin", "^", bases[idx], node)
        return node

    def parse_primary():
        nonlocal pos
        kind, val = tokens[pos]
        if kind == 0:
            pos += 1
            return ("num", val)
        if kind == 1:
            pos += 1
            return ("var", val)
        if kind == 2 and val == "(":
            pos += 1
            node = parse_expr()
            if tokens[pos] != (2, ")"):
                raise SyntaxErr()
            pos += 1
            return node
        raise SyntaxErr()

    try:
        stmts = parse_program()
    except SyntaxErr:
        return "SYNTAX"

    env = {}

    def eval_node(node):
        tag = node[0]
        if tag == "num":
            val = node[1]
            if val is None:
                raise EvalErr("OVERFLOW")
            return val
        if tag == "var":
            name = node[1]
            if name not in env:
                raise EvalErr("UNDEFINED")
            return env[name]
        if tag == "neg":
            return -eval_node(node[1])
        if tag == "assign":
            val = eval_node(node[2])
            env[node[1]] = val
            return val

        op = node[1]
        a = eval_node(node[2])
        b = eval_node(node[3])
        if op == "+":
            res = a + b
            if res > LIMIT or res < -LIMIT:
                raise EvalErr("OVERFLOW")
            return res
        if op == "-":
            res = a - b
            if res > LIMIT or res < -LIMIT:
                raise EvalErr("OVERFLOW")
            return res
        if op == "*":
            res = a * b
            if res > LIMIT or res < -LIMIT:
                raise EvalErr("OVERFLOW")
            return res
        if op == "/":
            if b == 0:
                raise EvalErr("DIV_ZERO")
            q = abs(a) // abs(b)
            if (a < 0) ^ (b < 0):
                q = -q
            return q
        if op == "%":
            if b == 0:
                raise EvalErr("DIV_ZERO")
            r = abs(a) % abs(b)
            if a < 0:
                r = -r
            return r

        if b < 0:
            raise EvalErr("NEG_EXP")
        if b == 0:
            return 1
        if a == 0:
            return 0
        if a == 1:
            return 1
        if a == -1:
            return 1 if (b & 1) == 0 else -1
        if b >= 60:
            raise EvalErr("OVERFLOW")
        res = 1
        base = a
        exp = b
        while exp > 0:
            if exp & 1:
                res *= base
                if res > LIMIT or res < -LIMIT:
                    raise EvalErr("OVERFLOW")
            exp >>= 1
            if exp > 0:
                base *= base
                if base > LIMIT or base < -LIMIT:
                    raise EvalErr("OVERFLOW")
        return res

    try:
        result = 0
        for stmt in stmts:
            result = eval_node(stmt)
        return result
    except EvalErr as e:
        return e.args[0]
```
session: sess_45de20b8dd1f5263 fresh gen1 reason=explicit_fresh
