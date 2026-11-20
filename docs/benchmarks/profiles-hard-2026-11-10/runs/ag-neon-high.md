```python
def evaluate(src: str):
    if not isinstance(src, str):
        return "SYNTAX"

    LIMIT = 1_000_000_000_000_000_000

    T_NUM = 0
    T_NAME = 1
    T_EOF = 2

    tokens = []
    n = len(src)
    i = 0
    while i < n:
        c = src[i]
        if c == " " or c == "\t" or c == "\n":
            i += 1
        elif "0" <= c <= "9":
            j = i + 1
            while j < n and "0" <= src[j] <= "9":
                j += 1
            length = j - i
            if length >= 2 and c == "0":
                return "SYNTAX"
            if length > 19:
                val = None
            else:
                val = int(src[i:j])
                if val > LIMIT:
                    val = None
            tokens.append((T_NUM, val))
            i = j
        elif ("a" <= c <= "z") or c == "_":
            j = i + 1
            while j < n:
                ch = src[j]
                if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
                    j += 1
                else:
                    break
            tokens.append((T_NAME, src[i:j]))
            i = j
        elif c in "+-*/%^()=;":
            tokens.append((c, None))
            i += 1
        else:
            return "SYNTAX"

    if not tokens:
        return "SYNTAX"
    tokens.append((T_EOF, None))

    OP_PUSH = 0
    OP_OVF = 1
    OP_LOAD = 2
    OP_STORE = 3
    OP_END_STMT = 4
    OP_NEG = 5
    OP_ADD = 6
    OP_SUB = 7
    OP_MUL = 8
    OP_DIV = 9
    OP_MOD = 10
    OP_POW = 11

    BIN_PREC = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 4}
    STACK_PREC = {"(": 0, "+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "u-": 3, "^": 4}
    OP_MAP = {
        "+": OP_ADD,
        "-": OP_SUB,
        "*": OP_MUL,
        "/": OP_DIV,
        "%": OP_MOD,
        "^": OP_POW,
        "u-": OP_NEG,
    }

    code = []
    pos = 0
    while True:
        target_name = None
        if tokens[pos][0] == T_NAME and tokens[pos + 1][0] == "=":
            target_name = tokens[pos][1]
            pos += 2

        op_stack = []
        paren_depth = 0
        expect_operand = True

        while True:
            tk, tv = tokens[pos]
            if expect_operand:
                if tk == T_NUM:
                    if tv is None:
                        code.append((OP_OVF, None))
                    else:
                        code.append((OP_PUSH, tv))
                    expect_operand = False
                    pos += 1
                elif tk == T_NAME:
                    code.append((OP_LOAD, tv))
                    expect_operand = False
                    pos += 1
                elif tk == "(":
                    op_stack.append("(")
                    paren_depth += 1
                    pos += 1
                elif tk == "-":
                    op_stack.append("u-")
                    pos += 1
                elif tk == "+":
                    pos += 1
                else:
                    return "SYNTAX"
            else:
                if tk == ")":
                    if paren_depth == 0:
                        return "SYNTAX"
                    while True:
                        top = op_stack.pop()
                        if top == "(":
                            break
                        code.append((OP_MAP[top], None))
                    paren_depth -= 1
                    pos += 1
                elif tk in BIN_PREC:
                    prec = BIN_PREC[tk]
                    if tk != "^":
                        while op_stack and STACK_PREC[op_stack[-1]] >= prec:
                            code.append((OP_MAP[op_stack.pop()], None))
                    op_stack.append(tk)
                    expect_operand = True
                    pos += 1
                elif tk == ";" or tk == T_EOF:
                    if paren_depth != 0:
                        return "SYNTAX"
                    break
                else:
                    return "SYNTAX"

        while op_stack:
            code.append((OP_MAP[op_stack.pop()], None))

        if target_name is not None:
            code.append((OP_STORE, target_name))
        else:
            code.append((OP_END_STMT, None))

        if tokens[pos][0] == ";":
            pos += 1
            if tokens[pos][0] == T_EOF:
                break
        else:
            break

    env = {}
    stack = []
    last = 0
    for op, arg in code:
        if op == OP_PUSH:
            stack.append(arg)
        elif op == OP_OVF:
            return "OVERFLOW"
        elif op == OP_LOAD:
            val = env.get(arg)
            if val is None:
                return "UNDEFINED"
            stack.append(val)
        elif op == OP_STORE:
            last = stack.pop()
            env[arg] = last
        elif op == OP_END_STMT:
            last = stack.pop()
        elif op == OP_NEG:
            stack[-1] = -stack[-1]
        elif op == OP_ADD:
            b = stack.pop()
            res = stack[-1] + b
            if res > LIMIT or res < -LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == OP_SUB:
            b = stack.pop()
            res = stack[-1] - b
            if res > LIMIT or res < -LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == OP_MUL:
            b = stack.pop()
            res = stack[-1] * b
            if res > LIMIT or res < -LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == OP_DIV:
            b = stack.pop()
            if b == 0:
                return "DIV_ZERO"
            a = stack[-1]
            q = abs(a) // abs(b)
            if (a < 0) ^ (b < 0):
                q = -q
            stack[-1] = q
        elif op == OP_MOD:
            b = stack.pop()
            if b == 0:
                return "DIV_ZERO"
            a = stack[-1]
            r = abs(a) % abs(b)
            if a < 0:
                r = -r
            stack[-1] = r
        elif op == OP_POW:
            b = stack.pop()
            if b < 0:
                return "NEG_EXP"
            a = stack[-1]
            if b == 0:
                res = 1
            elif b == 1:
                res = a
            elif a == 0:
                res = 0
            elif a == 1:
                res = 1
            elif a == -1:
                res = -1 if (b & 1) else 1
            elif b >= 60:
                return "OVERFLOW"
            else:
                res = a ** b
                if res > LIMIT or res < -LIMIT:
                    return "OVERFLOW"
            stack[-1] = res

    return last
```
session: sess_2b480cb34d5bd657 fresh gen1 reason=explicit_fresh
