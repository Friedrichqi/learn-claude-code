"""Rendering helpers for the record store."""

from pkg import normalize_key


def header(title):
    return "# " + normalize_key(title)


def summarize(store, keys):
    lines = []
    for key in keys:
        lines.append("%s=%s" % (normalize_key(key), store.get(key)))
    return "\n".join(lines)
