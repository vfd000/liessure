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
}

KINDS = tuple(DEFAULT_WEIGHTS)

DEFAULT_NEWS_FEEDS = (
    "https://feeds.npr.org/1001/rss.xml"
    "|https://feeds.bbci.co.uk/news/world/rss.xml"
    "|https://www.theguardian.com/science/rss"
    "|https://feeds.arstechnica.com/arstechnica/index"
    "|https://hnrss.org/best"
)

DEFAULT_PROFILE = (
    "Curious adult who runs a self-hosted home lab, hikes and backpacks in the "
    "Pacific Northwest, plays retro games, and is learning Telugu. Likes "
    "explainers, engineering and how-things-work, science, history, travel, "
    "food, and long-form craft. Has little patience for hype, drama or outrage."
)


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

    invidious: Service = field(default_factory=Service)
    invidious_topics: list[str] = field(default_factory=list)

    data_dir: str = "/data"

    llm_url: str = ""
    llm_key: str = ""
    llm_model: str = "fast-haiku"
    curator_profile: str = ""
    curator_channels: list[str] = field(default_factory=list)
    language_channels: list[str] = field(default_factory=list)
    curator_share: float = 0.8
    curator_picks: int = 15
    curator_min_score: int = 7
    curate_every_hours: float = 12

    news_feeds: list[str] = field(default_factory=list)
    news_max_age_hours: float = 72
    lingopie_url: str = ""

    language_name: str = "Telugu"
    language_wikipedia: str = "te"
    language_practice_url: str = ""
    language_video_queries: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        def svc(prefix: str) -> Service:
            url = _strip(_env(f"{prefix}_URL"))
            return Service(url=url, public_url=_strip(_env(f"{prefix}_PUBLIC_URL")))

        return cls(
            user_agent=_env("LIESSURE_USER_AGENT", cls.user_agent),
            weights=parse_weights(_env("LIESSURE_WEIGHTS")),
            wikipedia_lang=_env("WIKIPEDIA_LANG", "en"),
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
            invidious=svc("INVIDIOUS"),
            invidious_topics=parse_list(
                _env(
                    "INVIDIOUS_TOPICS",
                    "documentary|how it's made|science explained|history explained"
                    "|cooking|hiking|retro games|music live session",
                )
            ),
            data_dir=_env("DATA_DIR", "/data"),
            llm_url=_strip(_env("LLM_URL")),
            llm_key=_env("LLM_KEY"),
            llm_model=_env("LLM_MODEL", "fast-haiku"),
            curator_profile=_env("CURATOR_PROFILE", DEFAULT_PROFILE),
            curator_channels=parse_list(_env("CURATOR_CHANNELS")),
            language_channels=parse_list(_env("LANGUAGE_CHANNELS")),
            curator_share=float(_env("CURATOR_SHARE", "0.8") or 0.8),
            curator_picks=int(_env("CURATOR_PICKS", "15") or 15),
            curator_min_score=int(_env("CURATOR_MIN_SCORE", "7") or 7),
            curate_every_hours=float(_env("CURATE_EVERY_HOURS", "12") or 12),
            news_feeds=parse_list(_env("NEWS_FEEDS", DEFAULT_NEWS_FEEDS)),
            news_max_age_hours=float(_env("NEWS_MAX_AGE_HOURS", "72") or 72),
            lingopie_url=_strip(_env("LINGOPIE_URL")),
            language_name=_env("LANGUAGE_NAME", "Telugu"),
            language_wikipedia=_env("LANGUAGE_WIKIPEDIA", "te"),
            language_practice_url=_strip(_env("LANGUAGE_PRACTICE_URL")),
            language_video_queries=parse_list(
                _env(
                    "LANGUAGE_VIDEO_QUERIES",
                    "learn telugu|telugu conversation practice|telugu for beginners"
                    "|telugu short film",
                )
            ),
        )
