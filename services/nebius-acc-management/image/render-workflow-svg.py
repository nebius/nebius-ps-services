#!/usr/bin/env python3
from __future__ import annotations

import base64
import pathlib
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

ROOT = pathlib.Path(__file__).resolve().parent
DOT_PATH = ROOT / "nebius-acc-workflow.dot"
SVG_PATH = ROOT / "nebius-acc-workflow.svg"
ICON_URL = "https://upload.wikimedia.org/wikipedia/commons/9/99/Sample_User_Icon.png"


def render_svg() -> None:
    subprocess.run(
        ["dot", "-Tsvg", str(DOT_PATH), "-o", str(SVG_PATH)],
        check=True,
    )


def embed_user_icon() -> None:
    request = urllib.request.Request(
        ICON_URL,
        headers={"User-Agent": "nebius-acc-workflow/1.0"},
    )
    with urllib.request.urlopen(request) as response:
        icon_bytes = response.read()

    data_uri = "data:image/png;base64," + base64.b64encode(icon_bytes).decode("ascii")

    tree = ET.parse(SVG_PATH)
    root = tree.getroot()
    ns = {"svg": SVG_NS}

    user_group = None
    for group in root.findall(".//svg:g[@class='node']", ns):
        title = group.find("svg:title", ns)
        if title is not None and title.text == "user":
            user_group = group
            break

    if user_group is None:
        raise RuntimeError("User node not found in SVG output.")

    ellipse = user_group.find("svg:ellipse", ns)
    if ellipse is None:
        raise RuntimeError("User node has no ellipse to size the icon.")

    cx = float(ellipse.attrib["cx"])
    cy = float(ellipse.attrib["cy"])
    rx = float(ellipse.attrib["rx"])
    ry = float(ellipse.attrib["ry"])

    for child in list(user_group):
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in {"ellipse", "text"}:
            user_group.remove(child)

    image = ET.Element(
        f"{{{SVG_NS}}}image",
        {
            "x": f"{cx - rx}",
            "y": f"{cy - ry}",
            "width": f"{rx * 2}",
            "height": f"{ry * 2}",
            "preserveAspectRatio": "xMidYMid meet",
            "href": data_uri,
            f"{{{XLINK_NS}}}href": data_uri,
        },
    )
    user_group.append(image)

    tree.write(SVG_PATH, encoding="utf-8", xml_declaration=True)


def main() -> None:
    render_svg()
    embed_user_icon()


if __name__ == "__main__":
    main()
