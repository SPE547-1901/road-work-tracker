"""Offline tests: python -m unittest discover tests"""
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest import mock

import argparse

from tracker import common, news, pipeline
from tracker import init as init_mod
from tracker.adapters import wzdx

HERE = os.path.dirname(__file__)
REGION = {"id": "t", "title": "Test", "bbox": [-94.8, 38.8, -94.3, 39.3], "timezone": "America/Chicago",
          "sources": [{"id": "wz", "type": "wzdx", "label": "Example DOT", "url": "https://x"}]}


def load(path):
    with open(path) as f:
        return json.load(f)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as f:
        return f.read()


class FixedToday(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(common, "today", return_value=dt.date(2026, 11, 13))
        p.start()
        self.addCleanup(p.stop)
        for mod in (wzdx, news, pipeline):
            if hasattr(mod, "today"):
                q = mock.patch.object(mod, "today", return_value=dt.date(2026, 11, 13))
                q.start()
                self.addCleanup(q.stop)


class TestWzdx(FixedToday):
    def test_build(self):
        ps = wzdx.build(json.loads(fixture("wzdx-v4.geojson")), REGION["sources"][0], REGION)
        by = {p["route"]: p for p in ps}
        self.assertEqual(set(by), {"I-35", "MO 152"})           # detour and out-of-area zone dropped
        i35 = by["I-35"]
        self.assertEqual(i35["direction"], "Both directions")    # two directions merged into one card
        self.assertTrue(i35["fullClosure"])                      # southbound is all lanes closed
        self.assertEqual((i35["startDate"], i35["endDate"]), ("2026-11-01", "2026-12-15"))
        self.assertEqual(i35["speedLimit"], "45")                # 72 km/h -> 45 mph
        self.assertEqual(len(i35["segments"]), 2)
        mo = by["MO 152"]                                        # v3 flat properties
        self.assertEqual(mo["impactLevel"], "future")
        self.assertEqual(mo["speedLimit"], "45")

    def test_detours_on_request(self):
        src = dict(REGION["sources"][0], eventTypes=["work-zone", "detour"])
        ps = wzdx.build(json.loads(fixture("wzdx-v4.geojson")), src, REGION)
        self.assertIn("US 69", {p["route"] for p in ps})


class TestNews(FixedToday):
    def test_parse_and_match(self):
        rows = news.parse_feed(fixture("news.rss"))
        self.assertEqual(len(rows), 3)
        projects = wzdx.build(json.loads(fixture("wzdx-v4.geojson")), REGION["sources"][0], REGION)
        for p in projects:
            p["status"] = common.status_for(p)
        cfg = {"news": {"feeds": [{"agency": "Example DOT", "url": "https://n", "keywords": ["I-35", "ribbon"]}]}}
        with mock.patch.object(news, "http_get", return_value=fixture("news.rss")):
            items = news.collect(cfg, projects, [])
        self.assertEqual([i["url"].rsplit("/", 1)[1] for i in items], ["ribbon", "i35-lanes"])
        lane = next(i for i in items if i["url"].endswith("i35-lanes"))
        self.assertEqual(lane["topic"], "roadwork")
        self.assertEqual(lane["date"], "2026-11-10")
        self.assertEqual(len(lane["projectIds"]), 1)
        self.assertNotIn("<p>", lane["summary"])
        self.assertEqual(next(i for i in items if i["url"].endswith("ribbon"))["topic"], "other")


class TestPipeline(FixedToday):
    def test_run_twice_and_hold(self):
        feed = json.loads(fixture("wzdx-v4.geojson"))
        with tempfile.TemporaryDirectory() as d:
            out, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            with mock.patch.object(wzdx, "fetch", return_value=feed):
                pipeline.run(REGION, out, cache)
                first = load(os.path.join(out, "projects.json"))["projects"]
                pipeline.run(REGION, out, cache)
            second = load(os.path.join(out, "projects.json"))["projects"]
            self.assertEqual([p["id"] for p in first], [p["id"] for p in second])
            self.assertEqual(first[0]["firstSeen"], second[0]["firstSeen"])
            region = load(os.path.join(out, "region.json"))
            self.assertEqual(region["agencies"][0]["label"], "Example DOT")
            self.assertIn("tiles", region["map"])
            # a failing source keeps its stored projects
            with mock.patch.object(wzdx, "fetch", side_effect=RuntimeError("down")):
                st = pipeline.run(REGION, out, cache)
            self.assertFalse(st["sources"]["wz"]["ok"])
            kept = load(os.path.join(out, "projects.json"))["projects"]
            self.assertEqual(len(kept), len(second))
            # offline rebuild from the cache
            st = pipeline.run(REGION, out, cache, offline=True)
            self.assertTrue(st["sources"]["wz"]["ok"])

    def test_validate(self):
        self.assertEqual(pipeline.validate(REGION), [])
        bad = dict(REGION, bbox=[1, 2, 0, 0], sources=[{"id": "a", "type": "nope"}])
        self.assertEqual(len(pipeline.validate(bad)), 2)

    def test_example_configs_validate(self):
        root = os.path.join(HERE, "..", "config", "regions")
        for fn in os.listdir(root):
            pipeline.load_config(os.path.join(root, fn))


class TestInit(unittest.TestCase):
    def run_init(self, root, **kw):
        a = argparse.Namespace(states=None, id=None, title=None, bbox=None, timezone="America/Chicago", list=False,
                               include_cwz=False, force=False, root=root)
        for k, v in kw.items():
            setattr(a, k, v)
        with mock.patch.object(init_mod, "http_get", side_effect=RuntimeError("offline")):
            return init_mod.cmd_init(a)

    def test_two_states_from_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_init(d, states="MO,Kansas", id="KC Metro")
            path = os.path.join(d, "config", "regions", "kc-metro.json")
            cfg = load(path)
            labels = [s["label"] for s in cfg["sources"]]
            self.assertIn("Missouri DOT", labels)
            self.assertIn("Kansas DOT", labels)
            self.assertTrue(all(s["type"] == "wzdx" for s in cfg["sources"]))
            self.assertLess(cfg["bbox"][0], -102)     # covers western Kansas
            self.assertGreater(cfg["bbox"][2], -89.2)  # and eastern Missouri
            self.assertEqual(pipeline.validate(cfg), [])
            self.assertTrue(os.path.exists(os.path.join(d, "config", "news", "kc-metro.json")))
            with self.assertRaises(SystemExit):        # won't overwrite without --force
                self.run_init(d, states="MO", id="kc-metro")

    def test_api_key_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_init(d, states="OH", id="oh")
            src = load(os.path.join(d, "config", "regions", "oh.json"))["sources"][0]
            self.assertIn("${OHIO_DOT_API_KEY}", src["url"])
            self.assertIn("OHIO_DOT_API_KEY", src["_note"])
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
                wzdx.fetch(src, {})

    def test_unknown_state(self):
        with self.assertRaises(SystemExit):
            init_mod.parse_states("MO,Atlantis")


if __name__ == "__main__":
    unittest.main()
