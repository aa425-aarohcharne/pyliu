try:
    from .verification import main
except ImportError:  # pragma: no cover - direct script execution fallback
    from verification import main


if __name__ == "__main__":
    raise SystemExit(main())
