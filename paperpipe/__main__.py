"""Allow ``python -m paperpipe ...`` alongside the ``paperpipe`` console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
