"""py2app config — produces ``Tokenmon.app`` so macOS treats us as a
real app for TCC (Privacy permissions like Screen Recording).

Build (alias mode — fast iteration, source files are *referenced*, not
copied; code changes go live without rebuilding):

    uv run python setup.py py2app -A

Output: ``dist/Tokenmon.app``. Add that bundle to System Settings →
Privacy & Security → Screen Recording. The TCC entry then sticks
between launches because macOS keys it on the bundle identifier
(``com.tokenmon.menubar``) instead of the Python binary path.

For a release build (self-contained, slower to produce, copies all
deps):

    uv run python setup.py py2app
"""
from setuptools import setup, Distribution


class _NoRequiresDistribution(Distribution):
    """py2app 0.28's build_app explicitly errors out if the Distribution
    has any ``install_requires`` set — but modern setuptools auto-fills
    that from pyproject.toml's ``[project.dependencies]`` whenever
    setup.py is invoked. Strip the value back out so py2app's check
    passes."""

    def parse_config_files(self, *args, **kwargs):  # noqa: D401
        super().parse_config_files(*args, **kwargs)
        self.install_requires = []
        self.extras_require = {}


APP = ["scripts/tokenmon_app.py"]

PLIST = {
    "CFBundleIdentifier": "com.tokenmon.menubar",
    "CFBundleName": "Tokenmon",
    "CFBundleDisplayName": "Tokenmon",
    "CFBundleVersion": "0.1.0",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleExecutable": "Tokenmon",
    # Menubar app — no Dock icon, no main menu bar takeover.
    "LSUIElement": True,
    "NSHighResolutionCapable": True,
    # Modern macOS hides Privacy dialogs unless we declare a usage
    # string for each TCC-protected API we call. Without these, the
    # consent prompt either never appears or gets denied silently.
    "NSScreenCaptureUsageDescription": (
        "Tokenmon liest den Inhalt des aktiven Fensters, "
        "damit dein Companion darüber chatten kann."
    ),
    "NSAppleEventsUsageDescription": (
        "Tokenmon kann optional skriptbare Apps wie Safari oder Terminal "
        "abfragen, um strukturierte Inhalte zu erhalten."
    ),
    "NSAppleScriptEnabled": False,
}

OPTIONS = {
    # Pull in the full tokenmon source tree. py2app's modulegraph
    # otherwise misses lazy / dynamic imports (panes, providers).
    "packages": ["tokenmon"],
    # Don't ship a giant standard-library bundle — alias mode references
    # the active interpreter, and full builds get them from the venv.
    "argv_emulation": False,
    "plist": PLIST,
    "iconfile": None,
}

setup(
    name="Tokenmon",
    app=APP,
    options={"py2app": OPTIONS},
    distclass=_NoRequiresDistribution,
)
