"""py2app entry point.

Kept as a dedicated single-file launcher (not ``__main__.py``) because
py2app builds an .app bundle from exactly one entry script, and a
trivial wrapper here keeps the bundle config decoupled from the menubar
package's internal layout."""
from tokenmon.menubar import main

if __name__ == "__main__":
    main()
