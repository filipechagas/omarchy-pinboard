#!/usr/bin/env python3
"""JSON-line backend helper for the Omapin QML plugin."""

from __future__ import annotations

import email.utils
import fcntl
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree


PLUGIN_ID = "io.github.filipechagas.omapin"
API_BASE = "https://api.pinboard.in/v1"
API_TIMEOUT = 10
API_INTERVAL = 3.0
API_BODY_LIMIT = 2 * 1024 * 1024
TITLE_TIMEOUT = 4
TITLE_BODY_LIMIT = 1024 * 1024
TITLE_REDIRECT_LIMIT = 5
CLIPBOARD_LIMIT = 8192
INPUT_LIMIT = 1024 * 1024
STATE_FILE_LIMIT = 8 * 1024 * 1024
TOKEN_USERNAME_LIMIT = 255
TOKEN_SECRET_LIMIT = 4096
SUGGESTION_TAG_LIMIT = 32
SUGGESTION_ENTRY_LIMIT = 64
TAG_VOCABULARY_LIMIT = 5000
MAX_RETRY_ATTEMPTS = 12
INITIAL_RETRY_DELAY = 15
RETRY_DELAYS = (15, 45, 180, 900, 3600)
MAX_RETRY_AFTER = RETRY_DELAYS[-1]

CANONICAL_ATTRIBUTES = (
    "omarchy-plugin",
    PLUGIN_ID,
    "field",
    "token",
)
LEGACY_ATTRIBUTES = (
    "target",
    "default",
    "service",
    "ommapin",
    "username",
    "pinboard_auth_token",
    "application",
    "rust-keyring",
)
SECRET_LABEL = "Omapin Pinboard token"


class HelperError(Exception):
    """An expected error that is safe to return to the caller."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        retryable: bool | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


class ApiError(HelperError):
    """A sanitized Pinboard transport or API error."""


def _invalid_response() -> ApiError:
    return ApiError(
        "Pinboard returned an invalid response.",
        "invalid_response",
        True,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def error_response(error: HelperError) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": False, "error": error.message}
    if error.code is not None:
        response["code"] = error.code
    if error.retryable is not None:
        response["retryable"] = error.retryable
    return response


def parse_auth_token(value: Any) -> tuple[str, str]:
    """Validate a Pinboard username:TOKEN credential."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise HelperError(
            "The Pinboard token must use the username:TOKEN format.",
            "invalid_token",
        )
    username, token_part = value.split(":", 1)
    if (
        not username
        or not token_part
        or len(username) > TOKEN_USERNAME_LIMIT
        or len(token_part) > TOKEN_SECRET_LIMIT
        or any(character.isspace() for character in username)
        or any(character.isspace() for character in token_part)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in username)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in token_part)
    ):
        raise HelperError(
            "The Pinboard token must use the username:TOKEN format.",
            "invalid_token",
        )
    return username, token_part


class CredentialStore:
    """Read and write the token without exposing it to process arguments."""

    def __init__(self, runner: Callable[..., Any] = subprocess.run) -> None:
        self.runner = runner

    def _run(self, arguments: Sequence[str], input_value: str | None = None) -> Any:
        try:
            return self.runner(
                list(arguments),
                input=input_value,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=API_TIMEOUT,
                check=False,
            )
        except Exception:
            raise HelperError(
                "Secure token storage is unavailable.",
                "secret_storage_unavailable",
            ) from None

    def _lookup(self, attributes: Sequence[str]) -> str | None:
        result = self._run(("secret-tool", "lookup", *attributes))
        if result.returncode == 1:
            if self._has_stderr(result):
                raise HelperError(
                    "Unable to read the Pinboard token from secure storage.",
                    "secret_storage_error",
                )
            return None
        if result.returncode != 0:
            raise HelperError(
                "Unable to read the Pinboard token from secure storage.",
                "secret_storage_error",
            )
        output = result.stdout
        if isinstance(output, bytes):
            try:
                output = output.decode("utf-8")
            except UnicodeDecodeError:
                raise HelperError(
                    "Unable to read the Pinboard token from secure storage.",
                    "secret_storage_error",
                ) from None
        value = output or ""
        if value.endswith("\n"):
            value = value[:-1]
            if value.endswith("\r"):
                value = value[:-1]
        return value or None

    @staticmethod
    def _has_stderr(result: Any) -> bool:
        stderr = getattr(result, "stderr", "")
        return bool(stderr)

    def save(self, token: Any) -> str:
        username, _ = parse_auth_token(token)
        result = self._run(
            (
                "secret-tool",
                "store",
                "--label",
                SECRET_LABEL,
                *CANONICAL_ATTRIBUTES,
            ),
            token,
        )
        if result.returncode != 0:
            raise HelperError(
                "Unable to save the Pinboard token securely.",
                "secret_storage_error",
            )
        return username

    def resolve(self, migrate_legacy: bool = True) -> tuple[str, str, bool] | None:
        canonical = self._lookup(CANONICAL_ATTRIBUTES)
        if canonical is not None:
            try:
                username, _ = parse_auth_token(canonical)
            except HelperError:
                raise HelperError(
                    "The stored Pinboard token is invalid.",
                    "invalid_stored_token",
                ) from None
            return canonical, username, False

        if not migrate_legacy:
            return None
        legacy = self._lookup(LEGACY_ATTRIBUTES)
        if legacy is None:
            return None
        try:
            username, _ = parse_auth_token(legacy)
        except HelperError:
            raise HelperError(
                "The legacy Pinboard token is invalid.",
                "invalid_stored_token",
            ) from None
        self.save(legacy)
        return legacy, username, True

    def clear(self) -> None:
        failed = False
        for attributes in (CANONICAL_ATTRIBUTES, LEGACY_ATTRIBUTES):
            try:
                result = self._run(("secret-tool", "clear", *attributes))
                if result.returncode == 1 and not self._has_stderr(result):
                    continue
                if result.returncode != 0:
                    failed = True
            except Exception:
                failed = True
        if failed:
            raise HelperError(
                "Unable to clear the Pinboard token from secure storage.",
                "secret_storage_error",
            )


def default_state_directory() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    if root:
        return Path(root).expanduser() / "omapin"
    return Path.home() / ".local" / "state" / "omapin"


class StateDirectory:
    """Permission-hardened state files with atomic replacement and flock locks."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_state_directory()
        try:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not stat.S_ISDIR(self.path.lstat().st_mode):
                raise OSError("state path is not a directory")
            os.chmod(self.path, 0o700)
        except OSError:
            raise HelperError(
                "Omapin's local state directory is unavailable.",
                "state_unavailable",
            ) from None

    def file(self, name: str) -> Path:
        return self.path / name

    def read_text(self, name: str) -> str | None:
        path = self.file(name)
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise HelperError(
                "Omapin's local state is unavailable.",
                "state_unavailable",
            ) from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > STATE_FILE_LIMIT:
                raise OSError("state file is not a bounded regular file")
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                content = stream.read(STATE_FILE_LIMIT + 1)
                if len(content) > STATE_FILE_LIMIT:
                    raise OSError("state file is oversized")
                return content
        except (OSError, UnicodeError):
            raise HelperError(
                "Omapin's local state is unavailable.",
                "state_unavailable",
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def atomic_write(self, name: str, content: str) -> None:
        if len(content.encode("utf-8")) > STATE_FILE_LIMIT:
            raise HelperError(
                "Omapin's local state is too large.",
                "state_unavailable",
            )
        destination = self.file(name)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{name}.",
            dir=self.path,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
            os.chmod(destination, 0o600, follow_symlinks=False)
            directory_descriptor = os.open(
                self.path,
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception as error:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            if isinstance(error, HelperError):
                raise
            raise HelperError(
                "Omapin's local state is unavailable.",
                "state_unavailable",
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def locked(self, name: str) -> Iterator[None]:
        return _StateLock(self.file(name))


class _StateLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor = -1

    def __enter__(self) -> None:
        try:
            self.descriptor = os.open(
                self.path,
                os.O_RDWR
                | os.O_CREAT
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            if not stat.S_ISREG(os.fstat(self.descriptor).st_mode):
                raise OSError("lock is not a regular file")
            os.fchmod(self.descriptor, 0o600)
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        except OSError:
            if self.descriptor >= 0:
                os.close(self.descriptor)
                self.descriptor = -1
            raise HelperError(
                "Omapin's local state is unavailable.",
                "state_unavailable",
            ) from None

    def __exit__(self, exception_type: Any, exception: Any, traceback: Any) -> None:
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)
            self.descriptor = -1


_EXPLICIT_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def normalize_url(value: Any, add_missing_scheme: bool = True) -> str:
    """Trim and validate an HTTP(S) URL, optionally adding https://."""
    if not isinstance(value, str):
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
    candidate = value.strip()
    if not candidate or len(candidate) > 65536:
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in candidate
    ):
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")

    if candidate.startswith("//"):
        if not add_missing_scheme:
            raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
        candidate = "https:" + candidate
    elif not _EXPLICIT_SCHEME.match(candidate):
        if not add_missing_scheme:
            raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", candidate) and not re.match(
            r"^[^/:]+:\d+(?:/|$)", candidate
        ):
            raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
        candidate = "https://" + candidate

    try:
        parts = urllib.parse.urlsplit(candidate)
        hostname = parts.hostname
        parts.port
    except ValueError:
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url") from None
    scheme = parts.scheme.lower()
    if (
        scheme not in ("http", "https")
        or not hostname
        or set(hostname) == {"."}
    ):
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
    return urllib.parse.urlunsplit(
        (scheme, parts.netloc, parts.path, parts.query, parts.fragment)
    )


def validate_bookmark_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise HelperError("The bookmark payload must be an object.", "invalid_payload")

    url = normalize_url(payload.get("url"))
    title_value = payload.get("title")
    if not isinstance(title_value, str):
        raise HelperError("The bookmark title is required.", "invalid_title")
    title = title_value.strip()
    if not title or len(title) > 255:
        raise HelperError(
            "The bookmark title must contain 1 to 255 characters.",
            "invalid_title",
        )

    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 65536:
        raise HelperError(
            "Bookmark notes must contain at most 65536 characters.",
            "invalid_notes",
        )

    tags_value = payload.get("tags", [])
    if not isinstance(tags_value, list) or len(tags_value) > 100:
        raise HelperError("Bookmarks may have at most 100 tags.", "invalid_tags")
    tags: list[str] = []
    seen_tags: set[str] = set()
    for tag in tags_value:
        if (
            not isinstance(tag, str)
            or not tag
            or len(tag) > 255
            or "," in tag
            or any(character.isspace() for character in tag)
        ):
            raise HelperError(
                "Each tag must be nonblank, at most 255 characters, and contain no commas or whitespace.",
                "invalid_tags",
            )
        if tag not in seen_tags:
            tags.append(tag)
            seen_tags.add(tag)

    private = payload.get("private", True)
    read_later = payload.get("readLater", False)
    if not isinstance(private, bool) or not isinstance(read_later, bool):
        raise HelperError(
            "private and readLater must be JSON booleans.",
            "invalid_payload",
        )

    intent = payload.get("intent", "create")
    if intent not in ("create", "update"):
        raise HelperError(
            "Bookmark intent must be create or update.",
            "invalid_intent",
        )

    return {
        "url": url,
        "title": title,
        "notes": notes,
        "tags": tags,
        "private": private,
        "readLater": read_later,
        "intent": intent,
    }


def _api_payload(body: bytes | str) -> Any:
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise _invalid_response() from None
    elif isinstance(body, str):
        text = body
    else:
        raise _invalid_response()
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError, ValueError):
        try:
            root = ElementTree.fromstring(text)
        except (ElementTree.ParseError, TypeError):
            raise _invalid_response() from None
        if root.tag.rsplit("}", 1)[-1].lower() != "result":
            raise _invalid_response()
        code = root.attrib.get("code")
        if not code:
            raise _invalid_response()
        return {"result_code": code}


def _result_error(result_code: Any) -> ApiError | None:
    if not isinstance(result_code, str):
        return _invalid_response()
    normalized = " ".join(
        result_code.lower().replace("_", " ").replace("-", " ").split()
    )
    if normalized == "done":
        return None
    if "rate limit" in normalized or "too many" in normalized:
        return ApiError(
            "Pinboard rate-limited the request.",
            "rate_limited",
            True,
        )
    if any(
        phrase in normalized
        for phrase in (
            "access denied",
            "unauthorized",
            "forbidden",
            "authentication",
            "auth token",
            "token",
        )
    ):
        return ApiError(
            "Pinboard rejected the stored token.",
            "authentication_failed",
            False,
        )
    if any(
        phrase in normalized
        for phrase in (
            "timeout",
            "timed out",
            "tempor",
            "try again",
            "overload",
            "unavailable",
            "server error",
            "internal error",
            "service busy",
            "something went wrong",
        )
    ):
        return ApiError(
            "Pinboard is temporarily unavailable.",
            "api_unavailable",
            True,
        )
    if "already exists" in normalized:
        return ApiError(
            "This bookmark already exists.",
            "bookmark_exists",
            False,
        )
    if any(
        phrase in normalized
        for phrase in ("invalid", "missing", "bad request", "not found")
    ):
        return ApiError(
            "Pinboard rejected the bookmark data.",
            "invalid_request",
            False,
        )
    return _invalid_response()


def _raise_read_result_error(payload: Mapping[str, Any]) -> None:
    if set(payload) != {"result_code"}:
        return
    error = _result_error(payload["result_code"])
    if error is None:
        raise _invalid_response()
    raise error


def parse_add_response(body: bytes | str) -> None:
    payload = _api_payload(body)
    if not isinstance(payload, Mapping) or set(payload) != {"result_code"}:
        raise _invalid_response()
    error = _result_error(payload["result_code"])
    if error is not None:
        raise error


def _response_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    values: Sequence[Any] = value[: SUGGESTION_ENTRY_LIMIT * 4]
    tags: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if (
            not tag
            or len(tag) > 255
            or "," in tag
            or any(character.isspace() for character in tag)
            or tag in seen
        ):
            continue
        tags.append(tag)
        seen.add(tag)
    return tags


def _strict_response_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Sequence[Any] = value.split()
    elif isinstance(value, list):
        values = value
    else:
        raise _invalid_response()
    if len(values) > 100:
        raise _invalid_response()
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 255
            or "," in value
            or any(character.isspace() for character in value)
        ):
            raise _invalid_response()
        if value not in seen:
            tags.append(value)
            seen.add(value)
    return tags


def parse_get_response(body: bytes | str) -> dict[str, Any] | None:
    payload = _api_payload(body)
    if not isinstance(payload, Mapping):
        raise _invalid_response()
    if "posts" not in payload:
        _raise_read_result_error(payload)
        raise _invalid_response()
    posts = payload.get("posts")
    if not isinstance(posts, list):
        raise _invalid_response()
    if not posts:
        return None
    post = posts[0]
    if not isinstance(post, Mapping):
        raise _invalid_response()
    href = post.get("href")
    title = post.get("description")
    notes = post.get("extended")
    timestamp = post.get("time")
    tags_value = post.get("tags") if "tags" in post else post.get("tag")
    shared = post.get("shared")
    read_later = post.get("toread")
    if not isinstance(href, str) or not href or href != href.strip():
        raise _invalid_response()
    try:
        normalize_url(href, add_missing_scheme=False)
    except HelperError:
        raise _invalid_response() from None
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        raise _invalid_response()
    if not isinstance(notes, str) or len(notes) > 65536:
        raise _invalid_response()
    tags = _strict_response_tags(tags_value)
    if shared not in ("yes", "no") or read_later not in ("yes", "no"):
        raise _invalid_response()
    if not isinstance(timestamp, str):
        raise _invalid_response()
    return {
        "url": href,
        "title": title,
        "notes": notes,
        "tags": tags,
        "private": shared == "no",
        "readLater": read_later == "yes",
        "time": timestamp,
    }


def parse_suggest_response(body: bytes | str) -> tuple[list[str], list[str]]:
    payload = _api_payload(body)
    if isinstance(payload, Mapping):
        _raise_read_result_error(payload)
        entries: Sequence[Any] = (payload,)
    elif isinstance(payload, list):
        entries = payload
    else:
        raise _invalid_response()
    if len(entries) > SUGGESTION_ENTRY_LIMIT:
        raise _invalid_response()

    recommended: list[str] = []
    popular: list[str] = []
    mappings: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        mappings.append(entry)
        for child in entry.values():
            if isinstance(child, Mapping):
                mappings.append(child)
        if len(mappings) > SUGGESTION_ENTRY_LIMIT:
            raise _invalid_response()
    for entry in mappings:
        for tag in _response_tags(entry.get("recommended", [])):
            if tag not in recommended:
                recommended.append(tag)
            if len(recommended) >= SUGGESTION_TAG_LIMIT:
                break
        if len(recommended) >= SUGGESTION_TAG_LIMIT:
            break
    for entry in mappings:
        for tag in _response_tags(entry.get("popular", [])):
            if tag not in popular:
                popular.append(tag)
            if len(recommended) + len(popular) >= SUGGESTION_TAG_LIMIT:
                break
        if len(recommended) + len(popular) >= SUGGESTION_TAG_LIMIT:
            break
    return recommended, popular


def parse_tags_response(body: bytes | str) -> list[str]:
    payload = _api_payload(body)
    if not isinstance(payload, Mapping):
        raise ApiError(
            "Pinboard returned an invalid response.",
            "invalid_response",
            True,
        )
    if set(payload) == {"result_code"}:
        try:
            float(payload["result_code"])
        except (TypeError, ValueError, OverflowError):
            _raise_read_result_error(payload)

    counts: list[tuple[str, float]] = []
    for name, count in payload.items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or "," in name
            or any(character.isspace() for character in name)
            or isinstance(count, bool)
        ):
            raise _invalid_response()
        try:
            numeric_count = float(count)
        except (TypeError, ValueError, OverflowError):
            raise _invalid_response() from None
        if not math.isfinite(numeric_count) or numeric_count < 0:
            raise _invalid_response()
        counts.append((name, numeric_count))
    counts.sort(key=lambda item: (-item[1], item[0]))
    return [name for name, _ in counts[:TAG_VOCABULARY_LIMIT]]


def parse_retry_after(value: Any, now: float | None = None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        try:
            seconds = int(text)
        except ValueError:
            return None
        return float(min(MAX_RETRY_AFTER, seconds))
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return None
        timestamp = parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    current = time.time() if now is None else now
    delay = timestamp - current
    if not math.isfinite(delay):
        return None
    return min(float(MAX_RETRY_AFTER), max(0.0, delay))


def classify_http_error(status: int, retry_after: float | None = None) -> ApiError:
    if 300 <= status <= 399:
        return ApiError(
            "Pinboard redirected the request.",
            "redirect_rejected",
            False,
        )
    if status == 429:
        return ApiError(
            "Pinboard rate-limited the request.",
            "rate_limited",
            True,
            retry_after,
        )
    if status in (408, 425) or 500 <= status <= 599:
        return ApiError(
            "Pinboard is temporarily unavailable.",
            "api_unavailable",
            True,
            retry_after,
        )
    if status in (401, 403):
        return ApiError(
            "Pinboard rejected the stored token.",
            "authentication_failed",
            False,
        )
    return ApiError(
        "Pinboard rejected the request.",
        "api_rejected",
        False,
    )


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    return str(value) if value is not None else None


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


class ApiRequester:
    """Serialize and pace authenticated Pinboard GET requests."""

    def __init__(
        self,
        state: StateDirectory,
        opener: Any = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.state = state
        self.opener = (
            opener
            if opener is not None
            else urllib.request.build_opener(_RejectRedirectHandler()).open
        )
        self.clock = clock
        self.sleeper = sleeper

    def get(self, token: str, endpoint: str, parameters: Mapping[str, Any]) -> bytes:
        if endpoint not in ("posts/add", "posts/get", "posts/suggest", "tags/get"):
            raise ApiError("Invalid Pinboard API operation.", "invalid_operation", False)
        query = dict(parameters)
        query["format"] = "json"
        query["auth_token"] = token
        authenticated_url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"

        with self.state.locked("api.lock"):
            previous = self._read_timestamp("api-last-call")
            current = self.clock()
            stored_not_before = self._read_timestamp("api-not-before")
            not_before = min(stored_not_before, current + MAX_RETRY_AFTER)
            if stored_not_before > not_before:
                self.state.atomic_write("api-not-before", f"{not_before:.9f}\n")
            if previous > current:
                paced_at = current + API_INTERVAL
            else:
                paced_at = previous + API_INTERVAL
            wait = max(0.0, paced_at - current, not_before - current)
            if wait > API_TIMEOUT:
                raise ApiError(
                    "Pinboard rate-limited the request.",
                    "rate_limited",
                    True,
                    wait,
                )
            if wait:
                self.sleeper(wait)
            started = self.clock()
            self.state.atomic_write("api-last-call", f"{started:.9f}\n")
            try:
                return self._open(authenticated_url)
            except ApiError as error:
                if error.retryable and (
                    error.code == "rate_limited" or error.retry_after is not None
                ):
                    retry_after = error.retry_after
                    if retry_after is None or not math.isfinite(retry_after):
                        retry_after = float(INITIAL_RETRY_DELAY)
                    blocked_until = self.clock() + max(0.0, retry_after)
                    self.state.atomic_write(
                        "api-not-before",
                        f"{max(not_before, blocked_until):.9f}\n",
                    )
                raise
            finally:
                # Waiting from completion is conservative and guarantees that
                # request starts remain at least three seconds apart.
                completed = self.clock()
                self.state.atomic_write("api-last-call", f"{completed:.9f}\n")

    def _read_timestamp(self, name: str) -> float:
        text = self.state.read_text(name)
        try:
            value = float(text) if text is not None else 0.0
        except ValueError:
            return 0.0
        return value if math.isfinite(value) else 0.0

    def defer(self, retry_after: float | None = None) -> None:
        delay = retry_after
        if delay is None or not math.isfinite(delay):
            delay = float(INITIAL_RETRY_DELAY)
        delay = min(float(MAX_RETRY_AFTER), max(0.0, delay))
        with self.state.locked("api.lock"):
            current = self.clock()
            not_before = self._read_timestamp("api-not-before")
            self.state.atomic_write(
                "api-not-before",
                f"{max(not_before, current + delay):.9f}\n",
            )

    def _open(self, authenticated_url: str) -> bytes:
        request = urllib.request.Request(
            authenticated_url,
            headers={"User-Agent": "Omapin/4"},
        )
        try:
            open_method = (
                self.opener.open if hasattr(self.opener, "open") else self.opener
            )
            response = open_method(request, timeout=API_TIMEOUT)
            try:
                get_url = getattr(response, "geturl", None)
                final_url = get_url() if get_url is not None else None
                if final_url is not None and final_url != authenticated_url:
                    raise ApiError(
                        "Pinboard redirected the request.",
                        "redirect_rejected",
                        False,
                    )
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if not 200 <= int(status) <= 299:
                    retry_after = parse_retry_after(
                        _header(getattr(response, "headers", None), "Retry-After"),
                        self.clock(),
                    )
                    raise classify_http_error(int(status), retry_after)
                body = response.read(API_BODY_LIMIT + 1)
                if len(body) > API_BODY_LIMIT:
                    raise ApiError(
                        "Pinboard returned an oversized response.",
                        "invalid_response",
                        True,
                    )
                return body
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        except urllib.error.HTTPError as error:
            try:
                retry_after = parse_retry_after(
                    _header(error.headers, "Retry-After"),
                    self.clock(),
                )
                classified = classify_http_error(error.code, retry_after)
            finally:
                error.close()
            raise classified from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ApiError(
                "Unable to reach Pinboard.",
                "network_error",
                True,
            ) from None
        except ApiError:
            raise
        except Exception:
            raise ApiError(
                "Unable to complete the Pinboard request.",
                "network_error",
                True,
            ) from None


class PinboardApi:
    def __init__(self, token: str, requester: ApiRequester) -> None:
        self.token = token
        self.requester = requester

    def _parse(self, body: bytes, parser: Callable[[bytes], Any]) -> Any:
        try:
            return parser(body)
        except ApiError as error:
            if error.code == "rate_limited":
                self.requester.defer(error.retry_after)
            raise

    def add(self, payload: Mapping[str, Any]) -> None:
        parameters = {
            "url": payload["url"],
            "description": payload["title"],
            "extended": payload["notes"],
            "tags": " ".join(payload["tags"]),
            "replace": "yes" if payload["intent"] == "update" else "no",
            "shared": "no" if payload["private"] else "yes",
            "toread": "yes" if payload["readLater"] else "no",
        }
        self._parse(
            self.requester.get(self.token, "posts/add", parameters),
            parse_add_response,
        )

    def get(self, url: str) -> dict[str, Any] | None:
        return self._parse(
            self.requester.get(self.token, "posts/get", {"url": url}),
            parse_get_response,
        )

    def suggest(self, url: str) -> tuple[list[str], list[str]]:
        return self._parse(
            self.requester.get(self.token, "posts/suggest", {"url": url}),
            parse_suggest_response,
        )

    def tags(self) -> list[str]:
        return self._parse(
            self.requester.get(self.token, "tags/get", {}),
            parse_tags_response,
        )


def retry_delay(attempts: int, retry_after: float | None = None) -> float:
    """Return the delay after a failed queued attempt."""
    delay = RETRY_DELAYS[min(max(attempts, 0), len(RETRY_DELAYS) - 1)]
    if retry_after is not None and math.isfinite(retry_after):
        delay = max(float(delay), min(float(MAX_RETRY_AFTER), retry_after))
    return float(delay)


class QueueStore:
    """Account-scoped durable queue."""

    def __init__(
        self,
        state: StateDirectory,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.state = state
        self.clock = clock
        self.id_factory = id_factory

    def _load(self) -> list[dict[str, Any]]:
        text = self.state.read_text("queue.json")
        if text is None:
            return []
        try:
            document = json.loads(text, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt") from None
        if (
            not isinstance(document, Mapping)
            or type(document.get("version")) is not int
            or document.get("version") != 1
            or not isinstance(document.get("items"), list)
        ):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        items: list[dict[str, Any]] = []
        identifiers: set[str] = set()
        for candidate in document["items"]:
            item = self._validate_item(candidate)
            if item["id"] in identifiers:
                raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
            identifiers.add(item["id"])
            items.append(item)
        return items

    @staticmethod
    def _validate_item(candidate: Any) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        required = {
            "id",
            "account",
            "url",
            "title",
            "notes",
            "tags",
            "private",
            "readLater",
            "intent",
            "status",
            "attempts",
            "nextAttemptAt",
            "lastError",
            "createdAt",
            "updatedAt",
        }
        if not required.issubset(candidate):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        item_id = candidate["id"]
        account = candidate["account"]
        if (
            not isinstance(item_id, str)
            or not item_id
            or any(character.isspace() for character in item_id)
            or not isinstance(account, str)
            or not account
            or ":" in account
            or any(character.isspace() for character in account)
        ):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        status = candidate["status"]
        attempts = candidate["attempts"]
        if status not in ("pending", "failed") or type(attempts) is not int:
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        if attempts < 0 or attempts > MAX_RETRY_ATTEMPTS:
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")

        payload_source = {
            key: candidate[key]
            for key in (
                "url",
                "title",
                "notes",
                "tags",
                "private",
                "readLater",
                "intent",
            )
        }
        try:
            validated_payload = validate_bookmark_payload(payload_source)
        except HelperError:
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt") from None
        if any(candidate[key] != value for key, value in validated_payload.items()):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")

        def finite_timestamp(value: Any) -> bool:
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and value >= 0
            )

        if (
            not finite_timestamp(candidate["createdAt"])
            or not finite_timestamp(candidate["updatedAt"])
            or not isinstance(candidate["lastError"], str)
        ):
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        next_attempt = candidate["nextAttemptAt"]
        if status == "pending":
            if attempts >= MAX_RETRY_ATTEMPTS or not finite_timestamp(next_attempt):
                raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        elif attempts < 1 or next_attempt is not None:
            raise HelperError("The bookmark queue is unreadable.", "queue_corrupt")
        return dict(candidate)

    def _write(self, items: list[dict[str, Any]]) -> None:
        document = {"version": 1, "items": items}
        self.state.atomic_write(
            "queue.json",
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n",
        )

    @staticmethod
    def _for_account(items: Sequence[dict[str, Any]], account: str) -> list[dict[str, Any]]:
        selected = [dict(item) for item in items if item.get("account") == account]
        selected.sort(
            key=lambda item: (float(item["createdAt"]), str(item["id"]))
        )
        return selected

    def list(self, account: str) -> list[dict[str, Any]]:
        with self.state.locked("queue.lock"):
            return self._for_account(self._load(), account)

    def enqueue(
        self,
        account: str,
        payload: Mapping[str, Any],
        error: ApiError,
    ) -> list[dict[str, Any]]:
        validated_payload = validate_bookmark_payload(payload)
        with self.state.locked("queue.lock"):
            items = self._load()
            self._upsert(items, account, validated_payload, error)
            self._write(items)
            return self._for_account(items, account)

    def _upsert(
        self,
        items: list[dict[str, Any]],
        account: str,
        payload: Mapping[str, Any],
        error: ApiError,
    ) -> None:
        now = self.clock()
        retry_after = error.retry_after
        if retry_after is None or not math.isfinite(retry_after):
            retry_after = 0.0
        delay = max(
            float(INITIAL_RETRY_DELAY),
            min(float(MAX_RETRY_AFTER), retry_after),
        )
        matches = [
            item
            for item in items
            if item["account"] == account and item["url"] == payload["url"]
        ]
        if matches:
            current = min(matches, key=lambda item: (item["createdAt"], item["id"]))
            items[:] = [
                item
                for item in items
                if item is current
                or not (
                    item["account"] == account and item["url"] == payload["url"]
                )
            ]
        else:
            current = {
                "id": self.id_factory(),
                "account": account,
                "createdAt": now,
            }
            items.append(current)
        current.update(
            {
                **dict(payload),
                "status": "pending",
                "attempts": 0,
                "nextAttemptAt": now + delay,
                "lastError": error.message,
                "updatedAt": now,
            }
        )

    def submit(
        self,
        account: str,
        payload: Mapping[str, Any],
        submitter: Callable[[Mapping[str, Any]], None],
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Submit while excluding stale queued delivery for the same URL."""
        validated_payload = validate_bookmark_payload(payload)
        with self.state.locked("queue.lock"):
            items = self._load()
            try:
                submitter(validated_payload)
            except ApiError as error:
                if not error.retryable:
                    raise
                self._upsert(items, account, validated_payload, error)
                self._write(items)
                return True, self._for_account(items, account)

            remaining = [
                item
                for item in items
                if not (
                    item["account"] == account
                    and item["url"] == validated_payload["url"]
                )
            ]
            if len(remaining) != len(items):
                self._write(remaining)
            return False, self._for_account(remaining, account)

    def remove(self, account: str, item_id: Any) -> list[dict[str, Any]]:
        if not isinstance(item_id, str) or not item_id:
            raise HelperError("A queue item id is required.", "invalid_queue_item")
        with self.state.locked("queue.lock"):
            items = self._load()
            remaining = [
                item
                for item in items
                if not (item.get("account") == account and item.get("id") == item_id)
            ]
            if len(remaining) == len(items):
                raise HelperError("Queue item not found.", "queue_item_not_found")
            self._write(remaining)
            return self._for_account(remaining, account)

    @staticmethod
    def _payload(item: Mapping[str, Any]) -> dict[str, Any]:
        return validate_bookmark_payload(
            {
                key: item.get(key)
                for key in (
                    "url",
                    "title",
                    "notes",
                    "tags",
                    "private",
                    "readLater",
                    "intent",
                )
            }
        )

    def retry_one(
        self,
        account: str,
        submitter: Callable[[Mapping[str, Any]], None],
        force: bool = False,
        item_id: Any = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if item_id is not None and (not isinstance(item_id, str) or not item_id):
            raise HelperError("A valid queue item id is required.", "invalid_queue_item")

        with self.state.locked("queue.lock"):
            items = self._load()
            now = self.clock()
            changed = False
            if force:
                matched = False
                for item in items:
                    if item.get("account") != account:
                        continue
                    if item_id is not None and item.get("id") != item_id:
                        continue
                    if item.get("status") not in ("pending", "failed"):
                        continue
                    matched = True
                    if item.get("status") == "failed":
                        item["attempts"] = 0
                    item["status"] = "pending"
                    item["nextAttemptAt"] = now
                    item["updatedAt"] = now
                    changed = True
                if item_id is not None and not matched:
                    raise HelperError("Queue item not found.", "queue_item_not_found")

            due = [
                item
                for item in items
                if item.get("account") == account
                and item.get("status") == "pending"
                and isinstance(item.get("nextAttemptAt"), (int, float))
                and float(item["nextAttemptAt"]) <= now
                and (item_id is None or item.get("id") == item_id)
            ]
            due.sort(
                key=lambda item: (
                    float(item.get("nextAttemptAt", 0)),
                    float(item.get("createdAt", 0)),
                )
            )
            if not due:
                if changed:
                    self._write(items)
                return {"processed": False, "result": "none"}, self._for_account(
                    items, account
                )

            item = due[0]
            try:
                submitter(self._payload(item))
            except ApiError as error:
                failure = error
            except HelperError:
                failure = ApiError(
                    "The queued bookmark is invalid.",
                    "invalid_queue_item",
                    False,
                )
            except Exception:
                failure = ApiError(
                    "Unable to complete the Pinboard request.",
                    "network_error",
                    True,
                )
            else:
                item_id_value = item.get("id")
                item_url = item.get("url")
                items = [
                    candidate
                    for candidate in items
                    if not (
                        candidate.get("account") == account
                        and candidate.get("url") == item_url
                    )
                ]
                self._write(items)
                return {
                    "processed": True,
                    "result": "submitted",
                    "id": item_id_value,
                }, self._for_account(items, account)

            attempts = int(item.get("attempts", 0)) + 1
            item["attempts"] = attempts
            item["lastError"] = failure.message
            item["updatedAt"] = self.clock()
            if not failure.retryable or attempts >= MAX_RETRY_ATTEMPTS:
                item["status"] = "failed"
                item["nextAttemptAt"] = None
                result_name = "failed"
            else:
                item["status"] = "pending"
                item["nextAttemptAt"] = item["updatedAt"] + retry_delay(
                    attempts,
                    failure.retry_after,
                )
                result_name = "rescheduled"
            self._write(items)
            return {
                "processed": True,
                "result": result_name,
                "id": item.get("id"),
            }, self._for_account(items, account)


class _FirstTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.complete = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if not self.complete and tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if self.in_title and tag.lower() == "title":
            self.in_title = False
            self.complete = True

    def handle_data(self, data: str) -> None:
        if self.in_title and not self.complete:
            self.parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.parts).split())


def _title_url(value: Any, add_missing_scheme: bool) -> str:
    normalized = normalize_url(value, add_missing_scheme=add_missing_scheme)
    parts = urllib.parse.urlsplit(normalized)
    if parts.username is not None or parts.password is not None:
        raise HelperError("A valid HTTP or HTTPS URL is required.", "invalid_url")
    return normalized


class _LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        redirects = int(getattr(request, "_omapin_redirects", 0)) + 1
        if redirects > TITLE_REDIRECT_LIMIT:
            raise urllib.error.HTTPError(new_url, code, "redirect limit", headers, file_pointer)
        try:
            normalized = _title_url(new_url, add_missing_scheme=False)
        except HelperError:
            raise urllib.error.HTTPError(
                new_url,
                code,
                "invalid redirect",
                headers,
                file_pointer,
            ) from None
        source_scheme = urllib.parse.urlsplit(request.full_url).scheme.lower()
        destination_scheme = urllib.parse.urlsplit(normalized).scheme.lower()
        if source_scheme == "https" and destination_scheme == "http":
            raise urllib.error.HTTPError(
                new_url,
                code,
                "HTTPS downgrade",
                headers,
                file_pointer,
            )
        redirected = super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is not None:
            setattr(redirected, "_omapin_redirects", redirects)
        return redirected


def fetch_page_title(url: Any, opener: Any = None) -> str:
    try:
        normalized = _title_url(url, add_missing_scheme=True)
    except HelperError:
        # A syntactically valid URL with userinfo is intentionally not fetched.
        normalize_url(url)
        return ""
    if opener is None:
        opener = urllib.request.build_opener(_LimitedRedirectHandler())
    request = urllib.request.Request(normalized, headers={"User-Agent": "Omapin/4"})
    try:
        open_method = opener.open if hasattr(opener, "open") else opener
        response = open_method(request, timeout=TITLE_TIMEOUT)
        try:
            get_url = getattr(response, "geturl", None)
            if get_url is not None:
                response_url = get_url()
            else:
                response_url = None
            if response_url is not None:
                try:
                    final_url = _title_url(response_url, add_missing_scheme=False)
                except HelperError:
                    return ""
                initial_scheme = urllib.parse.urlsplit(normalized).scheme.lower()
                final_scheme = urllib.parse.urlsplit(final_url).scheme.lower()
                if initial_scheme == "https" and final_scheme == "http":
                    return ""
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if not 200 <= int(status) <= 299:
                return ""
            headers = getattr(response, "headers", None)
            content_type = (_header(headers, "Content-Type") or "").lower()
            media_type = content_type.split(";", 1)[0].strip()
            if media_type not in ("text/html", "application/xhtml+xml"):
                return ""
            content_length = _header(headers, "Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > TITLE_BODY_LIMIT:
                        return ""
                except ValueError:
                    return ""
            body = response.read(TITLE_BODY_LIMIT + 1)
            if len(body) > TITLE_BODY_LIMIT:
                return ""
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    except urllib.error.HTTPError as error:
        error.close()
        return ""
    except HelperError:
        raise
    except Exception:
        return ""

    charset = "utf-8"
    match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type)
    if match:
        charset = match.group(1)
    try:
        text = body.decode(charset, "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    parser = _FirstTitleParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return ""
    return parser.title


_SENSITIVE_MIME_HINTS = (
    "password",
    "secret",
    "sensitive",
    "credential",
    "keepass",
    "bitwarden",
    "1password",
)


def _completed_stdout(result: Any) -> str:
    output = getattr(result, "stdout", "") or ""
    if isinstance(output, bytes):
        return output.decode("utf-8")
    return str(output)


def _bounded_clipboard_command(
    arguments: Sequence[str],
) -> tuple[int, bytes, bool]:
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    selector: selectors.BaseSelector | None = None
    stdout: Any = None
    deadline = time.monotonic() + 2.0
    oversized = False
    try:
        process = subprocess.Popen(
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout = process.stdout
        if stdout is None:
            return -1, b"", False
        os.set_blocking(stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(arguments), 2)
            events = selector.select(remaining)
            if not events:
                if process.poll() is not None:
                    break
                raise subprocess.TimeoutExpired(list(arguments), 2)
            chunk = os.read(
                stdout.fileno(),
                min(4096, CLIPBOARD_LIMIT + 1 - len(output)),
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > CLIPBOARD_LIMIT:
                oversized = True
                process.kill()
                break
        remaining = max(0.0, deadline - time.monotonic())
        return_code = process.wait(timeout=remaining)
        return return_code, bytes(output), oversized
    except Exception:
        return -1, b"", oversized
    finally:
        if selector is not None:
            selector.close()
        if stdout is not None:
            stdout.close()
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


def _clipboard_command(
    arguments: Sequence[str],
    runner: Callable[..., Any] | None,
) -> tuple[int, str, bool]:
    if runner is None:
        return_code, output, oversized = _bounded_clipboard_command(arguments)
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError:
            return -1, "", oversized
        return return_code, text, oversized
    result = runner(
        list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=2,
        check=False,
    )
    output = _completed_stdout(result)
    oversized = len(output.encode("utf-8")) > CLIPBOARD_LIMIT
    return result.returncode, output, oversized


def _clipboard_types(value: str) -> tuple[str, ...] | None:
    types = tuple(
        sorted(mime.strip().lower() for mime in value.splitlines() if mime.strip())
    )
    if any(hint in mime for mime in types for hint in _SENSITIVE_MIME_HINTS):
        return None
    if not any(
        mime == "text"
        or mime.startswith("text/")
        or mime in ("utf8_string", "string")
        for mime in types
    ):
        return None
    return types


def clipboard_url(runner: Callable[..., Any] | None = None) -> str:

    try:
        return_code, types_text, oversized = _clipboard_command(
            ("wl-paste", "--list-types"), runner
        )
        if return_code != 0 or oversized:
            return ""
        initial_types = _clipboard_types(types_text)
        if initial_types is None:
            return ""

        return_code, text, oversized = _clipboard_command(
            ("wl-paste", "--no-newline", "--type", "text"), runner
        )
        if return_code != 0 or oversized:
            return ""
        return_code, final_types_text, oversized = _clipboard_command(
            ("wl-paste", "--list-types"), runner
        )
        if return_code != 0 or oversized:
            return ""
        final_types = _clipboard_types(final_types_text)
        if final_types is None or final_types != initial_types:
            return ""
        return normalize_url(text, add_missing_scheme=False)
    except Exception:
        return ""


class PinboardHelper:
    """Operation dispatcher with injectable external boundaries."""

    def __init__(
        self,
        credentials: Any = None,
        state_directory: str | os.PathLike[str] | None = None,
        requester: ApiRequester | None = None,
        api_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        clipboard_reader: Callable[[], str] = clipboard_url,
        title_reader: Callable[[Any], str] = fetch_page_title,
    ) -> None:
        self.credentials = credentials if credentials is not None else CredentialStore()
        self.state = StateDirectory(state_directory)
        self.requester = requester or ApiRequester(
            self.state,
            clock=clock,
            sleeper=sleeper,
        )
        self.api_factory = api_factory or (
            lambda token: PinboardApi(token, self.requester)
        )
        self.queue = QueueStore(self.state, clock=clock)
        self.clipboard_reader = clipboard_reader
        self.title_reader = title_reader
        self._response_secrets: list[str] = []

    def _authentication(self) -> tuple[str, str, bool]:
        authentication = self.credentials.resolve(migrate_legacy=True)
        if authentication is None:
            raise HelperError(
                "A Pinboard token is required.",
                "not_authenticated",
                False,
            )
        self._response_secrets.append(authentication[0])
        return authentication

    def handle(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._response_secrets = []
        supplied_token = payload.get("token") if isinstance(payload, Mapping) else None
        if isinstance(supplied_token, str):
            self._response_secrets.append(supplied_token)
        try:
            if not isinstance(payload, Mapping):
                raise HelperError("The request payload must be an object.", "invalid_payload")
            response = self._handle(operation, payload)
        except HelperError as error:
            response = error_response(error)
        except Exception:
            response = error_response(
                HelperError("Internal helper error.", "internal_error", False)
            )
        secrets = self._response_secrets
        self._response_secrets = []
        return _redact(response, secrets)

    def _handle(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "status":
            authentication = self.credentials.resolve(migrate_legacy=True)
            if authentication is None:
                return {
                    "ok": True,
                    "tokenConfigured": False,
                    "authenticated": False,
                    "queue": [],
                }
            self._response_secrets.append(authentication[0])
            _, username, migrated = authentication
            return {
                "ok": True,
                "tokenConfigured": True,
                "authenticated": True,
                "username": username,
                "migrated": migrated,
                "queue": self.queue.list(username),
            }

        if operation == "save-token":
            token = payload.get("token")
            username, _ = parse_auth_token(token)
            queue = self.queue.list(username)
            username = self.credentials.save(token)
            return {
                "ok": True,
                "tokenConfigured": True,
                "authenticated": True,
                "username": username,
                "message": "Pinboard token saved.",
                "queue": queue,
            }

        if operation == "clear-token":
            self.credentials.clear()
            return {
                "ok": True,
                "tokenConfigured": False,
                "authenticated": False,
                "message": "Pinboard token cleared.",
                "queue": [],
            }

        if operation == "clipboard":
            return {"ok": True, "text": self.clipboard_reader()}

        if operation == "fetch-title":
            return {"ok": True, "title": self.title_reader(payload.get("url"))}

        if operation not in {
            "tags",
            "duplicate",
            "suggest",
            "submit",
            "queue-list",
            "queue-retry-due",
            "queue-retry-now",
            "queue-remove",
        }:
            raise HelperError("Unknown helper operation.", "unknown_operation")

        token, account, _ = self._authentication()
        api = self.api_factory(token)

        if operation == "tags":
            return {"ok": True, "tags": api.tags()}

        if operation == "duplicate":
            bookmark = api.get(normalize_url(payload.get("url")))
            return {
                "ok": True,
                "exists": bookmark is not None,
                "bookmark": bookmark,
            }

        if operation == "suggest":
            recommended, popular = api.suggest(normalize_url(payload.get("url")))
            return {
                "ok": True,
                "recommended": recommended,
                "popular": popular,
            }

        if operation == "submit":
            bookmark_payload = payload.get("payload", payload)
            bookmark = validate_bookmark_payload(bookmark_payload)
            queued, queue = self.queue.submit(account, bookmark, api.add)
            if queued:
                return {
                    "ok": True,
                    "queued": True,
                    "message": "Pinboard is unavailable; the bookmark was queued.",
                    "queue": queue,
                }
            action = "updated" if bookmark["intent"] == "update" else "saved"
            return {
                "ok": True,
                "queued": False,
                "message": f"Bookmark {action}.",
                "queue": queue,
            }

        if operation == "queue-list":
            return {"ok": True, "queue": self.queue.list(account)}

        if operation in ("queue-retry-due", "queue-retry-now"):
            result, queue = self.queue.retry_one(
                account,
                api.add,
                force=operation == "queue-retry-now",
                item_id=payload.get("id"),
            )
            messages = {
                "none": "No queued bookmark is due.",
                "submitted": "Queued bookmark submitted.",
                "rescheduled": "Queued bookmark retry rescheduled.",
                "failed": "Queued bookmark marked as failed.",
            }
            return {
                "ok": True,
                **result,
                "message": messages[result["result"]],
                "queue": queue,
            }

        if operation == "queue-remove":
            queue = self.queue.remove(account, payload.get("id"))
            return {
                "ok": True,
                "message": "Queued bookmark removed.",
                "queue": queue,
            }

        raise HelperError("Unknown helper operation.", "unknown_operation")


def _redact(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret:
                encoded_secrets = {
                    secret,
                    urllib.parse.quote(secret, safe=""),
                    urllib.parse.quote_plus(secret, safe=""),
                }
                for encoded_secret in sorted(encoded_secrets, key=len, reverse=True):
                    redacted = redacted.replace(encoded_secret, "[redacted]")
        return redacted
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    return value


def _read_protocol_line(input_stream: Any = None) -> str:
    stream = sys.stdin.buffer if input_stream is None else input_stream
    line = stream.readline(INPUT_LIMIT + 1)
    if isinstance(line, bytes):
        if len(line) > INPUT_LIMIT:
            raise HelperError("The JSON request is too large.", "request_too_large")
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError:
            raise HelperError("The request is not valid JSON.", "invalid_json") from None
    if not isinstance(line, str):
        raise HelperError("The request is not valid JSON.", "invalid_json")
    try:
        encoded = line.encode("utf-8")
    except UnicodeEncodeError:
        raise HelperError("The request is not valid JSON.", "invalid_json") from None
    if len(encoded) > INPUT_LIMIT:
        raise HelperError("The JSON request is too large.", "request_too_large")
    return line


def run_protocol(
    arguments: Sequence[str] | None = None,
    input_stream: Any = None,
    output_stream: Any = None,
    helper: PinboardHelper | None = None,
) -> int:
    argv = list(sys.argv if arguments is None else arguments)
    stdout = sys.stdout if output_stream is None else output_stream
    secrets: list[str] = []
    try:
        if len(argv) != 2:
            raise HelperError(
                "Exactly one helper operation is required.",
                "invalid_invocation",
            )
        line = _read_protocol_line(input_stream)
        if not line:
            raise HelperError("A JSON request object is required.", "invalid_json")
        try:
            payload = json.loads(
                line,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            raise HelperError("The request is not valid JSON.", "invalid_json") from None
        if not isinstance(payload, dict):
            raise HelperError("The JSON request must be an object.", "invalid_payload")
        token = payload.get("token")
        if isinstance(token, str):
            secrets.append(token)
        backend = helper if helper is not None else PinboardHelper()
        response = backend.handle(argv[1], payload)
    except HelperError as error:
        response = error_response(error)
    except Exception:
        response = error_response(
            HelperError("Internal helper error.", "internal_error", False)
        )

    response = _redact(response, secrets)
    try:
        encoded = json.dumps(
            response,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        encoded = json.dumps(
            {
                "ok": False,
                "error": "Internal helper error.",
                "code": "internal_error",
                "retryable": False,
            },
            separators=(",", ":"),
        )
    stdout.write(encoded + "\n")
    stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_protocol())
