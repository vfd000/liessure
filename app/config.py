"""Settings, all from environment variables.

Every source is optional: one with no base URL (or no credential) is
simply left out of the feed, so the app runs with only Wikipedia on day one
and picks up each household service as its secret is added.

Each service has two URLs:
  *_URL         where the Liessure server reaches the API (can be a LAN address)
  *_PUBLIC_URL  what goes into the links you tap (defaults to *_URL)
"""

from __future__ import annotations

import json
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
    "telemetry": 1,
    "home": 1,
    "math": 2,
    "recall": 2,
    "todo": 2,
    "infosec": 2,
    "bite": 3,
    "lesson": 3,
    "career": 2,
    "kquiz": 2,
    "trivia": 2,
    "session": 3,
    "exercise": 2,
    "recommend": 2,
    "flashcard": 3,
    "geoquiz": 1,
}

# Kinds shown at most this many times per feed load. The feed is a break,
# not a work queue.
MAX_PER_LOAD = {"job": 1, "skill": 1, "telemetry": 1, "home": 1, "recall": 1, "todo": 1, "career": 1, "kquiz": 1, "trivia": 2, "session": 2, "exercise": 1, "recommend": 1, "flashcard": 2, "geoquiz": 2}

# Cards whose text is kept when you open or play them, to quiz you on later.
RECALL_KINDS = frozenset({"wikipedia", "news", "homelab", "language", "video", "infosec", "bite"})

# Per-person preferences, keyed by Zitadel sub: what someone reads in, their interests, what they're
# learning, careers they're exploring. They're personal, so they never live in this (public) repo: they
# come from USER_PREFS_FILE (default /data/user-prefs.json, on the server's data volume) and USER_PREFS
# (JSON, overrides the file). Shape of one entry:
#   {"lang": "es", "lang_name": "Spanish", "wikipedia": "es", "news_feeds": [...], "interests": [...],
#    "video_topics": [...], "nebula_feeds": [...], "nebula_share": 0.5, "weights": {"math": 1, ...},
#    "geo_bites": true, "learning": [{"name": "English", "label": "Inglés", "code": "en",
#    "style": "reading" | "beginner"}], "kinds_off": [...],
#    "career": {"now": "...", "likes": [...], "explore": [...]}, "labels": {...}}
# Interests and languages move to the household graph with per-user curation.

# The page's lesson labels in a reader's language (anything in prefs["labels"] overrides).
LESSON_LABELS: dict[str, dict[str, str]] = {
    "es": {"translation": "Traducción", "reading": "lectura", "phrase": "frase", "open": "Abrir",
           "explain": "Ver explicación", "hear": "Escuchar", "note": "Nota", "translate": "Traducir",
           "ask": "¿Qué significa?", "flashcard": "Tarjeta",
           "llm": "Escrito por el LLM: Traducir lo comprueba."},
}


def parse_user_prefs(raw: str, path: str = "") -> dict[str, dict]:
    prefs: dict[str, dict] = {}
    sources_: list[str] = []
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                sources_.append(f.read())
        except OSError:
            pass
    if raw:
        sources_.append(raw)
    for text in sources_:
        try:
            for sub, p in json.loads(text).items():
                prefs[str(sub)] = {**prefs.get(str(sub), {}), **p}
        except (ValueError, AttributeError):
            pass
    return prefs


# Kinds only the owner sees: they read the owner's own services (jobs, lists, the lab).
OWNER_ONLY_KINDS = frozenset({"job", "skill", "todo", "telemetry", "session", "exercise", "recommend"})

# Kinds whose titles never go to the LLM as evidence of what you like.
UNCURATED_KINDS = frozenset({"job", "skill", "telemetry", "home", "math", "recall", "todo", "trivia", "session", "exercise", "geoquiz"})

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
# Security research worth reading, besides Phrack (which has no feed: see sources.phrack_issue).
DEFAULT_INFOSEC_FEEDS = (
    "https://googleprojectzero.blogspot.com/feeds/posts/default"
    "|https://portswigger.net/research/rss"
    "|https://blog.trailofbits.com/feed/"
    "|https://www.thezdi.com/blog?format=rss"
    "|https://krebsonsecurity.com/feed/"
    "|https://www.schneier.com/feed/atom/"
    "|https://tldrsec.com/feed.xml"
    "|https://lwn.net/headlines/rss"
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
    # The share of video cards that come from Nebula when Invidious is also configured.
    nebula_share: float = 0.25

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

    infosec_feeds: list[str] = field(default_factory=list)
    infosec_max_age_days: float = 30
    phrack: bool = True

    # The household jobs service (vfd000/jobs): JOBS_TOKEN is its MCP_API_TOKEN.
    jobs: Service = field(default_factory=Service)
    jobs_token: str = ""
    jobs_min_score: int = 70

    # Grafana on the telemetry LXC (alerts, and Prometheus through its datasource proxy).
    grafana: Service = field(default_factory=Service)
    grafana_token: str = ""
    grafana_prom_uid: str = "PBFA97CFB590B2093"

    # Home Assistant (energy from the Enphase Envoy, the weather).
    ha: Service = field(default_factory=Service)
    ha_token: str = ""
    # Sign-in (Zitadel OIDC). Off unless the client id, secret and allowlist are all set.
    zitadel_issuer: str = "https://sso.liess.tv"
    zitadel_client_id: str = ""
    zitadel_client_secret: str = ""
    zitadel_user_subs: list[str] = field(default_factory=list)
    # The person whose services Liessure reads and whose data predates multi-user (axl).
    owner_sub: str = "387450248433827843"
    user_prefs: dict[str, dict] = field(default_factory=dict)
    # Set per reader (main.settings_for): the language LLM answers and Claude tutoring use ("" = English),
    # and whether language cards may show a whole Wikipedia article in the language being learned.
    reply_lang: str = ""
    # Off by default: for a learner a whole article in the language is a wall of text; lessons do it better.
    language_wiki: bool = False
    public_url: str = "https://liessure.liess.tv"

    @property
    def sso_enabled(self) -> bool:
        return bool(self.zitadel_client_id and self.zitadel_client_secret and self.zitadel_user_subs)

    # The household lists service (vfd000/lists): LISTS_TOKEN is its MCP_API_TOKEN. Read only here.
    lists: Service = field(default_factory=Service)
    lists_token: str = ""
    todo_list_id: int = 6

    # Claude sessions on the agent box (Session Hub): the fleet relay's GET /v1/board, with a token that
    # opens nothing else. Cards link to the hub board; they never answer or approve anything.
    sessions_url: str = ""
    sessions_token: str = ""
    hub_url: str = "https://agents.liess.tv/hub/"

    # Exercise cards: the household Exercises list (lists service) and the activity tracker (vfd000/activity-tracker,
    # its MCP_API_TOKEN, read only) to know whether there's been a run today.
    exercise_list_id: int = 15
    activity_url: str = ""
    activity_token: str = ""

    # Recommendations with a Get it button: Jellyseerr (requests go on to Radarr/Sonarr). LINK is its public URL.
    jellyseerr_url: str = ""
    jellyseerr_api_key: str = ""
    jellyseerr_link: str = ""

    # What a kWh is worth: PSE's effective rate, what solar saves (or earns back) per kWh.
    home_energy_rate: float = 0.22

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
            nebula_share=float(_env("NEBULA_SHARE", "0.25")),
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
            infosec_feeds=parse_list(_env("INFOSEC_FEEDS", DEFAULT_INFOSEC_FEEDS)),
            infosec_max_age_days=float(_env("INFOSEC_MAX_AGE_DAYS", "30")),
            phrack=_env("PHRACK", "1") not in ("0", "false", "no"),
            jobs=svc("JOBS"),
            jobs_token=_env("JOBS_TOKEN"),
            jobs_min_score=int(_env("JOBS_MIN_SCORE", "70")),
            grafana=svc("GRAFANA"),
            grafana_token=_env("GRAFANA_TOKEN"),
            grafana_prom_uid=_env("GRAFANA_PROM_UID", cls.grafana_prom_uid),
            ha=svc("HA"),
            ha_token=_env("HA_TOKEN"),
            home_energy_rate=float(_env("HOME_ENERGY_RATE", "0.22")),
            zitadel_issuer=_strip(_env("ZITADEL_ISSUER", "https://sso.liess.tv")),
            zitadel_client_id=_env("ZITADEL_CLIENT_ID"),
            zitadel_client_secret=_env("ZITADEL_CLIENT_SECRET"),
            zitadel_user_subs=[x.strip() for x in _env("ZITADEL_USER_SUBS").split(",") if x.strip()],
            owner_sub=_env("OWNER_SUB", cls.owner_sub),
            language_wiki=_env("LANGUAGE_WIKI", "0") not in ("0", "false", "no"),
            user_prefs=parse_user_prefs(_env("USER_PREFS"), _env("USER_PREFS_FILE", "/data/user-prefs.json")),
            public_url=_strip(_env("PUBLIC_URL", cls.public_url)),
            lists=svc("LISTS"),
            lists_token=_env("LISTS_TOKEN"),
            todo_list_id=int(_env("TODO_LIST_ID", "6")),
            sessions_url=_strip(_env("SESSIONS_URL")),
            sessions_token=_env("SESSIONS_TOKEN"),
            hub_url=_env("HUB_URL", cls.hub_url),
            exercise_list_id=int(_env("EXERCISE_LIST_ID", "15")),
            activity_url=_strip(_env("ACTIVITY_URL")),
            activity_token=_env("ACTIVITY_TOKEN"),
            jellyseerr_url=_strip(_env("JELLYSEERR_URL")),
            jellyseerr_api_key=_env("JELLYSEERR_API_KEY"),
            jellyseerr_link=_strip(_env("JELLYSEERR_LINK", "https://requests.liess.tv")),
        )


def language_queries(name: str) -> list[str]:
    """Invidious searches for a language being learned."""
    return [
        f"learn {name} for beginners",
        f"{name} conversation practice",
        f"{name} comprehensible input",
        f"{name} listening practice slow",
    ]
