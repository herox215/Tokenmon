"""Allow ``python -m tokenmon.menubar`` to start the app — required by the
launchd plist that invokes the menubar this way."""
from tokenmon.menubar import main

if __name__ == "__main__":
    main()
