"""audioop is in the standard library up to Python 3.12; on 3.13+ install audioop-lts."""
try:
    import audioop  # noqa: F401
except ImportError:          # Python 3.13+
    import audioop_lts as audioop  # noqa: F401
