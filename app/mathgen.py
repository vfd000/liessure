"""Math problems and puzzles, generated here so every answer is exact.

Each generator returns a Problem: the question, the answer the page checks
against, a hint and a worked solution. The page checks answers itself
(numbers, fractions like 3/8, decimals), so cards work offline too.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable


@dataclass
class Problem:
    topic: str
    question: str
    answer: str            # what the page compares against: "42", "-3", "5/12", "28.3"
    hint: str
    solution: str
    tolerance: float = 0   # absolute; for answers rounded to a decimal place
    wiki: str = "Mathematics"
    answer_note: str = ""  # e.g. "as a fraction", "to one decimal"


def _frac(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _signed(n: int) -> str:
    return f"+ {n}" if n >= 0 else f"− {-n}"


def _term(k: int, var: str) -> str:
    return var if k == 1 else f"{k}{var}"


# ------------------------------------------------------------- arithmetic

def multiply(rng: random.Random) -> Problem:
    a, b = rng.randint(13, 99), rng.randint(13, 99)
    t, u = divmod(b, 10)
    return Problem("mental arithmetic", f"{a} × {b} = ?", str(a * b),
                   f"Split {b} into {t * 10} + {u}.",
                   f"{a} × {t * 10} = {a * t * 10}, {a} × {u} = {a * u}; together {a * b}.",
                   wiki="Mental calculation")


def square(rng: random.Random) -> Problem:
    n = rng.randint(31, 99)
    r = round(n, -1)
    d = n - r
    return Problem("mental arithmetic", f"{n}² = ?", str(n * n),
                   f"(a + b)² = a² + 2ab + b² with a = {r}.",
                   f"{r}² = {r * r}, 2·{r}·{d} = {2 * r * d}, {d}² = {d * d}; total {n * n}.",
                   wiki="Square (algebra)")


# Percents that are easy to take of anything: when the number is one of these, swap them round.
EASY_PERCENT = {20: "a fifth", 25: "a quarter", 40: None, 50: "half", 80: None}


def percent(rng: random.Random) -> Problem:
    p = rng.choice([5, 12.5, 15, 17.5, 22.5, 35, 36, 44, 45, 48, 65, 85, 88, 120, 135])
    n = rng.choice([20, 25, 40, 50, 64, 80, 120, 160, 240, 360, 480, 640, 840, 1200])
    ans = Fraction(str(p)) * n / 100
    q = f"What is {p:g}% of {n}?"
    if n in EASY_PERCENT and float(p).is_integer() and (p % 10 or p > 100):
        # a% of b = b% of a: 120% of 40 is 40% of 120.
        name = EASY_PERCENT[n]
        how = f"{name} of {p:g}" if name else f"{n // 10} × {p / 10:g} (10% of {p:g} is {p / 10:g})"
        return Problem("percentages", q, _frac(ans),
                       f"Percentages swap: {p:g}% of {n} is the same as {n}% of {p:g}.",
                       f"{p:g}% of {n} = {n}% of {p:g} = {how} = {float(ans):g}.",
                       wiki="Percentage")
    return Problem("percentages", q, _frac(ans),
                   "Build it from 10%, 5% and 1% pieces.",
                   f"{p:g}% of {n} = {p:g}/100 × {n} = {float(ans):g}.",
                   wiki="Percentage")


def fractions_add(rng: random.Random) -> Problem:
    b, d = rng.sample([3, 4, 5, 6, 7, 8, 9, 10, 12], 2)
    a, c = rng.randint(1, b - 1), rng.randint(1, d - 1)
    op = rng.choice(["+", "−"])
    ans = Fraction(a, b) + Fraction(c, d) if op == "+" else Fraction(a, b) - Fraction(c, d)
    lcd = b * d // math.gcd(b, d)
    return Problem("fractions", f"{a}/{b} {op} {c}/{d} = ?", _frac(ans),
                   f"Common denominator: {lcd}.",
                   f"{a}/{b} = {a * lcd // b}/{lcd}, {c}/{d} = {c * lcd // d}/{lcd}; result {_frac(ans)} in lowest terms.",
                   wiki="Fraction", answer_note="as a fraction in lowest terms")


# ---------------------------------------------------------------- algebra

def linear(rng: random.Random) -> Problem:
    x = rng.choice([n for n in range(-12, 13) if n])
    a, b = rng.randint(2, 9), rng.randint(-20, 20)
    c = a * x + b
    return Problem("algebra", f"Solve for x: {a}x {_signed(b)} = {c}", str(x),
                   "Undo the addition first, then the multiplication.",
                   f"{a}x = {c} {_signed(-b)} = {c - b}, so x = {c - b}/{a} = {x}.",
                   wiki="Linear equation")


def system(rng: random.Random) -> Problem:
    x, y = rng.randint(-6, 9), rng.randint(-6, 9)
    a, b, c, d = rng.randint(1, 5), rng.randint(1, 5), rng.randint(1, 5), rng.randint(1, 5)
    while a * d == b * c:
        d = rng.randint(1, 6)
    e, f = a * x + b * y, c * x + d * y
    return Problem("algebra", f"{_term(a, 'x')} + {_term(b, 'y')} = {e} and {_term(c, 'x')} + {_term(d, 'y')} = {f}. "
                   "What is x + y?", str(x + y),
                   "Eliminate one variable: scale the equations so its coefficients match.",
                   f"x = {x}, y = {y}, so x + y = {x + y}.",
                   wiki="System of linear equations")


def quadratic(rng: random.Random) -> Problem:
    r1, r2 = rng.randint(-9, 9), rng.randint(-9, 9)
    s, p = r1 + r2, r1 * r2
    return Problem("algebra", f"x² {_signed(-s)}x {_signed(p)} = 0. What is the larger root?", str(max(r1, r2)),
                   "Find two numbers that multiply to the constant and add to minus the x coefficient.",
                   f"(x {_signed(-r1)})(x {_signed(-r2)}) = 0, so x = {r1} or {r2}; the larger is {max(r1, r2)}.",
                   wiki="Quadratic equation")


# ---------------------------------------------------------- number theory

def last_digit(rng: random.Random) -> Problem:
    a, n = rng.randint(2, 9), rng.randint(21, 99)
    cycle = []
    v = a % 10
    while v not in cycle:
        cycle.append(v)
        v = v * a % 10
    return Problem("number theory", f"What is the last digit of {a}^{n}?", str(pow(a, n, 10)),
                   f"Last digits of powers of {a} repeat. Find the cycle.",
                   f"The last digits cycle {', '.join(map(str, cycle))} (length {len(cycle)}); "
                   f"{n} mod {len(cycle)} picks {pow(a, n, 10)}.",
                   wiki="Modular arithmetic")


def gcd_lcm(rng: random.Random) -> Problem:
    g = rng.choice([4, 6, 8, 9, 12, 14, 15, 18])
    a, b = g * rng.randint(2, 9), g * rng.randint(2, 9)
    while a == b:
        b = g * rng.randint(2, 9)
    if rng.random() < 0.5:
        ans = math.gcd(a, b)
        return Problem("number theory", f"Greatest common divisor of {a} and {b}?", str(ans),
                       "Euclid: gcd(a, b) = gcd(b, a mod b).",
                       f"gcd({a}, {b}) = {ans}.", wiki="Euclidean algorithm")
    ans = a * b // math.gcd(a, b)
    return Problem("number theory", f"Least common multiple of {a} and {b}?", str(ans),
                   "lcm(a, b) = a·b / gcd(a, b).",
                   f"gcd = {math.gcd(a, b)}, so lcm = {a}·{b}/{math.gcd(a, b)} = {ans}.",
                   wiki="Least common multiple")


def divisors(rng: random.Random) -> Problem:
    n = rng.choice([36, 48, 60, 72, 84, 90, 96, 100, 120, 144, 180, 210, 240, 360])
    m, f, k = n, {}, 2
    while m > 1:
        while m % k == 0:
            f[k] = f.get(k, 0) + 1
            m //= k
        k += 1
    count = math.prod(e + 1 for e in f.values())
    fact = " · ".join(f"{p}^{e}" if e > 1 else str(p) for p, e in f.items())
    return Problem("number theory", f"How many positive divisors does {n} have?", str(count),
                   "Factor it into primes, then multiply (each exponent + 1).",
                   f"{n} = {fact}, so {' × '.join(str(e + 1) for e in f.values())} = {count}.",
                   wiki="Divisor function")


# ----------------------------------------------- counting and probability

def choose(rng: random.Random) -> Problem:
    n, k = rng.randint(6, 12), rng.randint(2, 4)
    q = rng.choice([f"How many ways to choose a {k}-person committee from {n} people?",
                    f"How many ways to pick {k} of {n} pizza toppings?",
                    f"You can take {k} of your {n} unread books on a trip. How many different sets?"])
    return Problem("combinatorics", q, str(math.comb(n, k)),
                   "Order doesn't matter: n! / (k!(n−k)!).",
                   f"C({n}, {k}) = {math.comb(n, k)}.", wiki="Combination")


def dice(rng: random.Random) -> Problem:
    s = rng.randint(3, 11)
    ways = sum(1 for a in range(1, 7) for b in range(1, 7) if a + b == s)
    ans = Fraction(ways, 36)
    return Problem("probability", f"Roll two dice. Probability the total is {s}?", _frac(ans),
                   "Count the pairs out of 36.",
                   f"{ways} of the 36 pairs total {s}: {ways}/36" + (f" = {_frac(ans)}." if ways % 36 and _frac(ans) != f"{ways}/36" else "."),
                   wiki="Dice", answer_note="as a fraction")


def at_least_one(rng: random.Random) -> Problem:
    n = rng.randint(2, 4)
    ans = 1 - Fraction(5, 6) ** n
    return Problem("probability", f"Roll {n} dice. Probability of at least one six?", _frac(ans),
                   "Easier through the complement: no sixes at all.",
                   f"1 − (5/6)^{n} = {_frac(ans)}.", wiki="Complementary event", answer_note="as a fraction")


# -------------------------------------------------------------- sequences

def sequence(rng: random.Random) -> Problem:
    kind = rng.choice(["arith", "geo", "quad"])
    if kind == "arith":
        a, d = rng.randint(-20, 30), rng.choice([n for n in range(-9, 13) if n])
        seq = [a + i * d for i in range(6)]
        how = f"add {d} each time"
    elif kind == "geo":
        a, r = rng.randint(1, 5), rng.choice([2, 3, -2])
        seq = [a * r ** i for i in range(6)]
        how = f"multiply by {r} each time"
    else:
        a, b, c = rng.randint(1, 3), rng.randint(-4, 4), rng.randint(-5, 5)
        seq = [a * i * i + b * i + c for i in range(1, 7)]
        how = f"the differences grow by {2 * a} each time"
    return Problem("sequences", f"Next term: {', '.join(map(str, seq[:5]))}, …?", str(seq[5]),
                   "Look at the differences between terms (and their differences).",
                   f"The rule: {how}. Next is {seq[5]}.", wiki="Sequence")


# --------------------------------------------------------------- geometry

def pythagoras(rng: random.Random) -> Problem:
    a, b, c = rng.choice([(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25), (20, 21, 29)])
    k = rng.randint(1, 4)
    a, b, c = a * k, b * k, c * k
    if rng.random() < 0.5:
        return Problem("geometry", f"A right triangle has legs {a} and {b}. Hypotenuse?", str(c),
                       "a² + b² = c².", f"√({a}² + {b}²) = √{a * a + b * b} = {c}.",
                       wiki="Pythagorean theorem")
    return Problem("geometry", f"A ladder {c} m long reaches {b} m up a wall. How far is its foot from the wall?",
                   str(a), "The ladder is the hypotenuse.", f"√({c}² − {b}²) = √{c * c - b * b} = {a} m.",
                   wiki="Pythagorean theorem")


def circle(rng: random.Random) -> Problem:
    r = rng.randint(3, 15)
    area = math.pi * r * r
    return Problem("geometry", f"Area of a circle with radius {r}?", f"{area:.1f}",
                   "πr², and π ≈ 3.1416.", f"π × {r}² = π × {r * r} ≈ {area:.1f}.",
                   tolerance=0.06, wiki="Area of a circle", answer_note="to one decimal place")


def angles(rng: random.Random) -> Problem:
    n = rng.randint(5, 12)
    each = Fraction(180 * (n - 2), n)
    return Problem("geometry", f"Each interior angle of a regular {n}-sided polygon, in degrees?", _frac(each),
                   "The interior angles of an n-gon sum to (n − 2) × 180°.",
                   f"({n} − 2) × 180 / {n} = {float(each):g}°.", wiki="Regular polygon",
                   answer_note="a fraction is fine")


# ---------------------------------------------------------------- puzzles
# Classics, with numbers that change each time so the answer can't be remembered.

def bat_and_ball(rng: random.Random) -> Problem:
    diff = rng.choice([1.00, 2.00, 5.00, 10.00])
    ball = rng.choice([0.05, 0.10, 0.25, 0.40])
    total = 2 * ball + diff
    return Problem("puzzle", f"A bat and a ball cost ${total:.2f} together. The bat costs ${diff:.2f} more than "
                   f"the ball. How many cents is the ball?", str(round(ball * 100)),
                   "Call the ball b: then b + (b + difference) = total.",
                   f"2b + {diff:.2f} = {total:.2f}, so b = ${ball:.2f}: {round(ball * 100)} cents "
                   "(not the tempting first guess).", wiki="Cognitive reflection test")


def handshakes(rng: random.Random) -> Problem:
    n = rng.randint(6, 30)
    return Problem("puzzle", f"{n} people meet and everyone shakes hands with everyone else once. How many handshakes?",
                   str(n * (n - 1) // 2), "Each handshake is a pair of people.",
                   f"C({n}, 2) = {n}·{n - 1}/2 = {n * (n - 1) // 2}.", wiki="Handshaking lemma")


def gauss_sum(rng: random.Random) -> Problem:
    n = rng.choice([50, 80, 99, 100, 150, 200, 250, 1000])
    return Problem("puzzle", f"Add up every whole number from 1 to {n}.", str(n * (n + 1) // 2),
                   "Pair the first with the last, the second with the second-to-last…",
                   f"{n // 2 if n % 2 == 0 else n} pairs… n(n + 1)/2 = {n * (n + 1) // 2}.",
                   wiki="Triangular number")


def lily_pad(rng: random.Random) -> Problem:
    d = rng.randint(20, 60)
    k = rng.choice([2, 4, 8])
    back = int(math.log2(k))
    return Problem("puzzle", f"A patch of lily pads doubles in size every day and covers the whole lake on day {d}. "
                   f"On which day did it cover 1/{k} of the lake?", str(d - back),
                   "Work backwards: one day earlier it was half.",
                   f"Halving {back} time(s) from day {d}: day {d - back}.", wiki="Exponential growth")


def consecutive(rng: random.Random) -> Problem:
    k = rng.choice([3, 5])
    first = rng.randint(10, 90)
    total = sum(range(first, first + k))
    return Problem("puzzle", f"{k} consecutive whole numbers add up to {total}. What is the largest?",
                   str(first + k - 1), f"The middle one is the average: {total} / {k}.",
                   f"Middle = {total // k}, so they run {first}…{first + k - 1}.", wiki="Arithmetic progression")


def work_rate(rng: random.Random) -> Problem:
    a, b = rng.choice([(3, 6), (4, 12), (6, 12), (10, 15), (12, 24), (20, 30)])
    ans = Fraction(a * b, a + b)
    return Problem("puzzle", f"One pipe fills a tank in {a} hours, another in {b}. Together, how many hours?",
                   _frac(ans), "Add the rates: 1/a + 1/b of a tank per hour.",
                   f"1/{a} + 1/{b} = {_frac(Fraction(1, a) + Fraction(1, b))} per hour, so {_frac(ans)} hours.",
                   wiki="Rate (mathematics)")


GENERATORS: list[tuple[Callable[[random.Random], Problem], int]] = [
    (multiply, 3), (square, 2), (percent, 3), (fractions_add, 2),
    (linear, 3), (system, 2), (quadratic, 2),
    (last_digit, 2), (gcd_lcm, 2), (divisors, 2),
    (choose, 2), (dice, 2), (at_least_one, 1),
    (sequence, 3),
    (pythagoras, 2), (circle, 1), (angles, 1),
    (bat_and_ball, 1), (handshakes, 1), (gauss_sum, 1), (lily_pad, 1), (consecutive, 1), (work_rate, 1),
]


def problem(rng: random.Random) -> Problem:
    return rng.choices([g for g, _ in GENERATORS], weights=[w for _, w in GENERATORS])[0](rng)
