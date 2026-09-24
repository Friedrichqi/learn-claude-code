"""Rendering helpers for the record store."""

from pkg import canonical_key


def header(title):
    return "# " + canonical_key(title)


def summarize(store, keys):
    lines = []
    for key in keys:
        lines.append("%s=%s" % (canonical_key(key), store.get(key)))
    return "\n".join(lines)
