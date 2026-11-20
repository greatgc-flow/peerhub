def price_order(items: list[tuple[str, int, int]], coupons: list[str], member: bool) -> int:
    if sum(q for _, q, _ in items) == 0:
        return 0
    cs = set(coupons)
    if member and "VIPFREE" in cs:
        return 100
    sub1 = sum(((q + 1) // 2 if cat == "book" and "BOGO" in cs else q) * p for cat, q, p in items)
    cur = sub1
    has10, has500 = "SAVE10" in cs, "FLAT500" in cs
    if member:
        if has500:
            cur = max(0, cur - 500)
        if has10:
            cur -= cur * 15 // 100
    else:
        if has10:
            cur -= cur // 10
        elif has500:
            cur = max(0, cur - 500)
    if sub1 > 1000:
        cur += cur // 10
    return max(100, cur)
