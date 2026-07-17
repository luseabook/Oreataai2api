"""Mail domain ranking and registration identity helpers.

Pure-ish helpers with process-local domain outcome stats. No network or DB I/O.
"""

from __future__ import annotations

import re
import secrets
import threading
from typing import Dict, List, Tuple

MAIL_DOMAIN_STATS_LOCK = threading.Lock()
MAIL_DOMAIN_STATS: Dict[str, Dict[str, int]] = {}

def record_mail_domain_outcome(domain: str, status: str) -> None:
    normalized = str(domain or "").strip().lower()
    if not normalized:
        return
    outcome = str(status or "")
    with MAIL_DOMAIN_STATS_LOCK:
        stats = MAIL_DOMAIN_STATS.setdefault(
            normalized,
            {"success": 0, "failure": 0, "verify_timeout": 0},
        )
        if outcome == "verified":
            stats["success"] += 1
            return
        stats["failure"] += 1
        if outcome == "verify_timeout":
            stats["verify_timeout"] += 1


def rank_mail_domains(domains: List[str]) -> List[str]:
    unique: List[str] = []
    seen = set()
    for domain in domains:
        key = str(domain or "").strip()
        if not key:
            continue
        lowered = key.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(key)
    with MAIL_DOMAIN_STATS_LOCK:
        def sort_key(domain: str) -> Tuple[float, int, int, str]:
            stats = MAIL_DOMAIN_STATS.get(domain.lower(), {})
            success = int(stats.get("success") or 0)
            failure = int(stats.get("failure") or 0)
            timeout = int(stats.get("verify_timeout") or 0)
            total = success + failure
            # Laplace smoothing so unused domains stay competitive.
            rate = (success + 1.0) / (total + 2.0)
            return (-rate, timeout, failure, domain.lower())

        return sorted(unique, key=sort_key)


def soft_order_mail_domains(ranked: List[str]) -> List[str]:
    """Keep success ranking, but avoid always locking onto the #1 domain."""
    if len(ranked) <= 1:
        return list(ranked)
    top_k = min(6, len(ranked))
    weights = [max(1, (top_k - index) ** 2) for index in range(top_k)]
    total = sum(weights)
    pick = secrets.randbelow(total)
    running = 0
    chosen_index = 0
    for index, weight in enumerate(weights):
        running += weight
        if pick < running:
            chosen_index = index
            break
    chosen = ranked[chosen_index]
    rest = [domain for index, domain in enumerate(ranked) if index != chosen_index]
    return [chosen] + rest


_MAIL_FIRST_NAMES = (
    "alex", "aria", "blake", "cara", "dean", "ella", "finn", "gina", "hugo", "iris",
    "jade", "kyle", "lena", "mira", "noah", "owen", "paige", "quin", "reed", "skye",
    "theo", "uma", "vera", "wade", "yuki", "zane", "amy", "ben", "chris", "diana",
    "ethan", "faye", "grace", "hank", "ivan", "jess", "kai", "lucy", "mark", "nina",
    "omar", "ruby", "sam", "tara", "vince", "will", "zoe", "alan", "bella", "cole",
)
_MAIL_LAST_NAMES = (
    "baker", "brooks", "chen", "clark", "cross", "davis", "ford", "grant", "hayes", "kim",
    "lane", "lee", "moss", "nash", "park", "reed", "shaw", "stone", "west", "young",
    "allen", "bell", "cole", "dunn", "fox", "gray", "hart", "owen", "page", "ward",
    "casey", "drake", "ellis", "frost", "green", "hill", "james", "knox", "long", "mills",
)
_MAIL_WORDS = (
    "amber", "cedar", "cloud", "coral", "ember", "frost", "grove", "harbor", "ivory", "jazz",
    "lotus", "maple", "north", "orbit", "pearl", "quilt", "river", "sable", "tide", "violet",
    "willow", "zenith", "pixel", "nova", "spark", "leaf", "pine", "dawn", "dusk", "mint",
    "oasis", "ridge", "sonic", "trail", "urban", "vivid", "wave", "yarn", "bloom", "canyon",
)
_MAIL_SYLLABLES = (
    "ba", "be", "bo", "ca", "ce", "co", "da", "de", "di", "fa", "fi", "ga", "go", "ha", "he",
    "ja", "jo", "ka", "ki", "la", "li", "lo", "ma", "me", "mi", "mo", "na", "ne", "ni", "pa",
    "pe", "po", "ra", "re", "ri", "ro", "sa", "se", "si", "so", "ta", "te", "ti", "to", "va",
    "ve", "vi", "wa", "ya", "za",
)


def _mail_digit_tail(min_len: int = 2, max_len: int = 4) -> str:
    length = secrets.choice(list(range(min_len, max_len + 1)))
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def generate_mailbox_local_part() -> str:
    """Human-looking local parts with light structure, without fixed bot prefixes."""
    digits = _mail_digit_tail()
    roll = secrets.randbelow(100)
    if roll < 30:
        first = secrets.choice(_MAIL_FIRST_NAMES)
        last = secrets.choice(_MAIL_LAST_NAMES)
        sep = secrets.choice([".", "_", ""])
        local = f"{first}{sep}{last}"
        if secrets.randbelow(100) < 70:
            local = f"{local}{digits}"
    elif roll < 55:
        word = secrets.choice(_MAIL_WORDS)
        if secrets.randbelow(100) < 45:
            local = f"{secrets.choice(_MAIL_FIRST_NAMES)}{secrets.choice(['.', '_', ''])}{word}"
            if secrets.randbelow(100) < 60:
                local = f"{local}{digits}"
        else:
            local = f"{word}{digits}"
    elif roll < 72:
        first = secrets.choice(_MAIL_FIRST_NAMES)
        year = str(secrets.choice(range(1988, 2006)))
        local = f"{first}{secrets.choice(['', '.', '_'])}{year if secrets.randbelow(100) < 55 else digits}"
    elif roll < 88:
        initials = f"{secrets.choice(_MAIL_FIRST_NAMES)[0]}{secrets.choice(_MAIL_LAST_NAMES)[0]}"
        word = secrets.choice(_MAIL_WORDS)
        sep = secrets.choice([".", "_", ""])
        local = f"{initials}{sep}{word}{digits}"
    else:
        count = secrets.choice([3, 4, 5])
        local = "".join(secrets.choice(_MAIL_SYLLABLES) for _ in range(count))
        if secrets.randbelow(100) < 75:
            local = f"{local}{digits}"

    local = re.sub(r"[^a-z0-9._-]", "", str(local).lower())
    local = re.sub(r"[._-]{2,}", ".", local).strip("._-")
    if len(local) < 5:
        local = f"{secrets.choice(_MAIL_WORDS)}{_mail_digit_tail()}"
    if len(local) > 30:
        local = local[:30].rstrip("._-")
    if re.match(r"^(create|oreate|probe|test|tmp|mail)([._-]|$)", local):
        local = f"{secrets.choice(_MAIL_FIRST_NAMES)}{secrets.choice(['.', '_', ''])}{secrets.choice(_MAIL_WORDS)}{digits}"
        local = re.sub(r"[._-]{2,}", ".", local).strip("._-")
    return local


def generate_registration_password() -> str:
    """Varied passwords that still satisfy common complexity checks."""
    upper = secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    lowers = "abcdefghijkmnopqrstuvwxyz"
    special = secrets.choice("@#!$%")
    lower_body = "".join(secrets.choice(lowers) for _ in range(secrets.choice([4, 5, 6])))
    digits = "".join(str(secrets.randbelow(10)) for _ in range(secrets.choice([2, 3, 4])))
    patterns = (
        f"{upper}{lower_body}{special}{digits}",
        f"{lower_body}{upper}{digits}{special}",
        f"{upper}{special}{lower_body}{digits}",
        f"{lower_body}{digits}{special}{upper}{secrets.choice(lowers)}",
        f"{secrets.choice(lowers)}{upper}{lower_body[:3]}{special}{digits}",
    )
    password = secrets.choice(patterns)
    # Keep a predictable minimum shape if a pattern somehow collapses.
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"\d", password) or not re.search(r"[@#!$%]", password):
        password = f"{upper}{lower_body}{special}{digits}"
    return password[:16]

