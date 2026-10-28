# SPDX-License-Identifier: MIT
"""Simpan/muat proyek (.glz = JSON; gambar disimpan sebagai PNG base64)."""
from __future__ import annotations

import base64
import io
import json

import numpy as np
from PIL import Image

from . import model
from .model import (Project, Layer, Device, PathShape, ImageShape, ImageOptions, Poly)

VERSION = 1


def _png_b64(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def save_project(project, path):
    shapes = []
    for s in project.shapes:
        if isinstance(s, PathShape):
            shapes.append({
                "type": "path", "layer": s.layer, "kind": s.kind, "name": s.name, "group": s.group,
                "polys": [{"closed": p.closed, "pts": np.round(p.pts, 4).ravel().tolist()} for p in s.polys],
            })
        elif isinstance(s, ImageShape):
            shapes.append({
                "type": "image", "layer": s.layer, "name": s.name, "group": s.group,
                "x": s.x, "y": s.y, "w": s.w, "h": s.h,
                "opts": s.opts.to_dict(), "png": _png_b64(s.img),
            })
    data = {"version": VERSION, "device": project.device.to_dict(),
            "layers": [l.to_dict() for l in project.layers], "shapes": shapes}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    project.path = path


def _set_group(shape, d, gmap):
    g = d.get("group")
    if g is not None:
        shape.group = gmap.setdefault(g, next(model._ids))


def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pr = Project()
    pr.device = Device.from_dict(data.get("device", {}))
    layers = [Layer.from_dict(d) for d in data.get("layers", [])]
    if layers:
        pr.layers = layers
    pr.shapes = []
    gmap = {}      # id grup tersimpan -> id baru (hindari tabrakan dengan id sesi ini)
    for d in data.get("shapes", []):
        if d.get("type") == "path":
            polys = [Poly(np.array(p["pts"], dtype=float).reshape(-1, 2), p["closed"]) for p in d["polys"]]
            pr.shapes.append(PathShape(polys, d.get("layer", 0), d.get("kind", "path"), d.get("name", "")))
            _set_group(pr.shapes[-1], d, gmap)
        elif d.get("type") == "image":
            img = Image.open(io.BytesIO(base64.b64decode(d["png"]))).convert("L")
            pr.shapes.append(ImageShape(img, d["x"], d["y"], d["w"], d["h"],
                                        ImageOptions.from_dict(d.get("opts", {})),
                                        d.get("layer", 0), d.get("name", "")))
            _set_group(pr.shapes[-1], d, gmap)
    pr.path = path
    return pr
