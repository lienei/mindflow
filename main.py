# -*- coding: utf-8 -*-
"""
MindFlow Android v0.3
Kivy mobile port of the desktop MindFlow data model.

Core goals:
- Compatible with desktop .mflow JSON v2
- Touch selection / pan / pinch zoom
- Add child / sibling / edit / delete
- Memorisation progress
- Hyperlinks
- Embedded PNG image display
- Internal autosave
"""

__version__ = "0.3.0"

import base64
import json
import math
import os
import re
import webbrowser
from dataclasses import dataclass, field, asdict
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import Label as CoreLabel
from kivy.metrics import dp, sp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line, RoundedRectangle, Rectangle, Ellipse
from kivy.utils import platform


APP_NAME = "MindFlow Android"
PROGRESS_STEPS = [None, 0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100]


def find_cjk_font():
    """Use a system CJK font without bundling font files."""
    candidates = []
    if platform == "android":
        candidates += [
            "/system/fonts/NotoSansCJK-Regular.ttc",
            "/system/fonts/NotoSansSC-Regular.otf",
            "/system/fonts/NotoSansCJKsc-Regular.otf",
            "/system/fonts/DroidSansFallback.ttf",
        ]
    elif platform == "win":
        candidates += [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\simhei.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


CJK_FONT = find_cjk_font()


def button_kwargs():
    kw = {
        "font_size": sp(15),
        "size_hint_x": None,
        "width": dp(90),
    }
    if CJK_FONT:
        kw["font_name"] = CJK_FONT
    return kw


def toolbar_button_kwargs():
    """Large, direct-touch toolbar buttons for Android.

    These deliberately do NOT use a ScrollView and do not force a fixed width,
    which avoids Android taps being interpreted as horizontal scrolling.
    """
    kw = {
        "font_size": sp(14),
        "size_hint": (1, 1),
    }
    if CJK_FONT:
        kw["font_name"] = CJK_FONT
    return kw


def label_kwargs():
    kw = {"font_size": sp(14)}
    if CJK_FONT:
        kw["font_name"] = CJK_FONT
    return kw


@dataclass
class MindNode:
    id: str
    text: str
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    note: str = ""
    color: str = "#E8E8FF"
    collapsed: bool = False
    progress: float | None = None
    image_data: str = ""
    image_width: int = 0
    image_height: int = 0
    link: str = ""


class MindMapModel:
    def __init__(self):
        root = MindNode(str(uuid4()), "中心主题")
        self.nodes = {root.id: root}
        self.root_id = root.id

    def add_child(self, parent_id, text=""):
        node = MindNode(str(uuid4()), text, parent_id=parent_id)
        self.nodes[node.id] = node
        self.nodes[parent_id].children.append(node.id)
        return node.id

    def add_sibling(self, node_id, text=""):
        node = self.nodes[node_id]
        if node.parent_id is None:
            return self.add_child(node_id, text)
        parent = self.nodes[node.parent_id]
        sibling = MindNode(str(uuid4()), text, parent_id=node.parent_id)
        self.nodes[sibling.id] = sibling
        idx = parent.children.index(node_id)
        parent.children.insert(idx + 1, sibling.id)
        return sibling.id

    def delete_subtree(self, node_id):
        if node_id == self.root_id or node_id not in self.nodes:
            return False
        node = self.nodes[node_id]
        parent = self.nodes[node.parent_id]
        if node_id in parent.children:
            parent.children.remove(node_id)

        def rec(nid):
            for cid in list(self.nodes[nid].children):
                rec(cid)
            self.nodes.pop(nid, None)

        rec(node_id)
        return True

    def to_dict(self):
        return {
            "version": 2,
            "root_id": self.root_id,
            "nodes": {nid: asdict(node) for nid, node in self.nodes.items()},
        }

    @classmethod
    def from_dict(cls, data):
        obj = cls.__new__(cls)
        obj.root_id = data["root_id"]
        obj.nodes = {}
        for nid, raw in data["nodes"].items():
            defaults = {
                "note": "",
                "color": "#E8E8FF",
                "collapsed": False,
                "progress": None,
                "image_data": "",
                "image_width": 0,
                "image_height": 0,
                "link": "",
                "children": [],
                "parent_id": None,
            }
            defaults.update(raw)
            defaults["id"] = defaults.get("id", nid)
            obj.nodes[nid] = MindNode(**defaults)
        return obj


class MindMapCanvas(Widget):
    """OpenGL-rendered touch mind-map canvas."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app_ref = app_ref
        self.model = app_ref.model
        self.selected_id = self.model.root_id

        self.camera_x = 0.0
        self.camera_y = 0.0
        self.zoom = 1.0

        self.world_positions = {}
        self.node_sizes = {}
        self.node_boxes = {}
        self.link_boxes = {}
        self.subtree_heights = {}
        self.image_texture_cache = {}

        self._touches = {}
        self._drag_uid = None
        self._drag_start = None
        self._drag_camera_start = None
        self._drag_moved = False
        self._pinch_start_distance = None
        self._pinch_start_zoom = None
        self._pinch_world_anchor = None

        self.bind(size=self._schedule_redraw, pos=self._schedule_redraw)
        Clock.schedule_once(lambda dt: self.fit_view(), 0.2)

    def set_model(self, model):
        self.model = model
        self.selected_id = model.root_id
        self.image_texture_cache.clear()
        self.fit_view()

    def _schedule_redraw(self, *_):
        Clock.unschedule(self._redraw_clock)
        Clock.schedule_once(self._redraw_clock, 0)

    def _redraw_clock(self, _dt):
        self.redraw()

    def _depth(self, nid):
        depth = 0
        cur = self.model.nodes[nid]
        while cur.parent_id and cur.parent_id in self.model.nodes:
            depth += 1
            cur = self.model.nodes[cur.parent_id]
        return depth

    def _visible_nodes(self):
        out = []

        def visit(nid):
            if nid not in self.model.nodes:
                return
            out.append(nid)
            node = self.model.nodes[nid]
            if node.collapsed:
                return
            for cid in node.children:
                visit(cid)

        visit(self.model.root_id)
        return out

    def _font_size(self, nid):
        return sp(17 if nid == self.model.root_id else 15)

    def _label_texture(self, text, font_size, max_width=None, color=(0.12, 0.12, 0.16, 1)):
        kwargs = {
            "text": text or " ",
            "font_size": font_size,
            "color": color,
            "halign": "center",
            "valign": "middle",
        }
        if CJK_FONT:
            kwargs["font_name"] = CJK_FONT
        if max_width:
            kwargs["text_size"] = (max_width, None)
        lab = CoreLabel(**kwargs)
        lab.refresh()
        return lab.texture

    def _image_display_size(self, node, max_w):
        if not node.image_data or not node.image_width or not node.image_height:
            return (0.0, 0.0)
        scale = min(1.0, max_w / node.image_width, dp(180) / node.image_height)
        return node.image_width * scale, node.image_height * scale

    def _node_size(self, nid):
        node = self.model.nodes[nid]
        root = nid == self.model.root_id
        base_w = dp(176 if root else 156)
        base_h = dp(56 if root else 48)
        max_w = base_w * 2.0
        pad_x = dp(16)
        pad_y = dp(11)

        # First measure without wrapping so the node can expand naturally.
        tex = self._label_texture(node.text or " ", self._font_size(nid))
        desired_w = min(max_w, max(base_w, tex.width + pad_x * 2))
        content_w = max(dp(40), desired_w - pad_x * 2)
        wrapped = self._label_texture(node.text or " ", self._font_size(nid), content_w)
        text_h = max(sp(20), wrapped.height)

        iw, ih = self._image_display_size(node, max_w - pad_x * 2)
        if iw:
            desired_w = min(max_w, max(desired_w, iw + pad_x * 2))
            total_h = pad_y + ih + dp(8) + text_h + pad_y
        else:
            total_h = max(base_h, pad_y * 2 + text_h)
        return float(desired_w), float(total_h)

    def compute_layout(self):
        visible = self._visible_nodes()
        if not visible:
            return

        self.node_sizes = {nid: self._node_size(nid) for nid in visible}
        depth_max_w = {}
        for nid in visible:
            d = self._depth(nid)
            depth_max_w[d] = max(depth_max_w.get(d, 0), self.node_sizes[nid][0])

        depth_left = {0: 0.0}
        max_depth = max(depth_max_w) if depth_max_w else 0
        for d in range(1, max_depth + 1):
            depth_left[d] = depth_left[d - 1] + depth_max_w[d - 1] + dp(76)

        vgap = dp(24)
        self.subtree_heights = {}

        def subtree_height(nid):
            node_h = self.node_sizes[nid][1]
            node = self.model.nodes[nid]
            children = [cid for cid in node.children if cid in self.node_sizes]
            if node.collapsed or not children:
                h = node_h
            else:
                child_total = sum(subtree_height(cid) for cid in children)
                child_total += vgap * (len(children) - 1)
                h = max(node_h, child_total)
            self.subtree_heights[nid] = h
            return h

        subtree_height(self.model.root_id)
        positions = {}

        def place(nid, top_y):
            sh = self.subtree_heights[nid]
            w, _h = self.node_sizes[nid]
            d = self._depth(nid)
            center_y = top_y + sh / 2.0
            center_x = depth_left[d] + w / 2.0
            positions[nid] = (center_x, center_y)

            node = self.model.nodes[nid]
            children = [cid for cid in node.children if cid in self.node_sizes]
            if node.collapsed or not children:
                return
            children_total = sum(self.subtree_heights[cid] for cid in children)
            children_total += vgap * (len(children) - 1)
            cursor = top_y + (sh - children_total) / 2.0
            for cid in children:
                place(cid, cursor)
                cursor += self.subtree_heights[cid] + vgap

        root_h = self.subtree_heights[self.model.root_id]
        place(self.model.root_id, -root_h / 2.0)
        self.world_positions = positions

    def world_to_screen(self, wx, wy):
        return (
            self.center_x + (wx - self.camera_x) * self.zoom,
            self.center_y - (wy - self.camera_y) * self.zoom,
        )

    def screen_to_world(self, sx, sy):
        return (
            self.camera_x + (sx - self.center_x) / self.zoom,
            self.camera_y - (sy - self.center_y) / self.zoom,
        )

    @staticmethod
    def _hex_rgba(value, alpha=1.0):
        value = (value or "#E8E8FF").lstrip("#")
        if len(value) == 8:
            value = value[-6:]
        try:
            return (
                int(value[0:2], 16) / 255.0,
                int(value[2:4], 16) / 255.0,
                int(value[4:6], 16) / 255.0,
                alpha,
            )
        except Exception:
            return (0.91, 0.91, 1.0, alpha)

    def _effective_link(self, nid):
        node = self.model.nodes[nid]
        if node.link.strip():
            return node.link.strip()
        text = (node.text or "").strip()
        if re.match(r"^(https?://|mailto:|file://)", text, flags=re.I):
            return text
        if re.match(r"^www\.[^\s]+$", text, flags=re.I):
            return "https://" + text
        if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/.*)?$", text):
            return "https://" + text
        return ""

    def _effective_progress(self, nid):
        node = self.model.nodes[nid]
        descendant_values = []

        def collect(cid):
            c = self.model.nodes[cid]
            if c.progress is not None:
                descendant_values.append(float(c.progress))
            for gcid in c.children:
                if gcid in self.model.nodes:
                    collect(gcid)

        for cid in node.children:
            if cid in self.model.nodes:
                collect(cid)
        if descendant_values:
            return sum(descendant_values) / len(descendant_values)
        return node.progress

    def _image_texture(self, node):
        if not node.image_data:
            return None
        key = (node.id, hash(node.image_data[:100]))
        if key in self.image_texture_cache:
            return self.image_texture_cache[key]
        try:
            raw = base64.b64decode(node.image_data)
            tex = CoreImage(BytesIO(raw), ext="png").texture
            self.image_texture_cache[key] = tex
            return tex
        except Exception:
            return None

    def redraw(self):
        self.compute_layout()
        self.node_boxes.clear()
        self.link_boxes.clear()

        with self.canvas:
            self.canvas.clear()

            # Branches first.
            for nid, (wx, wy) in self.world_positions.items():
                node = self.model.nodes[nid]
                pid = node.parent_id
                if not pid or pid not in self.world_positions:
                    continue
                pxw, pyw = self.world_positions[pid]
                px, py = self.world_to_screen(pxw, pyw)
                x, y = self.world_to_screen(wx, wy)
                pw = self.node_sizes[pid][0] * self.zoom
                cw = self.node_sizes[nid][0] * self.zoom
                sx = px + pw / 2.0
                ex = x - cw / 2.0
                dx = max(dp(10), ex - sx)
                c1x = sx + dx * 0.32
                c2x = sx + dx * 0.72
                depth = self._depth(pid)
                width = max(dp(1.2), (dp(5.5) if depth == 0 else dp(2.8)) * self.zoom)
                Color(0.65, 0.64, 0.96, 1)
                Line(
                    bezier=(sx, py, c1x, py, c2x, y, ex, y),
                    width=width,
                    cap="round",
                    joint="round",
                )

            # Nodes.
            for nid, (wx, wy) in self.world_positions.items():
                x, y = self.world_to_screen(wx, wy)
                ww, wh = self.node_sizes[nid]
                w = ww * self.zoom
                h = wh * self.zoom
                left = x - w / 2.0
                bottom = y - h / 2.0
                self.node_boxes[nid] = (left, bottom, left + w, bottom + h)
                node = self.model.nodes[nid]
                radius = min(dp(18) * self.zoom, h / 2.0)

                # Slight shadow.
                Color(0.15, 0.15, 0.25, 0.10)
                RoundedRectangle(pos=(left + dp(2), bottom - dp(2)), size=(w, h), radius=[radius])

                Color(*self._hex_rgba(node.color, 1))
                RoundedRectangle(pos=(left, bottom), size=(w, h), radius=[radius])

                if nid == self.selected_id:
                    Color(0.36, 0.38, 0.95, 1)
                    Line(rounded_rectangle=(left, bottom, w, h, radius), width=max(dp(1.5), dp(2) * self.zoom))
                else:
                    Color(0.69, 0.69, 0.96, 1)
                    Line(rounded_rectangle=(left, bottom, w, h, radius), width=max(dp(0.8), dp(1) * self.zoom))

                pad = dp(14) * self.zoom
                content_w = max(dp(30), w - pad * 2)
                image_tex = self._image_texture(node)
                image_h = 0
                if image_tex:
                    iw, ih = self._image_display_size(node, max(dp(40), ww - dp(28)))
                    iw *= self.zoom
                    ih *= self.zoom
                    image_h = ih
                    Color(1, 1, 1, 1)
                    Rectangle(texture=image_tex, pos=(x - iw / 2, bottom + h - pad - ih), size=(iw, ih))

                link = self._effective_link(nid)
                text_color = (0.12, 0.30, 0.78, 1) if link else (0.11, 0.11, 0.15, 1)
                font_size = self._font_size(nid) * self.zoom
                tex = self._label_texture(node.text or " ", font_size, content_w, text_color)

                if image_h:
                    available_bottom = bottom + pad
                    available_top = bottom + h - pad - image_h - dp(7) * self.zoom
                    ty = available_bottom + max(0, (available_top - available_bottom - tex.height) / 2)
                else:
                    ty = y - tex.height / 2
                tx = x - min(tex.width, content_w) / 2
                Color(1, 1, 1, 1)
                Rectangle(texture=tex, pos=(tx, ty), size=(min(tex.width, content_w), tex.height))

                progress = self._effective_progress(nid)
                if progress is not None and progress < 100:
                    badge = max(dp(18), dp(22) * self.zoom)
                    bx = left + dp(7) * self.zoom
                    by = bottom - badge * 0.35
                    Color(1, 1, 1, 1)
                    RoundedRectangle(pos=(bx, by), size=(badge, badge), radius=[dp(3) * self.zoom])
                    Color(0.55, 0.55, 0.64, 1)
                    Line(rounded_rectangle=(bx, by, badge, badge, dp(3) * self.zoom), width=max(1, self.zoom))
                    sectors = int(round(float(progress) / 12.5))
                    Color(0.28, 0.29, 0.33, 1)
                    inset = badge * 0.16
                    for i in range(min(8, sectors)):
                        # Clockwise pie sectors. Kivy uses degrees counter-clockwise;
                        # drawing negative ranges gives a familiar progress dial.
                        Ellipse(
                            pos=(bx + inset, by + inset),
                            size=(badge - inset * 2, badge - inset * 2),
                            angle_start=90 - (i + 1) * 45,
                            angle_end=90 - i * 45,
                        )

                if progress is not None and progress >= 100:
                    Color(0.20, 0.20, 0.24, 0.9)
                    Line(points=(tx, ty + tex.height / 2, tx + min(tex.width, content_w), ty + tex.height / 2), width=max(1, self.zoom))

                if link:
                    r = max(dp(9), dp(10) * self.zoom)
                    cx = left + w - dp(8) * self.zoom
                    cy = bottom + h - dp(8) * self.zoom
                    Color(1, 1, 1, 1)
                    Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))
                    Color(0.35, 0.37, 0.85, 1)
                    Line(circle=(cx, cy, r), width=max(1, self.zoom))
                    link_tex = self._label_texture("↗", sp(13) * self.zoom, None, (0.28, 0.30, 0.78, 1))
                    Color(1, 1, 1, 1)
                    Rectangle(texture=link_tex, pos=(cx - link_tex.width / 2, cy - link_tex.height / 2), size=link_tex.size)
                    self.link_boxes[nid] = (cx - r * 1.4, cy - r * 1.4, cx + r * 1.4, cy + r * 1.4)

    def _node_at(self, x, y):
        # Reverse order so deeper/later nodes win if anything overlaps.
        for nid in reversed(list(self.node_boxes.keys())):
            l, b, r, t = self.node_boxes[nid]
            if l <= x <= r and b <= y <= t:
                return nid
        return None

    def _link_at(self, x, y):
        for nid, (l, b, r, t) in self.link_boxes.items():
            if l <= x <= r and b <= y <= t:
                return nid
        return None

    def select_node(self, nid):
        if nid in self.model.nodes:
            self.selected_id = nid
            self.app_ref.on_node_selected(nid)
            self.redraw()

    def fit_view(self):
        self.compute_layout()
        if not self.world_positions or self.width <= 1 or self.height <= 1:
            return
        min_x = min(self.world_positions[n][0] - self.node_sizes[n][0] / 2 for n in self.world_positions)
        max_x = max(self.world_positions[n][0] + self.node_sizes[n][0] / 2 for n in self.world_positions)
        min_y = min(self.world_positions[n][1] - self.node_sizes[n][1] / 2 for n in self.world_positions)
        max_y = max(self.world_positions[n][1] + self.node_sizes[n][1] / 2 for n in self.world_positions)
        world_w = max(dp(100), max_x - min_x)
        world_h = max(dp(100), max_y - min_y)
        self.camera_x = (min_x + max_x) / 2
        self.camera_y = (min_y + max_y) / 2
        self.zoom = max(0.35, min(1.6, min((self.width - dp(50)) / world_w, (self.height - dp(50)) / world_h)))
        self.redraw()

    def ensure_selected_visible(self):
        nid = self.selected_id
        if nid not in self.world_positions:
            return
        self.camera_x, self.camera_y = self.world_positions[nid]
        self.redraw()

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)

        touch.grab(self)
        self._touches[touch.uid] = touch

        if len(self._touches) == 2:
            touches = list(self._touches.values())
            self._pinch_start_distance = math.dist(touches[0].pos, touches[1].pos)
            self._pinch_start_zoom = self.zoom
            cx = (touches[0].x + touches[1].x) / 2
            cy = (touches[0].y + touches[1].y) / 2
            self._pinch_world_anchor = self.screen_to_world(cx, cy)
            self._drag_uid = None
            return True

        nid = self._node_at(touch.x, touch.y)
        link_nid = self._link_at(touch.x, touch.y)

        if link_nid:
            self.select_node(link_nid)
            self.app_ref.open_link(link_nid)
            return True

        self._drag_uid = touch.uid
        self._drag_start = touch.pos
        self._drag_camera_start = (self.camera_x, self.camera_y)
        self._drag_moved = False
        touch.ud["node_down"] = nid

        if nid:
            self.select_node(nid)
            if getattr(touch, "is_double_tap", False):
                Clock.schedule_once(lambda dt, n=nid: self.app_ref.edit_node(n), 0)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)

        if touch.uid in self._touches:
            self._touches[touch.uid] = touch

        if len(self._touches) >= 2:
            touches = list(self._touches.values())[:2]
            dist = max(dp(10), math.dist(touches[0].pos, touches[1].pos))
            if self._pinch_start_distance:
                new_zoom = self._pinch_start_zoom * dist / self._pinch_start_distance
                new_zoom = max(0.25, min(3.0, new_zoom))
                cx = (touches[0].x + touches[1].x) / 2
                cy = (touches[0].y + touches[1].y) / 2
                wx, wy = self._pinch_world_anchor
                self.zoom = new_zoom
                self.camera_x = wx - (cx - self.center_x) / self.zoom
                self.camera_y = wy + (cy - self.center_y) / self.zoom
                self.redraw()
            return True

        if self._drag_uid == touch.uid and self._drag_start:
            dx = touch.x - self._drag_start[0]
            dy = touch.y - self._drag_start[1]
            if abs(dx) + abs(dy) > dp(6):
                self._drag_moved = True
                self.camera_x = self._drag_camera_start[0] - dx / self.zoom
                self.camera_y = self._drag_camera_start[1] + dy / self.zoom
                self.redraw()
            return True

        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
        self._touches.pop(touch.uid, None)
        if len(self._touches) < 2:
            self._pinch_start_distance = None
            self._pinch_start_zoom = None
            self._pinch_world_anchor = None
        if self._drag_uid == touch.uid:
            self._drag_uid = None
            self._drag_start = None
        return True


class MindFlowAndroidApp(App):
    status_text = StringProperty("就绪")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = APP_NAME
        self.model = MindMapModel()
        self.map_widget = None
        self.status_label = None
        self._save_event = None

    @property
    def save_path(self):
        return Path(self.user_data_dir) / "current.mflow"

    def build(self):
        self._load_autosave()

        root = BoxLayout(orientation="vertical")

        # Android v0.3:
        # Do NOT put action buttons inside a horizontal ScrollView.
        # ScrollView can swallow small finger movements and make buttons feel dead.
        # Use a fixed two-row toolbar so every tap goes directly to a Button.
        toolbar = GridLayout(
            cols=6,
            rows=2,
            spacing=dp(4),
            padding=(dp(4), dp(4)),
            size_hint_y=None,
            height=dp(108),
        )

        buttons = [
            ("＋子主题", self.add_child),
            ("＋同级", self.add_sibling),
            ("编辑", self.edit_selected),
            ("删除", self.delete_selected),
            ("进度＋", self.progress_up),
            ("进度－", self.progress_down),
            ("折叠/展开", self.toggle_collapse),
            ("设置链接", self.edit_link),
            ("打开链接", self.open_selected_link),
            ("适屏", self.fit_view),
            ("保存", self.save_now),
            ("新建", self.new_map),
        ]

        for text, callback in buttons:
            btn = Button(text=text, **toolbar_button_kwargs())
            # Use a safe wrapper so Android callback exceptions are visible
            # in the status bar instead of looking like "nothing happened".
            btn.bind(
                on_release=lambda _b, fn=callback, name=text:
                    self._run_action(name, fn)
            )
            toolbar.add_widget(btn)

        root.add_widget(toolbar)

        self.map_widget = MindMapCanvas(self)
        root.add_widget(self.map_widget)

        self.status_label = Label(
            text=self.status_text,
            size_hint_y=None,
            height=dp(38),
            halign="left",
            valign="middle",
            color=(0.25, 0.25, 0.32, 1),
            **label_kwargs(),
        )
        self.status_label.bind(
            size=lambda inst, val:
                setattr(inst, "text_size", (inst.width - dp(16), None))
        )
        root.add_widget(self.status_label)
        self.bind(
            status_text=lambda _i, value:
                setattr(self.status_label, "text", value)
        )

        Clock.schedule_once(lambda dt: self.map_widget.fit_view(), 0.3)
        return root

    def _run_action(self, name, fn):
        """Run a toolbar action and make failures visible on-screen."""
        try:
            fn()
        except Exception as exc:
            self.status_text = f"{name}失败：{type(exc).__name__}: {exc}"

    def on_pause(self):
        self.save_now(silent=True)
        return True

    def on_stop(self):
        self.save_now(silent=True)

    def on_node_selected(self, nid):
        node = self.model.nodes[nid]
        text = node.text or "（空白节点）"
        self.status_text = f"已选中：{text[:40]}"

    def mark_changed(self):
        if self._save_event is not None:
            self._save_event.cancel()
        self._save_event = Clock.schedule_once(
            lambda dt: self.save_now(silent=True), 0.8
        )
        if self.map_widget:
            self.map_widget.redraw()

    def _load_autosave(self):
        try:
            if self.save_path.exists():
                data = json.loads(self.save_path.read_text(encoding="utf-8-sig"))
                self.model = MindMapModel.from_dict(data)
        except Exception:
            self.model = MindMapModel()

    def save_now(self, silent=False):
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            self.save_path.write_text(
                json.dumps(self.model.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not silent:
                self.status_text = "已保存到安卓应用内部存储"
            return True
        except Exception as exc:
            self.status_text = f"保存失败：{exc}"
            return False

    def fit_view(self):
        if self.map_widget:
            self.map_widget.fit_view()

    def add_child(self):
        if not self.map_widget:
            self.status_text = "画布尚未准备好"
            return

        parent_id = self.map_widget.selected_id or self.model.root_id
        if parent_id not in self.model.nodes:
            parent_id = self.model.root_id

        # Create a VISIBLE node first. Even if the editor/keyboard later fails,
        # the user can immediately see that the button worked.
        new_id = self.model.add_child(parent_id, "新主题")
        self.map_widget.selected_id = new_id
        self.status_text = "已创建子主题"
        self.mark_changed()

        def after_create(_dt):
            try:
                self.map_widget.compute_layout()
                self.map_widget.ensure_selected_visible()
                self.edit_node(new_id)
            except Exception as exc:
                self.status_text = f"节点已创建，但打开编辑框失败：{exc}"

        Clock.schedule_once(after_create, 0.12)

    def add_sibling(self):
        if not self.map_widget:
            self.status_text = "画布尚未准备好"
            return

        current_id = self.map_widget.selected_id or self.model.root_id
        if current_id not in self.model.nodes:
            current_id = self.model.root_id

        new_id = self.model.add_sibling(current_id, "新主题")
        self.map_widget.selected_id = new_id
        self.status_text = "已创建同级主题"
        self.mark_changed()

        def after_create(_dt):
            try:
                self.map_widget.compute_layout()
                self.map_widget.ensure_selected_visible()
                self.edit_node(new_id)
            except Exception as exc:
                self.status_text = f"节点已创建，但打开编辑框失败：{exc}"

        Clock.schedule_once(after_create, 0.12)

    def edit_selected(self):
        self.edit_node(self.map_widget.selected_id)

    def edit_node(self, nid):
        if nid not in self.model.nodes:
            return
        node = self.model.nodes[nid]
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        text_input = TextInput(
            text=node.text,
            multiline=True,
            font_size=sp(18),
            background_normal="",
            background_active="",
            background_color=(0.97, 0.97, 1, 1),
            foreground_color=(0.1, 0.1, 0.14, 1),
        )
        if CJK_FONT:
            text_input.font_name = CJK_FONT
        box.add_widget(text_input)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        cancel = Button(text="取消", **button_kwargs())
        save = Button(text="确定", **button_kwargs())
        row.add_widget(cancel)
        row.add_widget(save)
        box.add_widget(row)
        popup = Popup(title="编辑节点", content=box, size_hint=(0.88, 0.65), auto_dismiss=False)

        def do_save(*_):
            old = node.text
            node.text = text_input.text.strip()
            # URL text automatically becomes a clickable link, matching desktop v0.58.
            if self._looks_like_link(node.text):
                node.link = self._normalise_link(node.text)
            elif old and node.link == self._normalise_link(old) and self._looks_like_link(old):
                node.link = ""
            popup.dismiss()
            self.mark_changed()
            self.status_text = "节点已更新"

        cancel.bind(on_release=lambda *_: popup.dismiss())
        save.bind(on_release=do_save)
        popup.open()
        Clock.schedule_once(lambda dt: setattr(text_input, "focus", True), 0.2)

    def delete_selected(self):
        nid = self.map_widget.selected_id
        if nid == self.model.root_id:
            self.status_text = "中心主题不能删除"
            return
        node = self.model.nodes[nid]
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(text=f"删除“{node.text or '空白节点'}”以及全部子节点？", **label_kwargs())
        box.add_widget(msg)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        no = Button(text="取消", **button_kwargs())
        yes = Button(text="删除", **button_kwargs())
        row.add_widget(no)
        row.add_widget(yes)
        box.add_widget(row)
        popup = Popup(title="确认删除", content=box, size_hint=(0.82, 0.38), auto_dismiss=False)

        def do_delete(*_):
            parent = self.model.nodes[nid].parent_id
            self.model.delete_subtree(nid)
            self.map_widget.selected_id = parent or self.model.root_id
            popup.dismiss()
            self.mark_changed()

        no.bind(on_release=lambda *_: popup.dismiss())
        yes.bind(on_release=do_delete)
        popup.open()

    def _descendant_manual_progress(self, nid):
        values = []
        def rec(cid):
            node = self.model.nodes[cid]
            if node.progress is not None:
                values.append(node.progress)
            for gcid in node.children:
                if gcid in self.model.nodes:
                    rec(gcid)
        for cid in self.model.nodes[nid].children:
            if cid in self.model.nodes:
                rec(cid)
        return values

    def change_progress(self, direction):
        nid = self.map_widget.selected_id
        node = self.model.nodes[nid]
        if self._descendant_manual_progress(nid):
            self.status_text = "该节点进度由子节点自动汇总，不能直接修改"
            return
        current = node.progress
        try:
            idx = PROGRESS_STEPS.index(current)
        except ValueError:
            idx = 0
        idx = (idx + direction) % len(PROGRESS_STEPS)
        node.progress = PROGRESS_STEPS[idx]
        # Ancestors with child progress cannot keep manual progress.
        pid = node.parent_id
        while pid and pid in self.model.nodes:
            self.model.nodes[pid].progress = None
            pid = self.model.nodes[pid].parent_id
        self.mark_changed()
        self.status_text = f"背诵进度：{node.progress if node.progress is not None else '未开始'}"

    def progress_up(self):
        self.change_progress(1)

    def progress_down(self):
        self.change_progress(-1)

    def toggle_collapse(self):
        nid = self.map_widget.selected_id
        node = self.model.nodes[nid]
        if not node.children:
            self.status_text = "当前节点没有子节点"
            return
        node.collapsed = not node.collapsed
        self.mark_changed()

    def _looks_like_link(self, text):
        text = (text or "").strip()
        if not text or any(ch in text for ch in "\r\n\t"):
            return False
        return bool(
            re.match(r"^(https?://|ftp://|mailto:|file://)", text, flags=re.I)
            or re.match(r"^www\.[^\s]+$", text, flags=re.I)
            or re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/.*)?$", text)
        )

    def _normalise_link(self, value):
        value = (value or "").strip()
        if not value:
            return ""
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
            return value
        return "https://" + value

    def edit_link(self):
        nid = self.map_widget.selected_id
        node = self.model.nodes[nid]
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        ti = TextInput(text=node.link, multiline=False, font_size=sp(17))
        if CJK_FONT:
            ti.font_name = CJK_FONT
        box.add_widget(ti)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        cancel = Button(text="取消", **button_kwargs())
        save = Button(text="确定", **button_kwargs())
        row.add_widget(cancel)
        row.add_widget(save)
        box.add_widget(row)
        popup = Popup(title="设置超链接", content=box, size_hint=(0.88, 0.38), auto_dismiss=False)

        def do_save(*_):
            node.link = self._normalise_link(ti.text)
            popup.dismiss()
            self.mark_changed()

        cancel.bind(on_release=lambda *_: popup.dismiss())
        save.bind(on_release=do_save)
        popup.open()

    def _effective_link(self, nid):
        node = self.model.nodes[nid]
        if node.link.strip():
            return node.link.strip()
        text = (node.text or "").strip()
        if self._looks_like_link(text):
            return self._normalise_link(text)
        return ""

    def open_link(self, nid):
        link = self._effective_link(nid)
        if not link:
            self.status_text = "当前节点没有超链接"
            return False
        try:
            if platform == "android":
                from jnius import autoclass
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(link))
                PythonActivity.mActivity.startActivity(intent)
            else:
                webbrowser.open(link)
            self.status_text = "已打开超链接"
            return True
        except Exception as exc:
            self.status_text = f"打开链接失败：{exc}"
            return False

    def open_selected_link(self):
        self.open_link(self.map_widget.selected_id)

    def new_map(self):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        box.add_widget(Label(text="新建会覆盖当前自动保存的思维导图。", **label_kwargs()))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        no = Button(text="取消", **button_kwargs())
        yes = Button(text="新建", **button_kwargs())
        row.add_widget(no)
        row.add_widget(yes)
        box.add_widget(row)
        popup = Popup(title="新建思维导图", content=box, size_hint=(0.84, 0.38), auto_dismiss=False)

        def do_new(*_):
            self.model = MindMapModel()
            self.map_widget.set_model(self.model)
            popup.dismiss()
            self.save_now(silent=True)
            self.status_text = "已新建思维导图"

        no.bind(on_release=lambda *_: popup.dismiss())
        yes.bind(on_release=do_new)
        popup.open()


if __name__ == "__main__":
    MindFlowAndroidApp().run()
