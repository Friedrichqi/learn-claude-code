"""Key handling for the record store."""


def canonical_key(raw):
    """Return the canonical form of a record key."""
    if raw is None:
        raise ValueError("key must not be None")
    return str(raw).strip().lower().replace(" ", "_")
