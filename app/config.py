"""Settings, all from environment variables.

Every source is optional: one with no base URL (or no credential) is
simply left out of the feed, so the app runs with only Wikipedia on day one
and picks up each household service as its secret is added.

Each service has two URLs:
  *_URL         where the Liessure server reaches the API (can be a LAN address)
  *_PUBLIC_URL  what goes into the links you tap (defaults to *_URL)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    # Compose passes unset variables through as "", so empty means "use the default".
    return os.environ.get(name, "").strip() or default


def _strip(url: str) -> str:
    return url.rstrip("/")


DEFAULT_WEIGHTS = {
    "wikipedia": 3,
    "game": 2,
    "video": 3,
    "movie": 2,
    "show": 2,
    "song": 2,
    "photo": 3,
    "language": 3,
    "news": 2,
    "homelab": 2,
    "job": 1,
    "skill": 1,
}

# Kinds shown at most this many times per feed load. The feed is a break,
# not a work queue.
MAX_PER_LOAD = {"job": 1, "skill": 1}

# Kinds whose titles never go to the LLM as evidence of what you like.
UNCURATED_KINDS = frozenset({"job", "skill", "telemetry", "home"})

KINDS = tuple(DEFAULT_WEIGHTS)

DEFAULT_NEWS_FEEDS = (
    "https://www.seattletimes.com/feed/"
    "|https://feeds.npr.org/1001/rss.xml"
    "|https://feeds.bbci.co.uk/news/world/rss.xml"
    "|https://www.theguardian.com/science/rss"
    "|https://feeds.arstechnica.com/arstechnica/index"
    "|https://hnrss.org/best"
)

DEFAULT_PROFILE = (
    "Curious adult in Seattle who runs a self-hosted home lab, hikes and "
    "backpacks in the Pacific Northwest, travels, plays retro games, speaks "
    "Spanish, is learning Telugu (Mandarin next) and enjoys a taste of any "
    "language. Likes explainers, engineering and how-things-work, science, "
    "history, geography, travel, food and long-form craft. Has little "
    "patience for hype, drama or outrage."
)

DEFAULT_INTERESTS = (
    "self-hosting|computer networking|Pacific Northwest geology|backpacking"
    "|history of video games|linguistics|Telugu language|Mandarin Chinese"
)

# The tech in the home lab (Wikipedia article titles) and where home-lab
# design and builds get written up.
DEFAULT_HOMELAB_STACK = (
    "Proxmox Virtual Environment|ZFS|LXC|Docker (software)|Jellyfin|Immich|Home Assistant|Z-Wave"
    "|Nginx|Let's Encrypt|Mutual authentication|WireGuard|Ansible (software)|Grafana"
    "|Prometheus (software)|Linux Unified Key Setup|Ubiquiti|Pi-hole|1Password|RAID"
    "|Uninterruptible power supply|Power over Ethernet|Network-attached storage|Single sign-on"
)
DEFAULT_HOMELAB_FEEDS = (
    "https://www.servethehome.com/feed/"
    "|https://selfh.st/rss/"
    "|https://www.jeffgeerling.com/blog.xml"
    "|https://noted.lol/rss/"
    "|https://www.lawrencesystems.com/feed/"
    "|https://www.home-assistant.io/atom.xml"
)
DEFAULT_HOMELAB_TOPICS = (
    "homelab tour|home server rack build|homelab network design|proxmox homelab setup"
    "|self-hosted apps|home lab power efficiency|10 gigabit home network"
)

# "Name:wikipedia-code" pairs.
DEFAULT_LEARNING = "Telugu:te|Mandarin:zh"
DEFAULT_CURIOUS = (
    "French:fr|Korean:ko|Cantonese:zh-yue|Japanese:ja|Russian:ru|Ukrainian:uk"
    "|Hindi:hi|Indonesian:id|Tagalog:tl"
)
DEFAULT_FLUENT = "Spanish:es"


def parse_weights(raw: str) -> dict[str, int]:
    weights = dict(DEFAULT_WEIGHTS)
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        if key in weights:
            try:
                weights[key] = max(0, int(value))
            except ValueError:
                pass
    return weights


def parse_list(raw: str) -> list[str]:
    return [p.strip() for p in raw.split("|") if p.strip()]


def parse_langs(raw: str) -> list[tuple[str, str]]:
    out = []
    for item in parse_list(raw):
        name, _, code = item.partition(":")
        if name.strip():
            out.append((name.strip(), code.strip()))
    return out


def parse_coords(raw: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in raw.split(","))
        return lat, lon
    except ValueError:
        return None


@dataclass
class Service:
    url: str = ""
    public_url: str = ""

    @property
    def link_base(self) -> str:
        return self.public_url or self.url


@dataclass
class Settings:
    @property
    def curator_enabled(self) -> bool:
        return bool(self.llm_url and self.llm_key)

    user_agent: str = "Liessure/0.1 (self-hosted; personal use)"
    weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    wikipedia_lang: str = "en"
    home_name: str = "Seattle"
    home_coords: tuple[float, float] | None = (47.6062, -122.3321)
    wiki_interests: list[str] = field(default_factory=lambda: parse_list(DEFAULT_INTERESTS))

    jellyfin: Service = field(default_factory=Service)
    jellyfin_token: str = ""
    jellyfin_user_id: str = ""
    # Android package to offer for songs. Symfonium by default.
    music_app_package: str = "app.symfonik.music.player"
    music_app_name: str = "Symfonium"

    immich: Service = field(default_factory=Service)
    immich_api_key: str = ""

    romm: Service = field(default_factory=Service)
    romm_username: str = ""
    romm_password: str = ""
    romm_token: str = ""
    # Argosy (RomM's Android launcher) starts a game from argosy://launch?romm_id=N
    argosy: bool = True

    invidious: Service = field(default_factory=Service)
    invidious_topics: list[str] = field(default_factory=list)
    # watch.liess.tv's SSO shim lets an app past the Zitadel gate with an
    # X-Watch-Token from its watch-tokens.map (what Clipious uses).
    invidious_watch_token: str = ""
    # Where "Open" goes for a YouTube video: "youtube" (the app) or "invidious".
    video_link: str = "youtube"
    nebula_feeds: list[str] = field(default_factory=list)

    travel: Service = field(default_factory=Service)
    travel_token: str = ""

    # The household graph: the person's interests, languages, projects, home.
    graph: Service = field(default_factory=Service)
    graph_token: str = ""
    # The household entry's exact name (or system:id, e.g. zitadel:<sub>).
    graph_person: str = "Axl Liess"

    data_dir: str = "/data"

    llm_url: str = ""
    llm_key: str = ""
    llm_model: str = "fast-haiku"
    curator_profile: str = DEFAULT_PROFILE
    curator_channels: list[str] = field(default_factory=list)
    language_channels: list[str] = field(default_factory=list)
    curator_share: float = 0.8
    curator_picks: int = 15
    curator_min_score: int = 7
    curate_every_hours: float = 12
    # Send photo thumbnails to the model so captions describe the actual picture.
    curator_vision: bool = True

    news_feeds: list[str] = field(default_factory=list)
    news_max_age_hours: float = 72
    lingopie_url: str = ""

    # Languages being learned (first = current), ones you'd like a taste of,
    # and ones you already speak (their content counts as ordinary content).
    languages_learning: list[tuple[str, str]] = field(default_factory=lambda: parse_langs(DEFAULT_LEARNING))
    languages_curious: list[tuple[str, str]] = field(default_factory=lambda: parse_langs(DEFAULT_CURIOUS))
    languages_fluent: list[tuple[str, str]] = field(default_factory=lambda: parse_langs(DEFAULT_FLUENT))
    language_practice_url: str = ""

    homelab_stack: list[str] = field(default_factory=lambda: parse_list(DEFAULT_HOMELAB_STACK))
    homelab_feeds: list[str] = field(default_factory=list)
    homelab_topics: list[str] = field(default_factory=list)
    homelab_max_age_days: float = 30

    # The household jobs service (vfd000/jobs): JOBS_TOKEN is its MCP_API_TOKEN.
    jobs: Service = field(default_factory=Service)
    jobs_token: str = ""
    jobs_min_score: int = 70

    @classmethod
    def from_env(cls) -> "Settings":
        def svc(prefix: str) -> Service:
            url = _strip(_env(f"{prefix}_URL"))
            return Service(url=url, public_url=_strip(_env(f"{prefix}_PUBLIC_URL")))

        return cls(
            user_agent=_env("LIESSURE_USER_AGENT", cls.user_agent),
            weights=parse_weights(_env("LIESSURE_WEIGHTS")),
            wikipedia_lang=_env("WIKIPEDIA_LANG", "en"),
            home_name=_env("HOME_NAME", "Seattle"),
            home_coords=parse_coords(_env("HOME_COORDS", "47.6062,-122.3321")),
            wiki_interests=parse_list(_env("WIKI_INTERESTS", DEFAULT_INTERESTS)),
            jellyfin=svc("JELLYFIN"),
            jellyfin_token=_env("JELLYFIN_TOKEN"),
            jellyfin_user_id=_env("JELLYFIN_USER_ID"),
            music_app_package=_env("MUSIC_APP_PACKAGE", cls.music_app_package),
            music_app_name=_env("MUSIC_APP_NAME", cls.music_app_name),
            immich=svc("IMMICH"),
            immich_api_key=_env("IMMICH_API_KEY"),
            romm=svc("ROMM"),
            romm_username=_env("ROMM_USERNAME"),
            romm_password=_env("ROMM_PASSWORD"),
            romm_token=_env("ROMM_TOKEN"),
            argosy=_env("ARGOSY", "1") not in ("0", "false", "no"),
            invidious=svc("INVIDIOUS"),
            invidious_topics=parse_list(
                _env(
                    "INVIDIOUS_TOPICS",
                    "documentary|how it's made|science explained|history explained"
                    "|cooking|hiking|retro games|music live session|documental",
                )
            ),
            invidious_watch_token=_env("INVIDIOUS_WATCH_TOKEN"),
            video_link=_env("VIDEO_LINK", "youtube"),
            nebula_feeds=parse_list(_env("NEBULA_FEEDS", "https://rss.nebula.app/video.rss")),
            travel=svc("TRAVEL"),
            travel_token=_env("TRAVEL_TOKEN"),
            graph=svc("GRAPH"),
            graph_token=_env("GRAPH_TOKEN"),
            graph_person=_env("GRAPH_PERSON", "Axl Liess"),
            data_dir=_env("DATA_DIR", "/data"),
            llm_url=_strip(_env("LLM_URL")),
            llm_key=_env("LLM_KEY"),
            llm_model=_env("LLM_MODEL", "fast-haiku"),
            curator_profile=_env("CURATOR_PROFILE", DEFAULT_PROFILE),
            curator_channels=parse_list(_env("CURATOR_CHANNELS")),
            language_channels=parse_list(_env("LANGUAGE_CHANNELS")),
            curator_share=float(_env("CURATOR_SHARE", "0.8")),
            curator_picks=int(_env("CURATOR_PICKS", "15")),
            curator_min_score=int(_env("CURATOR_MIN_SCORE", "7")),
            curate_every_hours=float(_env("CURATE_EVERY_HOURS", "12")),
            curator_vision=_env("CURATOR_VISION", "1") not in ("0", "false", "no"),
            news_feeds=parse_list(_env("NEWS_FEEDS", DEFAULT_NEWS_FEEDS)),
            news_max_age_hours=float(_env("NEWS_MAX_AGE_HOURS", "72")),
            lingopie_url=_strip(_env("LINGOPIE_URL")),
            languages_learning=parse_langs(_env("LANGUAGES_LEARNING", DEFAULT_LEARNING)),
            languages_curious=parse_langs(_env("LANGUAGES_CURIOUS", DEFAULT_CURIOUS)),
            languages_fluent=parse_langs(_env("LANGUAGES_FLUENT", DEFAULT_FLUENT)),
            language_practice_url=_strip(_env("LANGUAGE_PRACTICE_URL")),
            homelab_stack=parse_list(_env("HOMELAB_STACK", DEFAULT_HOMELAB_STACK)),
            homelab_feeds=parse_list(_env("HOMELAB_FEEDS", DEFAULT_HOMELAB_FEEDS)),
            homelab_topics=parse_list(_env("HOMELAB_TOPICS", DEFAULT_HOMELAB_TOPICS)),
            homelab_max_age_days=float(_env("HOMELAB_MAX_AGE_DAYS", "30")),
            jobs=svc("JOBS"),
            jobs_token=_env("JOBS_TOKEN"),
            jobs_min_score=int(_env("JOBS_MIN_SCORE", "70")),
        )


def language_queries(name: str) -> list[str]:
    """Invidious searches for a language being learned."""
    return [
        f"learn {name} for beginners",
        f"{name} conversation practice",
        f"{name} comprehensible input",
        f"{name} listening practice slow",
    ]
