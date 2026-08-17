import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "pinboard_helper.py"
SPEC = importlib.util.spec_from_file_location("pinboard_helper", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)

DISTINCTIVE_TOKEN = "alice:THIS_IS_A_DISTINCTIVE_SECRET_42"


class FakeClock:
    def __init__(self, current=1000.0):
        self.current = float(current)
        self.sleeps = []

    def __call__(self):
        return self.current

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.current += duration

    def advance(self, duration):
        self.current += duration


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, url=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.url = url
        self.closed = False
        self.read_amounts = []

    def read(self, amount=-1):
        self.read_amounts.append(amount)
        return self.body if amount < 0 else self.body[:amount]

    def close(self):
        self.closed = True

    def geturl(self):
        return self.url if self.url is not None else None


class TrackingHTTPError(urllib.error.HTTPError):
    def __init__(self, url, code, headers=None, reason="error"):
        self.stream = io.BytesIO()
        self.was_closed = False
        super().__init__(url, code, reason, headers or {}, self.stream)

    def close(self):
        self.was_closed = True
        super().close()


class FakeCredentials:
    def __init__(self, token=DISTINCTIVE_TOKEN):
        self.token = token
        self.cleared = False

    def resolve(self, migrate_legacy=True):
        if self.token is None:
            return None
        username, _ = helper.parse_auth_token(self.token)
        return self.token, username, False

    def save(self, token):
        username, _ = helper.parse_auth_token(token)
        self.token = token
        return username

    def clear(self):
        self.token = None
        self.cleared = True


class FakeApi:
    def __init__(self, add_error=None):
        self.add_error = add_error
        self.added = []
        self.bookmark = None
        self.suggestions = (["recommended"], ["popular"])
        self.tag_names = ["one"]

    def add(self, payload):
        self.added.append(dict(payload))
        if self.add_error is not None:
            raise self.add_error

    def get(self, url):
        self.got_url = url
        return self.bookmark

    def suggest(self, url):
        self.suggested_url = url
        return self.suggestions

    def tags(self):
        return self.tag_names


def bookmark(**overrides):
    payload = {
        "url": "example.com/article",
        "title": "An article",
        "notes": "Notes",
        "tags": ["python", "reading"],
        "private": True,
        "readLater": False,
        "intent": "create",
    }
    payload.update(overrides)
    return payload


def api_post(**overrides):
    post = {
        "href": "https://example.com/path",
        "description": "Title",
        "extended": "Notes",
        "tags": "one two",
        "shared": "no",
        "toread": "yes",
        "time": "2026-08-16T00:00:00Z",
    }
    post.update(overrides)
    return post


class UrlAndValidationTests(unittest.TestCase):
    def test_normalize_url_trims_and_adds_https(self):
        self.assertEqual(
            helper.normalize_url("  example.com/path?q=1  "),
            "https://example.com/path?q=1",
        )
        self.assertEqual(
            helper.normalize_url("HTTP://example.com"),
            "http://example.com",
        )
        self.assertEqual(
            helper.normalize_url("localhost:8080/path"),
            "https://localhost:8080/path",
        )

    def test_normalize_url_rejects_non_http_or_invalid_urls(self):
        invalid = (
            "",
            "ftp://example.com",
            "https://",
            "https://exa mple.com",
            "https://example.com/\x00hidden",
            "javascript:alert(1)",
            "http://example.com:99999",
            "https://.",
            "https://...",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(helper.HelperError):
                helper.normalize_url(value)

    def test_explicit_url_mode_does_not_add_a_scheme(self):
        with self.assertRaises(helper.HelperError):
            helper.normalize_url("example.com", add_missing_scheme=False)
        self.assertEqual(
            helper.normalize_url("https://example.com", add_missing_scheme=False),
            "https://example.com",
        )

    def test_normalize_url_retains_local_and_address_hosts(self):
        valid = (
            "http://localhost",
            "http://localhost:8080/path",
            "http://intranet/path",
            "http://127.0.0.1/path",
            "http://[::1]/path",
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertEqual(helper.normalize_url(value), value)

    def test_bookmark_validation_normalizes_and_deduplicates_tags(self):
        validated = helper.validate_bookmark_payload(
            bookmark(title="  Title  ", tags=["one", "two", "one"])
        )
        self.assertEqual(validated["url"], "https://example.com/article")
        self.assertEqual(validated["title"], "Title")
        self.assertEqual(validated["tags"], ["one", "two"])

    def test_bookmark_validation_enforces_limits_and_types(self):
        invalid_payloads = (
            bookmark(title=" "),
            bookmark(title="x" * 256),
            bookmark(notes="x" * 65537),
            bookmark(tags=["two words"]),
            bookmark(tags=["comma,tag"]),
            bookmark(tags=["x"] * 101),
            bookmark(private="yes"),
            bookmark(readLater=1),
            bookmark(intent="delete"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=list(payload)), self.assertRaises(helper.HelperError):
                helper.validate_bookmark_payload(payload)

    def test_auth_token_requires_two_nonwhitespace_parts(self):
        self.assertEqual(helper.parse_auth_token("alice:token"), ("alice", "token"))
        for value in (
            "alice",
            ":token",
            "alice:",
            "a:b:c",
            "a:two words",
            "a:\x00token",
            "a:\x7ftoken",
            "a" * (helper.TOKEN_USERNAME_LIMIT + 1) + ":token",
            "a:" + "x" * (helper.TOKEN_SECRET_LIMIT + 1),
            1,
        ):
            with self.subTest(value=value), self.assertRaises(helper.HelperError):
                helper.parse_auth_token(value)


class ApiParsingTests(unittest.TestCase):
    def test_add_parses_json_and_xml_result(self):
        self.assertIsNone(helper.parse_add_response('{"result_code":"done"}'))
        self.assertIsNone(helper.parse_add_response('{"result_code":"DoNe"}'))
        self.assertIsNone(helper.parse_add_response('<result code="done" />'))

    def test_add_accepts_only_done_as_success(self):
        bodies = (
            '"done"',
            '{"result":"done"}',
            '{"code":"done"}',
            '{"result_code":"ok"}',
            '{"result_code":"success"}',
            '{"result_code":"unknown server reply"}',
            '{"result_code":null}',
            '{"result_code":1}',
            "{}",
            "[]",
        )
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(helper.ApiError) as context:
                helper.parse_add_response(body)
            self.assertEqual(context.exception.code, "invalid_response")
            self.assertTrue(context.exception.retryable)

    def test_add_classifies_result_errors(self):
        with self.assertRaises(helper.ApiError) as context:
            helper.parse_add_response('{"result_code":"something went wrong"}')
        self.assertTrue(context.exception.retryable)
        with self.assertRaises(helper.ApiError) as context:
            helper.parse_add_response('<result code="item already exists" />')
        self.assertFalse(context.exception.retryable)
        self.assertEqual(context.exception.code, "bookmark_exists")

    def test_result_classification_prioritizes_auth_and_transient_failures(self):
        expectations = {
            "invalid auth token": ("authentication_failed", False),
            "request timeout": ("api_unavailable", True),
            "server error: invalid payload": ("api_unavailable", True),
            "service overloaded": ("api_unavailable", True),
            "missing url": ("invalid_request", False),
            "item not found": ("invalid_request", False),
            "unexpected response code": ("invalid_response", True),
        }
        for result, expected in expectations.items():
            with self.subTest(result=result), self.assertRaises(helper.ApiError) as context:
                helper.parse_add_response(json.dumps({"result_code": result}))
            self.assertEqual(
                (context.exception.code, context.exception.retryable), expected
            )

    def test_get_maps_a_bookmark_and_empty_posts(self):
        body = json.dumps(
            {
                "date": "2026-08-17T00:00:00Z",
                "posts": [
                    {
                        **api_post(),
                    }
                ],
            }
        )
        self.assertEqual(
            helper.parse_get_response(body),
            {
                "url": "https://example.com/path",
                "title": "Title",
                "notes": "Notes",
                "tags": ["one", "two"],
                "private": True,
                "readLater": True,
                "time": "2026-08-16T00:00:00Z",
            },
        )
        self.assertIsNone(helper.parse_get_response('{"posts":[]}'))

    def test_get_preserves_the_exact_bookmark_target_url(self):
        target = "https://example.com/?x=1&auth_token=unrelated-target-value"
        body = json.dumps(
            {
                "posts": [
                    api_post(href=target),
                ]
            }
        )
        parsed = helper.parse_get_response(body)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["url"], target)

    def test_get_rejects_incomplete_or_type_invalid_posts(self):
        invalid_posts = (
            {},
            api_post(href="not a url"),
            api_post(href=" https://example.com/path "),
            api_post(description=" "),
            api_post(description="x" * 256),
            api_post(extended=None),
            api_post(tags={"one": 1}),
            api_post(tags=["valid", 1]),
            api_post(shared="maybe"),
            api_post(shared=True),
            api_post(toread="YES"),
            api_post(time=None),
        )
        for post in invalid_posts:
            with self.subTest(post=post), self.assertRaises(helper.ApiError) as context:
                helper.parse_get_response(json.dumps({"posts": [post]}))
            self.assertEqual(context.exception.code, "invalid_response")
            self.assertTrue(context.exception.retryable)

    def test_get_rejects_invalid_utf8(self):
        with self.assertRaises(helper.ApiError) as context:
            helper.parse_get_response(b'{"posts":[]}\xff')
        self.assertEqual(context.exception.code, "invalid_response")
        self.assertTrue(context.exception.retryable)

    def test_get_accepts_empty_notes_and_tags_with_tag_fallback(self):
        empty = helper.parse_get_response(
            json.dumps({"posts": [api_post(extended="", tags="")]})
        )
        self.assertEqual(empty["notes"], "")
        self.assertEqual(empty["tags"], [])

        fallback_post = api_post()
        del fallback_post["tags"]
        fallback_post["tag"] = "fallback one"
        fallback = helper.parse_get_response(json.dumps({"posts": [fallback_post]}))
        self.assertEqual(fallback["tags"], ["fallback", "one"])

    def test_suggest_accepts_documented_list_and_mapping_shapes(self):
        recommended, popular = helper.parse_suggest_response(
            '[{"popular":["p1","p2"]},{"recommended":["r1","p2"]}]'
        )
        self.assertEqual(recommended, ["r1", "p2"])
        self.assertEqual(popular, ["p1", "p2"])
        self.assertEqual(
            helper.parse_suggest_response('{"recommended":["r"],"popular":["p"]}'),
            (["r"], ["p"]),
        )
        self.assertEqual(
            helper.parse_suggest_response(
                '{"wrapper":{"recommended":["nested"],"popular":["deep"]}}'
            ),
            (["nested"], ["deep"]),
        )

    def test_suggest_ignores_junk_instead_of_turning_it_into_tags(self):
        recommended, popular = helper.parse_suggest_response(
            json.dumps(
                [
                    {"recommended": ["valid", 1, {}, "two words", "comma,tag"]},
                    {"popular": {"not": "a list"}},
                    {"popular": "junk words"},
                    "junk",
                ]
            )
        )
        self.assertEqual(recommended, ["valid"])
        self.assertEqual(popular, [])

    def test_suggestions_are_bounded_before_returning_to_qml(self):
        recommended, popular = helper.parse_suggest_response(
            json.dumps(
                [
                    {"recommended": [f"recommended-{index}" for index in range(100)]},
                    {"popular": [f"popular-{index}" for index in range(100)]},
                ]
            )
        )
        self.assertEqual(len(recommended) + len(popular), helper.SUGGESTION_TAG_LIMIT)
        self.assertEqual(recommended[0], "recommended-0")

    def test_xml_result_errors_are_supported_for_read_endpoints(self):
        for parser in (
            helper.parse_get_response,
            helper.parse_suggest_response,
            helper.parse_tags_response,
        ):
            with self.subTest(parser=parser.__name__), self.assertRaises(
                helper.ApiError
            ) as context:
                parser('<result code="access denied" />')
            self.assertFalse(context.exception.retryable)

    def test_read_endpoints_reject_write_success_envelopes(self):
        for parser in (
            helper.parse_get_response,
            helper.parse_suggest_response,
            helper.parse_tags_response,
        ):
            with self.subTest(parser=parser.__name__), self.assertRaises(
                helper.ApiError
            ) as context:
                parser('{"result_code":"done"}')
            self.assertEqual(context.exception.code, "invalid_response")

    def test_tags_are_sorted_by_count_then_name(self):
        self.assertEqual(
            helper.parse_tags_response(
                '{"z":"2","a":"2","middle":"5","code":"1"}'
            ),
            ["middle", "a", "z", "code"],
        )

    def test_tags_preserve_result_code_name_and_bound_the_vocabulary(self):
        self.assertEqual(
            helper.parse_tags_response('{"result_code":1}'),
            ["result_code"],
        )
        payload = {
            f"tag-{index}": index
            for index in range(helper.TAG_VOCABULARY_LIMIT + 5)
        }
        parsed = helper.parse_tags_response(json.dumps(payload))
        self.assertEqual(len(parsed), helper.TAG_VOCABULARY_LIMIT)
        self.assertEqual(parsed[0], f"tag-{helper.TAG_VOCABULARY_LIMIT + 4}")
        self.assertNotIn("tag-0", parsed)

    def test_pinboard_add_sends_exact_v1_parameters(self):
        class Requester:
            def get(self, token, endpoint, parameters):
                self.call = token, endpoint, parameters
                return b'{"result_code":"done"}'

        requester = Requester()
        api = helper.PinboardApi(DISTINCTIVE_TOKEN, requester)
        api.add(helper.validate_bookmark_payload(bookmark(intent="update", private=False, readLater=True)))
        token, endpoint, parameters = requester.call
        self.assertEqual(token, DISTINCTIVE_TOKEN)
        self.assertEqual(endpoint, "posts/add")
        self.assertEqual(
            parameters,
            {
                "url": "https://example.com/article",
                "description": "An article",
                "extended": "Notes",
                "tags": "python reading",
                "replace": "yes",
                "shared": "yes",
                "toread": "yes",
            },
        )


class RetryAndTransportTests(unittest.TestCase):
    def test_http_classification_and_retry_after(self):
        limited = helper.classify_http_error(429, 90)
        self.assertTrue(limited.retryable)
        self.assertEqual(limited.retry_after, 90)
        self.assertTrue(helper.classify_http_error(503).retryable)
        self.assertFalse(helper.classify_http_error(400).retryable)
        self.assertEqual(helper.parse_retry_after("12"), 12)
        self.assertIsNone(helper.parse_retry_after("12.5"))
        self.assertIsNone(helper.parse_retry_after("-1"))
        self.assertIsNone(helper.parse_retry_after("1e100"))
        self.assertEqual(
            helper.parse_retry_after(str(helper.MAX_RETRY_AFTER + 1)),
            helper.MAX_RETRY_AFTER,
        )
        self.assertIsNone(helper.parse_retry_after("9" * 5000))
        self.assertIsNone(helper.parse_retry_after("Infinity"))
        self.assertIsNone(helper.parse_retry_after("NaN"))

    def test_retry_backoff_sequence_and_retry_after_floor(self):
        self.assertEqual(
            [helper.retry_delay(attempt) for attempt in range(6)],
            [15.0, 45.0, 180.0, 900.0, 3600.0, 3600.0],
        )
        self.assertEqual(helper.retry_delay(1, 120), 120)
        self.assertEqual(helper.retry_delay(2, 1e100), helper.MAX_RETRY_AFTER)

    def test_requester_persists_and_applies_three_second_pacing(self):
        clock = FakeClock(100)
        opened = []

        def opener(request, timeout):
            opened.append((request.full_url, timeout))
            return FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as temporary:
            state = helper.StateDirectory(temporary)
            requester = helper.ApiRequester(state, opener, clock, clock.sleep)
            requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(clock.sleeps, [3.0])
            self.assertEqual([call[1] for call in opened], [10, 10])
            self.assertEqual(
                stat.S_IMODE(os.stat(Path(temporary) / "api-last-call").st_mode),
                0o600,
            )

    def test_429_retry_after_is_sanitized_and_classified(self):
        clock = FakeClock(100)
        http_error = None

        def opener(request, timeout):
            nonlocal http_error
            http_error = TrackingHTTPError(
                request.full_url,
                429,
                {"Retry-After": "75"},
                "contains " + DISTINCTIVE_TOKEN,
            )
            raise http_error

        with tempfile.TemporaryDirectory() as temporary:
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary), opener, clock, clock.sleep
            )
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertTrue(context.exception.retryable)
            self.assertEqual(context.exception.retry_after, 75)
            self.assertNotIn(DISTINCTIVE_TOKEN, str(context.exception))
            self.assertTrue(http_error.was_closed)

    def test_429_not_before_blocks_a_different_endpoint(self):
        clock = FakeClock(100)
        calls = []
        rate_limit = None

        def opener(request, timeout):
            nonlocal rate_limit
            calls.append(request.full_url)
            if len(calls) == 1:
                rate_limit = TrackingHTTPError(
                    request.full_url,
                    429,
                    {"Retry-After": "20"},
                )
                raise rate_limit
            return FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as temporary:
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary), opener, clock, clock.sleep
            )
            with self.assertRaises(helper.ApiError):
                requester.get(DISTINCTIVE_TOKEN, "posts/get", {"url": "https://a"})
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(context.exception.code, "rate_limited")
            self.assertEqual(context.exception.retry_after, 20)
            self.assertEqual(clock.sleeps, [])
            self.assertIn("posts/get", calls[0])
            self.assertEqual(len(calls), 1)
            clock.advance(20)
            requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertIn("tags/get", calls[1])
            self.assertTrue(rate_limit.was_closed)
            self.assertEqual(
                float((Path(temporary) / "api-not-before").read_text()),
                120.0,
            )

    def test_503_retry_after_blocks_a_different_endpoint(self):
        clock = FakeClock(100)
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise TrackingHTTPError(
                    request.full_url,
                    503,
                    {"Retry-After": "20"},
                )
            return FakeResponse(b"{}")

        with tempfile.TemporaryDirectory() as temporary:
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary), opener, clock, clock.sleep
            )
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "posts/get", {"url": "https://a"})
            self.assertEqual(context.exception.code, "api_unavailable")
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(context.exception.code, "rate_limited")
            self.assertEqual(len(calls), 1)
            clock.advance(20)
            requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(len(calls), 2)

    def test_persisted_not_before_is_capped(self):
        clock = FakeClock(100)
        with tempfile.TemporaryDirectory() as temporary:
            state = helper.StateDirectory(temporary)
            state.atomic_write("api-not-before", "1e100\n")
            requester = helper.ApiRequester(
                state,
                lambda request, timeout: FakeResponse(b"{}"),
                clock,
                clock.sleep,
            )

            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(context.exception.retry_after, helper.MAX_RETRY_AFTER)
            self.assertEqual(
                float(state.read_text("api-not-before")),
                clock() + helper.MAX_RETRY_AFTER,
            )

            clock.advance(helper.MAX_RETRY_AFTER)
            self.assertEqual(requester.get(DISTINCTIVE_TOKEN, "tags/get", {}), b"{}")

    def test_body_rate_limit_persists_global_backoff(self):
        clock = FakeClock(100)

        def opener(request, timeout):
            return FakeResponse(b'{"result_code":"rate limit"}')

        with tempfile.TemporaryDirectory() as temporary:
            state = helper.StateDirectory(temporary)
            requester = helper.ApiRequester(state, opener, clock, clock.sleep)
            api = helper.PinboardApi(DISTINCTIVE_TOKEN, requester)

            with self.assertRaises(helper.ApiError) as context:
                api.add(helper.validate_bookmark_payload(bookmark()))
            self.assertEqual(context.exception.code, "rate_limited")
            self.assertEqual(
                float(state.read_text("api-not-before")),
                clock() + helper.INITIAL_RETRY_DELAY,
            )

    def test_api_response_redirect_is_rejected_and_closed(self):
        redirected = FakeResponse(
            b"{}",
            url="https://elsewhere.example/result",
        )

        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary),
                lambda request, timeout: redirected,
                clock,
                clock.sleep,
            )
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(context.exception.code, "redirect_rejected")
            self.assertTrue(redirected.closed)

        redirect_error = None

        def redirecting_opener(request, timeout):
            nonlocal redirect_error
            redirect_error = TrackingHTTPError(request.full_url, 302)
            raise redirect_error

        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary),
                redirecting_opener,
                clock,
                clock.sleep,
            )
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            self.assertEqual(context.exception.code, "redirect_rejected")
            self.assertTrue(redirect_error.was_closed)

    def test_unknown_network_exception_cannot_leak_token_or_authenticated_url(self):
        def opener(request, timeout):
            raise RuntimeError(request.full_url + " " + DISTINCTIVE_TOKEN)

        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            requester = helper.ApiRequester(
                helper.StateDirectory(temporary), opener, clock, clock.sleep
            )
            with self.assertRaises(helper.ApiError) as context:
                requester.get(DISTINCTIVE_TOKEN, "tags/get", {})
            rendered = json.dumps(helper.error_response(context.exception))
            self.assertNotIn(DISTINCTIVE_TOKEN, rendered)
            self.assertNotIn("auth_token=", rendered)


class StateDirectoryTests(unittest.TestCase):
    def test_rejects_symlinked_state_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            alias = root / "alias"
            alias.symlink_to(target, target_is_directory=True)

            with self.assertRaises(helper.HelperError) as context:
                helper.StateDirectory(alias)
            self.assertEqual(context.exception.code, "state_unavailable")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_rejects_symlinked_and_special_state_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = helper.StateDirectory(root / "state")
            victim = root / "victim"
            victim.write_text("unchanged", encoding="utf-8")
            victim.chmod(0o644)
            state.file("queue.json").symlink_to(victim)
            state.file("queue.lock").symlink_to(victim)

            with self.assertRaises(helper.HelperError):
                state.read_text("queue.json")
            with self.assertRaises(helper.HelperError):
                with state.locked("queue.lock"):
                    pass
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)

            fifo = state.file("fifo")
            os.mkfifo(fifo)
            with self.assertRaises(helper.HelperError):
                state.read_text("fifo")

    def test_rejects_oversized_state_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = helper.StateDirectory(temporary)
            state.file("large").write_bytes(b"")
            os.truncate(state.file("large"), helper.STATE_FILE_LIMIT + 1)
            with self.assertRaises(helper.HelperError) as context:
                state.read_text("large")
            self.assertEqual(context.exception.code, "state_unavailable")


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = FakeClock(500)
        self.state = helper.StateDirectory(self.temporary.name)
        identifiers = iter(("one", "two", "three", "four"))
        self.queue = helper.QueueStore(
            self.state,
            clock=self.clock,
            id_factory=lambda: next(identifiers),
        )
        self.transient = helper.ApiError("Temporary failure.", "temporary", True)

    def test_queue_deduplicates_only_within_an_account(self):
        first = helper.validate_bookmark_payload(bookmark())
        updated = helper.validate_bookmark_payload(bookmark(title="Updated"))
        self.queue.enqueue("alice", first, self.transient)
        alice = self.queue.enqueue("alice", updated, self.transient)
        self.queue.enqueue("bob", first, self.transient)
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0]["title"], "Updated")
        self.assertEqual(len(self.queue.list("bob")), 1)
        self.assertNotEqual(alice[0]["id"], self.queue.list("bob")[0]["id"])
        queue_path = Path(self.temporary.name) / "queue.json"
        self.assertEqual(stat.S_IMODE(queue_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(self.temporary.name).stat().st_mode), 0o700)

    def test_enqueue_supersedes_a_failed_item_with_fresh_payload_and_backoff(self):
        queued = self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), self.transient
        )
        item_id = queued[0]["id"]

        def reject(_payload):
            raise helper.ApiError("Permanent.", "invalid_request", False)

        _, failed = self.queue.retry_one("alice", reject, force=True)
        self.assertEqual(failed[0]["status"], "failed")
        refreshed = self.queue.enqueue(
            "alice",
            helper.validate_bookmark_payload(
                bookmark(title="Newest title", intent="update")
            ),
            self.transient,
        )
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0]["id"], item_id)
        self.assertEqual(refreshed[0]["title"], "Newest title")
        self.assertEqual(refreshed[0]["status"], "pending")
        self.assertEqual(refreshed[0]["attempts"], 0)
        self.assertEqual(refreshed[0]["nextAttemptAt"], self.clock() + 15)

    def test_initial_retry_obeys_longer_retry_after(self):
        error = helper.ApiError("Rate limited.", "rate_limited", True, 80)
        queued = self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), error
        )
        self.assertEqual(queued[0]["nextAttemptAt"], 580)

    def test_due_retry_processes_at_most_one_item(self):
        first = helper.validate_bookmark_payload(bookmark(url="one.example"))
        second = helper.validate_bookmark_payload(bookmark(url="two.example"))
        self.queue.enqueue("alice", first, self.transient)
        self.queue.enqueue("alice", second, self.transient)
        self.queue.enqueue("bob", first, self.transient)
        self.clock.advance(15)
        submitted = []
        result, remaining = self.queue.retry_one("alice", submitted.append)
        self.assertEqual(result["result"], "submitted")
        self.assertEqual(len(submitted), 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(len(self.queue.list("bob")), 1)

    def test_successful_retry_removes_legacy_duplicates_for_the_same_url(self):
        self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), self.transient
        )
        document = json.loads(self.state.read_text("queue.json"))
        failed_duplicate = copy.deepcopy(document["items"][0])
        failed_duplicate.update(
            {
                "id": "legacy-failed",
                "title": "Stale title",
                "status": "failed",
                "attempts": 1,
                "nextAttemptAt": None,
            }
        )
        document["items"].append(failed_duplicate)
        self.state.atomic_write("queue.json", json.dumps(document))
        self.clock.advance(15)

        submitted = []
        result, remaining = self.queue.retry_one("alice", submitted.append)
        self.assertEqual(result["result"], "submitted")
        self.assertEqual(len(submitted), 1)
        self.assertEqual(remaining, [])

    def test_transient_retry_is_rescheduled_with_backoff(self):
        payload = helper.validate_bookmark_payload(bookmark())
        self.queue.enqueue("alice", payload, self.transient)
        self.clock.advance(15)

        def fail(_payload):
            raise helper.ApiError("Still unavailable.", "temporary", True, 30)

        result, items = self.queue.retry_one("alice", fail)
        self.assertEqual(result["result"], "rescheduled")
        self.assertEqual(items[0]["attempts"], 1)
        self.assertEqual(items[0]["nextAttemptAt"], self.clock() + 45)

    def test_item_becomes_failed_after_twelve_retry_attempts_and_remains_visible(self):
        self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), self.transient
        )

        def fail(_payload):
            raise self.transient

        for _ in range(helper.MAX_RETRY_ATTEMPTS):
            result, items = self.queue.retry_one("alice", fail, force=True)
        self.assertEqual(result["result"], "failed")
        self.assertEqual(items[0]["status"], "failed")
        self.assertEqual(items[0]["attempts"], helper.MAX_RETRY_ATTEMPTS)
        self.assertIsNone(items[0]["nextAttemptAt"])

    def test_retry_now_reactivates_failed_item_and_remove_is_account_scoped(self):
        queued = self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), self.transient
        )
        item_id = queued[0]["id"]

        def reject(_payload):
            raise helper.ApiError("Rejected.", "invalid_request", False)

        _, items = self.queue.retry_one("alice", reject, force=True, item_id=item_id)
        self.assertEqual(items[0]["status"], "failed")
        result, items = self.queue.retry_one(
            "alice", lambda _payload: None, force=True, item_id=item_id
        )
        self.assertEqual(result["result"], "submitted")
        self.assertEqual(items, [])

        bob_item = self.queue.enqueue(
            "bob", helper.validate_bookmark_payload(bookmark()), self.transient
        )[0]
        with self.assertRaises(helper.HelperError):
            self.queue.remove("alice", bob_item["id"])
        self.assertEqual(len(self.queue.list("bob")), 1)

    def test_mixed_valid_and_invalid_queue_items_fail_closed(self):
        self.queue.enqueue(
            "alice", helper.validate_bookmark_payload(bookmark()), self.transient
        )
        document = json.loads(self.state.read_text("queue.json"))
        valid = document["items"][0]

        malformed_items = []
        malformed_items.append({})
        for field, value in (
            ("account", ""),
            ("status", "processing"),
            ("attempts", True),
            ("title", " "),
            ("createdAt", "yesterday"),
            ("lastError", 1),
            ("nextAttemptAt", None),
        ):
            candidate = copy.deepcopy(valid)
            candidate["id"] = "bad-" + field
            candidate[field] = value
            malformed_items.append(candidate)
        failed_with_due_time = copy.deepcopy(valid)
        failed_with_due_time.update(
            {"id": "bad-failed", "status": "failed", "attempts": 1}
        )
        malformed_items.append(failed_with_due_time)

        for candidate in malformed_items:
            with self.subTest(candidate=candidate):
                mixed = {"version": 1, "items": [valid, candidate]}
                self.state.atomic_write("queue.json", json.dumps(mixed))
                with self.assertRaises(helper.HelperError) as context:
                    self.queue.list("alice")
                self.assertEqual(context.exception.code, "queue_corrupt")

    def test_queue_requires_version_one_and_finite_json_values(self):
        for raw in (
            '{"version":2,"items":[]}',
            '{"version":1,"items":[{"createdAt":Infinity}]}',
        ):
            with self.subTest(raw=raw):
                self.state.atomic_write("queue.json", raw)
                with self.assertRaises(helper.HelperError) as context:
                    self.queue.list("alice")
                self.assertEqual(context.exception.code, "queue_corrupt")

    def test_corrupt_queue_is_not_rewritten_and_blocks_submit(self):
        raw = '{"version":1,"items":[{}, {"unrelated":"record"}]}\n'
        self.state.atomic_write("queue.json", raw)
        calls = []
        with self.assertRaises(helper.HelperError) as context:
            self.queue.submit(
                "alice",
                helper.validate_bookmark_payload(bookmark()),
                calls.append,
            )
        self.assertEqual(context.exception.code, "queue_corrupt")
        self.assertEqual(calls, [])
        self.assertEqual(self.state.read_text("queue.json"), raw)


class CredentialStoreTests(unittest.TestCase):
    @staticmethod
    def result(returncode=0, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_status_migrates_legacy_with_exact_attributes_and_stdin(self):
        calls = []
        results = iter(
            (
                self.result(returncode=1),
                self.result(stdout=DISTINCTIVE_TOKEN + "\n"),
                self.result(),
            )
        )

        def runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return next(results)

        store = helper.CredentialStore(runner)
        resolved = store.resolve(migrate_legacy=True)
        self.assertEqual(resolved, (DISTINCTIVE_TOKEN, "alice", True))
        self.assertEqual(
            calls[0][0], ["secret-tool", "lookup", *helper.CANONICAL_ATTRIBUTES]
        )
        self.assertEqual(
            calls[1][0], ["secret-tool", "lookup", *helper.LEGACY_ATTRIBUTES]
        )
        self.assertEqual(
            calls[2][0],
            [
                "secret-tool",
                "store",
                "--label",
                "Omapin Pinboard token",
                *helper.CANONICAL_ATTRIBUTES,
            ],
        )
        self.assertEqual(calls[2][1]["input"], DISTINCTIVE_TOKEN)
        self.assertNotIn(DISTINCTIVE_TOKEN, calls[2][0])
        self.assertFalse(any(call[0][1] == "clear" for call in calls))

    def test_canonical_token_prevents_legacy_lookup(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return self.result(stdout=DISTINCTIVE_TOKEN + "\n")

        resolved = helper.CredentialStore(runner).resolve()
        self.assertEqual(resolved, (DISTINCTIVE_TOKEN, "alice", False))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ["secret-tool", "lookup", *helper.CANONICAL_ATTRIBUTES])

    def test_lookup_does_not_hide_whitespace_stored_in_a_token(self):
        def runner(arguments, **kwargs):
            return self.result(stdout=DISTINCTIVE_TOKEN + "\n\n")

        with self.assertRaises(helper.HelperError) as context:
            helper.CredentialStore(runner).resolve()
        self.assertEqual(context.exception.code, "invalid_stored_token")

    def test_lookup_status_one_with_stderr_is_an_operational_error(self):
        def runner(arguments, **kwargs):
            return self.result(returncode=1, stderr="keyring locked " + DISTINCTIVE_TOKEN)

        with self.assertRaises(helper.HelperError) as context:
            helper.CredentialStore(runner).resolve()
        self.assertEqual(context.exception.code, "secret_storage_error")
        self.assertNotIn(DISTINCTIVE_TOKEN, str(context.exception))

    def test_clear_removes_canonical_and_legacy_items(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return self.result()

        helper.CredentialStore(runner).clear()
        self.assertEqual(
            calls,
            [
                ["secret-tool", "clear", *helper.CANONICAL_ATTRIBUTES],
                ["secret-tool", "clear", *helper.LEGACY_ATTRIBUTES],
            ],
        )

    def test_clear_still_attempts_legacy_after_a_canonical_failure(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            if len(calls) == 1:
                raise OSError(DISTINCTIVE_TOKEN)
            return self.result()

        with self.assertRaises(helper.HelperError):
            helper.CredentialStore(runner).clear()
        self.assertEqual(calls[1], ["secret-tool", "clear", *helper.LEGACY_ATTRIBUTES])

    def test_clear_status_one_is_idempotent_only_with_empty_stderr(self):
        calls = []

        def missing_runner(arguments, **kwargs):
            calls.append(arguments)
            return self.result(returncode=1, stderr="")

        helper.CredentialStore(missing_runner).clear()
        self.assertEqual(len(calls), 2)

        locked_calls = []

        def locked_runner(arguments, **kwargs):
            locked_calls.append(arguments)
            if len(locked_calls) == 1:
                return self.result(
                    returncode=1,
                    stderr="keyring locked " + DISTINCTIVE_TOKEN,
                )
            return self.result(returncode=1, stderr="")

        with self.assertRaises(helper.HelperError) as context:
            helper.CredentialStore(locked_runner).clear()
        self.assertEqual(context.exception.code, "secret_storage_error")
        self.assertNotIn(DISTINCTIVE_TOKEN, str(context.exception))
        self.assertEqual(len(locked_calls), 2)

    def test_secret_tool_errors_do_not_expose_stderr(self):
        def runner(arguments, **kwargs):
            return self.result(returncode=2, stderr=DISTINCTIVE_TOKEN)

        with self.assertRaises(helper.HelperError) as context:
            helper.CredentialStore(runner).resolve()
        self.assertNotIn(DISTINCTIVE_TOKEN, str(context.exception))


class PublicHelpersTests(unittest.TestCase):
    def test_fetch_title_reads_html_only_and_collapses_whitespace(self):
        response = FakeResponse(
            b"<html><head><title>  First\n title &amp; more </title><title>Second</title></head></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        title = helper.fetch_page_title("example.com", opener=lambda request, timeout: response)
        self.assertEqual(title, "First title & more")
        self.assertEqual(response.read_amounts, [helper.TITLE_BODY_LIMIT + 1])
        self.assertTrue(response.closed)

    def test_fetch_title_returns_empty_for_non_html_or_network_error(self):
        response = FakeResponse(b"data", headers={"Content-Type": "application/json"})
        self.assertEqual(
            helper.fetch_page_title("https://example.com", opener=lambda request, timeout: response),
            "",
        )

        def fail(request, timeout):
            raise RuntimeError(DISTINCTIVE_TOKEN)

        self.assertEqual(helper.fetch_page_title("https://example.com", opener=fail), "")

    def test_fetch_title_rejects_userinfo_downgrades_and_oversized_pages(self):
        self.assertEqual(
            helper.fetch_page_title("https://user:password@example.com"),
            "",
        )

        downgrade = FakeResponse(
            b"<title>Unsafe</title>",
            headers={"Content-Type": "text/html"},
            url="http://example.com/final",
        )
        self.assertEqual(
            helper.fetch_page_title(
                "https://example.com",
                opener=lambda request, timeout: downgrade,
            ),
            "",
        )
        self.assertTrue(downgrade.closed)

        userinfo = FakeResponse(
            b"<title>Unsafe</title>",
            headers={"Content-Type": "text/html"},
            url="https://user:password@example.com/final",
        )
        self.assertEqual(
            helper.fetch_page_title(
                "https://example.com",
                opener=lambda request, timeout: userinfo,
            ),
            "",
        )
        oversized = FakeResponse(
            b"<title>Too large</title>",
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(helper.TITLE_BODY_LIMIT + 1),
            },
        )
        self.assertEqual(
            helper.fetch_page_title(
                "https://example.com",
                opener=lambda request, timeout: oversized,
            ),
            "",
        )
        self.assertEqual(oversized.read_amounts, [])

        unknown_size = FakeResponse(
            b"x" * (helper.TITLE_BODY_LIMIT + 1),
            headers={"Content-Type": "text/html"},
        )
        self.assertEqual(
            helper.fetch_page_title(
                "https://example.com",
                opener=lambda request, timeout: unknown_size,
            ),
            "",
        )
        self.assertEqual(unknown_size.read_amounts, [helper.TITLE_BODY_LIMIT + 1])

    def test_title_redirect_handler_limits_hops_and_rejects_downgrade(self):
        handler = helper._LimitedRedirectHandler()
        request = urllib.request.Request("http://example.com/start")
        for index in range(helper.TITLE_REDIRECT_LIMIT):
            request = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                f"http://example.com/{index}",
            )
        with self.assertRaises(urllib.error.HTTPError) as context:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://example.com/too-many",
            )
        context.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as context:
            handler.redirect_request(
                urllib.request.Request("https://example.com/start"),
                None,
                302,
                "Found",
                {},
                "http://example.com/downgrade",
            )
        context.exception.close()

    def test_fetch_title_closes_http_errors(self):
        error = TrackingHTTPError("https://example.com", 302)

        def fail(request, timeout):
            raise error

        self.assertEqual(helper.fetch_page_title("https://example.com", opener=fail), "")
        self.assertTrue(error.was_closed)

    def test_clipboard_returns_only_explicit_urls(self):
        outputs = iter(
            (
                SimpleNamespace(returncode=0, stdout="text/plain\n"),
                SimpleNamespace(returncode=0, stdout="https://example.com/path"),
                SimpleNamespace(returncode=0, stdout="text/plain\n"),
            )
        )
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return next(outputs)

        self.assertEqual(helper.clipboard_url(runner), "https://example.com/path")
        self.assertEqual(
            calls,
            [
                ["wl-paste", "--list-types"],
                ["wl-paste", "--no-newline", "--type", "text"],
                ["wl-paste", "--list-types"],
            ],
        )

    def test_clipboard_skips_sensitive_mime_without_reading_contents(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return SimpleNamespace(
                returncode=0,
                stdout="text/plain\napplication/x-keepass-password\n",
            )

        self.assertEqual(helper.clipboard_url(runner), "")
        self.assertEqual(len(calls), 1)

    def test_clipboard_discards_oversized_content(self):
        outputs = iter(
            (
                SimpleNamespace(returncode=0, stdout="text/plain\n"),
                SimpleNamespace(returncode=0, stdout="x" * (helper.CLIPBOARD_LIMIT + 1)),
            )
        )
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return next(outputs)

        self.assertEqual(helper.clipboard_url(runner), "")
        self.assertEqual(len(calls), 2)

    def test_production_clipboard_reader_kills_oversized_output_while_reading(self):
        return_code, output, oversized = helper._bounded_clipboard_command(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 20000)",
            )
        )
        self.assertTrue(oversized)
        self.assertLessEqual(len(output), helper.CLIPBOARD_LIMIT + 1)
        self.assertNotEqual(return_code, 0)

    def test_production_clipboard_reader_rejects_invalid_utf8(self):
        return_code, text, oversized = helper._clipboard_command(
            (
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'\\xff')",
            ),
            None,
        )
        self.assertEqual(return_code, -1)
        self.assertEqual(text, "")
        self.assertFalse(oversized)

    def test_clipboard_discards_content_when_selection_types_change(self):
        outputs = iter(
            (
                SimpleNamespace(returncode=0, stdout="text/plain\n"),
                SimpleNamespace(returncode=0, stdout="https://example.com"),
                SimpleNamespace(returncode=0, stdout="text/html\n"),
            )
        )

        def runner(arguments, **kwargs):
            return next(outputs)

        self.assertEqual(helper.clipboard_url(runner), "")


class OperationAndProtocolTests(unittest.TestCase):
    def make_backend(self, api=None, credentials=None, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        selected_api = api or FakeApi()
        backend = helper.PinboardHelper(
            credentials=credentials or FakeCredentials(),
            state_directory=temporary.name,
            api_factory=lambda token: selected_api,
            clock=FakeClock(),
            sleeper=lambda duration: None,
            **kwargs,
        )
        return backend, selected_api

    def test_status_save_clear_and_public_operations(self):
        credentials = FakeCredentials()
        backend, _ = self.make_backend(
            credentials=credentials,
            clipboard_reader=lambda: "https://clipboard.example",
            title_reader=lambda url: "Fetched " + url,
        )
        status = backend.handle("status", {})
        self.assertTrue(status["authenticated"])
        self.assertTrue(status["tokenConfigured"])
        self.assertNotIn(DISTINCTIVE_TOKEN, json.dumps(status))
        self.assertEqual(
            backend.handle("clipboard", {}),
            {"ok": True, "text": "https://clipboard.example"},
        )
        self.assertEqual(
            backend.handle("fetch-title", {"url": "https://example.com"})["title"],
            "Fetched https://example.com",
        )
        self.assertTrue(backend.handle("clear-token", {})["ok"])
        self.assertTrue(credentials.cleared)

    def test_save_token_does_not_change_credentials_when_queue_is_corrupt(self):
        credentials = FakeCredentials()
        backend, _ = self.make_backend(credentials=credentials)
        backend.state.atomic_write("queue.json", "not-json")

        response = backend.handle("save-token", {"token": "bob:new-token"})

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "queue_corrupt")
        self.assertEqual(credentials.token, DISTINCTIVE_TOKEN)

    def test_duplicate_suggest_and_tags_response_shapes(self):
        api = FakeApi()
        api.bookmark = {"url": "https://example.com"}
        backend, _ = self.make_backend(api=api)
        duplicate = backend.handle("duplicate", {"url": "example.com"})
        self.assertTrue(duplicate["exists"])
        self.assertEqual(api.got_url, "https://example.com")
        self.assertEqual(
            backend.handle("suggest", {"url": "example.com"})["recommended"],
            ["recommended"],
        )
        self.assertEqual(backend.handle("tags", {})["tags"], ["one"])

    def test_submit_success_and_transient_queue_dedupe(self):
        successful = FakeApi()
        backend, _ = self.make_backend(api=successful)
        response = backend.handle("submit", bookmark())
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["queued"], False)
        self.assertEqual(response["queue"], [])
        nested_response = backend.handle("submit", {"payload": bookmark()})
        self.assertTrue(nested_response["ok"])

        transient = FakeApi(helper.ApiError("Temporary.", "temporary", True))
        queued_backend, _ = self.make_backend(api=transient)
        first = queued_backend.handle("submit", bookmark())
        second = queued_backend.handle("submit", bookmark(title="New title"))
        self.assertTrue(first["queued"])
        self.assertEqual(len(second["queue"]), 1)
        self.assertEqual(second["queue"][0]["title"], "New title")

    def test_direct_success_removes_stale_failed_queue_item_and_returns_queue(self):
        api = FakeApi(helper.ApiError("Temporary.", "network_error", True))
        backend, _ = self.make_backend(api=api)
        queued = backend.handle("submit", bookmark(title="Queued old title"))
        self.assertTrue(queued["queued"])

        api.add_error = helper.ApiError("Rejected.", "invalid_request", False)
        retry = backend.handle("queue-retry-now", {})
        self.assertEqual(retry["result"], "failed")
        self.assertEqual(retry["queue"][0]["status"], "failed")

        api.add_error = None
        saved = backend.handle(
            "submit",
            bookmark(title="Newest direct title", intent="update"),
        )
        self.assertTrue(saved["ok"])
        self.assertFalse(saved["queued"])
        self.assertEqual(saved["queue"], [])
        self.assertEqual(backend.handle("queue-list", {})["queue"], [])

    def test_unrelated_auth_token_query_is_preserved_by_final_redaction(self):
        target = "https://example.com/?auth_token=bookmark-specific-value"
        api = FakeApi()
        api.bookmark = {"url": target}
        backend, _ = self.make_backend(api=api)
        response = backend.handle("duplicate", {"url": "example.com"})
        self.assertEqual(response["bookmark"]["url"], target)

        api.bookmark = {
            "url": "https://example.com/?auth_token=" + DISTINCTIVE_TOKEN
        }
        response = backend.handle("duplicate", {"url": "example.com"})
        self.assertNotIn(DISTINCTIVE_TOKEN, response["bookmark"]["url"])
        self.assertIn("[redacted]", response["bookmark"]["url"])

    def test_permanent_submit_failure_is_not_queued(self):
        api = FakeApi(helper.ApiError("Rejected.", "invalid_request", False))
        backend, _ = self.make_backend(api=api)
        response = backend.handle("submit", bookmark())
        self.assertFalse(response["ok"])
        self.assertFalse(response["retryable"])
        self.assertEqual(backend.handle("queue-list", {})["queue"], [])

    def test_stored_token_is_redacted_from_direct_operation_errors(self):
        unsafe = FakeApi(
            helper.ApiError(
                "request failed at https://api.pinboard.in/v1/posts/add?auth_token="
                + DISTINCTIVE_TOKEN,
                "network_error",
                False,
            )
        )
        backend, _ = self.make_backend(api=unsafe)
        rendered = json.dumps(backend.handle("submit", bookmark()))
        self.assertNotIn(DISTINCTIVE_TOKEN, rendered)
        self.assertNotIn("auth_token=" + DISTINCTIVE_TOKEN, rendered)

    def test_protocol_emits_one_json_object_for_success_and_expected_errors(self):
        backend, _ = self.make_backend()
        output = io.StringIO()
        exit_code = helper.run_protocol(
            ["pinboard_helper.py", "status"],
            io.StringIO("{}\n"),
            output,
            backend,
        )
        self.assertEqual(exit_code, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(json.loads(lines[0])["ok"])

        output = io.StringIO()
        helper.run_protocol(
            ["pinboard_helper.py", "status"],
            io.StringIO("not-json\n"),
            output,
            backend,
        )
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_json")

        output = io.StringIO()
        helper.run_protocol(
            ["pinboard_helper.py", "status"],
            io.StringIO('{"value":NaN}\n'),
            output,
            backend,
        )
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_json")

        output = io.StringIO()
        helper.run_protocol(
            ["pinboard_helper.py", "status"],
            io.BytesIO(b"{\"value\":\xff}\n"),
            output,
            backend,
        )
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_json")

    def test_protocol_redacts_token_even_from_a_bad_injected_backend(self):
        class UnsafeBackend:
            def handle(self, operation, payload):
                return {
                    "ok": False,
                    "error": "failed " + payload["token"],
                    "url": "https://api.pinboard.in/v1/tags/get?auth_token="
                    + payload["token"],
                }

        output = io.StringIO()
        helper.run_protocol(
            ["pinboard_helper.py", "save-token"],
            io.StringIO(json.dumps({"token": DISTINCTIVE_TOKEN}) + "\n"),
            output,
            UnsafeBackend(),
        )
        rendered = output.getvalue()
        self.assertNotIn(DISTINCTIVE_TOKEN, rendered)
        self.assertNotIn("auth_token=" + DISTINCTIVE_TOKEN, rendered)

    def test_unknown_operation_and_nonobject_input_are_json_errors(self):
        backend, _ = self.make_backend()
        self.assertEqual(backend.handle("unknown", {})["code"], "unknown_operation")
        output = io.StringIO()
        helper.run_protocol(
            ["pinboard_helper.py", "status"],
            io.StringIO("[]\n"),
            output,
            backend,
        )
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_payload")


if __name__ == "__main__":
    unittest.main()
