from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    db_path: Path
    match_threshold: int
    telegram_bot_token: str
    telegram_chat_id: str
    gmail_email: str
    gmail_app_password: str
    scraper_timeout: int
    scraper_user_agent: str
    max_results_per_source: int
    min_monitor_interval_minutes: int
    telegram_digest_max_jobs: int
    telegram_max_message_chars: int
    templates_dir: Path
    generated_dir: Path
    timezone_name: str = "America/Bogota"
    enable_selenium: bool = False
    selenium_headless: bool = True
    selenium_page_load_timeout: int = 30
    selenium_scroll_pause: int = 3
    selenium_max_scrolls: int = 5


def load_settings() -> Settings:
    load_dotenv()
    db_path = Path(os.getenv("JOBOPS_DB_PATH", "./data/jobops.db"))
    templates_dir = Path("./templates")
    generated_dir = Path("./generated")
    return Settings(
        db_path=db_path,
        match_threshold=int(os.getenv("JOBOPS_MATCH_THRESHOLD", "65")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        gmail_email=os.getenv("GMAIL_EMAIL", "").strip(),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", "").strip(),
        scraper_timeout=int(os.getenv("JOBOPS_SCRAPER_TIMEOUT", "20")),
        scraper_user_agent=os.getenv(
            "JOBOPS_SCRAPER_USER_AGENT",
            "JobOpsPersonalAssistant/1.0 personal-job-monitor",
        ).strip(),
        max_results_per_source=int(os.getenv("JOBOPS_MAX_RESULTS_PER_SOURCE", "25")),
        min_monitor_interval_minutes=int(os.getenv("JOBOPS_MIN_MONITOR_INTERVAL_MINUTES", "10")),
        telegram_digest_max_jobs=int(os.getenv("JOBOPS_TELEGRAM_DIGEST_MAX_JOBS", "10")),
        telegram_max_message_chars=int(os.getenv("JOBOPS_TELEGRAM_MAX_MESSAGE_CHARS", "3500")),
        templates_dir=templates_dir,
        generated_dir=generated_dir,
        timezone_name=os.getenv("JOBOPS_TIMEZONE", "America/Bogota").strip() or "America/Bogota",
        enable_selenium=_parse_bool(os.getenv("JOBOPS_ENABLE_SELENIUM", "false")),
        selenium_headless=_parse_bool(os.getenv("JOBOPS_SELENIUM_HEADLESS", "true")),
        selenium_page_load_timeout=int(os.getenv("JOBOPS_SELENIUM_PAGE_LOAD_TIMEOUT", "30")),
        selenium_scroll_pause=int(os.getenv("JOBOPS_SELENIUM_SCROLL_PAUSE", "3")),
        selenium_max_scrolls=int(os.getenv("JOBOPS_SELENIUM_MAX_SCROLLS", "5")),
    )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
