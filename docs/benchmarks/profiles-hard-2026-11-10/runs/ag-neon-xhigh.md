```python
_LIMIT = 10**18
_LIMIT_STR = "1" + "0" * 18

_T_NUM = 0
_T_NAME = 1
_T_PLUS = 2
_T_MINUS = 3
_T_MUL = 4
_T_DIV = 5
_T_MOD = 6
_T_POW = 7
_T_LP = 8
_T_RP = 9
_T_EQ = 10
_T_SEMI = 11

_SYMBOLS = {
    "+": _T_PLUS,
    "-": _T_MINUS,
    "*": _T_MUL,
    "/": _T_DIV,
    "%": _T_MOD,
    "^": _T_POW,
    "(": _T_LP,
    ")": _T_RP,
    "=": _T_EQ,
    ";": _T_SEMI,
}

_OP_PUSH = 0
_OP_OVF = 1
_OP_LOAD = 2
_OP_NEG = 3
_OP_ADD = 4
_OP_SUB = 5
_OP_MUL = 6
_OP_DIV = 7
_OP_MOD = 8
_OP_POW = 9
_OP_STORE_RES = 10
_OP_POP_RES = 11

_O_LP = 0
_O_ADD = 1
_O_SUB = 2
_O_MUL = 3
_O_DIV = 4
_O_MOD = 5
_O_UPOS = 6
_O_UNEG = 7
_O_POW = 8

_PREC = (0, 1, 1, 2, 2, 2, 3, 3, 4)

_OP_TO_VM = (
    None,
    _OP_ADD,
    _OP_SUB,
    _OP_MUL,
    _OP_DIV,
    _OP_MOD,
    None,
    _OP_NEG,
    _OP_POW,
)

_BIN_TOK_TO_OP = {
    _T_PLUS: _O_ADD,
    _T_MINUS: _O_SUB,
    _T_MUL: _O_MUL,
    _T_DIV: _O_DIV,
    _T_MOD: _O_MOD,
    _T_POW: _O_POW,
}


def evaluate(src: str):
    if not isinstance(src, str):
        return "SYNTAX"

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
            if length > 1 and ch == "0":
                return "SYNTAX"
            if length < 19:
                tokens.append((_T_NUM, int(src[start:i])))
            elif length == 19 and src[start:i] == _LIMIT_STR:
                tokens.append((_T_NUM, _LIMIT))
            else:
                tokens.append((_T_NUM, None))
        elif ("a" <= ch <= "z") or ch == "_":
            start = i
            i += 1
            while i < n:
                c = src[i]
                if ("a" <= c <= "z") or ("0" <= c <= "9") or c == "_":
                    i += 1
                else:
                    break
            tokens.append((_T_NAME, src[start:i]))
        else:
            sym = _SYMBOLS.get(ch)
            if sym is None:
                return "SYNTAX"
            tokens.append((sym, None))
            i += 1

    num_toks = len(tokens)
    if num_toks == 0:
        return "SYNTAX"

    code = []
    pos = 0
    while True:
        if pos >= num_toks:
            return "SYNTAX"

        assign_name = None
        if (
            tokens[pos][0] == _T_NAME
            and pos + 1 < num_toks
            and tokens[pos + 1][0] == _T_EQ
        ):
            assign_name = tokens[pos][1]
            pos += 2

        ops = []
        expect_operand = True

        while pos < num_toks:
            ttype, tval = tokens[pos]
            if ttype == _T_SEMI:
                break
            if expect_operand:
                if ttype == _T_NUM:
                    if tval is None:
                        code.append((_OP_OVF, None))
                    else:
                        code.append((_OP_PUSH, tval))
                    expect_operand = False
                elif ttype == _T_NAME:
                    code.append((_OP_LOAD, tval))
                    expect_operand = False
                elif ttype == _T_LP:
                    ops.append(_O_LP)
                elif ttype == _T_PLUS:
                    ops.append(_O_UPOS)
                elif ttype == _T_MINUS:
                    ops.append(_O_UNEG)
                else:
                    return "SYNTAX"
            else:
                if ttype == _T_RP:
                    while ops and ops[-1] != _O_LP:
                        vm_op = _OP_TO_VM[ops.pop()]
                        if vm_op is not None:
                            code.append((vm_op, None))
                    if not ops:
                        return "SYNTAX"
                    ops.pop()
                else:
                    op = _BIN_TOK_TO_OP.get(ttype)
                    if op is None:
                        return "SYNTAX"
                    if op != _O_POW:
                        prec = _PREC[op]
                        while ops and _PREC[ops[-1]] >= prec:
                            vm_op = _OP_TO_VM[ops.pop()]
                            if vm_op is not None:
                                code.append((vm_op, None))
                    ops.append(op)
                    expect_operand = True
            pos += 1

        if expect_operand:
            return "SYNTAX"

        while ops:
            top = ops.pop()
            if top == _O_LP:
                return "SYNTAX"
            vm_op = _OP_TO_VM[top]
            if vm_op is not None:
                code.append((vm_op, None))

        if assign_name is not None:
            code.append((_OP_STORE_RES, assign_name))
        else:
            code.append((_OP_POP_RES, None))

        if pos >= num_toks:
            break
        pos += 1
        if pos >= num_toks:
            break

    env = {}
    stack = []
    last_val = 0

    for op, arg in code:
        if op == _OP_PUSH:
            stack.append(arg)
        elif op == _OP_OVF:
            return "OVERFLOW"
        elif op == _OP_LOAD:
            val = env.get(arg)
            if val is None:
                return "UNDEFINED"
            stack.append(val)
        elif op == _OP_NEG:
            stack[-1] = -stack[-1]
        elif op == _OP_ADD:
            b = stack.pop()
            res = stack[-1] + b
            if res > _LIMIT or res < -_LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == _OP_SUB:
            b = stack.pop()
            res = stack[-1] - b
            if res > _LIMIT or res < -_LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == _OP_MUL:
            b = stack.pop()
            res = stack[-1] * b
            if res > _LIMIT or res < -_LIMIT:
                return "OVERFLOW"
            stack[-1] = res
        elif op == _OP_DIV:
            b = stack.pop()
            if b == 0:
                return "DIV_ZERO"
            a = stack[-1]
            q = abs(a) // abs(b)
            if (a < 0) ^ (b < 0):
                q = -q
            stack[-1] = q
        elif op == _OP_MOD:
            b = stack.pop()
            if b == 0:
                return "DIV_ZERO"
            a = stack[-1]
            r = abs(a) % abs(b)
            if a < 0:
                r = -r
            stack[-1] = r
        elif op == _OP_POW:
            b = stack.pop()
            a = stack[-1]
            if b < 0:
                return "NEG_EXP"
            if b == 0 or a == 1:
                res = 1
            elif a == 0:
                res = 0
            elif a == -1:
                res = -1 if (b & 1) else 1
            elif b == 1:
                res = a
            elif b >= 60:
                return "OVERFLOW"
            else:
                neg = a < 0 and (b & 1)
                base = -a if a < 0 else a
                res = 1
                e = b
                while e:
                    if e & 1:
                        res *= base
                        if res > _LIMIT:
                            return "OVERFLOW"
                    e >>= 1
                    if e:
                        base *= base
                        if base > _LIMIT:
                            return "OVERFLOW"
                if neg:
                    res = -res
            stack[-1] = res
        elif op == _OP_STORE_RES:
            last_val = stack.pop()
            env[arg] = last_val
        elif op == _OP_POP_RES:
            last_val = stack.pop()

    return last_val
```
session: sess_c5e864692fa14f63 fresh gen1 reason=explicit_fresh
