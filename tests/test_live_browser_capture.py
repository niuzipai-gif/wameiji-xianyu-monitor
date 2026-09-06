import asyncio

from cd_monitor.services.live_browser_capture import capture_search_html


def test_capture_search_html_writes_xianyu_html_and_parses_samples(tmp_path) -> None:
    output = tmp_path / "xianyu.html"
    screenshot = tmp_path / "xianyu.png"
    network_log = tmp_path / "xianyu.network.json"
    html = """
    <main>
      <article>
        <a href="https://www.goofish.com/item?id=1">Artist SRCL-3520 初回限定</a>
        <span>到手价 260元</span>
      </article>
    </main>
    """
    fake_playwright = _FakePlaywright(html)

    result = asyncio.run(
        capture_search_html(
            "xianyu",
            "SRCL-3520",
            output,
            state_file="data/xianyu_state.json",
            screenshot_path=screenshot,
            network_log_path=network_log,
            timeout_seconds=1,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert output.read_text(encoding="utf-8") == html
    assert screenshot.read_bytes() == b"fake-screenshot"
    assert '"https://www.goofish.com/api/search"' in network_log.read_text(encoding="utf-8")
    assert result["source"] == "xianyu"
    assert result["status"] == "ok"
    assert result["item_count"] == 1
    assert result["screenshot_path"] == str(screenshot)
    assert result["network_log_path"] == str(network_log)
    assert result["network_response_count"] == 1
    assert "goofish.com/search" in result["search_entry_url"]
    assert fake_playwright.chromium.browser.context_kwargs["storage_state"] == "data/xianyu_state.json"
    assert fake_playwright.chromium.browser.closed is True


def test_capture_search_html_reports_human_required_on_security_text(tmp_path) -> None:
    output = tmp_path / "xianyu-security.html"
    fake_playwright = _FakePlaywright("<html>请完成 CAPTCHA 安全验证后继续</html>")

    result = asyncio.run(
        capture_search_html(
            "xianyu",
            "SRCL-3520",
            output,
            timeout_seconds=1,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert result["status"] == "human_required"
    assert result["error_type"] == "security_check"
    assert output.exists()


def test_capture_search_html_can_use_persistent_profile(tmp_path) -> None:
    output = tmp_path / "xianyu.html"
    profile_dir = tmp_path / "profile"
    html = """
    <main>
      <article>
        <a href="https://www.goofish.com/item?id=1">Artist SRCL-3520 初回限定</a>
        <span>到手价 260元</span>
      </article>
    </main>
    """
    fake_playwright = _FakePlaywright(html)

    result = asyncio.run(
        capture_search_html(
            "xianyu",
            "SRCL-3520",
            output,
            profile_dir=profile_dir,
            timeout_seconds=1,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert result["status"] == "ok"
    assert fake_playwright.chromium.profile_dir == str(profile_dir)
    assert fake_playwright.chromium.profile_kwargs["headless"] is False
    assert fake_playwright.chromium.browser.context_kwargs == {}
    assert fake_playwright.chromium.persistent_context.closed is True


def test_xianyu_state_file_wins_over_generic_wameiji_profile(tmp_path) -> None:
    output = tmp_path / "xianyu.html"
    fake_playwright = _FakePlaywright("<main><article>SRCL-3520 260元</article></main>")

    result = asyncio.run(
        capture_search_html(
            "xianyu",
            "SRCL-3520",
            output,
            state_file="data/xianyu_state.json",
            profile_dir=tmp_path / "wameiji-profile",
            timeout_seconds=1,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert result["status"] == "ok"
    assert fake_playwright.chromium.browser.context_kwargs["storage_state"] == "data/xianyu_state.json"
    assert fake_playwright.chromium.persistent_context.closed is False
    assert fake_playwright.chromium.browser.closed is True


def test_xianyu_explicit_profile_wins_over_state_file(tmp_path) -> None:
    output = tmp_path / "xianyu.html"
    profile_dir = tmp_path / "goofish-profile"
    fake_playwright = _FakePlaywright(
        "<main><article><a href='https://www.goofish.com/item?id=1'>SRCL-3520 260元</a></article></main>"
    )

    result = asyncio.run(
        capture_search_html(
            "xianyu",
            "SRCL-3520",
            output,
            state_file="data/xianyu_state.json",
            xianyu_profile_dir=profile_dir,
            timeout_seconds=1,
            playwright_factory=lambda: fake_playwright,
        )
    )

    assert result["status"] == "ok"
    assert fake_playwright.chromium.profile_dir == str(profile_dir)
    assert fake_playwright.chromium.profile_kwargs["headless"] is False
    assert fake_playwright.chromium.browser.closed is False
    assert fake_playwright.chromium.persistent_context.closed is True


class _FakePlaywright:
    def __init__(self, html: str) -> None:
        self.chromium = _FakeChromium(html)


class _FakeChromium:
    def __init__(self, html: str) -> None:
        self.browser = _FakeBrowser(html)
        self.persistent_context = _FakeContext(html)
        self.profile_dir = None
        self.profile_kwargs = {}

    async def launch(self, **_kwargs):
        return self.browser

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.profile_dir = user_data_dir
        self.profile_kwargs = kwargs
        return self.persistent_context


class _FakeBrowser:
    def __init__(self, html: str) -> None:
        self.context_kwargs = {}
        self.context = _FakeContext(html)
        self.closed = False

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self.context

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, html: str) -> None:
        self.page = _FakePage(html)
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self) -> None:
        self.closed = True


class _FakePage:
    def __init__(self, html: str) -> None:
        self.html = html
        self.handlers = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def goto(self, _url: str, **_kwargs) -> None:
        if "response" in self.handlers:
            self.handlers["response"](_FakeResponse())
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    async def content(self):
        return self.html

    async def screenshot(self, **kwargs) -> None:
        output = kwargs["path"]
        with open(output, "wb") as file:
            file.write(b"fake-screenshot")


class _FakeResponse:
    url = "https://www.goofish.com/api/search"
    status = 200

    @property
    def headers(self):
        return {"content-type": "application/json; charset=utf-8"}

    async def text(self):
        return '{"items":[{"title":"Artist SRCL-3520 初回限定","price":"260"}]}'
