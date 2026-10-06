#!/usr/bin/env python
"""Find the Experience Builder widgets bound to a layer that no longer exists.

An Experience Builder widget names its layer by a data source id such as
dataSource_1-18f2a3b4c5d-layer-7-3: the app's web map data source, the web map
layer id, and the sublayer id. A web map edit or a service republish that
removes that sublayer id leaves the id in the widget config. The builder shows
no error, the app loads, and the widget does nothing when a user clicks it in
production. A renumber that gives the old id to another layer still resolves:
this tool checks that an id exists, not which layer it names.

This tool reads the app configuration, collects the value of every key that
holds a data source id, and checks each one against the data sources the app
declares, the layers the web map holds, and the layers the map and feature
services actually publish. It also audits and compares the two copies of
the app configuration: the item /data, which is the published copy that users
get, and the config/config.json resource, which is the builder's draft and
becomes the item /data the next time an author clicks Publish.

    python deadwidget.py --self-test
    python deadwidget.py app.json --webmap webmap.json --service URL=service.json
    python deadwidget.py app.json --resource config.json --webmap ITEMID=webmap.json
    python deadwidget.py --portal https://org.maps.arcgis.com --item APPITEMID
    python deadwidget.py app.json --webmap webmap.json --out report.json --apply

Read-only. Nothing is written without --apply, and --apply writes only the
report file.

Exit codes: 0 every reference resolves in every copy audited, 1 a dangling
reference in either copy, 2 an input could not be read, a reference could not
be judged or is of a kind this tool does not audit, or the tool could not
complete, so the run proves nothing, 64 usage error. A divergence between the two copies is reported but
does not change the exit code.
"""

from __future__ import print_function

import argparse
import atexit
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# The environment variable a portal token is read from when --token is absent.
# A token typed on the command line is visible to every process on the
# machine, so the variable is the better habit.
TOKEN_ENV = "DEADWIDGET_TOKEN"

# Seconds to wait for one REST call.
HTTP_TIMEOUT = 60

# Largest body read from one REST call. The largest app configuration measured
# was under 1 MB and the largest web map under 2 MB.
MAX_BODY_BYTES = 32 * 1024 * 1024

# How many paths are printed under one finding, and how many divergence lines.
SAMPLE = 5

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

# Keys whose string value is a data source id. Measured on real app configs:
# useDataSources entries carry the first three, bookmarks carry
# mapDataSourceId, and the map widget carries initialMapDataSourceID.
ID_KEYS = ("dataSourceId", "mainDataSourceId", "rootDataSourceId",
           "mapDataSourceId", "initialMapDataSourceID")

# Keys whose list holds jimu layer view ids, <map widget id>-<data source id>
# (Esri's JimuLayerView). The Swipe widget stores the first two, the Map
# Layers widget the last two. The keys of the two objects in VIEW_MAPS are
# jimu map view ids of the same shape.
VIEW_KEYS = ("leadingLayersId", "trailingLayersId", "showJimuLayerViewIds",
             "hiddenJimuLayerViewIds")
VIEW_MAPS = ("swipeMapViewList", "customizeLayerOptions")

# Why an entry is INERT: a reference no widget reads, which fails nothing.
MAP_TABLE = ("The table is in MAP mode, so this entry makes no tab. If the "
             "layer was renumbered, its new tab lost the settings in this "
             "entry")
STALE_VIEW = ("No map widget in this app shows this map view, so the widget "
              "never reads the settings kept under it. The builder leaves "
              "them behind when a map widget changes its data source")

# The shape Experience Builder gives the data source ids it generates. A
# configInfo key is read as a data source id only when it has this shape or is
# a declared data source, because other widgets key configInfo by other things.
DS_ID_SHAPE = re.compile(r"^(dataSource_\d+|widget_\d+_output)")

# Web map layer types whose sublayers become data sources of their own.
MAPSVC_TYPES = ("ArcGISMapServiceLayer", "ArcGISTiledMapServiceLayer")

# Web map layer types that have child data sources this tool does not model
# (Esri's DataSourceTypes lists SUBTYPE_SUBLAYER and KNOWLEDGE_GRAPH_SUBLAYER).
# A layer with a featureCollection is the third such kind. A child of one is
# NOT AUDITED, never DANGLING: nothing here knows what ids it has.
OPAQUE_TYPES = {"SubtypeGroupLayer": "subtype group layer",
                "KnowledgeGraphLayer": "knowledge graph layer"}

# Data source types that hold layers and have no data views, so
# <main id>-<suffix> under one of them is always a layer reference.
CONTAINER_TYPES = ("WEB_MAP", "WEB_SCENE", "MAP_SERVICE", "FEATURE_SERVICE")

# C0 control characters and DEL. Printed raw, a label could move the cursor,
# clear the screen, or forge a report line such as a clean VERDICT.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# A service url, and the layer index when the url names one layer.
SERVICE_URL = re.compile(r"^(.*/(?:FeatureServer|MapServer))(?:/(\d+))?$",
                         re.IGNORECASE)

ITEM_ID = re.compile(r"^[0-9a-fA-F]{32}$")

OK = "OK"
DANGLING = "DANGLING"
UNJUDGED = "UNJUDGED"
INERT = "INERT"
NOT_AUDITED = "NOT AUDITED"

# Print order, worst first.
RANK = {DANGLING: 0, UNJUDGED: 1, NOT_AUDITED: 2, INERT: 3, OK: 4}

# The only url schemes fetched. urllib also opens file: and ftp: urls, and a
# file: url with a host is an SMB connection on Windows. Service urls come
# from web map data that another organization can own.
WEB_SCHEMES = ("http", "https")

# The item /data is what users get. config/config.json is the builder's draft:
# the builder writes it on Save and copies it to /data on Publish (Esri
# solution.js issue 660).
DATA = "published copy"
RESOURCE = "builder draft"


class Unread(Exception):
    """An input that could not be read. Never mistaken for an empty one."""


class Ref(object):
    """One data source id found in an app configuration."""

    def __init__(self, dsid, path, owner, inert=""):
        self.dsid = dsid
        self.path = path
        self.owner = owner
        # Why no widget reads this entry, or "". A DANGLING verdict on an
        # inert reference is reported as INERT with this reason.
        self.inert = inert


class Verdict(object):
    """What one data source id resolves to. need is a service still to read."""

    def __init__(self, status, reason="", need=None):
        self.status = status
        self.reason = reason
        self.need = need


# ---------------------------------------------------------- decision core

def payload_error(doc):
    """The message of an ArcGIS error body, or None.

    A portal answers a missing item or an expired token with HTTP 200 and an
    error object. Read as data, that object has no layers and no widgets, and
    every reference would resolve against nothing.
    """
    if isinstance(doc, dict) and isinstance(doc.get("error"), dict):
        err = doc["error"]
        return "error %s: %s" % (err.get("code"), err.get("message") or "")
    return None


def check_app(doc):
    """Raise Unread unless doc is an Experience Builder app configuration."""
    problem = payload_error(doc)
    if problem:
        raise Unread(problem)
    if (not isinstance(doc, dict) or not isinstance(doc.get("widgets"), dict)
            or not isinstance(doc.get("dataSources"), dict)):
        # A file with no widgets would audit zero references and pass. That
        # is the vacuous pass, so a document of the wrong kind is unread.
        raise Unread("not an Experience Builder app configuration: it has no "
                     "widgets and dataSources objects")
    return doc


def service_root(url):
    """(service url, layer index or None) for a service url, else (None, None).

    The query string is dropped: a secured layer url can carry a token in it,
    and the same service must match however the url was written.
    """
    if not isinstance(url, str):
        return None, None
    bare = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    match = SERVICE_URL.match(bare)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def service_ids(doc):
    """The layer and table ids a service description publishes, as strings.

    A nested sublayer is there twice: as its own id, 4, and as the chain
    of ids from its top-level group down, 3-4. Experience Builder names a
    nested sublayer both ways: one real app used the first form, and the
    saved configuration of another used the second.
    """
    problem = payload_error(doc)
    if problem:
        raise Unread(problem)
    if not isinstance(doc, dict) or not any(
            isinstance(doc.get(key), list) for key in ("layers", "tables")):
        raise Unread("not a map or feature service description")
    ids = set()
    parent = {}
    for key in ("layers", "tables"):
        entries = doc.get(key)
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("id") is not None:
                ids.add("%s" % (entry["id"],))
                if entry.get("parentLayerId") is not None:
                    parent["%s" % (entry["id"],)] = "%s" % (
                        entry["parentLayerId"],)
    for lid in list(ids):
        chain = [lid]
        up = parent.get(lid)
        # A parent that is not published, such as -1, ends the chain, and
        # so does a loop in a malformed description.
        while up in ids and up not in chain:
            chain.insert(0, up)
            up = parent.get(up)
        ids.add("-".join(chain))
    if not ids:
        # A secured service answers an anonymous read with an empty layer
        # list. Read as "publishes nothing", every sublayer bound to it would
        # be reported dangling when the truth is that nobody could look.
        raise Unread("the service lists no layers and no tables, which is "
                     "what a secured service returns to an anonymous read")
    return frozenset(ids)


def overhaul(doc):
    """The sublayer ids a layers array decides, or None when it decides none.

    doc is a web map layer or a Map Image Layer item's /data. The ArcGIS JS
    API builds the sublayers from such an array alone only when an entry
    carries a minScale (isSublayerOverhaul in @arcgis/core sublayerUtils.js).
    Without one, as Map Viewer Classic writes it, the array only overrides
    the service's sublayers by id, so the service decides. The web map is
    tried after the item, and the last array that decides wins
    (createSublayersForOrigin in @arcgis/core SublayersOwner.js).
    """
    subs = doc.get("layers") if isinstance(doc, dict) else None
    subs = [s for s in subs if isinstance(s, dict)] \
        if isinstance(subs, list) else []
    if any(s.get("minScale") is not None or (
            isinstance(s.get("layerDefinition"), dict) and
            s["layerDefinition"].get("minScale") is not None)
            for s in subs):
        return frozenset("%s" % (s.get("id"),) for s in subs)
    return None


def item_data(doc):
    """The sublayer ids a Map Image Layer item's /data decides, or an empty
    set when it decides none. An item with no data answers with an empty
    body, which http_json and read_json_file return as None."""
    problem = payload_error(doc)
    if problem:
        raise Unread(problem)
    return overhaul(doc) or frozenset()


def index_webmap(doc):
    """Every child data source id suffix a web map can produce.

    Returns (entries, mapsvc). entries maps a suffix to its layer, with the
    (service, index) pair to confirm against when the layer url names one
    layer. mapsvc maps a map service layer's suffix to its sublayer rules.

    A child data source id is the parent's id, a dash, and the child's own id
    (Esri's DataSourceConstructorOptions.jimuChildId). So a group layer's child
    is <group>-<child>. A nested map service sublayer was measured in two
    forms, <layer>-<sublayer> and <layer>-<group sublayer>-<sublayer>, and
    service_ids publishes both.

    A map service layer added from a Map Image Layer item gets its sublayers
    from that item's /data when the web map's own array does not decide them,
    so mapsvc names the item to read.
    """
    problem = payload_error(doc)
    if problem:
        raise Unread(problem)
    if not isinstance(doc, dict) or not isinstance(
            doc.get("operationalLayers"), list):
        raise Unread("not a web map: it has no operationalLayers array")
    entries = {}
    mapsvc = {}

    def add(layer, prefix, kind):
        if not isinstance(layer, dict) or not layer.get("id"):
            return
        suffix = "%s%s" % (prefix, layer["id"])
        title = layer.get("title") or layer.get("name") or suffix
        ltype = layer.get("layerType") or ""
        root, index = service_root(layer.get("url"))
        opaque = OPAQUE_TYPES.get(ltype)
        if isinstance(layer.get("featureCollection"), dict):
            opaque = "feature collection"
        entries[suffix] = {"kind": kind, "title": title, "check": None,
                           "opaque": opaque}
        if ltype == "GroupLayer":
            children = layer.get("layers")
            for child in children if isinstance(children, list) else []:
                add(child, suffix + "-", "layer")
        elif ltype in MAPSVC_TYPES:
            listed = overhaul(layer)
            item = layer.get("itemId")
            item = item if listed is None and isinstance(item, str) \
                and ITEM_ID.match(item) else None
            mapsvc[suffix] = {"title": title, "url": root, "listed": listed,
                              "item": item}
        elif index is not None:
            entries[suffix]["check"] = (root, index)

    for layer in doc["operationalLayers"]:
        add(layer, "", "layer")
    tables = doc.get("tables")
    for table in tables if isinstance(tables, list) else []:
        add(table, "", "table")
    return entries, mapsvc


def member(url, sid, services, what):
    """Is layer sid published by the service at url?"""
    got = services.get(url)
    if got is None:
        return Verdict(UNJUDGED, "service %s has not been read" % url, url)
    if isinstance(got, str):
        return Verdict(UNJUDGED, "service %s could not be read: %s"
                       % (url, got))
    if sid in got:
        return Verdict(OK)
    return Verdict(DANGLING, "%s: the service %s no longer publishes layer %s"
                   % (what, url, sid))


def judge_child(child, index, services):
    """Resolve the part of an id after the web map data source id."""
    entries, mapsvc = index
    entry = entries.get(child)
    if entry is not None:
        if entry["check"]:
            return member(entry["check"][0], entry["check"][1], services,
                          "%s '%s'" % (entry["kind"], entry["title"]))
        return Verdict(OK)
    # Whole ids at dash boundaries only. A substring test finds layer-25-1
    # inside layer-25-15 and reports a reference that exists as dangling.
    best = None
    for lid in mapsvc:
        if child.startswith(lid + "-") and (best is None
                                            or len(lid) > len(best)):
            best = lid
    if best is None:
        for lid in sorted(entries):
            entry = entries[lid]
            if entry["opaque"] and child.startswith(lid + "-"):
                return Verdict(NOT_AUDITED, "children of the %s '%s' are not "
                               "audited" % (entry["opaque"], entry["title"]))
        return Verdict(DANGLING, "the web map has no layer or table with id "
                       "%s" % child)
    sid = child[len(best) + 1:]
    layer = mapsvc[best]
    what = "sublayer %s of '%s'" % (sid, layer["title"])
    chain = sid.split("-")
    if not all(part.isdigit() for part in chain):
        return Verdict(DANGLING, "%s: a sublayer id is a number, or a chain "
                       "of numbers from a top-level group sublayer down"
                       % what)
    listed = layer["listed"]
    source = "the web map's layers array for this service"
    if layer["item"]:
        key = ("item", layer["item"])
        got = services.get(key)
        if got is None:
            return Verdict(UNJUDGED, "layer item %s has not been read"
                           % layer["item"], key)
        if isinstance(got, str):
            return Verdict(UNJUDGED, "%s: its layer item %s could not be "
                           "read: %s" % (what, layer["item"], got))
        if got:
            listed = got
            source = "the layers array of its layer item %s" % layer["item"]
    if listed is not None and not listed.issuperset(chain):
        return Verdict(DANGLING, "%s: %s sets scale ranges and omits it, so it "
                       "never exists in the browser" % (what, source))
    if not layer["url"]:
        return Verdict(UNJUDGED, "%s: the web map layer has no service url to "
                       "confirm it against" % what)
    return member(layer["url"], sid, services, what)


def split_root(dsid, datasources):
    """(declared data source, child suffix or None), or (None, None)."""
    if dsid in datasources:
        return dsid, None
    best = None
    for key in datasources:
        # The dash is part of the test: dataSource_10-x is not a child of
        # dataSource_1.
        if dsid.startswith(key + "-") and (best is None
                                           or len(key) > len(best)):
            best = key
    if best is None:
        return None, None
    return best, dsid[len(best) + 1:]


def judge(dsid, datasources, webmaps, services):
    """Resolve one data source id against the app, its web maps and services.

    webmaps maps a web map item id to index_webmap's result, or to the reason
    it could not be read. services maps a service url to the set of ids it
    publishes, or to the reason it could not be read.
    """
    root, child = split_root(dsid, datasources)
    if root is None:
        return Verdict(DANGLING, "not a data source this app declares")
    ds = datasources[root] if isinstance(datasources[root], dict) else {}
    dtype = ds.get("type") or ""
    if child is None:
        # Whatever its type, a data source whose url names one layer of a
        # map or feature service is gone when the service drops that layer.
        url, index = service_root(ds.get("url"))
        if not ds.get("isOutputFromWidget") and index is not None:
            return member(url, index, services, "data source %s" % root)
        return Verdict(OK)
    if dtype == "WEB_MAP":
        item = ds.get("itemId")
        if not isinstance(item, str) or not item:
            return Verdict(UNJUDGED, "data source %s names no web map item id"
                           % root)
        got = webmaps.get(item)
        if isinstance(got, tuple):
            return judge_child(child, got, services)
        return Verdict(UNJUDGED, "web map %s could not be read: %s"
                       % (item, got or "it was never fetched"))
    if dtype in ("FEATURE_SERVICE", "MAP_SERVICE"):
        url = service_root(ds.get("url"))[0]
        if not url:
            return Verdict(UNJUDGED, "data source %s has no service url" % root)
        return member(url, child, services, "data source %s" % root)
    return Verdict(NOT_AUDITED, "children of a %s data source are not audited"
                   % (dtype or "untyped"))


def _owner(key, value):
    if isinstance(value, dict) and isinstance(value.get("label"), str) \
            and value["label"]:
        return "%s (%s)" % (key, value["label"])
    return key


def collect_refs(app):
    """Every data source id in an app configuration, with where it sits.

    The value of an id key is a reference and so is a configInfo key, and a
    layer or map view id with its widget id stripped. Nothing else is: a
    table's layersConfig id is a data source id with a suffix glued on, and
    matching text inside it is how a check invents defects.
    """
    datasources = app.get("dataSources") or {}
    found = []
    # The map views that exist: <map widget id>-<data source id> for each
    # data source a widget uses. A Swipe or Map Layers block keyed by any
    # other map view is left over, and the widget reads only the block of a
    # map view that exists (Esri's JimuLayerView).
    views = set()
    widgets = app.get("widgets")
    for wid in widgets if isinstance(widgets, dict) else {}:
        widget = widgets[wid] if isinstance(widgets[wid], dict) else {}
        uses = widget.get("useDataSources")
        for use in uses if isinstance(uses, list) else []:
            if isinstance(use, dict) and isinstance(use.get("dataSourceId"),
                                                    str):
                views.add("%s-%s" % (wid, use["dataSourceId"]))

    def container(dsid):
        ds = datasources.get(dsid)
        return isinstance(ds, dict) and ds.get("type") in CONTAINER_TYPES

    def walk(node, path, owner, inert):
        if isinstance(node, dict):
            main = node.get("mainDataSourceId")
            view_id = node.get("dataViewId")
            table_map = node.get("tableMode") == "MAP"
            for key in node:
                value = node[key]
                sub = "%s.%s" % (path, key)
                if key in ID_KEYS and isinstance(value, str) and value:
                    # <main>-<view> is a data view of main, and main is
                    # judged on its own key (Esri's UseDataSource). A view
                    # id is one word such as "selection", never a number:
                    # <main>-99 under a map service main is sublayer 99.
                    # A web map or a service has no data views: the suffix
                    # under one of them is a layer, and it is judged.
                    rest = None
                    if (key == "dataSourceId" and isinstance(main, str)
                            and main and value.startswith(main + "-")
                            and not container(main)):
                        rest = value[len(main) + 1:]
                    if isinstance(view_id, str) and view_id:
                        view = rest == view_id
                    else:
                        view = (rest is not None and "-" not in rest
                                and not rest.isdigit())
                    if not view:
                        found.append(Ref(value, sub, owner, inert))
                if key == "configInfo" and isinstance(value, dict):
                    for ckey in value:
                        if ckey in datasources or DS_ID_SHAPE.match(ckey):
                            found.append(Ref(ckey, "%s{%s}" % (sub, ckey),
                                             owner, inert))
                # A view id is the map widget id, a dash, and the data source
                # id. Widget ids hold no dash, so the rest is judged.
                if key in VIEW_KEYS and isinstance(value, list):
                    for pos, vid in enumerate(value):
                        if isinstance(vid, str) and vid:
                            found.append(Ref(vid.split("-", 1)[-1], "%s[%d]"
                                             % (sub, pos), owner, inert))
                if key in VIEW_MAPS and isinstance(value, dict):
                    # A stale block is one INERT note at most. What is
                    # inside it is never read, so it is not collected.
                    for vkey in value:
                        stale = "" if vkey in views else STALE_VIEW
                        found.append(Ref(vkey.split("-", 1)[-1], "%s{%s}"
                                         % (sub, vkey), owner,
                                         inert or stale))
                        if not stale:
                            walk(value[vkey], "%s.%s" % (sub, vkey), owner,
                                 inert)
                    continue
                # A table in MAP mode makes one tab per map layer and uses a
                # layersConfig entry only for the layer whose id it names
                # (read from the Table widget's code). A stale entry makes no
                # tab, so it does not fail the run.
                walk(value, sub, owner, inert or (
                    MAP_TABLE if table_map and key == "layersConfig" else ""))
        elif isinstance(node, list):
            for pos, value in enumerate(node):
                walk(value, "%s[%d]" % (path, pos), owner, inert)

    for key in app:
        value = app[key]
        if isinstance(value, dict):
            for sub in value:
                walk(value[sub], "%s.%s" % (key, sub),
                     _owner(sub, value[sub]), "")
        else:
            walk(value, key, key, "")
    return found


def webmap_items(app):
    """The web map item ids an app's WEB_MAP data sources name."""
    out = set()
    for ds in (app.get("dataSources") or {}).values():
        if isinstance(ds, dict) and ds.get("type") == "WEB_MAP" \
                and isinstance(ds.get("itemId"), str) and ds["itemId"]:
            out.add(ds["itemId"])
    return out


def signature(app):
    """The references and data source bindings of one configuration copy.

    The two copies are legitimately not byte-identical: the portal rewrites
    rich text when it writes the resource. Only what binds a widget to data
    is compared.
    """
    sig = set("%s = %s" % (ref.path, ref.dsid) for ref in collect_refs(app))
    for key, ds in (app.get("dataSources") or {}).items():
        ds = ds if isinstance(ds, dict) else {}
        url = ds.get("url")
        # The query string is dropped: a stored layer url can carry a token,
        # which must not be printed, and a rotated token is not a change.
        url = url.split("?", 1)[0] if isinstance(url, str) else ""
        sig.add("dataSources.%s = %s %s %s" % (
            key, ds.get("type") or "", ds.get("itemId") or "", url))
    return sig


class Report(object):
    """The outcome of one audit."""

    def __init__(self):
        self.surfaces = []     # (name, widgets, references)
        self.unread = []       # (what, reason)
        self.findings = []     # (surface, Ref, Verdict)
        self.only_data = []
        self.only_resource = []
        self.webmaps_read = 0
        self.webmaps_total = 0
        self.services_read = 0
        self.services_total = 0
        self.items_read = 0
        self.items_total = 0

    def count(self, status):
        return sum(1 for f in self.findings if f[2].status == status)

    @property
    def diverged(self):
        return bool(self.only_data or self.only_resource)


def audit(surfaces, webmap_get, service_get, item_get):
    """Audit one app. surfaces is [(name, loader)]; loaders raise Unread.
    item_get reads a Map Image Layer item's /data.

    Services are read only when a reference needs one, so a secured service
    nothing is bound to cannot fail the run.
    """
    report = Report()
    apps = []
    for name, loader in surfaces:
        try:
            apps.append((name, check_app(loader())))
        except Unread as exc:
            report.unread.append((name, "%s" % exc))
    webmaps = {}
    for _, app in apps:
        for item in sorted(webmap_items(app)):
            if item in webmaps:
                continue
            try:
                webmaps[item] = index_webmap(webmap_get(item))
                report.webmaps_read += 1
            except Unread as exc:
                webmaps[item] = "%s" % exc
                report.unread.append(("web map %s" % item, "%s" % exc))
    report.webmaps_total = len(webmaps)
    services = {}
    refs = [(name, app, collect_refs(app)) for name, app in apps]
    while True:
        verdicts = []
        need = set()
        for name, app, found in refs:
            for ref in found:
                verdict = judge(ref.dsid, app["dataSources"], webmaps,
                                services)
                verdicts.append((name, ref, verdict))
                if verdict.need:
                    need.add(verdict.need)
        if not need:
            break
        # A need is a service url, or ("item", id) for a layer item.
        for url in sorted(need, key=str):
            item = url[1] if isinstance(url, tuple) else None
            try:
                if item:
                    services[url] = item_data(item_get(item))
                    report.items_read += 1
                else:
                    services[url] = service_ids(service_get(url))
                    report.services_read += 1
            except Unread as exc:
                services[url] = "%s" % exc
                report.unread.append(("layer item %s" % item if item else
                                      "service %s" % url, "%s" % exc))
    report.items_total = len([u for u in services if isinstance(u, tuple)])
    report.services_total = len(services) - report.items_total
    for name, ref, verdict in verdicts:
        if verdict.status == DANGLING and ref.inert:
            verdict = Verdict(INERT, "%s. %s" % (verdict.reason, ref.inert))
        report.findings.append((name, ref, verdict))
    for name, app, found in refs:
        report.surfaces.append((name, len(app["widgets"]), len(found)))
    if len(apps) == 2:
        first, second = signature(apps[0][1]), signature(apps[1][1])
        report.only_data = sorted(first - second)
        report.only_resource = sorted(second - first)
    return report


def exit_code(report):
    """2 beats 1: a reference nobody could check is not a clean reference.

    NOT AUDITED is such a reference too. A widget bound to a web scene layer
    that was deleted is exactly the silent failure this tool exists for.
    """
    if report.unread or report.count(UNJUDGED) or report.count(NOT_AUDITED):
        return 2
    if report.count(DANGLING):
        return 1
    return 0


def grouped(report):
    """Non-OK findings grouped by status, id, owner and reason, worst first."""
    groups = {}
    for surface, ref, verdict in report.findings:
        if verdict.status == OK:
            continue
        key = (RANK[verdict.status], ref.dsid, ref.owner, verdict.status,
               verdict.reason)
        entry = groups.setdefault(key, {"surfaces": [], "paths": []})
        if surface not in entry["surfaces"]:
            entry["surfaces"].append(surface)
        if ref.path not in entry["paths"]:
            entry["paths"].append(ref.path)
    return [(key, groups[key]) for key in sorted(groups)]


def describe(report):
    """The lines the command line prints."""
    out = []
    for name, widgets, count in report.surfaces:
        out.append("deadwidget: %s, %d widget(s), %d data source reference(s)"
                   % (name, widgets, count))
    out.append("web maps read: %d of %d, services read: %d of %d"
               % (report.webmaps_read, report.webmaps_total,
                  report.services_read, report.services_total))
    if report.items_total:
        out[-1] += (", layer items read: %d of %d"
                    % (report.items_read, report.items_total))
    for key, entry in grouped(report):
        _, dsid, owner, status, reason = key
        out.append("")
        out.append("%-12s %s" % (status, dsid))
        out.append("             %s, in the %s" % (owner,
                                                    " and the ".join(
                                                        entry["surfaces"])))
        out.append("             %s" % reason)
        paths = entry["paths"]
        more = ""
        if len(paths) > 1:
            more = " (and %d more path(s))" % (len(paths) - 1)
        out.append("             at %s%s" % (paths[0], more))
    if report.diverged:
        out.append("")
        out.append("DIVERGED     the %s and the %s bind widgets differently. "
                   "Either the draft holds edits not yet published, or a REST "
                   "edit reached only the published copy and the next Publish "
                   "from the builder will overwrite it. This alone does not "
                   "fail the run." % (RESOURCE, DATA))
        for label, lines in (("only in the " + DATA, report.only_data),
                             ("only in the " + RESOURCE,
                              report.only_resource)):
            for line in lines[:SAMPLE]:
                out.append("             %s: %s" % (label, line))
            if len(lines) > SAMPLE:
                out.append("             %s: ... and %d more"
                           % (label, len(lines) - SAMPLE))
    if report.unread:
        out.append("")
        out.append("%d input(s) COULD NOT BE READ, so they are not clean, they "
                   "are unknown:" % len(report.unread))
        for what, reason in report.unread:
            out.append("  %s: %s" % (what, reason))
    out.append("")
    out.append("references: %d found, %d ok, %d dangling, %d unjudged, "
               "%d inert, %d not audited"
               % (len(report.findings), report.count(OK),
                  report.count(DANGLING), report.count(UNJUDGED),
                  report.count(INERT), report.count(NOT_AUDITED)))
    code = exit_code(report)
    if code == 2 and (report.unread or report.count(UNJUDGED)):
        verdict = ("INCOMPLETE. Something could not be read, so this run "
                   "proves nothing about it.")
    elif code == 2:
        verdict = ("INCOMPLETE. %d reference(s) are of a kind this tool does "
                   "not audit, so this run proves nothing about them."
                   % report.count(NOT_AUDITED))
    elif code == 1:
        # Counted as bindings, one id in one widget, not as id occurrences: a
        # useDataSources entry carries the same id under two keys.
        bound = len([key for key, _ in grouped(report)
                     if key[3] == DANGLING])
        verdict = ("%d widget binding(s) point at a layer that does not "
                   "exist." % bound)
    elif report.count(INERT):
        verdict = ("every widget binding resolves, but %d reference(s) that "
                   "no widget reads do not: see INERT."
                   % report.count(INERT))
    else:
        verdict = "every data source reference resolves."
    if report.diverged:
        verdict += (" The builder draft and the published copy bind widgets "
                    "differently: see DIVERGED.")
    out.append("VERDICT: " + verdict)
    return out


def document(report, source):
    """The report file. No credential ever enters it."""
    findings = []
    for key, entry in grouped(report):
        _, dsid, owner, status, reason = key
        findings.append({"status": status, "dataSourceId": dsid,
                         "owner": owner, "reason": reason,
                         "surfaces": entry["surfaces"],
                         "paths": entry["paths"]})
    return {"deadwidget": 1, "source": source, "exit": exit_code(report),
            "surfaces": [{"name": n, "widgets": w, "references": c}
                         for n, w, c in report.surfaces],
            "unread": [{"input": w, "reason": r} for w, r in report.unread],
            "findings": findings,
            "divergence": {"onlyInItemData": report.only_data,
                           "onlyInResource": report.only_resource},
            "counts": {"found": len(report.findings),
                       "ok": report.count(OK),
                       "dangling": report.count(DANGLING),
                       "unjudged": report.count(UNJUDGED),
                       "inert": report.count(INERT),
                       "notAudited": report.count(NOT_AUDITED)}}


# ------------------------------------------------------------------ inputs

def redact(text, secret):
    """text without secret, raw or in either form a url gives it.
    urlencode writes a space as +, quote writes it as %20."""
    if secret:
        for form in (secret, urllib.parse.quote(secret, safe=""),
                     urllib.parse.quote_plus(secret, safe="")):
            text = text.replace(form, "***")
    return text


def read_json_file(path):
    """A JSON file, or Unread. utf-8-sig, because Windows tools write a BOM.
    An empty file is None, as an empty response is: every reader but
    item_data refuses None."""
    try:
        with io.open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
        return json.loads(text) if text.strip() else None
    except (IOError, OSError, ValueError, RecursionError) as exc:
        raise Unread("%s: %s" % (path, exc))


def http_json(url, token=None, referer=None, bust=False,
              limit=MAX_BODY_BYTES, timeout=HTTP_TIMEOUT, redirect_ok=None):
    """GET url?f=json and parse it. Any failure is Unread, token redacted.

    With a token, a redirect is followed only to a url redirect_ok accepts:
    the Location of a redirect usually keeps the query, token included.
    """
    params = {"f": "json"}
    if token:
        params["token"] = token
    if bust:
        # The draft resource is cached. A read without a junk parameter
        # can return the copy from before the last save.
        params["_ts"] = "%d" % (time.time() * 1000)
    if urllib.parse.urlsplit(url).scheme.lower() not in WEB_SCHEMES:
        raise Unread(redact("%s: only http and https urls are fetched" % url,
                            token))
    try:
        # Built inside the try: urllib refuses some urls here, and its
        # message quotes the whole url, token included.
        request = urllib.request.Request(
            "%s?%s" % (url, urllib.parse.urlencode(params)))
        if referer:
            request.add_header("Referer", referer)
        if bust:
            request.add_header("Cache-Control", "no-cache")
        response = urllib.request.build_opener(WebOnlyRedirect(
            redirect_ok if token else None)).open(request, timeout=timeout)
        try:
            raw = response.read(limit + 1)
        finally:
            response.close()
    except Exception as exc:
        # Deliberately broad: a refused socket, a timeout, an HTTP 500 and a
        # TLS failure all mean the same thing here, this input is unread.
        raise Unread(redact("%s: %s" % (url, exc), token))
    if len(raw) > limit:
        raise Unread("%s: larger than %d bytes, not read" % (url, limit))
    try:
        text = raw.decode("utf-8-sig")
        return json.loads(text) if text.strip() else None
    except (ValueError, RecursionError):
        raise Unread("%s: the response is not JSON" % url)


class WebOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """urllib follows a redirect to ftp:. A service must not be able to send
    the tool anywhere that http_json would refuse to go directly, nor carry
    the token to a host that sends_token refuses."""

    def __init__(self, token_ok=None):
        urllib.request.HTTPRedirectHandler.__init__(self)
        self.token_ok = token_ok

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        refused = None
        if urllib.parse.urlsplit(newurl).scheme.lower() not in WEB_SCHEMES:
            refused = "a url that is not http or https"
        elif self.token_ok and not self.token_ok(newurl):
            refused = "a host the token may not go to"
        if refused:
            # Closed here: urllib leaves the refused response open.
            fp.close()
            raise urllib.error.HTTPError(
                newurl, code, "a redirect to %s was refused" % refused,
                headers, None)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def is_loopback(host):
    return host in ("127.0.0.1", "localhost", "::1")


def reads_host(url, portal, trusted=()):
    """May online mode send any request to this url?

    Only to the portal's own host, a host named with --trust-host, or, for
    ArcGIS Online, another arcgis.com host, which is where an organization's
    hosted services live. Service urls come from web map data, which anybody
    can author. Fetched blindly, they would send this machine's requests to
    any address it can reach, and print what came back.
    """
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        # A url urllib cannot parse is not fetched, and gets no token.
        return False
    home = (urllib.parse.urlparse(portal).hostname or "").lower()
    if host == home or host in trusted:
        return True
    return host.endswith(".arcgis.com") and home.endswith(".arcgis.com")


def sends_token(url, portal, trusted=()):
    """May the portal token go to this url? Only to a host reads_host
    accepts, and only over https or to this machine."""
    if not reads_host(url, portal, trusted):
        return False
    target = urllib.parse.urlparse(url)
    return target.scheme == "https" or is_loopback(target.hostname)


def rest_root(portal):
    """https://org.example.com/portal -> .../portal/sharing/rest."""
    base = portal.rstrip("/")
    for tail in ("/sharing/rest", "/home"):
        if base.lower().endswith(tail):
            base = base[:-len(tail)]
    return base + "/sharing/rest"


def bind_webmaps(specs, items):
    """Map web map item ids to files from --webmap [ITEMID=]FILE specs.

    An unkeyed file is bound when the app names exactly one web map that no
    keyed file covers. Anything else is ambiguous and a usage error, because a
    web map read for the wrong item audits the wrong layers.
    """
    keyed = {}
    loose = []
    for spec in specs:
        match = re.match(r"^([0-9A-Za-z]+)=(.+)$", spec)
        if match:
            keyed[match.group(1)] = match.group(2)
        else:
            loose.append(spec)
    if loose:
        open_items = sorted(set(items) - set(keyed))
        if len(loose) != 1 or len(open_items) != 1:
            raise ValueError(
                "cannot tell which web map %s is. Name it: --webmap "
                "ITEMID=FILE. This app's web map item(s): %s"
                % (", ".join(loose), ", ".join(sorted(items)) or "none"))
        keyed[open_items[0]] = loose[0]
    return keyed


def parse_services(specs):
    """--service URL=FILE pairs, keyed by the normalized service url."""
    out = {}
    for spec in specs:
        url, sep, path = spec.partition("=")
        root = service_root(url)[0]
        if not sep or not path or not root:
            raise ValueError("--service takes URL=FILE, where URL is a "
                             "MapServer or FeatureServer url: %s" % spec)
        out[root] = path
    return out


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse exits 2 on a usage error, and 2 here means "an input could
        # not be read". A typo must not look like an unreachable portal.
        # say(), not argparse's own print, so a missing or dead stderr cannot
        # turn the 64 into a traceback's 1.
        say(self.format_usage().rstrip("\n"), True)
        say("%s: error: %s" % (self.prog, message), True)
        self.exit(64)


def _parse(argv):
    ap = _Parser(
        prog="deadwidget.py",
        description="Find the Experience Builder widgets bound to a layer "
                    "that no longer exists.",
        epilog="Read-only. Nothing is written without --apply.")
    ap.add_argument("app", nargs="?", metavar="APP_JSON",
                    help="the app's item /data, the published copy, saved "
                         "as JSON (offline mode)")
    ap.add_argument("--resource", metavar="FILE",
                    help="the app's config/config.json resource, the "
                         "builder's draft, to audit and compare with APP_JSON")
    ap.add_argument("--webmap", action="append", default=[],
                    metavar="[ITEMID=]FILE",
                    help="a web map's /data saved as JSON. Repeatable.")
    ap.add_argument("--service", action="append", default=[],
                    metavar="URL=FILE",
                    help="a service's ?f=json description saved as JSON. "
                         "Repeatable.")
    ap.add_argument("--layer-item", dest="layer_item", action="append",
                    default=[], metavar="ITEMID=FILE",
                    help="a Map Image Layer item's /data saved as JSON, for a "
                         "web map layer added from that item. Repeatable.")
    ap.add_argument("--portal", metavar="URL",
                    help="portal url (online mode), for example "
                         "https://org.maps.arcgis.com")
    ap.add_argument("--item", metavar="ITEMID",
                    help="the app's item id (online mode)")
    ap.add_argument("--token",
                    help="a portal token. Prefer the %s environment variable."
                         % TOKEN_ENV)
    ap.add_argument("--trust-host", dest="trust_host", action="append",
                    default=[], metavar="HOST",
                    help="another host to read services from and send the "
                         "token to, such as a federated server. Repeatable.")
    ap.add_argument("--out", metavar="FILE", help="path for a JSON report")
    ap.add_argument("--apply", action="store_true",
                    help="write --out. Without this nothing is written.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    return ap.parse_args(argv)


def say(text, err=False):
    """Print one line of printable ASCII. A scheduled job on Windows redirects
    stdout as cp1252, and a widget label outside it would crash the report
    halfway. A control character comes out as \\xNN, so text from a config
    or a portal cannot move the cursor or start a line of its own.

    A stream is None under pythonw or with its fd closed, and then nothing is
    printed. A failed write to stdout raises, so the run reports it and exits
    2. A failed write to stderr is dropped: nothing is left to report it to.
    """
    text = CONTROL.sub(lambda m: "\\x%02x" % ord(m.group()), text)
    stream = sys.stderr if err else sys.stdout
    if stream is None:
        return
    try:
        print(text.encode("ascii", "backslashreplace").decode("ascii"),
              file=stream)
    except OSError:
        if not err:
            raise
        drop(stream)


def _usage(message):
    say("error: %s" % message, True)
    return 64


def _offline(args):
    """Loaders for saved JSON files. Returns (surfaces, webmap_get,
    service_get, item_get, source) or raises ValueError for a usage error."""
    services = parse_services(args.service)
    layer_items = {}
    for spec in args.layer_item:
        item, _, path = spec.partition("=")
        if not path or not ITEM_ID.match(item):
            raise ValueError("--layer-item takes ITEMID=FILE, where ITEMID is "
                             "32 hexadecimal characters: %s" % spec)
        layer_items[item] = path
    surfaces = [(DATA, lambda: read_json_file(args.app))]
    if args.resource:
        surfaces.append((RESOURCE, lambda: read_json_file(args.resource)))
    items = set()
    read = False
    for _, loader in surfaces:
        try:
            items |= webmap_items(check_app(loader()))
            read = True
        except Unread:
            pass        # audit() reports it; there is nothing to bind to
    # With no configuration read there is nothing to bind a web map to, and
    # the unread app, not a --webmap usage error, is what the run reports.
    files = bind_webmaps(args.webmap, items) if read else {}

    def webmap_get(item):
        if item not in files:
            raise Unread("no --webmap file was given for this item")
        return read_json_file(files[item])

    def service_get(url):
        if url not in services:
            raise Unread("no --service file was given for this url")
        return read_json_file(services[url])

    def item_get(item):
        if item not in layer_items:
            raise Unread("no --layer-item file was given for this item")
        return read_json_file(layer_items[item])

    return surfaces, webmap_get, service_get, item_get, args.app


def _online(args, token):
    """Loaders that read the portal and the services over REST."""
    rest = rest_root(args.portal)
    referer = args.portal.rstrip("/")
    trusted = set(host.lower() for host in args.trust_host)

    def token_ok(url):
        return sends_token(url, args.portal, trusted)

    def get(url, bust=False):
        if not reads_host(url, args.portal, trusted):
            raise Unread("not on the portal's host or a host named with "
                         "--trust-host, so it was not fetched")
        secret = token if token and token_ok(url) else None
        return http_json(url, secret, referer, bust, redirect_ok=token_ok)

    item_url = "%s/content/items/%s" % (rest, args.item)
    surfaces = [(DATA, lambda: get(item_url + "/data")),
                (RESOURCE, lambda: get(item_url +
                                       "/resources/config/config.json",
                                       bust=True))]

    def webmap_get(item):
        if not ITEM_ID.match(item):
            raise Unread("not an item id, so it was not fetched")
        return get("%s/content/items/%s/data" % (rest, item))

    # A layer item's /data is read like a web map's.
    return (surfaces, webmap_get, get, webmap_get,
            "%s item %s" % (referer, args.item))


def main(argv=None, environ=None):
    args = _parse(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ
    if args.self_test:
        return self_test()
    online = bool(args.portal or args.item)
    if online and args.app:
        return _usage("give APP_JSON or --portal and --item, not both")
    if not online and not args.app:
        return _usage("give APP_JSON, or --portal and --item. Use "
                      "--self-test to verify the tool without them.")
    if args.apply and not args.out:
        return _usage("--apply needs --out")
    if args.out and os.path.exists(args.out):
        # The saved app JSON is often the only copy from before an edit.
        # A spec such as ITEMID=FILE is tried whole and after its "=".
        for spec in ([args.app, args.resource] + args.webmap + args.service
                     + args.layer_item):
            for path in [spec] + (spec or "").split("=", 1)[1:]:
                if (path and os.path.exists(path)
                        and os.path.samefile(path, args.out)):
                    return _usage("--out %s is one of the inputs, and the "
                                  "report would replace it" % args.out)
    # Stripped: `set DEADWIDGET_TOKEN=abc ` on Windows keeps the space.
    token = (args.token or environ.get(TOKEN_ENV) or "").strip() or None
    if online:
        if not args.portal or not args.item:
            return _usage("online mode needs both --portal and --item")
        if not re.match(r"^https?://", args.portal):
            return _usage("--portal must start with https:// or http://")
        if not ITEM_ID.match(args.item):
            return _usage("--item must be 32 hexadecimal characters")
        try:
            host = urllib.parse.urlparse(args.portal).hostname or ""
        except ValueError as exc:
            return _usage("--portal is not a valid url: %s" % exc)
        if token and args.portal.startswith("http://") \
                and not is_loopback(host):
            return _usage("refusing to send a token to a plain http portal")
    try:
        if online:
            loaders = _online(args, token)
        else:
            try:
                loaders = _offline(args)
            except ValueError as exc:
                return _usage(exc)
        return _run(args, token, *loaders)
    except Exception as exc:
        # Deliberately broad. Exit 1 means "dangling references found", so a
        # crash on input nobody foresaw must never leave with it.
        say("error: deadwidget could not complete, so this run proves "
            "nothing: %s: %s" % (type(exc).__name__, redact("%s" % exc, token)),
            True)
        return 2


def drop(stream):
    """Point a dead stream's fd at devnull, or the flush at exit fails again
    and Python turns the exit code into 120."""
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, stream.fileno())
    os.close(null)


def flushed(code, stream):
    """code, or 2 when the reader of the output went away, as with | head.
    A stream that is None was never there, so it cannot have gone away."""
    if stream is None:
        return code
    try:
        stream.flush()
    except OSError:
        drop(stream)
        return 2
    return code


def _run(args, token, surfaces, webmap_get, service_get, item_get, source):
    """Audit, print, and write the report file behind --apply."""
    report = audit(surfaces, webmap_get, service_get, item_get)
    for line in describe(report):
        say(redact(line, token))
    code = exit_code(report)
    if args.out:
        if not args.apply:
            say("")
            say("Check only. %s was not written. Re-run with --apply."
                % args.out)
            return code
        text = redact(json.dumps(document(report, source), indent=2,
                                 sort_keys=True), token)
        try:
            with io.open(args.out, "w", encoding="ascii") as handle:
                handle.write(text + "\n")
        except (IOError, OSError) as exc:
            say("error: could not write %s: %s" % (args.out, exc), True)
            return 2
        say("")
        say("wrote %s" % args.out)
    return code


# --------------------------------------------------------------- self-test

def summary(passed, failed):
    """The footer. 0 only when nothing failed."""
    total = passed + len(failed)
    print("%d assertions, %d failed" % (total, len(failed)))
    for label in failed:
        print("  FAILED: %s" % label)
    return 1 if failed else 0


def self_test():
    """Assertions over the decision core, the files and one loopback portal."""
    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label, kind=ValueError):
        try:
            fn()
        except kind as exc:
            check(True, label)
            return exc
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    print("deadwidget self-test: synthetic configs, one loopback portal, no "
          "credentials")
    print("-" * 68)

    wm_id = "0123456789abcdef0123456789abcdef"
    app_id = "fedcba9876543210fedcba9876543210"

    def fixture(base, other=None):
        """A web map, its services, and an app bound to it."""
        other = other or base
        zoning = base + "/Planning/Zoning/MapServer"
        utilities = base + "/Utilities/Water/MapServer"
        hydrants = other + "/Hosted/Hydrants/FeatureServer"
        parks = base + "/Hosted/Parks/FeatureServer"
        roads = base + "/Transport/Roads/MapServer"
        lookup = base + "/Reference/Lookup/MapServer"
        webmap = {"operationalLayers": [
            {"id": "18f00000001-layer-25", "layerType":
             "ArcGISMapServiceLayer", "title": "Zoning", "url": zoning,
             "layers": [{"id": n, "minScale": 0} for n in range(16)]},
            {"id": "18f00000002-layer-3", "layerType":
             "ArcGISMapServiceLayer", "title": "Water", "url": utilities},
            {"id": "18f00000003-layer-4", "layerType": "ArcGISFeatureLayer",
             "title": "Hydrants", "url": hydrants + "/3?token=abc"},
            {"id": "Group_5", "layerType": "GroupLayer", "title": "Reference",
             "layers": [
                 {"id": "18f00000004-layer-6", "layerType":
                  "ArcGISFeatureLayer", "title": "Parks", "url": parks + "/0"},
                 {"id": "18f00000005-layer-7", "layerType":
                  "ArcGISMapServiceLayer", "title": "Roads", "url": roads,
                  "layers": [{"id": 0}, {"id": 1}]}]},
            {"id": "18f00000006-layer-8", "layerType": "VectorTileLayer",
             "title": "Basemap overlay"}],
            "tables": [{"id": "Lookup_0", "title": "Lookup",
                        "url": lookup + "/5"}]}
        services = {
            zoning: {"layers": [{"id": n} for n in range(16)]},
            utilities: {"layers": [{"id": 0}, {"id": 1, "subLayerIds": [2]},
                                   {"id": 2, "parentLayerId": 1}]},
            hydrants: {"layers": [{"id": n} for n in range(4)]},
            parks: {"layers": [{"id": 0}]},
            roads: {"layers": [{"id": 0}, {"id": 1}]},
            lookup: {"layers": [{"id": 0}], "tables": [{"id": 5}]},
        }

        def use(dsid, root="dataSource_1"):
            return {"dataSourceId": dsid, "mainDataSourceId": dsid,
                    "rootDataSourceId": root}

        root = "dataSource_1-"
        app = {
            "widgets": {
                "widget_1": {"uri": "widgets/arcgis/arcgis-map/",
                             "label": "Map",
                             "useDataSources": [use("dataSource_1")],
                             "config": {"initialMapDataSourceID":
                                        "dataSource_1"}},
                "widget_2": {"label": "Zoning search", "useDataSources": [
                    use(root + "18f00000001-layer-25-15"),
                    use(root + "18f00000001-layer-25-1")]},
                "widget_3": {"label": "Water near me", "config": {
                    "configInfo": {"dataSource_1": {"x": 1},
                                   "someOtherKey": {}}},
                    "useDataSources": [use(root + "18f00000002-layer-3-2")]},
                "widget_4": {"label": "Hydrant table", "useDataSources": [
                    use(root + "18f00000003-layer-4")],
                    "config": {"tableMode": "MAP", "layersConfig": [
                        {"id": root + "18f00000003-layer-4-77",
                         "useDataSource": use(root + "18f00000003-layer-4")}]}},
                "widget_5": {"label": "Parks list", "useDataSources": [
                    use(root + "Group_5-18f00000004-layer-6"),
                    use(root + "Group_5-18f00000005-layer-7-1"),
                    use(root + "Lookup_0")]},
                "widget_6": {"label": "Bookmarks", "config": {"bookmarks": [
                    {"mapDataSourceId": "dataSource_1"}]}},
            },
            "dataSources": {
                "dataSource_1": {"id": "dataSource_1", "type": "WEB_MAP",
                                 "itemId": wm_id},
                "widget_2_output_1": {"id": "widget_2_output_1",
                                      "type": "FEATURE_LAYER",
                                      "isOutputFromWidget": True,
                                      "originDataSources": [
                                          use(root + "18f00000001-layer-25-15")
                                      ]},
            },
            "messageConfigs": {},
            "exbVersion": "1.19.0",
        }
        return app, webmap, services

    base = "https://gis.example.com/server/rest/services"
    app, webmap, services = fixture(base)

    def run(app_doc, webmap_doc=None, service_docs=None, resource=None,
            items=None):
        docs = services if service_docs is None else service_docs
        surfaces = [(DATA, lambda: app_doc)]
        if resource is not None:
            surfaces.append((RESOURCE, lambda: resource))

        def wm_get(item):
            if webmap_doc is None:
                raise Unread("no web map")
            return webmap_doc

        def svc_get(url):
            if url not in docs:
                raise Unread("no service")
            return docs[url]

        def item_get(item):
            if item not in (items or {}):
                raise Unread("no layer item")
            return items[item]
        return audit(surfaces, wm_get, svc_get, item_get)

    def only_map_of(item):
        return {"widgets": {"widget_1": {"useDataSources": [
            {"dataSourceId": "dataSource_1"}]}},
            "dataSources": {"dataSource_1": {"type": "WEB_MAP",
                                             "itemId": item}}}

    def statuses(report):
        return dict((ref.dsid, verdict.status)
                    for _, ref, verdict in report.findings)

    def copy(doc):
        return json.loads(json.dumps(doc))

    # ---- the clean app
    report = run(app, webmap)
    check(exit_code(report) == 0 and report.count(DANGLING) == 0,
          "the clean fixture resolves every reference and exits 0")
    check(len(report.findings) == 33,
          "and it checked all 33 references, not zero")
    check(report.services_read == 6 and report.services_total == 6,
          "it read the six services a reference needed")
    check(report.webmaps_read == 1, "and the one web map the app names")
    shifted = copy(services)
    shifted[base + "/Planning/Zoning/MapServer"] = {"layers": [
        {"id": n, "name": "Parcels" if n == 15 else "Zoning %d" % n}
        for n in range(16)]}
    check(exit_code(run(app, webmap, shifted)) == 0,
          "a republish that gives id 15 to another layer still reads ok: "
          "the tool checks that an id exists, not what it names (Limits)")

    # ---- trap (b): whole ids at dash boundaries, never substrings
    wm = copy(webmap)
    wm["operationalLayers"][0]["layers"] = [{"id": 15, "minScale": 0}]
    got = statuses(run(app, wm))
    check(got["dataSource_1-18f00000001-layer-25-15"] == OK,
          "sublayer 15 is published, so layer-25-15 resolves")
    check(got["dataSource_1-18f00000001-layer-25-1"] == DANGLING,
          "and layer-25-1 is DANGLING although it is a prefix of layer-25-15"
          "  <-- pinned defect")
    wm["operationalLayers"][0]["layers"] = [{"id": 1, "minScale": 0}]
    got = statuses(run(app, wm))
    check(got["dataSource_1-18f00000001-layer-25-1"] == OK and
          got["dataSource_1-18f00000001-layer-25-15"] == DANGLING,
          "and the reverse: layer-25-15 is not found inside a published "
          "layer-25-1  <-- pinned defect")
    index = index_webmap(webmap)
    svc_x = base + "/X/MapServer"
    svc_y = base + "/Y/MapServer"
    nest = index_webmap({"operationalLayers": [
        {"id": "X-layer-2", "layerType": "ArcGISMapServiceLayer",
         "url": svc_x},
        {"id": "A", "layerType": "ArcGISMapServiceLayer", "url": svc_x},
        {"id": "A-5", "layerType": "ArcGISMapServiceLayer", "url": svc_y},
        {"id": "B", "layerType": "ArcGISMapServiceLayer"}]})
    nest_svc = {svc_x: frozenset(["5"]), svc_y: frozenset(["3"])}
    check(judge_child("X-layer-215", nest, nest_svc).status == DANGLING,
          "a deleted layer X-layer-215 is not sublayer 5 of X-layer-2: the "
          "dash is part of the match  <-- pinned defect")
    got = judge("dataSource_10-18f00000001-layer-25-15", app["dataSources"],
                {wm_id: index}, {})
    check(got.status == DANGLING and
          "not a data source this app declares" in got.reason,
          "dataSource_10-... is not a child of dataSource_1  <-- pinned defect")
    check(judge_child("A-5-3", nest, nest_svc).status == OK,
          "when two map service ids both prefix a reference, the longer one "
          "owns it")
    check(judge_child("B-x", nest, nest_svc).status == DANGLING,
          "a sublayer id that is not a number is DANGLING even when the "
          "service could not be asked")
    both = {"dataSource_1": {"type": "WEB_MAP", "itemId": "x"},
            "dataSource_1-extra": {"type": "WEB_MAP", "itemId": wm_id}}
    check(split_root("dataSource_1-extra-18f00000006-layer-8", both)
          == ("dataSource_1-extra", "18f00000006-layer-8"),
          "when two declared ids both prefix a reference, the longer one "
          "owns it")
    check(judge_child("18f00000001-layer-25-abc", index, {}).status
          == DANGLING, "a sublayer id that is not a number is DANGLING")
    # Group sublayer 3 holds 4; 4 holds 5. A chain names a nested sublayer
    # from its top-level group down, as a second real app's config does.
    tree = service_ids({"layers": [
        {"id": 3, "parentLayerId": -1, "subLayerIds": [4]},
        {"id": 4, "parentLayerId": 3, "subLayerIds": [5]},
        {"id": 5, "parentLayerId": 4}, {"id": 0, "parentLayerId": -1}]})
    check(tree == frozenset(["0", "3", "4", "5", "3-4", "3-4-5"]),
          "a service publishes a nested sublayer by its own id and by the "
          "chain from its top-level group")
    chains = {svc_x: tree}
    check([judge_child("X-layer-2-" + sid, nest, chains).status
           for sid in ("3-4", "3-4-5", "4", "5")] == [OK] * 4,
          "a nested sublayer resolves as <layer>-<group>-<sublayer> and as "
          "<layer>-<sublayer>  <-- pinned defect")
    check([judge_child("X-layer-2-" + sid, nest, chains).status
           for sid in ("0-4", "4-5", "3-5", "3-4-9", "3-x")] == [DANGLING] * 5,
          "a chain whose ids are not parent and child from the top down is "
          "DANGLING")
    check(service_ids({"layers": [{"id": 1, "parentLayerId": 2},
                                  {"id": 2, "parentLayerId": 1}]})
          == frozenset(["1", "2", "2-1", "1-2"]),
          "a parent loop in a malformed service ends, and does not hang")
    nested_app = {"widgets": {"w": {"useDataSources": [
        {"dataSourceId": "dataSource_1-L-3-4",
         "mainDataSourceId": "dataSource_1-L-3-4",
         "rootDataSourceId": "dataSource_1"}]}},
        "dataSources": {"dataSource_1": {"type": "WEB_MAP", "itemId": wm_id}}}
    nested_wm = {"operationalLayers": [
        {"id": "L", "layerType": "ArcGISMapServiceLayer", "url": svc_x}]}
    tree_doc = {"layers": [{"id": 3, "parentLayerId": -1},
                           {"id": 4, "parentLayerId": 3}]}
    report = run(nested_app, nested_wm, {svc_x: tree_doc})
    check(exit_code(report) == 0 and report.services_read == 1,
          "an app bound to a nested sublayer by its chain exits 0 after "
          "reading the service, not 1 unread  <-- pinned defect")
    report = run(nested_app, nested_wm, {svc_x: {"layers": [
        {"id": 3, "parentLayerId": -1}, {"id": 4, "parentLayerId": -1}]}})
    check(exit_code(report) == 1,
          "and exits 1 once a republish moves sublayer 4 out of group 3")
    wm = copy(nested_wm)
    wm["operationalLayers"][0]["layers"] = [{"id": 4, "minScale": 0}]
    check(statuses(run(nested_app, wm, {svc_x: tree_doc}))[
        "dataSource_1-L-3-4"] == DANGLING,
          "a scale-range layers array that omits the group omits the chain")

    # ---- trap (a): a map service with no layers array publishes what the
    # service holds, so the service is read
    report = run(app, webmap)
    got = statuses(report)
    check(got["dataSource_1-18f00000002-layer-3-2"] == OK,
          "a map service layer with no layers array resolves sublayer 2 from "
          "the service  <-- pinned defect")
    check("dataSource_1-18f00000002-layer-3-2" not in
          [ref.dsid for _, ref, v in report.findings if v.status != OK],
          "and a nested service sublayer resolves by its own id, "
          "<layer>-<sublayer>")
    docs = dict(services)
    docs[base + "/Utilities/Water/MapServer"] = {"layers": [{"id": 0},
                                                            {"id": 1}]}
    got = statuses(run(app, webmap, docs))
    check(got["dataSource_1-18f00000002-layer-3-2"] == DANGLING,
          "and it is DANGLING once a republish drops sublayer 2 from the "
          "service")
    docs = dict(services)
    del docs[base + "/Utilities/Water/MapServer"]
    report = run(app, webmap, docs)
    check(statuses(report)["dataSource_1-18f00000002-layer-3-2"] == UNJUDGED,
          "a service that could not be read leaves its sublayers UNJUDGED, "
          "never ok  <-- pinned defect")
    check(exit_code(report) == 2,
          "and the run exits 2, because unread is not clean  <-- pinned defect")
    check(any("Water" in what for what, _ in report.unread),
          "and the unread service is named")

    # ---- a secured service answering anonymously
    docs = dict(services)
    docs[base + "/Utilities/Water/MapServer"] = {"layers": [], "tables": []}
    report = run(app, webmap, docs)
    check(statuses(report)["dataSource_1-18f00000002-layer-3-2"] == UNJUDGED,
          "an empty layer list is read as secured, not as a service that "
          "publishes nothing  <-- pinned defect")
    check(exit_code(report) == 2, "and exits 2 rather than 1")
    raises(lambda: service_ids({"error": {"code": 499,
                                          "message": "Token Required"}}),
           "a service error body is unread, not a service with no layers",
           Unread)
    exc = raises(lambda: service_ids({"error": {"code": 498, "message":
                                                "Invalid token."}}),
                 "a service answering 498 is unread", Unread)
    check("error 498: Invalid token." in "%s" % exc,
          "and the portal's message is kept, not 'not a service'  "
          "<-- pinned defect")
    raises(lambda: service_ids({"currentVersion": 11.1}),
           "a body with no layers or tables key is not a service description",
           Unread)
    raises(lambda: service_ids([1, 2]), "nor is a JSON array", Unread)
    check(service_ids({"layers": [{"id": 0}, "junk", {"name": "x"}],
                       "tables": [{"id": 4}]}) == frozenset(["0", "4"]),
          "layer and table ids are both published, and junk entries skipped")
    check(service_ids({"layers": 5, "tables": [{"id": 2}]})
          == frozenset(["2"]), "a layers key that is not a list is ignored")
    check(index_webmap({"operationalLayers": [
        {"id": "G", "layerType": "GroupLayer", "layers": 7}],
        "tables": 5})[0] == {"G": {"kind": "layer", "title": "G",
                                   "check": None, "opaque": None}},
          "a group's layers and a tables key that are not lists are ignored")

    # ---- a layers array with scale ranges is exclusive, one without is
    # only overrides, and the service still decides
    wm = copy(webmap)
    wm["operationalLayers"][0]["layers"] = [{"id": n, "minScale": 0}
                                            for n in range(16) if n != 1]
    reason = [v.reason for _, r, v in run(app, wm).findings
              if r.dsid.endswith("layer-25-1")][0]
    check("omits it, so it never exists in the browser" in reason,
          "a sublayer a layers array with a minScale omits is DANGLING "
          "without asking the service")
    wm["operationalLayers"][0]["layers"] = [{"id": 3, "popupInfo": {}}]
    report = run(app, wm)
    check(statuses(report)["dataSource_1-18f00000001-layer-25-1"] == OK and
          exit_code(report) == 0,
          "a Map Viewer Classic layers array, popups and no minScale, only "
          "overrides: a sublayer it omits resolves from the service  "
          "<-- pinned defect")
    wm["operationalLayers"][0]["layers"] = [
        {"id": 3, "layerDefinition": {"minScale": 5000}}, "junk"]
    check(statuses(run(app, wm))["dataSource_1-18f00000001-layer-25-1"]
          == DANGLING and statuses(run(app, wm))[
              "dataSource_1-18f00000001-layer-25-15"] == DANGLING,
          "and a minScale inside layerDefinition makes the array exclusive")
    docs = dict(services)
    docs[base + "/Planning/Zoning/MapServer"] = {"layers": [
        {"id": n} for n in range(14)]}
    got = run(app, webmap, docs)
    reason = [v.reason for _, r, v in got.findings
              if r.dsid.endswith("layer-25-15")][0]
    check(statuses(got)["dataSource_1-18f00000001-layer-25-15"] == DANGLING
          and "no longer publishes layer 15" in reason,
          "a sublayer the web map lists but a republish renumbered away is "
          "DANGLING")
    check(exit_code(got) == 1, "and a dangling reference exits 1")
    wm = copy(webmap)
    wm["operationalLayers"][0]["layers"] = []
    check(index_webmap(wm)[1]["18f00000001-layer-25"]["listed"] is None,
          "an empty layers array is read as absent: the service decides")
    wm["operationalLayers"][0]["url"] = None
    report = run(app, wm)
    got = statuses(report)
    reason = [v.reason for _, r, v in report.findings
              if r.dsid.endswith("layer-25-15")][0]
    check(got["dataSource_1-18f00000001-layer-25-15"] == UNJUDGED and
          "the web map layer has no service url to confirm it" in reason,
          "a map service layer with no url cannot be confirmed, so UNJUDGED, "
          "and says why")
    check(report.unread == [] and exit_code(report) == 2,
          "and the run exits 2 although every input was read  "
          "<-- pinned defect")
    check(describe(report)[-1] == "VERDICT: INCOMPLETE. Something could not "
          "be read, so this run proves nothing about it.",
          "and its verdict is incomplete, with nothing unread and nothing inert"
          "  <-- pinned defect")

    # ---- a map service layer added from a Map Image Layer item. When the
    # web map's array does not decide the sublayers, the item's /data can
    layer_item = "abcdef0123456789abcdef0123456789"
    water_2 = "dataSource_1-18f00000002-layer-3-2"
    wm = copy(webmap)
    wm["operationalLayers"][1]["itemId"] = layer_item
    decides = {"layers": [{"id": 0, "minScale": 0},
                          {"id": 1, "layerDefinition": {"minScale": 0}}]}
    report = run(app, wm, items={layer_item: decides})
    reason = [v.reason for _, r, v in report.findings if r.dsid == water_2][0]
    check(statuses(report)[water_2] == DANGLING and exit_code(report) == 1 and
          "the layers array of its layer item %s sets scale ranges and omits "
          "it" % layer_item in reason,
          "a sublayer the layer item's scale-range array omits is DANGLING, "
          "although the web map has no layers array and the service "
          "publishes it  <-- pinned defect")
    check(report.items_read == 1 and describe(report)[1].endswith(
        ", layer items read: 1 of 1"),
          "and the run counts the layer item it read")
    check([exit_code(run(app, wm, items={layer_item: data})) for data in
           (None, {}, {"layers": [{"id": 0, "popupInfo": {}}]}, [1])]
          == [0] * 4,
          "a layer item with no data, or with no scale ranges, leaves the "
          "service to decide")
    report = run(app, wm)
    reason = [v.reason for _, r, v in report.findings if r.dsid == water_2][0]
    check(statuses(report)[water_2] == UNJUDGED and exit_code(report) == 2 and
          "its layer item %s could not be read" % layer_item in reason and
          ("layer item %s" % layer_item, "no layer item") in report.unread,
          "a layer item that cannot be read leaves the sublayer UNJUDGED and "
          "the run exits 2, never 0  <-- pinned defect")
    raises(lambda: item_data({"error": {"code": 403, "message": "denied"}}),
           "a layer item error body is unread", Unread)
    report = run(only_map_of(wm_id), wm)
    check(exit_code(report) == 0 and report.items_total == 0,
          "a layer item is read only when a reference needs it")
    wm["operationalLayers"][1]["layers"] = [{"id": 2, "minScale": 0}]
    report = run(app, wm)
    check(statuses(report)[water_2] == OK and report.items_total == 0,
          "the web map's own scale-range array wins, and the item is not read")
    wm["operationalLayers"][1]["layers"] = []
    wm["operationalLayers"][1]["itemId"] = "../x"
    check(index_webmap(wm)[1]["18f00000002-layer-3"]["item"] is None,
          "an itemId that is not an item id is never read")

    # ---- feature layers, tables, groups
    docs = dict(services)
    docs[base + "/Hosted/Hydrants/FeatureServer"] = {"layers": [
        {"id": 0}, {"id": 1}]}
    got = run(app, webmap, docs)
    check(got.count(DANGLING) == 2,
          "a feature layer whose service no longer has index 3 is DANGLING")
    reason = [v.reason for _, r, v in got.findings
              if r.dsid.endswith("layer-4") and v.status == DANGLING][0]
    check(base + "/Hosted/Hydrants/FeatureServer no longer" in reason and
          "token" not in reason,
          "and the url it was checked against drops the token query string")
    check(statuses(run(app, webmap))["dataSource_1-Lookup_0"] == OK,
          "a web map table resolves by its own id, with no sublayer index")
    check(judge_child("Lookup_0-0", index, {}).status == DANGLING,
          "and a table id with an index glued on is DANGLING")
    got = statuses(run(app, webmap))
    check(got["dataSource_1-Group_5-18f00000004-layer-6"] == OK,
          "a group layer's child is <group>-<child>")
    check(got["dataSource_1-Group_5-18f00000005-layer-7-1"] == OK,
          "and a map service inside a group is <group>-<layer>-<sublayer>")
    check(judge_child("18f00000004-layer-6", index, {}).status == DANGLING,
          "a group child addressed without its group is DANGLING")
    check(judge_child("18f00000006-layer-8", index, {}).status == OK,
          "a layer with no service url resolves on the web map alone")
    check(judge_child("Group_5", index, {}).status == OK,
          "and so does the group itself")
    check(judge_child("18f00000099-layer-1", index, {}).status == DANGLING,
          "a layer the web map no longer holds is DANGLING")
    wm = copy(webmap)
    wm["operationalLayers"].append({"title": "no id"})
    wm["operationalLayers"].append("junk")
    wm["tables"].append({"id": "Plain_9"})
    entries = index_webmap(wm)[0]
    check("Plain_9" in entries and len(entries) == 9,
          "layers with no id and non-objects are skipped when indexing")
    check(entries["18f00000003-layer-4"]["check"] ==
          (base + "/Hosted/Hydrants/FeatureServer", "3"),
          "a feature layer is confirmed against its service root and index")
    lower = base + "/Hosted/Hydrants/featureserver"
    tiles = base + "/Basemaps/Tiles/MapServer"
    odd = index_webmap({"operationalLayers": [
        {"id": "L", "layerType": "ArcGISFeatureLayer", "url": lower + "/3"},
        {"id": "T", "layerType": "ArcGISTiledMapServiceLayer", "url": tiles}]})
    got = judge_child("L", odd, {lower: frozenset(["0", "1"])})
    check(got.status == DANGLING,
          "a layer url written .../featureserver/3 is still checked against "
          "its service  <-- pinned defect")
    check(judge_child("T-2", odd, {tiles: frozenset(["2"])}).status == OK and
          judge_child("T-5", odd, {tiles: frozenset(["2"])}).status
          == DANGLING,
          "a tiled map service layer's sublayers are read from its service")
    gone = {"widgets": {"w": {"config": {"configInfo": {
        "widget_9_output_123": {}}}}}, "dataSources": {}}
    report = run(gone)
    check([r.dsid for _, r, _ in report.findings] == ["widget_9_output_123"]
          and exit_code(report) == 1,
          "a configInfo key naming a widget output the app no longer declares "
          "is DANGLING")

    # ---- children of layer kinds the tool does not model
    svc_v = base + "/Utility/Valves/FeatureServer"
    opaque = index_webmap({"operationalLayers": [
        {"id": "18f00000009-layer-2", "layerType": "SubtypeGroupLayer",
         "title": "Valves", "url": svc_v + "/0", "layers": [
             {"id": "18f0000000a-layer-3", "subtypeCode": 1}]},
        {"id": "Sketch_4412", "layerType": "ArcGISFeatureLayer",
         "title": "Sketch", "featureCollection": {"layers": []}},
        {"id": "kg1", "layerType": "KnowledgeGraphLayer", "title": "Graph"},
        {"id": "Plain_1", "layerType": "ArcGISFeatureLayer",
         "url": svc_v + "/0"}]})
    valves = {svc_v: frozenset(["0"])}
    for child, kind in (("18f00000009-layer-2-18f0000000a-layer-3",
                         "subtype group layer 'Valves'"),
                        ("Sketch_4412-0", "feature collection 'Sketch'"),
                        ("kg1-Person", "knowledge graph layer 'Graph'")):
        got = judge_child(child, opaque, valves)
        check(got.status == NOT_AUDITED and kind in got.reason,
              "a child of a %s is NOT AUDITED, not DANGLING  "
              "<-- pinned defect" % kind.split(" '")[0])
    check(judge_child("kg10", opaque, valves).status == DANGLING,
          "a deleted layer kg10 is not a child of the knowledge graph kg1: "
          "the dash is part of the match  <-- pinned defect")
    check(judge_child("18f00000009-layer-2", opaque, valves).status == OK,
          "and the subtype group layer itself is still checked")
    check(judge_child("Plain_1-0", opaque, valves).status == DANGLING,
          "a child of a plain feature layer, which has none, stays DANGLING")

    # ---- the data sources an app declares
    got = judge("dataSource_3-18f00000001-layer-25-0", app["dataSources"],
                {wm_id: index}, {})
    check(got.status == DANGLING and "not a data source" in got.reason,
          "a reference to a data source the app no longer declares is "
          "DANGLING")
    check(judge("widget_2_output_1", app["dataSources"], {}, {}).status == OK,
          "a declared widget output resolves")
    check(judge("widget_2_output_1-x", app["dataSources"], {}, {}).status
          == NOT_AUDITED, "a child of a widget output is NOT AUDITED")
    dsm = {"dataSource_7": {"type": "WEB_SCENE"}, "dataSource_8": "junk"}
    check(judge("dataSource_7-layer-1", dsm, {}, {}).status == NOT_AUDITED,
          "a web scene's children are NOT AUDITED, and say so")
    check("untyped" in judge("dataSource_8-x", dsm, {}, {}).reason,
          "a data source that is not an object is treated as untyped")
    svc_url = base + "/Hosted/Parks/FeatureServer"
    fs = {"dataSource_9": {"type": "FEATURE_SERVICE", "url": svc_url},
          "dataSource_11": {"type": "MAP_SERVICE"},
          "dataSource_12": {"type": "FEATURE_LAYER", "url": svc_url + "/4"},
          "dataSource_13": {"type": "FEATURE_LAYER", "url": svc_url + "/4",
                            "isOutputFromWidget": True},
          "dataSource_14": {"type": "FEATURE_LAYER", "url": svc_url},
          "dataSource_15": {"type": "SUBTYPE_GROUP_LAYER",
                            "url": svc_url + "/4"}}
    known = {svc_url: frozenset(["0"])}
    check(judge("dataSource_9-0", fs, {}, known).status == OK,
          "a feature service data source's child is its layer index")
    check(judge("dataSource_9-2", fs, {}, known).status == DANGLING,
          "and an index the service does not publish is DANGLING")
    check(judge("dataSource_9-0", fs, {}, {}).need == svc_url,
          "and the service is asked for only when a reference needs it")
    got = judge("dataSource_11-0", fs, {}, {})
    check(got.status == UNJUDGED and "has no service url" in got.reason,
          "a service data source with no url is UNJUDGED, and says why")
    check(judge("dataSource_12", fs, {}, known).status == DANGLING,
          "a standalone feature layer whose index was removed is DANGLING")
    check(judge("dataSource_13", fs, {}, {}).status == OK,
          "a widget output is never checked against a service")
    check(judge("dataSource_14", fs, {}, {}).status == OK,
          "a feature layer url with no index is not checked")
    check(judge("dataSource_15", fs, {}, known).status == DANGLING,
          "a data source of any type whose url names a removed layer is "
          "DANGLING")
    odd = {"dataSource_1": {"type": "WEB_MAP", "itemId": [wm_id]}}
    got = judge("dataSource_1-L-0", odd, {wm_id: index}, {})
    check(got.status == UNJUDGED and "names no web map item id" in got.reason,
          "a web map item id that is not a string is UNJUDGED, not a crash")
    odd["dataSource_2"] = {"type": "WEB_MAP", "itemId": 5}
    odd["dataSource_3"] = {"type": "WEB_MAP", "itemId": wm_id}
    check(webmap_items({"dataSources": odd}) == set([wm_id]),
          "and only string item ids are fetched  <-- pinned defect")
    odd["dataSource_4"] = {"type": "FEATURE_LAYER", "itemId": "f" * 32}
    check(webmap_items({"dataSources": odd}) == set([wm_id]),
          "and a hosted feature layer's item id is not taken for a web map")
    check(member(svc_url, "0", {svc_url: "boom"}, "x").status == UNJUDGED,
          "a service remembered as unreadable stays UNJUDGED")
    got = judge("dataSource_1-18f00000006-layer-8", app["dataSources"],
                {wm_id: "error 403: denied"}, {})
    check(got.status == UNJUDGED and "403" in got.reason,
          "a web map that could not be read leaves its children UNJUDGED")
    got = judge("dataSource_1-18f00000006-layer-8", app["dataSources"], {}, {})
    check(got.status == UNJUDGED and "never fetched" in got.reason,
          "and one that was never fetched says so")

    # ---- collecting references
    refs = collect_refs(app)
    paths = [r.path for r in refs]
    check("widgets.widget_2.useDataSources[1].dataSourceId" in paths,
          "a useDataSources dataSourceId is collected with its path")
    check("widgets.widget_3.config.configInfo{dataSource_1}" in paths,
          "a configInfo key that is a data source id is collected")
    check(not [p for p in paths if "someOtherKey" in p],
          "and a configInfo key of another shape is not")
    named = {"widgets": {"w": {"config": {"configInfo": {"parcels": {}}}}},
             "dataSources": {"parcels": {"type": "FEATURE_LAYER"}}}
    check([r.dsid for r in collect_refs(named)] == ["parcels"],
          "unless the app declares it as a data source id")
    gone = copy(app)
    gone["widgets"]["widget_3"]["config"]["configInfo"] = {"dataSource_3": {}}
    got = statuses(run(gone, webmap))
    check(got.get("dataSource_3") == DANGLING,
          "a configInfo key naming a data source the app no longer declares "
          "is collected and DANGLING  <-- pinned defect")
    check(not [p for p in paths if p.endswith("layersConfig[0].id")],
          "a table's layersConfig id, which only embeds an id, is not "
          "collected  <-- pinned defect")
    check([r.owner for r in refs if r.path.startswith("widgets.widget_2")][0]
          == "widget_2 (Zoning search)", "the owner is the widget and its "
          "label")
    check([r.owner for r in refs if r.path.startswith("dataSources")][0]
          == "widget_2_output_1",
          "an output's origin reference is owned by the output data source")
    check(len([p for p in paths if "mapDataSourceId" in p or
               "initialMapDataSourceID" in p]) == 2,
          "bookmarks and the map widget's initial map are collected")
    view = {"widgets": {"w": {"useDataSources": [
        {"dataSourceId": "dataSource_1-L-2-selection",
         "mainDataSourceId": "dataSource_1-L-2"}]}}, "dataSources": {},
        "top": ["x", {"dataSourceId": "dataSource_1"}]}
    ids = [r.dsid for r in collect_refs(view)]
    check(ids == ["dataSource_1-L-2", "dataSource_1"],
          "a data view id is judged through its main data source, and a "
          "reference in a top-level array is still collected")
    check([r.owner for r in collect_refs(view)][1] == "top",
          "and owned by its top-level key")

    def view_ids(use_ds):
        return [r.dsid for r in collect_refs(
            {"widgets": {"w": {"useDataSources": [use_ds]}}})]
    check(view_ids({"dataSourceId": "dataSource_1-L-99",
                    "mainDataSourceId": "dataSource_1-L"})
          == ["dataSource_1-L-99", "dataSource_1-L"],
          "a numeric suffix after the main id is a sublayer, not a data view, "
          "so it is judged  <-- pinned defect")
    check(len(view_ids({"dataSourceId": "dataSource_1-L-2-x",
                        "mainDataSourceId": "dataSource_1"})) == 2,
          "and so is a suffix with a dash in it")
    check(view_ids({"dataSourceId": "dataSource_1-L-view_7",
                    "mainDataSourceId": "dataSource_1-L",
                    "dataViewId": "view_7"}) == ["dataSource_1-L"],
          "a data view named by dataViewId is judged through its main")
    check(len(view_ids({"dataSourceId": "dataSource_1-L-other",
                        "mainDataSourceId": "dataSource_1-L",
                        "dataViewId": "view_7"})) == 2,
          "and a suffix that is not that dataViewId is judged")
    # Listed here, not read from CONTAINER_TYPES: a type deleted from the
    # constant must fail its own assertion.
    for dtype in ("WEB_MAP", "WEB_SCENE", "MAP_SERVICE", "FEATURE_SERVICE"):
        got = collect_refs({"widgets": {"w": {"useDataSources": [
            {"dataSourceId": "dataSource_1-Parcels_4077",
             "mainDataSourceId": "dataSource_1",
             "rootDataSourceId": "dataSource_1"}]}},
            "dataSources": {"dataSource_1": {"type": dtype}}})
        check([r.dsid for r in got][0] == "dataSource_1-Parcels_4077",
              "a one-word suffix under a %s main is a layer, not a data "
              "view, so it is judged  <-- pinned defect" % dtype)
    report = run({"widgets": {"w": {"useDataSources": [
        {"dataSourceId": "dataSource_1-Parcels_4077",
         "mainDataSourceId": "dataSource_1",
         "rootDataSourceId": "dataSource_1"}]}},
        "dataSources": {"dataSource_1": app["dataSources"]["dataSource_1"]}},
        {"operationalLayers": []})
    check(exit_code(report) == 1 and report.count(DANGLING) == 1,
          "and a layer the web map no longer holds exits 1, not a clean 0  "
          "<-- pinned defect")
    check(view_ids({"dataSourceId": "dataSource_1-L-selection",
                    "mainDataSourceId": "dataSource_1-L",
                    "dataViewId": 7}) == ["dataSource_1-L"] and
          view_ids({"dataSourceId": "dataSource_1-L-selection",
                    "mainDataSourceId": "dataSource_1-L",
                    "dataViewId": ""}) == ["dataSource_1-L"],
          "a dataViewId that is not a non-empty string falls back to the "
          "one-word rule")
    top = run({"widgets": {}, "dataSources": {},
               "top": [{"dataSourceId": "dataSource_9"}]})
    check(statuses(top) == {"dataSource_9": DANGLING} and exit_code(top) == 1,
          "a dangling reference in a top-level array is DANGLING, not inert  "
          "<-- pinned defect")
    check(collect_refs({"widgets": {"w": {"config": {"byId": {
        "dataSource_5": {"dataSourceId": "dataSource_1"}}}}},
        "dataSources": {"dataSource_5": {}}})[0].path
        == "widgets.w.config.byId.dataSource_5.dataSourceId" and
        len(collect_refs({"widgets": {"w": {"config": {"byId": {
            "dataSource_5": {}}}}}, "dataSources": {"dataSource_5": {}}}))
        == 0, "a key shaped like a data source id is a reference only in "
        "configInfo  <-- pinned defect")
    action = {"widgets": {}, "dataSources": {}, "messageConfigs": {
        "messageConfig_8": {"actions": [{"config": {"actionUseDataSource": {
            "dataSourceId": "dataSource_1-L-0",
            "mainDataSourceId": "dataSource_1-L-0",
            "rootDataSourceId": "dataSource_1"}}}]}}}
    found = collect_refs(action)
    check(len(found) == 3 and found[0].owner == "messageConfig_8" and
          found[0].path.startswith("messageConfigs.messageConfig_8.actions[0]"),
          "a message action's data source is collected and owned by its "
          "message config")
    view = "widget_1-dataSource_1"
    swipe = copy(app)
    swipe["widgets"]["widget_7"] = {"label": "Swipe", "config": {
        "swipeMapViewList": {view: {
            "leadingLayersId": [view + "-18f00000099-layer-1", 5],
            "trailingLayersId": [view + "-18f00000001-layer-25-15"]}}}}
    # widget_1-dataSource_4 is a map view the map widget left behind when
    # it moved to dataSource_1. The builder keeps its block.
    old = "widget_1-dataSource_4"
    swipe["widgets"]["widget_8"] = {"label": "Map Layers", "config": {
        "customizeLayerOptions": {view: {
            "showJimuLayerViewIds": [view + "-Group_5-18f00000004-layer-6"],
            "hiddenJimuLayerViewIds": [view + "-18f00000003-layer-4"]},
            old: {"showJimuLayerViewIds": [old + "-L1", old + "-L2"]}}}}
    refs = [r for r in collect_refs(swipe)
            if r.owner.startswith(("widget_7", "widget_8"))]
    found = [(r.dsid, r.path.split(".config.")[1]) for r in refs]
    check(found == [
        ("dataSource_1", "swipeMapViewList{%s}" % view),
        ("dataSource_1-18f00000099-layer-1",
         "swipeMapViewList.%s.leadingLayersId[0]" % view),
        ("dataSource_1-18f00000001-layer-25-15",
         "swipeMapViewList.%s.trailingLayersId[0]" % view),
        ("dataSource_1", "customizeLayerOptions{%s}" % view),
        ("dataSource_1-Group_5-18f00000004-layer-6", "customizeLayerOptions."
         "%s.showJimuLayerViewIds[0]" % view),
        ("dataSource_1-18f00000003-layer-4", "customizeLayerOptions."
         "%s.hiddenJimuLayerViewIds[0]" % view),
        ("dataSource_4", "customizeLayerOptions{%s}" % old)],
          "Swipe and Map Layers layer view ids and map view keys are "
          "collected, each with its widget id stripped  <-- pinned defect")
    check([r.inert for r in refs] == [""] * 6 + [STALE_VIEW],
          "a block for a map view the map widget no longer shows is one "
          "inert key, and nothing inside it is collected  <-- pinned defect")
    report = run(swipe, webmap)
    got = statuses(report)
    check(got["dataSource_1-18f00000099-layer-1"] == DANGLING and
          got["dataSource_4"] == INERT and
          got["dataSource_1-18f00000001-layer-25-15"] == OK and
          exit_code(report) == 1,
          "a Swipe layer the web map lost exits 1, not a clean 0  "
          "<-- pinned defect")
    del swipe["widgets"]["widget_7"]
    swipe["widgets"]["widget_9"] = {"label": "Swipe", "config": {
        "swipeMapViewList": {"widget_1-dataSource_3": {
            "leadingLayersId": ["widget_1-dataSource_3-L5"]}}}}
    report = run(swipe, webmap)
    lines = describe(report)
    check(exit_code(report) == 0 and report.count(INERT) == 2 and
          report.count(DANGLING) == 0 and lines[-1] == "VERDICT: every "
          "widget binding resolves, but 2 reference(s) that no widget reads "
          "do not: see INERT." and [x for x in lines if STALE_VIEW in x],
          "a stale Map Layers or Swipe block the builder left behind is one "
          "INERT note each and exits 0, not N DANGLING and 1  "
          "<-- pinned defect")
    check(collect_refs({"widgets": {"w": 5, "v": {"useDataSources": [
        5, {"dataSourceId": 7}]}}, "dataSources": {}}) == [],
          "a widget or a useDataSources entry of the wrong type names no "
          "map view and no reference, and does not crash")
    got = run(app, webmap)
    inert = [r for _, r, _ in got.findings if r.inert]
    check(len(inert) == 3 and all("layersConfig" in r.path for r in inert),
          "references inside a MAP-mode table's layersConfig are marked inert")
    beside = {"widgets": {"t": {"config": {"tableMode": "MAP",
                                           "layersConfig": [], "other": {
                                               "useDataSource": {
                                                   "dataSourceId":
                                                   "dataSource_1-L-9"}}}}},
              "dataSources": {"dataSource_1": {"type": "WEB_MAP",
                                               "itemId": wm_id}}}
    report = run(beside, nested_wm, {svc_x: {"layers": [{"id": 0}]}})
    check(statuses(report) == {"dataSource_1-L-9": DANGLING} and
          exit_code(report) == 1,
          "a dangling reference beside layersConfig in a MAP-mode table's "
          "config is DANGLING, exit 1, not INERT  <-- pinned defect")

    # ---- inert and divergence
    broken = copy(app)
    cfg = broken["widgets"]["widget_4"]["config"]
    cfg["layersConfig"][0]["useDataSource"] = {
        "dataSourceId": "dataSource_1-18f00000003-layer-9",
        "mainDataSourceId": "dataSource_1-18f00000003-layer-9"}
    report = run(broken, webmap)
    check(report.count(INERT) == 2 and exit_code(report) == 0,
          "a stale entry a MAP-mode table ignores is INERT and does not fail "
          "the run")
    cfg["tableMode"] = "LAYERS"
    report = run(broken, webmap)
    check(report.count(DANGLING) == 2 and exit_code(report) == 1,
          "the same entry in a table that reads layersConfig is DANGLING")
    report = run(app, webmap, resource=copy(app))
    check(not report.diverged and exit_code(report) == 0,
          "two identical configuration copies do not diverge")
    styled = copy(app)
    styled["widgets"]["widget_6"]["config"]["text"] = "<p style='x'>hi</p>"
    report = run(app, webmap, resource=styled)
    check(not report.diverged,
          "copies that differ only in rich text do not diverge  "
          "<-- pinned defect")
    moved = copy(app)
    moved["widgets"]["widget_2"]["useDataSources"][1] = {
        "dataSourceId": "dataSource_1-18f00000001-layer-25-2",
        "mainDataSourceId": "dataSource_1-18f00000001-layer-25-2"}
    report = run(app, webmap, resource=moved)
    check(report.diverged and exit_code(report) == 0,
          "a widget bound to another layer in the draft diverges, and exits "
          "0 because both bindings resolve")
    check(len(report.only_data) == 3 and len(report.only_resource) == 2,
          "and both sides of the difference are reported")
    check(len(report.surfaces) == 2 and report.surfaces[1][0] == RESOURCE,
          "both copies are audited, not only the one the builder shows")
    rebound = copy(app)
    rebound["dataSources"]["dataSource_1"]["itemId"] = wm_id[::-1]
    check(run(app, webmap, resource=rebound).diverged,
          "a data source bound to another item in one copy diverges")
    retyped = copy(app)
    retyped["dataSources"]["widget_2_output_1"]["type"] = "TABLE"
    check(run(app, webmap, resource=retyped).diverged,
          "and so does a data source whose type changed in one copy")
    stored = copy(app)
    stored["dataSources"]["widget_2_output_1"]["url"] = (
        base + "/Hosted/Parks/FeatureServer?token=STORED-AAA")
    rotated = copy(stored)
    rotated["dataSources"]["widget_2_output_1"]["url"] = (
        base + "/Hosted/Parks/FeatureServer?token=STORED-BBB")
    check(not run(stored, webmap, resource=rotated).diverged,
          "a stored token that was rotated in one copy is not a divergence")
    moved_url = copy(rotated)
    moved_url["dataSources"]["widget_2_output_1"]["url"] = (
        base + "/Hosted/Other/FeatureServer?token=STORED-BBB")
    text = "\n".join(describe(run(stored, webmap, resource=moved_url)))
    check("DIVERGED" in text and "STORED" not in text,
          "and a divergence line never prints a token stored in a url  "
          "<-- pinned defect")

    # ---- unread inputs and vacuous passes
    report = run({"error": {"code": 400, "message": "Item does not exist"}})
    check(exit_code(report) == 2 and "400" in report.unread[0][1],
          "an error body where the app should be is unread, exit 2  "
          "<-- pinned defect")
    report = run({"pages": {}})
    check(exit_code(report) == 2 and report.findings == [],
          "a JSON file that is not an app config is not a clean app  "
          "<-- pinned defect")
    raises(lambda: check_app({"widgets": {}}),
           "a config with widgets and no dataSources object is unread", Unread)
    raises(lambda: check_app({"dataSources": {}}),
           "and so is one with dataSources and no widgets object", Unread)
    report = run(app, None)
    check(exit_code(report) == 2 and report.count(UNJUDGED) == 18,
          "a web map that cannot be read makes its children UNJUDGED and "
          "the run exit 2")
    only_map = {"widgets": {"widget_1": {"useDataSources": [
        {"dataSourceId": "dataSource_1"}]}},
        "dataSources": {"dataSource_1": app["dataSources"]["dataSource_1"]}}
    report = run(only_map, None)
    check(exit_code(report) == 2 and report.count(OK) == 1 and
          report.unread == [("web map %s" % wm_id, "no web map")],
          "a map widget bound to a web map that cannot be read exits 2, not "
          "0, although its one reference names no layer  <-- pinned defect")
    exc = raises(lambda: index_webmap({"error": {"code": 498, "message":
                                                 "Invalid token."}}),
                 "a web map error body is unread", Unread)
    check("error 498: Invalid token." in "%s" % exc,
          "and the portal's message is kept, not 'not a web map'  "
          "<-- pinned defect")
    raises(lambda: index_webmap({"version": "2.3"}),
           "a web map with no operationalLayers is unread", Unread)
    report = run(app, webmap, resource={"error": {"code": 404,
                                                  "message": "missing"}})
    check(exit_code(report) == 2 and report.unread[0][0] == RESOURCE,
          "an unreadable resource is named and exits 2, even when the item "
          "data is clean")
    dangling_and_unread = run(broken, webmap, {})
    check(exit_code(dangling_and_unread) == 2,
          "unread beats dangling in the exit code")
    empty = {"widgets": {}, "dataSources": {}}
    report = run(empty)
    check(exit_code(report) == 0 and report.findings == [],
          "an app with no widgets bound to data is clean with 0 references")

    # ---- the printed report
    report = run(broken, webmap, resource=moved)
    text = "\n".join(describe(report))
    check("DANGLING     dataSource_1-18f00000003-layer-9" in text,
          "the report names the dangling id")
    check("widget_4 (Hydrant table), in the published copy" in text,
          "and the widget and copy it sits in")
    check("VERDICT: 1 widget binding(s) point at a layer that does not "
          "exist. The builder draft and the published copy bind widgets "
          "differently: see DIVERGED." in text,
          "and a verdict that counts bindings, not id occurrences, and "
          "names the divergence")
    check("DIVERGED     the builder draft and the published copy" in text,
          "config/config.json is named the builder draft and the item data "
          "the published copy  <-- pinned defect")
    mixed = copy(broken)
    mixed["widgets"]["widget_6"]["config"]["bookmarks"][0][
        "mapDataSourceId"] = "widget_2_output_1-x"
    report = run(mixed, webmap)
    text = "\n".join(describe(report))
    check(exit_code(report) == 2 and "NOT AUDITED  widget_2_output_1-x" in text
          and "DANGLING     dataSource_1-18f00000003-layer-9" in text and
          "VERDICT: INCOMPLETE. 1 reference(s) are of a kind this tool does "
          "not audit" in text,
          "a reference that is not audited makes the run incomplete, exit 2, "
          "even beside a dangling one  <-- pinned defect")
    mixed = copy(broken)
    mixed["widgets"]["widget_9"] = {"config": {"tableMode": "MAP",
                                               "layersConfig": [
                                                   {"useDataSource": {
                                                       "dataSourceId":
                                                       "dataSource_1-gone"}}]}}
    text = "\n".join(describe(run(mixed, webmap)))
    check("INERT        dataSource_1-gone" in text and
          "VERDICT: 1 widget binding(s) point" in text,
          "the verdict counts only dangling bindings, not other findings")
    check("(and 1 more path(s))" in text,
          "a reference repeated at several paths is printed once")
    scene = {"widgets": {"widget_2": {"label": "Parcel search",
                                      "useDataSources": [{
                                          "dataSourceId": "dataSource_7-x-99",
                                          "rootDataSourceId": "dataSource_7"}]
                                      }},
             "dataSources": {"dataSource_7": {"type": "WEB_SCENE",
                                              "itemId": wm_id}}}
    report = run(scene)
    text = "\n".join(describe(report))
    check(exit_code(report) == 2 and report.count(NOT_AUDITED) == 1 and
          "every data source reference resolves" not in text and
          "references: 2 found," in text,
          "a web scene app bound to a layer nobody checked exits 2, never 0 "
          "with a clean verdict  <-- pinned defect")
    text = "\n".join(describe(run(broken, webmap, resource=copy(broken))))
    check("in the published copy and the builder draft" in text and
          text.count("DANGLING     ") == 1 and
          "useDataSource.dataSourceId (and 1 more path(s))" in text,
          "a reference dangling in both copies is printed once, naming both")
    single = copy(app)
    single["widgets"]["widget_6"]["config"]["bookmarks"][0][
        "mapDataSourceId"] = "dataSource_3"
    lines = describe(run(single, webmap))
    check("             at widgets.widget_6.config.bookmarks[0]."
          "mapDataSourceId" in lines,
          "a reference at one path is printed with that path alone")
    many = copy(app)
    for n in range(8):
        many["widgets"]["widget_x%d" % n] = {"useDataSources": [
            {"dataSourceId": "dataSource_1"}]}
    text = "\n".join(describe(run(app, webmap, resource=many)))
    check("... and 3 more" in text and
          text.count("only in the builder draft: ") == 6 and
          "only in the published copy" not in text,
          "a long divergence is cut to a sample")
    check("VERDICT: every data source reference resolves. The builder draft "
          "and the published copy bind widgets differently" in text,
          "and a divergence alone does not claim a dangling binding")
    lines = describe(run(app, webmap, {}))
    check("6 input(s) COULD NOT BE READ, so they are not clean, they are "
          "unknown:" in lines and lines[-1] == "VERDICT: INCOMPLETE. "
          "Something could not be read, so this run proves nothing about it.",
          "an incomplete run counts what it could not read and says it proves "
          "nothing  <-- pinned defect")
    lines = describe(run(app, webmap))
    check(lines[-1] == "VERDICT: every data source reference resolves." and
          not [x for x in lines if "DIVERGED" in x or "COULD NOT" in x],
          "a clean run says so, and nothing else  <-- pinned defect")
    # Five statuses with five different counts, so a swap of any two shows.
    mix = {"widgets": {"widget_1": {"config": {"bookmarks": [
        {"mapDataSourceId": dsid} for dsid in
        ["dataSource_1"] * 5 + ["dataSource_3"] * 4 +
        ["dataSource_11-0"] * 3 + ["dataSource_7-x"]]}},
        "widget_2": {"config": {"tableMode": "MAP", "layersConfig": [
            {"mapDataSourceId": "dataSource_1-gone"}] * 2}}},
        "dataSources": {"dataSource_1": app["dataSources"]["dataSource_1"],
                        "dataSource_11": {"type": "MAP_SERVICE"},
                        "dataSource_7": {"type": "WEB_SCENE"}}}
    report = run(mix, webmap)
    lines = describe(report)
    check("references: 15 found, 5 ok, 4 dangling, 3 unjudged, 2 inert, "
          "1 not audited" in lines,
          "the counts line names each status with its own count  "
          "<-- pinned defect")
    check(lines[-1] == "VERDICT: INCOMPLETE. Something could not be read, "
          "so this run proves nothing about it.",
          "an unjudged reference makes the verdict incomplete")
    doc = document(report, "app.json")
    check(doc["counts"] == {"found": 15, "ok": 5, "dangling": 4,
                            "unjudged": 3, "inert": 2, "notAudited": 1} and
          doc["exit"] == 2 and doc["unread"] == [] and
          [f["status"] for f in doc["findings"]] ==
          [DANGLING, UNJUDGED, NOT_AUDITED, INERT],
          "the report document carries every count, the exit and the "
          "findings worst first  <-- pinned defect")
    doc = document(run(broken, webmap), "app.json")
    check(doc["exit"] == 1 and doc["counts"]["dangling"] == 2 and
          doc["findings"][0]["status"] == DANGLING,
          "the report document carries the exit, the counts and the findings")
    lines = describe(run(broken, webmap))
    check(lines[-1] == "VERDICT: 1 widget binding(s) point at a layer that "
          "does not exist.", "a dangling run with one copy names no "
          "divergence")
    stale = copy(app)
    stale["widgets"]["widget_4"]["config"]["layersConfig"][0][
        "useDataSource"] = {"dataSourceId": "dataSource_1-18f00000003-layer-9"}
    lines = describe(run(stale, webmap))
    check(lines[-1] == "VERDICT: every widget binding resolves, but 1 "
          "reference(s) that no widget reads do not: see INERT."
          and "every data source reference resolves" not in "\n".join(lines),
          "an INERT reference is not called resolved  <-- pinned defect")
    check([x for x in lines if "so this entry makes no tab. If the layer was "
           "renumbered, its new tab lost the settings" in x],
          "and it says a renumbered layer lost the entry's settings")

    # ---- small helpers
    check(service_root("https://h/a/MapServer/3/?x=1") ==
          ("https://h/a/MapServer", "3") and
          service_root("https://h/a/MapServer/3#f") ==
          ("https://h/a/MapServer", "3"), "a layer url splits into service "
          "and index, without its query string or fragment")
    check(service_root(None) == (None, None) and
          service_root("https://h/a/ImageServer") == (None, None),
          "a url that is not a map or feature service is not a service")
    check(rest_root("https://org.example.com/portal/home/") ==
          "https://org.example.com/portal/sharing/rest",
          "a portal url pasted from the browser still finds sharing/rest")
    check(rest_root("https://org.example.com/portal/sharing/rest") ==
          "https://org.example.com/portal/sharing/rest",
          "and one that already ends in sharing/rest is not doubled")
    check(redact("a tok+en/x b tok%2Ben%2Fx", "tok+en/x") == "a *** b ***",
          "a token is redacted raw and url-quoted")
    check(redact("t=a+b%2F u=a%20b%2F v=a b/", "a b/") == "t=*** u=*** v=***",
          "a token with a space is redacted in both url forms  "
          "<-- pinned defect")
    check(redact("x", None) == "x", "and nothing is redacted with no token")
    portal = "https://org.maps.arcgis.com"
    check(sends_token("https://org.maps.arcgis.com/x", portal),
          "the token goes to the portal")
    check(sends_token("https://services3.arcgis.com/x", portal),
          "and to an ArcGIS Online services host")
    check(not sends_token("https://services3.arcgis.com/x",
                          "https://gis.example.com/portal"),
          "but an Enterprise portal's token never goes to arcgis.com  "
          "<-- pinned defect")
    check(not sends_token("https://gis.example.com/x", portal),
          "but not to a server on another host  <-- pinned defect")
    check(not sends_token("https://services.evilarcgis.com/x", portal) and
          not sends_token("https://services3.arcgis.com/x",
                          "https://org.evilarcgis.com"),
          "nor to a host that only ends in the letters arcgis.com  "
          "<-- pinned defect")
    check(not sends_token("https://[gis.example.com/x/MapServer", portal),
          "and a url urllib cannot parse gets no token, and no crash")
    check(sends_token("https://gis.example.com/x", portal,
                      {"gis.example.com"}),
          "unless that host is trusted by name")
    check(not sends_token("http://org.maps.arcgis.com/x", portal),
          "and never over plain http  <-- pinned defect")
    check(sends_token("http://127.0.0.1:1/x", "http://127.0.0.1:1"),
          "except to this machine")
    check(reads_host("https://org.maps.arcgis.com:6443/x", portal) and
          reads_host("http://gis.example.com/x", portal, {"gis.example.com"})
          and not reads_host("http://10.0.0.5/x/MapServer", portal),
          "online mode reads the portal's host on any port and a trusted "
          "host, and nothing a web map merely names  <-- pinned defect")
    check(bind_webmaps(["wm.json"], {wm_id}) == {wm_id: "wm.json"},
          "one unnamed web map file binds to the app's one web map")
    check(bind_webmaps(["%s=a.json" % wm_id, "b.json"], {wm_id, "b" * 32})
          == {wm_id: "a.json", "b" * 32: "b.json"},
          "a named file binds to its item and the loose one to the other")
    raises(lambda: bind_webmaps(["a.json"], {wm_id, "b" * 32}),
           "an unnamed file with two web maps in the app is refused")
    exc = raises(lambda: bind_webmaps(["a.json"], set()),
                 "and so is one for an app that names no web map")
    check("web map item(s): none" in "%s" % exc,
          "and the refusal says the app names none")
    check(parse_services(["%s/x/MapServer=c.json" % base]) ==
          {base + "/x/MapServer": "c.json"},
          "--service splits at the first = after the url")
    raises(lambda: parse_services(["c.json"]), "--service with no url is "
           "refused")
    raises(lambda: parse_services(["https://h/a/ImageServer=c.json"]),
           "and so is a url that is not a map or feature service")

    # ---- the command line, offline
    tmp = tempfile.mkdtemp(prefix="deadwidget-selftest-")
    atexit.register(shutil.rmtree, tmp, True)

    def put(name, doc, raw=None):
        path = os.path.join(tmp, name)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(raw if raw is not None else json.dumps(doc))
        return path

    def run_cli(argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            try:
                code = main(argv, {} if env is None else {TOKEN_ENV: env})
            except SystemExit as exc:
                code = exc.code
        finally:
            sys.stdout, sys.stderr = saved
        return code, out.getvalue(), err.getvalue()

    app_file = put("app.json", app)
    broken_file = put("broken.json", broken)
    moved_file = put("resource.json", moved)
    wm_file = put("webmap.json", webmap)
    svc_args = []
    for url in sorted(services):
        svc_args += ["--service", "%s=%s" % (url, put(
            "svc%d.json" % len(svc_args), services[url]))]
    code, out, err = run_cli([app_file, "--webmap", wm_file] + svc_args)
    check(code == 0 and "33 data source reference(s)" in out and
          "Check only" not in out,
          "offline: the clean app exits 0 after checking 33 references")
    code, out, err = run_cli([broken_file, "--webmap", "%s=%s" % (
        wm_id, wm_file)] + svc_args)
    check(code == 1 and "DANGLING" in out,
          "offline: the broken app exits 1, with the web map named by item")
    code, out, err = run_cli([app_file, "--resource", moved_file, "--webmap",
                              wm_file] + svc_args)
    check(code == 0 and "DIVERGED" in out,
          "offline: --resource compares the two copies, and a divergence "
          "alone exits 0")
    code, out, err = run_cli([app_file, "--webmap", wm_file])
    check(code == 2 and "no --service file was given" in out,
          "offline: a service no --service file covers is unread, exit 2")
    code, out, err = run_cli([app_file])
    check(code == 2 and "no --webmap file was given" in out,
          "offline: a web map no --webmap file covers is unread, exit 2")
    code, out, err = run_cli([os.path.join(tmp, "absent.json")])
    check(code == 2 and "COULD NOT BE READ" in out,
          "offline: a missing app file exits 2")
    code, out, err = run_cli([os.path.join(tmp, "absent.json"), "--webmap",
                              wm_file])
    check(code == 2 and "COULD NOT BE READ" in out and err == "",
          "offline: a missing app with an unnamed --webmap exits 2, not as a "
          "usage error  <-- pinned defect")
    deep = put("deep.json", None, raw="[" * 100000 + "]" * 100000)
    raises(lambda: read_json_file(deep),
           "a file nested too deep to parse is unread, not a crash", Unread)
    label = copy(app)
    label["widgets"]["widget_2"]["label"] = u"\u2192 Zoning \u6c34"
    label["widgets"]["widget_2"]["useDataSources"][0]["dataSourceId"] = (
        "dataSource_3")
    code, out, err = run_cli([put("label.json", label), "--webmap",
                              wm_file] + svc_args)
    check(code == 1 and "\\u2192 Zoning \\u6c34" in out and
          all(ord(c) < 128 for c in out),
          "a label outside ASCII is printed escaped, so a cp1252 stdout "
          "cannot crash the report  <-- pinned defect")
    forged = copy(app)
    forged["widgets"]["widget_2"]["label"] = (
        "\x1b]0;x\x07\x1b[2JMap\n\nVERDICT: every data source reference "
        "resolves.\r\x7f")
    forged["widgets"]["widget_2"]["useDataSources"][0]["dataSourceId"] = (
        "dataSource_3")
    code, out, err = run_cli([put("forged.json", forged), "--webmap",
                              wm_file] + svc_args)
    check(code == 1 and len([x for x in out.splitlines()
                             if x.startswith("VERDICT:")]) == 1 and
          "\\x1b]0;x\\x07\\x1b[2JMap\\x0a\\x0aVERDICT: every" in out and
          "\\x0d\\x7f)" in out and
          not [c for c in out if ord(c) < 32 and c != "\n" or ord(c) == 127],
          "a label's control characters are printed escaped, so it cannot "
          "drive the terminal or forge a VERDICT line  <-- pinned defect")
    real_audit = globals()["audit"]

    def crash(*args):
        raise TypeError("unforeseen, token %s" % "Kq7-SECRET")
    globals()["audit"] = crash
    try:
        code, out, err = run_cli([app_file, "--token", "Kq7-SECRET"])
    finally:
        globals()["audit"] = real_audit
    check(code == 2 and "could not complete" in err and
          "Kq7-SECRET" not in err,
          "a crash nobody foresaw exits 2, never 1, and is redacted  "
          "<-- pinned defect")

    class Gone(object):
        """stdout after the reader closed the pipe."""

        def __init__(self, fd):
            self.fd = fd

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def fileno(self):
            return self.fd
    small = put("small.json", {"widgets": {"w": {"useDataSources": [
        {"dataSourceId": "dataSource_9"}]}}, "dataSources": {}})
    clean = put("clean.json", {"widgets": {"w": {"useDataSources": [
        {"dataSourceId": "dataSource_2"}]}}, "dataSources": {
            "dataSource_2": {"type": "FEATURE_LAYER"}}})
    sink = os.open(os.path.join(tmp, "sink"), os.O_WRONLY | os.O_CREAT)
    saved = sys.stdout, sys.stderr
    try:
        check(flushed(1, Gone(sink)) == 2 and flushed(1, io.StringIO()) == 1,
              "output cut off by | head exits 2, not 1 or 120  "
              "<-- pinned defect")
        # 2>&1 | head: the error line about the dead stdout goes to the same
        # dead pipe, and that second failure must not escape main.
        sys.stdout = sys.stderr = Gone(sink)
        code = main([small])
        code = flushed(code, sys.stdout)
        sys.stdout, sys.stderr = saved
        check(code == 2, "and so does 2>&1 | head, where stderr is the same "
              "dead pipe  <-- pinned defect")
        # pythonw, or a closed fd 1 or 2, leaves the stream None.
        sys.stdout = sys.stderr = None
        codes = [flushed(main([arg]), sys.stdout)
                 for arg in (clean, small, os.path.join(tmp, "absent.json"))]
        try:
            main([clean, "--bogus"])
        except SystemExit as exc:
            codes.append(exc.code)
        sys.stdout = io.StringIO()
        codes.append(_usage("x"))
        codes.append(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = saved
        os.close(sink)
    check(codes == [0, 1, 2, 64, 64, ""], "with no stdout or stderr at all, "
          "as under pythonw, each exit code stands and none becomes a "
          "traceback's 1, and no error line strays into stdout  "
          "<-- pinned defect")
    # The 120 comes from the flush at interpreter shutdown, so only a real
    # process can show it. Its pipe has no reader from the start.
    reader, writer = os.pipe()
    os.close(reader)
    try:
        code = subprocess.call([sys.executable, os.path.abspath(__file__),
                                small], stdout=writer,
                               stderr=subprocess.DEVNULL, timeout=60)
    finally:
        os.close(writer)
    check(code == 2, "and a real run whose reader is gone exits 2 at "
          "shutdown, not 1 or 120  <-- pinned defect")
    # A report larger than the pipe buffer fails mid-run, not at shutdown.
    big = put("big.json", {"widgets": dict(
        ("w%d" % n, {"useDataSources": [{"dataSourceId": "dataSource_%d" % n}]})
        for n in range(400)), "dataSources": {}})
    reader, writer = os.pipe()
    os.close(reader)
    try:
        code = subprocess.call([sys.executable, os.path.abspath(__file__),
                                big], stdout=writer, stderr=writer,
                               timeout=60)
    finally:
        os.close(writer)
    check(code == 2, "and so does a real 2>&1 run cut off mid-report  "
          "<-- pinned defect")
    bad = put("bad.json", None, raw="{not json")
    check(run_cli([bad])[0] == 2, "offline: an app file that is not JSON "
          "exits 2")
    bom = put("bom.json", None, raw=u"\ufeff" + json.dumps(app))
    check(run_cli([bom, "--webmap", wm_file] + svc_args)[0] == 0,
          "offline: a UTF-8 BOM is read, not refused  <-- pinned defect")
    code, out, err = run_cli([app_file, "--webmap", "a.json", "--webmap",
                              "b.json"])
    check(code == 64 and "ITEMID=FILE" in err,
          "offline: two unnamed web maps are a usage error naming the fix")
    code, out, err = run_cli([app_file, "--service", "nothing"])
    check(code == 64, "offline: a malformed --service is a usage error")
    wm_item = copy(webmap)
    wm_item["operationalLayers"][1]["itemId"] = layer_item
    wm_item_file = put("webmap_item.json", wm_item)
    item_file = put("layeritem.json", decides)
    code, out, err = run_cli([app_file, "--webmap", wm_item_file,
                              "--layer-item", "%s=%s" % (layer_item,
                                                         item_file)]
                             + svc_args)
    check(code == 1 and "layer item %s sets scale ranges" % layer_item in out,
          "offline: --layer-item gives a layer item's /data, and a sublayer "
          "its scale-range array omits exits 1")
    code, out, err = run_cli([app_file, "--webmap", wm_item_file,
                              "--layer-item", "%s=%s" % (
                                  layer_item, put("empty.json", None, raw=""))]
                             + svc_args)
    check(code == 0, "offline: an empty --layer-item file, which is what the "
          "portal returns for an item with no data, leaves the service to "
          "decide")
    code, out, err = run_cli([app_file, "--webmap", wm_item_file] + svc_args)
    check(code == 2 and "no --layer-item file was given" in out,
          "offline: a layer item no --layer-item file covers is unread, exit 2")
    code, out, err = run_cli([app_file, "--layer-item", item_file])
    check(code == 64 and "--layer-item takes ITEMID=FILE" in err,
          "offline: --layer-item with no item id is a usage error")
    report_file = os.path.join(tmp, "report.json")
    code, out, err = run_cli([broken_file, "--webmap", wm_file,
                              "--out", report_file] + svc_args)
    check(code == 1 and "Check only" in out and
          not os.path.exists(report_file),
          "--out without --apply writes nothing at all  <-- pinned defect")
    code, out, err = run_cli([broken_file, "--webmap", wm_file,
                              "--out", report_file, "--apply"] + svc_args)
    written = read_json_file(report_file)
    check(code == 1 and written["counts"]["dangling"] == 2 and
          written["source"] == broken_file,
          "--out with --apply writes the JSON report and keeps the exit code")
    code, out, err = run_cli([broken_file, "--webmap", wm_file,
                              "--out", report_file, "--apply"] + svc_args)
    check(code == 1 and "wrote" in out,
          "and a later run replaces its own earlier report")
    inputs = [broken_file, moved_file, wm_file,
              svc_args[1].split("=", 1)[1], item_file]
    kept = [read_json_file(path) for path in inputs]
    codes = [run_cli([broken_file, "--resource", moved_file, "--webmap",
                      "%s=%s" % (wm_id, wm_file), "--layer-item",
                      "%s=%s" % (layer_item, item_file), "--out",
                      os.path.join(os.path.dirname(path), ".",
                                   os.path.basename(path)), "--apply"]
                     + svc_args)[0] for path in inputs]
    check(codes == [64] * 5 and
          [read_json_file(path) for path in inputs] == kept,
          "--out naming the app, the draft, a web map, a service or a "
          "layer item file, "
          "however it is spelled, is refused and the input is kept  "
          "<-- pinned defect")
    code, out, err = run_cli([broken_file, "--webmap", wm_file, "--out",
                              os.path.join(tmp, "no", "dir", "r.json"),
                              "--apply"] + svc_args)
    check(code == 2 and "could not write" in err,
          "a report that cannot be written exits 2, not 1")
    check(run_cli([])[0] == 64, "no input at all is a usage error")
    code, out, err = run_cli([app_file, "--portal", "https://x", "--item",
                              app_id])
    check(code == 64 and "not both" in err,
          "a file and a portal together are a usage error  <-- pinned defect")
    check(run_cli([app_file, "--apply"])[0] == 64,
          "--apply without --out is a usage error")
    check(run_cli([app_file, "--bogus"])[0] == 64,
          "an unknown flag exits 64, not argparse's 2  <-- pinned defect")
    code, out, err = run_cli(["--portal", "https://x"])
    check(code == 64 and "needs both --portal and --item" in err,
          "--portal without --item is a usage error that names the fix")
    check(run_cli(["--portal", "ftp://x", "--item", app_id])[0] == 64,
          "a portal url that is not http is refused")
    code, out, err = run_cli(["--portal", "https://[x", "--item", app_id])
    check(code == 64 and "not a valid url" in err,
          "and so is one urllib cannot parse, before any request")
    check(run_cli(["--portal", "https://x", "--item", "abc"])[0] == 64,
          "a truncated item id is refused before any request")
    code, out, err = run_cli(["--portal", "http://gis.example.com", "--item",
                              app_id, "--token", "SECRET123"])
    check(code == 64 and "plain http" in err and "SECRET123" not in err,
          "a token is never sent to a plain http portal  <-- pinned defect")
    argv_before = sys.argv
    try:
        sys.argv = ["deadwidget.py", app_file, "--webmap", wm_file] + svc_args
        check(run_cli(None)[0] == 0,
              "main with no argv reads the arguments after the program name")
    finally:
        sys.argv = argv_before

    # ---- the token gate in online mode, with http_json stubbed. The
    # loopback portal below cannot test it: loopback may have the token.
    sent = []
    real_http = globals()["http_json"]
    globals()["http_json"] = lambda url, secret, *a, **k: sent.append(
        (url, secret)) or {"layers": [{"id": 0}]}
    try:
        gate = argparse.Namespace(portal="https://gis.example.com/portal",
                                  item=app_id, trust_host=["other.example.com"])
        get = _online(gate, "TOKEN123")[2]
        for url in ("http://gis.example.com/s/X/MapServer",
                    "http://other.example.com/s/X/MapServer",
                    "https://gis.example.com/s/X/MapServer"):
            get(url)
    finally:
        globals()["http_json"] = real_http
    check([secret for _, secret in sent] == [None, None, "TOKEN123"],
          "online: a plain http url on the portal host or a trusted host is "
          "read without the token, and an https one with it  "
          "<-- pinned defect")
    token = "tok-SECRET-4f9a+/="
    routes = {}
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(parts.query))
            seen.append((self.headers.get("Host"), parts.path, query,
                         self.headers.get("Cache-Control"),
                         self.headers.get("Referer")))
            code, body = routes.get(parts.path, (404, "not found"))
            raw = body if isinstance(body, str) else json.dumps(body)
            raw = raw.encode("utf-8")
            self.send_response(code)
            if code == 302:
                self.send_header("Location",
                                 body.replace("{query}", parts.query))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        port = server.server_address[1]
        live = "http://127.0.0.1:%d" % port
        # One service sits on "localhost": the same stub, another host name,
        # so the token rule is tested against a real request.
        o_app, o_wm, o_services = fixture(live + "/server/rest/services",
                                          "http://localhost:%d/server/rest/"
                                          "services" % port)
        rest = "/sharing/rest/content/items/"

        def reset(resource=None):
            del seen[:]
            routes.clear()
            routes[rest + app_id + "/data"] = (200, o_app)
            routes[rest + app_id + "/resources/config/config.json"] = (
                200, o_app if resource is None else resource)
            routes[rest + wm_id + "/data"] = (200, o_wm)
            for url, body in o_services.items():
                routes[urllib.parse.urlsplit(url).path] = (200, body)

        online = ["--portal", live + "/", "--item", app_id]
        # The Hydrants service sits on localhost, a host the web map names
        # and the command line does not.
        trusting = online + ["--trust-host", "localhost"]
        reset()
        code, out, err = run_cli(online + ["--token", token])
        check(server.server_address[0] == "127.0.0.1",
              "the stand-in portal is bound to the loopback address only")
        check(code == 2 and "UNJUDGED" in out and "a host named with "
              "--trust-host, so it was not fetched" in out,
              "online: a service on a host nobody named on the command line "
              "is UNJUDGED, exit 2, and the reason names --trust-host")
        check(len(seen) == 8 and not [s for s in seen
                                      if s[0].startswith("localhost")],
              "online: and no request at all goes to a host that only web "
              "map data names  <-- pinned defect")
        portal_hits = [s for s in seen if s[0].startswith("127.0.0.1")]
        check(all(s[2].get("token") == token for s in portal_hits),
              "online: every request to the portal host carries the token")
        res = [s for s in seen if s[1].endswith("config.json")][0]
        check(res[3] == "no-cache" and "_ts" in res[2],
              "online: the resource read is cache-busted")
        check(not [s for s in seen if s is not res and
                   (s[3] or "_ts" in s[2])],
              "and no other read is")
        check(all(s[4] == live for s in seen),
              "online: every request carries the portal as Referer")
        check(token not in out and token not in err,
              "online: the token never reaches stdout or stderr  "
              "<-- pinned defect")
        reset()
        code, out, err = run_cli(online + ["--token", token, "--trust-host",
                                           "LOCALHOST"])
        other = [s for s in seen if s[0].startswith("localhost")]
        check(code == 0 and "builder draft, 6 widget(s)" in out,
              "online: with that host trusted, the clean app, its resource, "
              "web map and services exit 0")
        check(len(seen) == 9, "online: nine requests, two copies, one web "
                              "map and six services")
        check(len(other) == 1 and other[0][2].get("token") == token,
              "online: --trust-host lets the request and the token reach a "
              "named host")
        reset(resource={"widgets": {}, "dataSources": {}})
        code, out, err = run_cli(trusting)
        check(code == 0 and "DIVERGED" in out,
              "online: a draft that lost its bindings diverges, exit 0")
        report_file = os.path.join(tmp, "online.json")
        reset(resource={"error": {"code": 403, "message":
                                  "Not allowed with token %s" % token}})
        code, out, err = run_cli(online + ["--out", report_file, "--apply"],
                                 env=token + " ")
        with io.open(report_file, encoding="ascii") as handle:
            raw = handle.read()
        check(code == 2 and "Not allowed with token ***" in out,
              "online: a portal error that echoes the token is printed "
              "redacted  <-- pinned defect")
        check(all(s[2].get("token") == token for s in seen
                  if s[0].startswith("127.0.0.1")),
              "online: the token is read from %s, without the trailing "
              "space a Windows set line leaves  <-- pinned defect" % TOKEN_ENV)
        check(token not in raw and "item %s" % app_id in raw and
              "Not allowed with token ***" in raw,
              "online: the report file names the item and holds no token  "
              "<-- pinned defect")
        reset()
        routes[rest + app_id + "/resources/config/config.json"] = (
            404, "not found")
        code, out, err = run_cli(online)
        check(code == 2 and "HTTP Error 404" in out,
              "online: a resource that is not there exits 2")
        reset()
        routes[rest + app_id + "/data"] = (200, {"error": {
            "code": 498, "message": "Invalid token."}})
        code, out, err = run_cli(online + ["--token", token])
        check(code == 2 and "498" in out and token not in out,
              "online: an error body for the app is unread, exit 2")
        reset()
        routes[urllib.parse.urlsplit(
            live + "/server/rest/services/Planning/Zoning/MapServer").path] = (
            500, "boom")
        code, out, err = run_cli(trusting)
        check(code == 2 and "UNJUDGED" in out,
              "online: a service answering 500 leaves its sublayers UNJUDGED")
        reset()
        routes[rest + wm_id + "/data"] = (200, "<html>sign in</html>")
        code, out, err = run_cli(online)
        check(code == 2 and "not JSON" in out,
              "online: a sign-in page where JSON should be is unread")
        reset()
        with_item = copy(o_wm)
        with_item["operationalLayers"][1]["itemId"] = layer_item
        routes[rest + wm_id + "/data"] = (200, with_item)
        routes[rest + layer_item + "/data"] = (200, decides)
        code, out, err = run_cli(trusting + ["--token", token])
        check(code == 1 and "layer items read: 1 of 1" in out and [
            s for s in seen if s[1] == rest + layer_item + "/data" and
            s[2].get("token") == token],
              "online: a layer item's /data is read from the portal with the "
              "token, and a sublayer it omits exits 1")
        routes[rest + layer_item + "/data"] = (200, "")
        code, out, err = run_cli(trusting)
        check(code == 0, "online: an empty body, which is what the portal "
              "returns for an item with no data, leaves the service to decide")
        reset()
        bad_app = copy(o_app)
        bad_app["dataSources"]["dataSource_1"]["itemId"] = "../../etc"
        routes[rest + app_id + "/data"] = (200, bad_app)
        routes[rest + app_id + "/resources/config/config.json"] = (200,
                                                                   bad_app)
        code, out, err = run_cli(online)
        check(code == 2 and "not an item id" in out and
              not [s for s in seen if "etc" in s[1]],
              "online: a web map id that is not an item id is never fetched")
        reset()
        routes["/big"] = (200, "12345678901234567890")
        exc = raises(lambda: http_json(live + "/big", limit=10),
                     "a body over the size limit is unread, not truncated "
                     "JSON", Unread)
        check("larger than 10 bytes" in "%s" % exc,
              "and it says so, although its first bytes are valid JSON")
        exc = raises(lambda: http_json(live + "/a b", token="Zq9+/x"),
                     "a url urllib refuses is unread, not a crash", Unread)
        check("Zq9" not in "%s" % exc and "***" in "%s" % exc,
              "and urllib's error, which quotes the whole url, is redacted  "
              "<-- pinned defect")
        exc = raises(lambda: http_json(live + "/a b", token="Zq9 x/"),
                     "and so is one whose token holds a space", Unread)
        check("Zq9" not in "%s" % exc and "***" in "%s" % exc,
              "and the token's + for a space is redacted too  "
              "<-- pinned defect")
        # Port 1 on the loopback address: if a check below ever lets the
        # request through, it fails fast with another message.
        for url, kind in (("file:///tmp/Secret/MapServer", "a local file"),
                          ("file:////host.invalid/share/MapServer",
                           "a network share"),
                          ("FTP://127.0.0.1:1/pub/MapServer", "an ftp"),
                          ("gis.example.com/x", "a schemeless")):
            exc = raises(lambda: http_json(url), "%s url is unread" % kind,
                         Unread)
            check("only http and https urls are fetched" in "%s" % exc,
                  "and it is refused before any request is made  "
                  "<-- pinned defect")
        routes["/hop/MapServer"] = (302, "ftp://127.0.0.1:1/pub/MapServer")
        exc = raises(lambda: http_json(live + "/hop/MapServer"),
                     "a redirect to an ftp url is unread", Unread)
        check("redirect to a url that is not http or https was refused"
              in "%s" % exc, "and the ftp url is never opened  "
              "<-- pinned defect")
        # A collected response never warns, so the close is checked on the
        # handler itself.
        body = io.BytesIO(b"")
        raises(lambda: WebOnlyRedirect().redirect_request(
            urllib.request.Request(live), body, 302, "Found", {},
            "ftp://127.0.0.1:1/x"), "the handler refuses the ftp redirect",
            urllib.error.HTTPError)
        check(body.closed, "and closes the refused response, which urllib "
              "leaves open  <-- pinned defect")
        routes["/hop/MapServer"] = (302, live + "/big")
        check(http_json(live + "/hop/MapServer") == 12345678901234567890,
              "a redirect to another http url is still followed")
        # A portal host that redirects keeps the query, token included.
        relay = copy(o_wm)
        relay["operationalLayers"][1]["url"] = live + "/relay/MapServer"

        def relayed():
            reset()
            routes[rest + wm_id + "/data"] = (200, relay)
            routes["/relay/MapServer"] = (
                302, "http://localhost:%d/server/rest/services/Utilities/"
                "Water/MapServer?{query}" % port)
        relayed()
        code, out, err = run_cli(online + ["--token", token])
        check(code == 2 and "a redirect to a host the token may not go to "
              "was refused" in out and not [
                  s for s in seen if s[0].startswith("localhost") and
                  "token" in s[2]],
              "online: a redirect never carries the token to a host that may "
              "not have it  <-- pinned defect")
        relayed()
        code, out, err = run_cli(online + ["--token", token, "--trust-host",
                                           "localhost"])
        check(code == 0 and [s for s in seen if s[1].endswith("Water/"
                                                              "MapServer")
                             and s[2].get("token") == token],
              "online: and it follows one to a trusted host")
        relayed()
        code, out, err = run_cli(trusting)
        check(code == 0 and [s for s in seen if s[0].startswith("localhost")
                             and s[1].endswith("Water/MapServer")],
              "online: and a read with no token follows the redirect")
        reset()
        hostile = copy(o_wm)
        hostile["operationalLayers"][1]["url"] = (
            "file://127.0.0.1/etc/MapServer")
        routes[rest + wm_id + "/data"] = (200, hostile)
        code, out, err = run_cli(trusting)
        check(code == 2 and "service file://127.0.0.1/etc/MapServer could "
              "not be read" in out and "only http and https" in out,
              "online: a web map layer with a file: url leaves its sublayers "
              "UNJUDGED and fetches nothing  <-- pinned defect")
    finally:
        server.shutdown()
        server.server_close()

    # ---- the harness itself. A check() that cannot record a failure would
    # report every defect above as a pass.
    quiet = sys.stdout
    sys.stdout = io.StringIO()
    mark = len(failed)
    try:
        check(False, "probe: a false condition must be recorded")
        raises(lambda: None, "probe: a call that raises nothing must fail")
        raises(lambda: [][0], "probe: the wrong exception must fail")
    finally:
        sys.stdout = quiet
    probe = failed[mark:]
    del failed[mark:]
    check(len(probe) == 3,
          "check() and raises() really do record a failure  <-- pinned defect")
    sys.stdout = io.StringIO()
    try:
        red = summary(5, ["one"])
        shown = sys.stdout.getvalue()
    finally:
        sys.stdout = quiet
    check(red == 1 and "6 assertions, 1 failed" in shown and
          "FAILED: one" in shown,
          "and a failed run prints the failures and returns 1")

    # Imported rather than run, the module must do nothing: no audit, no
    # request, no exit.
    spec = importlib.util.spec_from_file_location(
        "deadwidget_import_probe", os.path.abspath(__file__))
    module = importlib.util.module_from_spec(spec)
    pyc = importlib.util.cache_from_source(os.path.abspath(__file__))
    before = os.path.exists(pyc)
    # Only --apply writes. Without this the import leaves a .pyc behind.
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    check(module.main is not main and module.TOKEN_ENV == TOKEN_ENV,
          "importing the tool as a module runs nothing")
    check(os.path.exists(pyc) == before,
          "and writes no bytecode file next to the tool  <-- pinned defect")

    print("-" * 68)
    return summary(passed[0], failed)


if __name__ == "__main__":
    sys.exit(flushed(main(), sys.stdout))
