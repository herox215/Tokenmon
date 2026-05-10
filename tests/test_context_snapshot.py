from tokenmon.context import ContextSnapshot, ContextResolver


def test_short_summary_includes_metadata_and_truncates_text():
    snap = ContextSnapshot(
        app_name="Safari",
        app_id="com.apple.Safari",
        kind="browser",
        window_title="Hello",
        url="https://example.com",
        text="line\n" * 500,
        source="macos_appscript_safari",
    )
    out = snap.short_summary(max_chars=40)
    assert "Safari (browser)" in out
    assert "url: https://example.com" in out
    assert "title: Hello" in out
    assert "[+" in out  # truncation marker


def test_for_prompt_marks_truncation():
    snap = ContextSnapshot(
        app_name="Term",
        app_id="net.kovidgoyal.kitty",
        kind="terminal",
        cwd="/Users/x/proj",
        text="x" * 100,
        source="kitty-remote",
    )
    out = snap.for_prompt(max_chars=20)
    assert "<window_context>" in out
    assert "kind: terminal" in out
    assert "cwd: /Users/x/proj" in out
    assert "[truncated]" in out


def test_resolver_returns_first_supporting_provider():
    class P:
        def __init__(self, name, supports_id, ret):
            self.name = name
            self._id = supports_id
            self._ret = ret
            self.calls = 0
        def supports(self, app_id):
            return app_id == self._id
        def snapshot(self, app_id, pid):
            self.calls += 1
            return self._ret

    snap = ContextSnapshot(
        app_name="X", app_id="com.x", kind="generic", source="p2",
    )
    p1 = P("p1", "com.other", None)
    p2 = P("p2", "com.x", snap)
    r = ContextResolver(providers=[p1, p2])
    assert r.resolve("com.x", 123) is snap
    assert p1.calls == 0
    assert p2.calls == 1
    # second call within TTL — no rescrape
    assert r.resolve("com.x", 123) is snap
    assert p2.calls == 1


def test_resolver_skips_provider_that_raises():
    class Boom:
        name = "boom"
        def supports(self, app_id):
            return True
        def snapshot(self, app_id, pid):
            raise RuntimeError("nope")

    class OK:
        name = "ok"
        def supports(self, app_id):
            return True
        def snapshot(self, app_id, pid):
            return ContextSnapshot(
                app_name="X", app_id=app_id, kind="generic", source=self.name,
            )

    r = ContextResolver(providers=[Boom(), OK()])
    snap = r.resolve("com.x", 1)
    assert snap is not None
    assert snap.source == "ok"


def test_resolver_returns_none_when_no_provider_matches():
    r = ContextResolver(providers=[])
    assert r.resolve("com.x", 1) is None


def test_resolver_invalidate_forces_rescrape():
    class Counter:
        name = "c"
        def __init__(self):
            self.n = 0
        def supports(self, app_id):
            return True
        def snapshot(self, app_id, pid):
            self.n += 1
            return ContextSnapshot(
                app_name="X", app_id=app_id, kind="generic", source="c",
            )

    c = Counter()
    r = ContextResolver(providers=[c], cache_ttl_s=999.0)
    r.resolve("com.x", 1)
    r.resolve("com.x", 1)
    assert c.n == 1
    r.invalidate()
    r.resolve("com.x", 1)
    assert c.n == 2
