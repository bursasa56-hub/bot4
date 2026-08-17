"""Поиск свежих русских вирусных видео TikTok."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

MIN_LIKES = 10_000
FALLBACK_LIKES = 1_000
MAX_AGE_SECONDS = 36 * 60 * 60
TIKWM_FEED_URL = "https://www.tikwm.com/api/feed/list"
TIKWM_COMMENTS_URL = "https://www.tikwm.com/api/comment/list"
REQUEST_HEADERS = {
    "Referer": "https://www.tikwm.com/",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Только RU/BY. Чужие ленты тащат английский, украинский и казахский.
SEARCH_REGIONS = ("RU", "BY", "RU", "BY", "RU")
REQUEST_DELAY = 1.1
MAX_RETRIES = 2
REQUEST_TIMEOUT = 20
ROUND_DELAY = 2.0
AUTHOR_COOLDOWN_SECONDS = 6 * 60 * 60
MAX_SEEN_IDS = 4000
SEEN_STATE_PATH = Path(__file__).resolve().parent / "seen_state.json"

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
LATIN_RE = re.compile(r"[A-Za-z]")
HASHTAG_OR_MENTION_RE = re.compile(r"[#@]\S+")
URL_RE = re.compile(r"https?://\S+", re.I)
SPACE_RE = re.compile(r"\s+")
UKRAINIAN_LETTERS_RE = re.compile(r"[ІіЇїЄєҐґ]")
KAZAKH_LETTERS_RE = re.compile(r"[ӘәҒғҚқҢңӨөҰұҮүҺһ]")
BELARUSIAN_LETTERS_RE = re.compile(r"[Ўў]")
UKRAINIAN_WORDS_RE = re.compile(
    r"(?i)(?<![А-Яа-яЁёІіЇїЄєҐґ])"
    r"(це|що|або|від|мені|тобі|дуже|зараз|можна|треба|дякую|привіт|"
    r"гарно|гарний|також|нічого|україн|київ|львів|харків|одеса|"
    r"дніпро|будь ласка|тому що|відео|дякуючи|сьогодні|"
    r"якщо|який|яка|які|буде|було)"
    r"(?![А-Яа-яЁёІіЇїЄєҐґ])"
)
KAZAKH_WORDS_RE = re.compile(
    r"(?i)(?<![А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһ])"
    r"(және|үшін|бұл|емес|қазақ|алматы|астана|шымкент|жақсы|рақмет|"
    r"керек|қалай|неге)"
    r"(?![А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһ])"
)
RUSSIAN_HINT_RE = re.compile(
    r"(?i)(?<![А-Яа-яЁё])"
    r"(это|как|что|меня|тебя|просто|очень|сегодня|когда|если|можно|надо|"
    r"почему|вообще|такой|такое|видео|смотри|привет|всем|здесь|теперь|"
    r"хочу|будет|после|только|больше|лучше|короче|реально|москва|питер|"
    r"россия|русск|бля|блин|типа|вообщем|короче)"
    r"(?![А-Яа-яЁё])"
)
ALLOWED_REGIONS = {"RU", "BY"}
REJECT_REGIONS = {
    "UA", "KZ", "UZ", "KG", "TJ", "AZ", "AM", "GE", "MD",
    "US", "GB", "DE", "PL", "TR", "IN", "PK", "BD", "ID", "PH", "BR", "MX",
}


@dataclass(slots=True)
class TikTokVideo:
    video_id: str
    title: str
    create_time: int
    likes: int
    plays: int
    duration: int
    author_id: str
    author_name: str
    region: str
    cover_url: str
    play_url: str
    size: int
    top_comment: str = ""
    top_comment_likes: int = 0

    @property
    def age_seconds(self) -> int:
        return max(0, int(time.time()) - self.create_time)

    @property
    def tiktok_url(self) -> str:
        return f"https://www.tiktok.com/share/video/{self.video_id}"


def _absolute_url(url: str | None) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.tikwm.com" + url
    return url


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_username(value: object) -> str:
    if isinstance(value, str):
        username = value.strip().lstrip("@")
        if username and "{" not in username:
            return username
    return ""


def _text_from_content_desc(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("desc") or ""))
        return " ".join(parts)
    return ""


def parse_video(raw: dict) -> TikTokVideo | None:
    video_id = str(raw.get("video_id") or raw.get("id") or "").strip()
    if not video_id:
        return None

    author = raw.get("author") or {}
    if not isinstance(author, dict):
        author = {}

    title = str(raw.get("title") or raw.get("desc") or "").strip()
    extra = _text_from_content_desc(raw.get("content_desc"))
    if extra:
        title = f"{title} {extra}".strip()

    return TikTokVideo(
        video_id=video_id,
        title=title,
        create_time=_as_int(raw.get("create_time")),
        likes=_as_int(raw.get("digg_count")),
        plays=_as_int(raw.get("play_count")),
        duration=_as_int(raw.get("duration")),
        author_id=_as_username(author.get("unique_id") or author.get("uniqueId")),
        author_name=author.get("nickname").strip() if isinstance(author.get("nickname"), str) else "",
        region=str(raw.get("region") or author.get("region") or "").strip().upper(),
        cover_url=_absolute_url(
            raw.get("cover") or raw.get("origin_cover") or raw.get("ai_dynamic_cover")
        ),
        play_url=_absolute_url(raw.get("play") or raw.get("wmplay")),
        size=_as_int(raw.get("size") or raw.get("wm_size")),
    )


def _meaningful_text(text: str) -> str:
    cleaned = HASHTAG_OR_MENTION_RE.sub(" ", text)
    cleaned = URL_RE.sub(" ", cleaned)
    return SPACE_RE.sub(" ", cleaned).strip()


def _title_fingerprint(video: TikTokVideo) -> str:
    text = _meaningful_text(video.title).lower()
    if len(text) < 18:
        return ""
    return text


def is_russian_video(video: TikTokVideo) -> bool:
    if video.region in REJECT_REGIONS:
        return False
    if video.region and video.region not in ALLOWED_REGIONS:
        return False

    blob = " ".join(part for part in (video.title, video.author_name) if part)
    if not blob:
        return False
    if UKRAINIAN_LETTERS_RE.search(blob) or KAZAKH_LETTERS_RE.search(blob):
        return False
    if BELARUSIAN_LETTERS_RE.search(blob):
        return False
    if UKRAINIAN_WORDS_RE.search(blob) or KAZAKH_WORDS_RE.search(blob):
        return False

    # Ник и хештеги не считаем: из-за них пролезал английский/украинский.
    plain = _meaningful_text(video.title)
    if len(plain) < 8:
        return False
    cyrillic_count = len(CYRILLIC_RE.findall(plain))
    latin_count = len(LATIN_RE.findall(plain))
    if cyrillic_count < 8:
        return False
    if latin_count >= cyrillic_count:
        return False
    return bool(RUSSIAN_HINT_RE.search(plain) or cyrillic_count >= 12)


def is_fresh(video: TikTokVideo) -> bool:
    return 0 < video.create_time and video.age_seconds <= MAX_AGE_SECONDS


async def _fetch_feed(session: AsyncSession, region: str) -> list[TikTokVideo]:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await session.get(
                TIKWM_FEED_URL,
                params={"region": region, "count": 20},
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in {403, 429, 500, 502, 503, 531}:
                last_error = f"HTTP {response.status_code} for {region}"
                await asyncio.sleep(1.5 * attempt)
                continue
            if response.status_code >= 400:
                logger.warning("Лента %s: HTTP %s", region, response.status_code)
                return []

            payload = response.json()
            message = str(payload.get("msg") or "")
            if "limit" in message.lower():
                last_error = message
                await asyncio.sleep(REQUEST_DELAY)
                continue
            if payload.get("code") not in (0, "0", None):
                logger.warning("Лента %s: %s", region, message)
                return []

            videos: list[TikTokVideo] = []
            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                video = parse_video(item)
                if video:
                    videos.append(video)
            return videos
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Ошибка ленты %s (попытка %s): %s", region, attempt, exc)
            await asyncio.sleep(0.4)

    logger.warning("Не удалось получить ленту %s: %s", region, last_error)
    return []


async def fetch_top_comment(session: AsyncSession, video: TikTokVideo) -> tuple[str, int]:
    """Возвращает текст и число лайков у самого залайканного комментария."""
    try:
        response = await session.get(
            TIKWM_COMMENTS_URL,
            params={"url": video.tiktok_url, "count": 30, "cursor": 0},
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            logger.warning("Комментарии %s: HTTP %s", video.video_id, response.status_code)
            return "", 0
        payload = response.json()
        if payload.get("code") not in (0, "0", None):
            logger.warning("Комментарии %s: %s", video.video_id, payload.get("msg"))
            return "", 0
        comments = (payload.get("data") or {}).get("comments") or []
        best_text = ""
        best_likes = -1
        for item in comments:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            likes = _as_int(item.get("digg_count") or item.get("like_count"))
            if likes > best_likes:
                best_likes = likes
                best_text = text
        if best_likes < 0:
            return "", 0
        return best_text, best_likes
    except Exception as exc:
        logger.warning("Не удалось получить комментарии %s: %s", video.video_id, exc)
        return "", 0


class VideoPool:
    """Ищет ролики в фоне с запуска бота и копит их в общую очередь."""

    def __init__(self) -> None:
        self.videos: list[TikTokVideo] = []
        self.seen: set[str] = set()
        self.seen_order: list[str] = []
        self.fingerprints: set[str] = set()
        self.author_seen_at: dict[str, float] = {}
        self.stop_event = asyncio.Event()
        self._new_item = asyncio.Event()
        self._load_state()

    def snapshot(self) -> list[TikTokVideo]:
        return list(self.videos)

    def _load_state(self) -> None:
        try:
            raw = json.loads(SEEN_STATE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning("Не удалось прочитать %s: %s", SEEN_STATE_PATH.name, exc)
            return
        if not isinstance(raw, dict):
            return
        ids = raw.get("ids") or []
        fingerprints = raw.get("fingerprints") or []
        authors = raw.get("authors") or {}
        if isinstance(ids, list):
            self.seen_order = [str(item) for item in ids if item]
            self.seen = set(self.seen_order)
        if isinstance(fingerprints, list):
            self.fingerprints = {str(item) for item in fingerprints if item}
        if isinstance(authors, dict):
            loaded: dict[str, float] = {}
            for key, value in authors.items():
                try:
                    loaded[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            self.author_seen_at = loaded
        logger.info(
            "Память поиска: %s роликов, %s авторов",
            len(self.seen),
            len(self.author_seen_at),
        )

    def _remember_id(self, video_id: str) -> None:
        if video_id in self.seen:
            return
        self.seen.add(video_id)
        self.seen_order.append(video_id)

    def _save_state(self) -> None:
        if len(self.seen_order) > MAX_SEEN_IDS:
            self.seen_order = self.seen_order[-MAX_SEEN_IDS:]
            self.seen = set(self.seen_order)
        fingerprints = list(self.fingerprints)
        if len(fingerprints) > MAX_SEEN_IDS:
            fingerprints = fingerprints[-MAX_SEEN_IDS:]
            self.fingerprints = set(fingerprints)
        cutoff = time.time() - AUTHOR_COOLDOWN_SECONDS * 4
        self.author_seen_at = {
            author: ts for author, ts in self.author_seen_at.items() if ts >= cutoff
        }
        payload = {
            "ids": self.seen_order,
            "fingerprints": fingerprints,
            "authors": self.author_seen_at,
        }
        try:
            SEEN_STATE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Не удалось сохранить память поиска: %s", exc)

    def _is_duplicate(self, video: TikTokVideo) -> bool:
        if video.video_id in self.seen:
            return True
        fingerprint = _title_fingerprint(video)
        if fingerprint and fingerprint in self.fingerprints:
            return True
        if video.author_id:
            last_seen = self.author_seen_at.get(video.author_id, 0)
            if last_seen and time.time() - last_seen < AUTHOR_COOLDOWN_SECONDS:
                return True
        return False

    def _add(self, video: TikTokVideo) -> None:
        if self._is_duplicate(video):
            self._remember_id(video.video_id)
            self._save_state()
            return
        self._remember_id(video.video_id)
        fingerprint = _title_fingerprint(video)
        if fingerprint:
            self.fingerprints.add(fingerprint)
        if video.author_id:
            self.author_seen_at[video.author_id] = time.time()
        self.videos.append(video)
        self._save_state()
        self._new_item.set()
        self._new_item = asyncio.Event()

    async def _enrich_and_add(self, session: AsyncSession, video: TikTokVideo) -> None:
        if self._is_duplicate(video):
            self._remember_id(video.video_id)
            return
        await asyncio.sleep(REQUEST_DELAY)
        text, likes = await fetch_top_comment(session, video)
        video.top_comment = text
        video.top_comment_likes = likes
        self._add(video)

    async def wait_change(self, timeout: float = 0.4) -> None:
        try:
            await asyncio.wait_for(self._new_item.wait(), timeout=timeout)
        except TimeoutError:
            return

    async def run(self) -> None:
        logger.info("Фоновый поиск видео запущен")
        async with AsyncSession(impersonate="chrome") as session:
            while not self.stop_event.is_set():
                fallbacks: dict[str, TikTokVideo] = {}
                added = 0
                for index, region in enumerate(SEARCH_REGIONS):
                    if self.stop_event.is_set():
                        return
                    if index:
                        await asyncio.sleep(REQUEST_DELAY)
                    videos = await _fetch_feed(session, region)
                    fresh = russian = matched = 0
                    for video in videos:
                        if not is_fresh(video):
                            continue
                        fresh += 1
                        if not is_russian_video(video):
                            continue
                        russian += 1
                        if self._is_duplicate(video):
                            self._remember_id(video.video_id)
                            continue
                        if video.likes >= MIN_LIKES:
                            matched += 1
                            added += 1
                            logger.info(
                                "В пул: %s, %s лайков, %s с назад",
                                video.video_id,
                                video.likes,
                                video.age_seconds,
                            )
                            await self._enrich_and_add(session, video)
                        elif video.likes >= FALLBACK_LIKES:
                            fallbacks[video.video_id] = video
                    logger.info(
                        "Лента %s: всего %s, свежих %s, русских %s, с 10к+ %s, в пуле %s",
                        region,
                        len(videos),
                        fresh,
                        russian,
                        matched,
                        len(self.videos),
                    )

                if added == 0 and fallbacks:
                    ranked = sorted(fallbacks.values(), key=lambda item: item.likes, reverse=True)
                    for video in ranked:
                        if self._is_duplicate(video):
                            self._remember_id(video.video_id)
                            continue
                        logger.info(
                            "В пул (запасной порог): %s, %s лайков",
                            video.video_id,
                            video.likes,
                        )
                        await self._enrich_and_add(session, video)

                await asyncio.sleep(ROUND_DELAY)
