"""Load an instance-owned browser frontend without forking the query engine.

The default deployment has no template and continues to use ``harness.PAGE``.  A deployment may
instead own its HTML, CSS and JavaScript alongside its instance YAML.  Only two inert template
values are substituted: the display name and the instance's example groups.  Query transport is
still the shared ``/ask`` NLWeb endpoint.
"""
from __future__ import annotations

import html
import json
import os

import instance


def _path(key):
    value = str(instance.frontend().get(key) or "").strip()
    if not value:
        return None
    base = os.path.realpath(os.path.dirname(instance.path()))
    target = os.path.realpath(os.path.join(base, value))
    if os.path.commonpath((base, target)) != base:
        raise ValueError(f"frontend.{key} must be inside the instance directory")
    return target


def asset(key):
    target = _path(key)
    if not target:
        return None
    with open(target, encoding="utf-8") as stream:
        return stream.read()


def page():
    rendered = asset("template")
    if rendered is None:
        return None
    return (rendered
            .replace("{{INSTANCE_NAME}}", html.escape(instance.identity()["name"]))
            .replace("{{EXAMPLES_JSON}}", json.dumps(instance.examples(), ensure_ascii=False)
                     .replace("</", "<\\/")))
