```python
import sys

try:
    sys.setrecursionlimit(20000)
except Exception:
    pass


class LangError(Exception):
    def __init__(self, msg: str):
        self.msg = msg


def int_add(a: int, b: int) -> int:
    res = a + b
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_sub(a: int, b: int) -> int:
    res = a - b
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_mul(a: int, b: int) -> int:
    res = a * b
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_div(a: int, b: int) -> int:
    if b == 0:
        raise LangError("DIV_ZERO")
    sign = -1 if (a < 0) ^ (b < 0) else 1
    res = sign * (abs(a) // abs(b))
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_mod(a: int, b: int) -> int:
    if b == 0:
        raise LangError("DIV_ZERO")
    q = int_div(a, b)
    res = a - b * q
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_pow(a: int, b: int) -> int:
    if b < 0:
        raise LangError("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return -1 if (b % 2 == 1) else 1
    if b > 60:
        raise LangError("OVERFLOW")
    res = a ** b
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_neg(a: int) -> int:
    res = -a
    if abs(res) > 10**18:
        raise LangError("OVERFLOW")
    return res


def int_pos(a: int) -> int:
    if abs(a) > 10**18:
        raise LangError("OVERFLOW")
    return a


def make_num(val_str: str) -> int:
    if len(val_str) > 19:
        raise LangError("OVERFLOW")
    val = int(val_str)
    if val > 10**18:
        raise LangError("OVERFLOW")
    return val


def lex(src: str):
    i = 0
    n = len(src)
    tokens = []
    while i < n:
        c = src[i]
        if c in ' \t\n':
            i += 1
            continue
        if '0' <= c <= '9':
            start = i
            while i < n and '0' <= src[i] <= '9':
                i += 1
            num_str = src[start:i]
            if len(num_str) > 1 and num_str[0] == '0':
                raise LangError("SYNTAX")
            tokens.append(('NUM', num_str))
            continue
        if ('a' <= c <= 'z') or c == '_':
            start = i
            while i < n and (('a' <= src[i] <= 'z') or ('0' <= src[i] <= '9') or src[i] == '_'):
                i += 1
            tokens.append(('NAME', src[start:i]))
            continue
        if c in '+-*/%^()=;':
            tokens.append((c, c))
            i += 1
            continue
        raise LangError("SYNTAX")
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def peek_type(self):
        tok = self.peek()
        return tok[0] if tok else None

    def peek_next_type(self):
        if self.pos + 1 < len(self.tokens):
            return self.tokens[self.pos + 1][0]
        return None

    def match(self, tok_type: str) -> bool:
        return self.peek_type() == tok_type

    def advance(self):
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        return tok

    def is_at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def parse_program(self):
        if self.is_at_end():
            raise LangError("SYNTAX")
        stmts = [self.parse_stmt()]
        while self.match(';'):
            self.advance()
            if self.is_at_end():
                break
            stmts.append(self.parse_stmt())
        if not self.is_at_end():
            raise LangError("SYNTAX")
        return stmts

    def parse_stmt(self):
        if self.match('NAME') and self.peek_next_type() == '=':
            name = self.advance()[1]
            self.advance()
            expr = self.parse_expr()
            return ('assign', name, expr)
        else:
            expr = self.parse_expr()
            return ('expr_stmt', expr)

    def parse_expr(self):
        first = self.parse_term()
        if not (self.match('+') or self.match('-')):
            return first
        items = [first]
        while self.match('+') or self.match('-'):
            op = self.advance()[0]
            term = self.parse_term()
            items.append((op, term))
        return ('expr_chain', items)

    def parse_term(self):
        first = self.parse_unary()
        if not (self.match('*') or self.match('/') or self.match('%')):
            return first
        items = [first]
        while self.match('*') or self.match('/') or self.match('%'):
            op = self.advance()[0]
            unary = self.parse_unary()
            items.append((op, unary))
        return ('term_chain', items)

    def parse_unary(self):
        ops = []
        while self.match('+') or self.match('-'):
            ops.append(self.advance()[0])
        node = self.parse_power()
        for op in reversed(ops):
            node = ('unary', op, node)
        return node

    def parse_power(self):
        node = self.parse_primary()
        if self.match('^'):
            self.advance()
            right = self.parse_unary()
            return ('power', node, right)
        return node

    def parse_primary(self):
        if self.match('NUM'):
            return ('num', self.advance()[1])
        if self.match('NAME'):
            return ('var', self.advance()[1])
        if self.match('('):
            self.advance()
            expr = self.parse_expr()
            if not self.match(')'):
                raise LangError("SYNTAX")
            self.advance()
            return expr
        raise LangError("SYNTAX")


def eval_node(node, env):
    tag = node[0]
    if tag == 'num':
        return make_num(node[1])
    if tag == 'var':
        name = node[1]
        if name not in env:
            raise LangError("UNDEFINED")
        return env[name]
    if tag == 'unary':
        op = node[1]
        val = eval_node(node[2], env)
        if op == '-':
            return int_neg(val)
        elif op == '+':
            return int_pos(val)
        raise LangError("SYNTAX")
    if tag == 'power':
        lval = eval_node(node[1], env)
        rval = eval_node(node[2], env)
        return int_pow(lval, rval)
    if tag == 'expr_chain':
        val = eval_node(node[1][0], env)
        for op, term in node[1][1:]:
            rval = eval_node(term, env)
            if op == '+':
                val = int_add(val, rval)
            elif op == '-':
                val = int_sub(val, rval)
        return val
    if tag == 'term_chain':
        val = eval_node(node[1][0], env)
        for op, unary in node[1][1:]:
            rval = eval_node(unary, env)
            if op == '*':
                val = int_mul(val, rval)
            elif op == '/':
                val = int_div(val, rval)
            elif op == '%':
                val = int_mod(val, rval)
        return val
    raise LangError("SYNTAX")


def evaluate(src: str):
    if not isinstance(src, str):
        return "SYNTAX"
    try:
        tokens = lex(src)
        parser = Parser(tokens)
        stmts = parser.parse_program()
        env = {}
        last_val = 0
        for stmt in stmts:
            if stmt[0] == 'assign':
                val = eval_node(stmt[2], env)
                env[stmt[1]] = val
                last_val = val
            elif stmt[0] == 'expr_stmt':
                val = eval_node(stmt[1], env)
                last_val = val
        return last_val
    except LangError as e:
        return e.msg
    except Exception:
        return "SYNTAX"
```
