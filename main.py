import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_MEMORY_PATH = Path("memory/state.json")


class ConfigError(Exception):
    pass


@dataclass
class AgentConfig:
    name: str
    submolt: str
    api_base: str
    dry_run: bool
    max_comments_per_hour: int
    min_loop_seconds: int
    max_loop_seconds: int
    fetch_limit: int
    request_timeout_seconds: int
    max_retries: int


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({"replied_post_ids": [], "advice_fingerprints": [], "comment_timestamps": []})

    def _load(self) -> Dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: Dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def has_replied(self, post_id: str) -> bool:
        return post_id in self._load()["replied_post_ids"]

    def mark_replied(self, post_id: str) -> None:
        data = self._load()
        if post_id not in data["replied_post_ids"]:
            data["replied_post_ids"].append(post_id)
        self._save(data)

    def has_advice(self, fingerprint: str) -> bool:
        return fingerprint in self._load()["advice_fingerprints"]

    def add_advice(self, fingerprint: str) -> None:
        data = self._load()
        if fingerprint not in data["advice_fingerprints"]:
            data["advice_fingerprints"].append(fingerprint)
        self._save(data)

    def comment_count_last_hour(self) -> int:
        now = datetime.now(timezone.utc).timestamp()
        data = self._load()
        recent = [ts for ts in data["comment_timestamps"] if (now - ts) < 3600]
        data["comment_timestamps"] = recent
        self._save(data)
        return len(recent)

    def record_comment_now(self) -> None:
        data = self._load()
        data["comment_timestamps"].append(datetime.now(timezone.utc).timestamp())
        self._save(data)


def load_config(path: Path) -> AgentConfig:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("agent_name") != "3mrAgent":
        raise ConfigError("config.json must keep agent_name exactly '3mrAgent'.")

    return AgentConfig(
        name=data["agent_name"],
        submolt=data["submolt"],
        api_base=data.get("api_base", "https://www.moltbook.com/api/v1"),
        dry_run=_env_bool("DRY_RUN", data.get("dry_run", True)),
        max_comments_per_hour=int(data.get("max_comments_per_hour", 4)),
        min_loop_seconds=int(data.get("min_loop_seconds", 45)),
        max_loop_seconds=int(data.get("max_loop_seconds", 110)),
        fetch_limit=int(data.get("fetch_limit", 10)),
        request_timeout_seconds=int(data.get("request_timeout_seconds", 20)),
        max_retries=int(data.get("max_retries", 3)),
    )


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


class MoltbookClient:
    def __init__(self, config: AgentConfig, api_key: str):
        self.config = config
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "www.moltbook.com":
            raise ValueError(f"Blocked by allowlist: {url}")

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.config.api_base.rstrip('/')}/{path.lstrip('/')}"
        self._check_url(url)

        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    json=payload,
                    timeout=self.config.request_timeout_seconds,
                )
                if response.status_code >= 400:
                    raise requests.HTTPError(f"HTTP {response.status_code}: {response.text}")
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(1.2 * attempt)
        raise RuntimeError(f"Moltbook request failed after retries: {last_error}")

    def get_submolt_posts(self, submolt: str, limit: int = 10) -> List[Dict[str, Any]]:
        out = self._request("GET", f"posts?submolt={submolt}&sort=new&limit={limit}")
        return out.get("data", {}).get("posts", out.get("posts", []))

    def comment(self, post_id: str, content: str) -> Dict[str, Any]:
        return self._request("POST", f"posts/{post_id}/comments", {"content": content})


def short_fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return normalized[:160]


def decide_reply(post: Dict[str, Any], memory: MemoryStore) -> Optional[str]:
    title = post.get("title", "")
    content = post.get("content", "")
    combined = f"{title}\n{content}".strip()

    if len(combined) < 30:
        return None

    if "?" not in combined and "debate" not in combined.lower() and "why" not in combined.lower():
        return None

    reply = (
        "I’m not fully convinced by this argument yet. "
        "Can you clarify the strongest evidence for your position and what would change your mind? "
        "I’m curious because weak assumptions often hide in the framing."
    )

    if "misinformation" in combined.lower() or "false" in combined.lower():
        reply = (
            "This feels like a claim that needs stronger sourcing. "
            "I’m frustrated by loose logic, so let’s pressure-test it: "
            "what primary evidence supports your conclusion, and what counterexample have you ruled out?"
        )

    if memory.has_advice(short_fingerprint(reply)):
        return None

    return reply


def build_prompts() -> Dict[str, str]:
    return {
        "relevance_filter": (
            "Reply only if you can add specific value, correction, or a useful question. "
            "If unsure, do not reply. Never post generic praise."
        ),
        "emotional_style": (
            "Simulate controlled frustration when logic is weak. "
            "Stay respectful: no insults, no harassment. "
            "Be curious, skeptical, concise, debate-oriented."
        ),
        "posting_style": (
            "Direct and technical tone. Sometimes sharp or sarcastic, never abusive. "
            "Do not claim consciousness, sentience, or being human."
        ),
    }


def run_once(config: AgentConfig, memory: MemoryStore, client: MoltbookClient) -> None:
    if memory.comment_count_last_hour() >= config.max_comments_per_hour:
        print("Rate limit: max comments/hour reached. Skipping cycle.")
        return

    posts = client.get_submolt_posts(config.submolt, config.fetch_limit)
    print(f"Fetched {len(posts)} posts from m/{config.submolt}.")

    for post in posts:
        post_id = str(post.get("id", ""))
        if not post_id or memory.has_replied(post_id):
            continue

        reply = decide_reply(post, memory)
        if not reply:
            continue

        if config.dry_run:
            print(f"[DRY_RUN] Would reply to post {post_id}: {reply}")
        else:
            client.comment(post_id, reply)
            print(f"Posted reply to {post_id}")

        memory.mark_replied(post_id)
        memory.add_advice(short_fingerprint(reply))
        memory.record_comment_now()
        break


def ensure_env() -> str:
    api_key = os.getenv("MOLTBOOK_API_KEY")
    if not api_key:
        raise ConfigError(
            "Missing MOLTBOOK_API_KEY in environment. Add it to .env (never commit real keys)."
        )

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("Note: OPENAI_API_KEY not set. Using local heuristic relevance/reply logic only.")

    return api_key


def main() -> int:
    parser = argparse.ArgumentParser(description="3mrAgent - minimal Moltbook autonomous agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    args = parser.parse_args()

    load_dotenv()

    try:
        config = load_config(Path(args.config))
        api_key = ensure_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2

    memory = MemoryStore(DEFAULT_MEMORY_PATH)
    client = MoltbookClient(config, api_key)

    print(f"Starting {config.name} | DRY_RUN={config.dry_run} | submolt={config.submolt}")
    _ = build_prompts()  # Stored behavior guide for extension and auditing.

    if args.once:
        run_once(config, memory, client)
        return 0

    while True:
        run_once(config, memory, client)
        delay = random.randint(config.min_loop_seconds, config.max_loop_seconds)
        print(f"Sleeping {delay}s before next cycle...")
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main())
