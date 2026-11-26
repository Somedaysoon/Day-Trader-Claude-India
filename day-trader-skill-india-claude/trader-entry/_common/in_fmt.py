"""Rupee and percent formatting with Indian digit grouping. Standard library only."""


def _group(int_part):
    """'1234567' -> '12,34,567' (last three digits, then pairs)."""
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs + [tail])


def inr(x, places=2):
    """1234567.5 -> 'Rs 12,34,567.50'. Negative amounts keep the sign in front."""
    x = float(x)
    sign = "-" if x < 0 else ""
    s = "%.*f" % (places, abs(x))
    whole, _, frac = s.partition(".")
    return "%sRs %s%s" % (sign, _group(whole), ("." + frac) if frac else "")


def crore(x, places=2):
    """Rupees -> crore text: 50000000 -> '5.00 crore' (1 crore = 10,000,000)."""
    return "%.*f crore" % (places, float(x) / 1e7)


def lakh(x, places=2):
    return "%.*f lakh" % (places, float(x) / 1e5)


def pct(x, places=2):
    return "%+.*f%%" % (places, float(x))
