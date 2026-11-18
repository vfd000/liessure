"""Disinterests: topics a reader never wants in their feed (each person keeps their own list).

A list entry is a category below (matched by its word list) or any other word or phrase (matched as a
whole word). A card matches on its title, subtitle, label and the start of its text. Keyword matching:
it will miss some cards and catch the odd innocent one; adding words tunes it.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORIES: dict[str, list[str]] = {
    "sports": [
        "sports?", "sportsperson", "sportsman", "sportswoman", "football", "footballer", "soccer", "basketball",
        "baseball", "softball", "cricket(er)?", "rugby", "hockey", "tennis", "golf(er)?", "boxing", "boxer",
        "wrestl(ing|er)", "athlet(e|es|ics)", "olympi(c|cs|an)", "paralympic", "quarterback", "racing driver",
        "motorsport", "nascar", "formula one", "f\\.c\\.", "fc", "premier league", "world cup", "super bowl",
        "nfl", "nba", "mlb", "nhl", "uefa", "fifa", "stadium", "e-?sports?", "volleyball", "handball",
        "lacrosse", "cyclist", "marathon", "goalkeeper", "midfielder", "defender \\(football\\)", "striker",
    ],
    "alcohol": [
        "alcohol(ic)?", "beers?", "brewer(y|ies|s)?", "brewing", "wines?", "winer(y|ies)", "vineyards?",
        "vintner", "whisk(e)?y", "bourbon", "scotch", "vodka", "gin", "rum", "tequila", "mezcal", "liqueurs?",
        "liquor", "spirits \\(drink\\)", "cocktails?", "brandy", "cognac", "sake", "ciders?", "distiller(y|ies)",
        "pubs?", "bartender", "champagne", "prosecco", "absinthe", "lager", "drunk(enness)?", "hangover",
        "booze", "sommelier", "taproom", "drinking", "drinkers?", "toast (said|before|to|with|for)",
        "toasts?(ing)? (each other|the|your|our|a)\\b", "cheers", "salud", "kanpai", "santé", "prost",
        "bars?( and| or)? (pubs?|clubs?)", "happy hour", "nightclubs?",
    ],
    "animal products": [
        "meats?", "beef", "pork", "bacon", "ham", "steaks?", "sausages?", "veal", "lamb chops?", "poultry",
        "chicken (wings?|nuggets?|breast|curry|soup)", "fried chicken", "butcher(s|y)?", "slaughter(house)?",
        "abattoir", "dairy", "cheeses?", "milk", "butter", "yogh?urt", "cream", "ice cream", "eggs? (dish|recipe)",
        "omelet(te)?s?", "scrambled eggs", "foie gras", "seafood", "shellfish", "caviar", "sushi", "sashimi",
        "burgers?", "hamburgers?", "hot dogs?", "salami", "pepperoni", "jerky", "barbecue", "bbq", "gelatin",
        "lard", "charcuterie", "kebab", "carnivore diet",
    ],
}

# What the page offers as one tap; anything else is typed.
SUGGESTED = ["sports", "alcohol", "animal products"]


def _pattern(entries: list[str]) -> re.Pattern[str] | None:
    parts: list[str] = []
    for e in entries:
        e = e.strip().casefold()
        if not e:
            continue
        parts += CATEGORIES.get(e, [re.escape(e)])
    if not parts:
        return None
    return re.compile(r"(?<![\w-])(" + "|".join(parts) + r")(?![\w-])", re.I)


_cache: dict[tuple[str, ...], re.Pattern[str] | None] = {}


def matches(entries: list[str], card: dict[str, Any]) -> bool:
    if not entries:
        return False
    key = tuple(sorted(e.casefold() for e in entries))
    if key not in _cache:
        _cache[key] = _pattern(list(key))
    pat = _cache[key]
    if pat is None:
        return False
    text = " ".join(str(card.get(k) or "") for k in ("title", "subtitle", "label")) + " " + \
        str(card.get("blurb") or "")[:400]
    return bool(pat.search(text))
