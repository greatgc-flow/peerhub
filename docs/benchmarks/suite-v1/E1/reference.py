def roman_to_int(s: str) -> int:
    if not s or len(s) > 15:
        return -1
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(s):
        v = vals.get(ch)
        if v is None:
            return -1
        nxt = vals.get(s[i + 1], 0) if i + 1 < len(s) else 0
        total += -v if v < nxt else v
    if not (1 <= total <= 3999):
        return -1
    symbols = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    out: list[str] = []
    rem = total
    for num, sym in symbols:
        q, rem = divmod(rem, num)
        out.append(sym * q)
    return total if "".join(out) == s else -1
