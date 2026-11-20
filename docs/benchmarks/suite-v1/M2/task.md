# M2

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `price_order(items: list[tuple[str, int, int]], coupons: list[str], member: bool) -> int`.

`items` holds `(category, qty, unit_cents)` (`qty >= 0`, `unit_cents >= 0`).
Treat `coupons` as a set. Ignore duplicates and unknown codes. Category and
coupon matches are case-sensitive. Use exact integer arithmetic (`//`).

R0 is the precedence rule. For conflicting clauses in R1–R7, the
higher-numbered rule wins, with one exception: R6 overrides R7.
Rules apply in their stated stages. An override changes only the conflicting
clause. Apply R6 to the final total, including any total set by R7.

- R1 (Subtotal & BOGO): Each item tuple contributes `qty * unit_cents`. If `"BOGO"` is in `coupons`, each tuple with `category == "book"` contributes `((qty + 1) // 2) * unit_cents` instead. Sum the contributions to get `sub1`.
- R2 (Base Discounts): Starting from `cur = sub1`: first, if `"SAVE10"` in `coupons`, `cur -= cur // 10`; second, if `"FLAT500"` in `coupons`, `cur = max(0, cur - 500)`.
- R3 (Member Discounts): If `member` is True, reverse the order of the two R2 discounts and replace the SAVE10 amount with `cur * 15 // 100`, evaluated at that step.
- R4 (Stacking Limit): If `member` is False and both `"SAVE10"` and `"FLAT500"` are in `coupons`, `"FLAT500"` is ignored.
- R5 (Tax): The taxed total is `cur + cur // 10` if `sub1 > 1000`, otherwise `cur`.
- R6 (Minimum Floor): If `sum(qty) == 0`, return `0`; otherwise final total is at least `100`.
- R7 (VIP Free): If `member` is True and `"VIPFREE"` is in `coupons`, the final total is `0`, waiving discounts (R2–R4), tax (R5), and the minimum floor (R6).
