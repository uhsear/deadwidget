# deadwidget

Find the Experience Builder widgets that are bound to a layer that no longer exists.

Somebody tidies a web map and removes a sublayer nobody seems to use. A week later somebody
republishes a map service with fewer sublayers, and the old ids at the end of the list are gone.
Neither change touches the Experience Builder app. The builder opens it without an error, the app
loads, and the map draws. The search widget, the table and the "near me" panel that pointed at
those sublayers now point at nothing. In production, a user clicks the widget and nothing
happens. Nobody sees an error, because none is raised.

This tool finds a binding whose layer id no longer exists. A renumber that gives an old id to a
different layer is another failure, because the widget then reads the wrong layer. This tool does
not find that one. Limits says why.

The link that broke is a string in the app configuration, such as
`dataSource_1-18f00000001-layer-25-15`. That string means "the web map data source, then web map
layer `18f00000001-layer-25`, then sublayer 15". No screen in the portal shows it next to the
list of sublayers that the service still publishes.

The obvious tools each cover part of this. The builder shows the app's data sources and lets you
re-bind a widget, one widget at a time, when you already know which one is broken. The ArcGIS API
for Python's `arcgis.apps.expbuilder.WebExperience` exposes an app's `datasources` dictionary. Its
`upload(auto_remap=True)` checks that each data source item can be found in a target portal. Both
work at the level of the item. Neither one reads a widget's sublayer id or compares it with the
layers that the service publishes. That comparison is this tool.

```
$ python deadwidget.py --self-test
deadwidget self-test: synthetic configs, one loopback portal, no credentials
--------------------------------------------------------------------
PASS  the clean fixture resolves every reference and exits 0
PASS  and it checked all 33 references, not zero
PASS  it read the six services a reference needed
PASS  and the one web map the app names
PASS  a republish that gives id 15 to another layer still reads ok: the tool checks that an id exists, not what it names (Limits)
PASS  sublayer 15 is published, so layer-25-15 resolves
PASS  and layer-25-1 is DANGLING although it is a prefix of layer-25-15  <-- pinned defect
PASS  and the reverse: layer-25-15 is not found inside a published layer-25-1  <-- pinned defect
PASS  a deleted layer X-layer-215 is not sublayer 5 of X-layer-2: the dash is part of the match  <-- pinned defect
PASS  dataSource_10-... is not a child of dataSource_1  <-- pinned defect
PASS  when two map service ids both prefix a reference, the longer one owns it
PASS  a sublayer id that is not a number is DANGLING even when the service could not be asked
PASS  when two declared ids both prefix a reference, the longer one owns it
PASS  a sublayer id that is not a number is DANGLING
...
PASS  a nested sublayer resolves as <layer>-<group>-<sublayer> and as <layer>-<sublayer>  <-- pinned defect
...
PASS  an app bound to a nested sublayer by its chain exits 0 after reading the service, not 1 unread  <-- pinned defect
...
PASS  a map service layer with no layers array resolves sublayer 2 from the service  <-- pinned defect
PASS  and a nested service sublayer resolves by its own id, <layer>-<sublayer>
PASS  and it is DANGLING once a republish drops sublayer 2 from the service
PASS  a service that could not be read leaves its sublayers UNJUDGED, never ok  <-- pinned defect
PASS  and the run exits 2, because unread is not clean  <-- pinned defect
...
PASS  a sublayer the layer item's scale-range array omits is DANGLING, although the web map has no layers array and the service publishes it  <-- pinned defect
...
PASS  a layer item that cannot be read leaves the sublayer UNJUDGED and the run exits 2, never 0  <-- pinned defect
...
PASS  a child of a subtype group layer is NOT AUDITED, not DANGLING  <-- pinned defect
PASS  a child of a feature collection is NOT AUDITED, not DANGLING  <-- pinned defect
PASS  a child of a knowledge graph layer is NOT AUDITED, not DANGLING  <-- pinned defect
...
PASS  a one-word suffix under a WEB_MAP main is a layer, not a data view, so it is judged  <-- pinned defect
...
PASS  a dangling reference in a top-level array is DANGLING, not inert  <-- pinned defect
...
PASS  a stale Map Layers or Swipe block the builder left behind is one INERT note each and exits 0, not N DANGLING and 1  <-- pinned defect
...
PASS  a dangling reference beside layersConfig in a MAP-mode table's config is DANGLING, exit 1, not INERT  <-- pinned defect
...
PASS  config/config.json is named the builder draft and the item data the published copy  <-- pinned defect
PASS  a reference that is not audited makes the run incomplete, exit 2, even beside a dangling one  <-- pinned defect
...
PASS  a web scene app bound to a layer nobody checked exits 2, never 0 with a clean verdict  <-- pinned defect
...
PASS  the counts line names each status with its own count  <-- pinned defect
...
PASS  an INERT reference is not called resolved  <-- pinned defect
...
PASS  a label outside ASCII is printed escaped, so a cp1252 stdout cannot crash the report  <-- pinned defect
PASS  a label's control characters are printed escaped, so it cannot drive the terminal or forge a VERDICT line  <-- pinned defect
PASS  a crash nobody foresaw exits 2, never 1, and is redacted  <-- pinned defect
PASS  output cut off by | head exits 2, not 1 or 120  <-- pinned defect
PASS  and so does 2>&1 | head, where stderr is the same dead pipe  <-- pinned defect
PASS  with no stdout or stderr at all, as under pythonw, each exit code stands and none becomes a traceback's 1, and no error line strays into stdout  <-- pinned defect
...
PASS  --out without --apply writes nothing at all  <-- pinned defect
...
PASS  --out naming the app, the draft, a web map, a service or a layer item file, however it is spelled, is refused and the input is kept  <-- pinned defect
...
PASS  a file and a portal together are a usage error  <-- pinned defect
...
PASS  online: a plain http url on the portal host or a trusted host is read without the token, and an https one with it  <-- pinned defect
...
PASS  online: and no request at all goes to a host that only web map data names  <-- pinned defect
...
PASS  online: the token never reaches stdout or stderr  <-- pinned defect
...
PASS  online: a portal error that echoes the token is printed redacted  <-- pinned defect
...
PASS  and urllib's error, which quotes the whole url, is redacted  <-- pinned defect
PASS  and so is one whose token holds a space
PASS  and the token's + for a space is redacted too  <-- pinned defect
PASS  a local file url is unread
PASS  and it is refused before any request is made  <-- pinned defect
PASS  a network share url is unread
PASS  and it is refused before any request is made  <-- pinned defect
...
PASS  and it is refused before any request is made  <-- pinned defect
...
PASS  and it is refused before any request is made  <-- pinned defect
PASS  a redirect to an ftp url is unread
PASS  and the ftp url is never opened  <-- pinned defect
...
PASS  a redirect to another http url is still followed
PASS  online: a redirect never carries the token to a host that may not have it  <-- pinned defect
PASS  online: and it follows one to a trusted host
PASS  online: and a read with no token follows the redirect
PASS  online: a web map layer with a file: url leaves its sublayers UNJUDGED and fetches nothing  <-- pinned defect
PASS  check() and raises() really do record a failure  <-- pinned defect
PASS  and a failed run prints the failures and returns 1
PASS  importing the tool as a module runs nothing
PASS  and writes no bytecode file next to the tool  <-- pinned defect
--------------------------------------------------------------------
283 assertions, 0 failed
```

The full run prints all 283 assertions. The `...` lines above are where this block is cut.

## Requirements

Python 3.9 or newer and nothing else. No `arcgis` package, no `arcpy`, and no third-party
package. Offline mode reads saved JSON files. Online mode uses `urllib` from the standard library.

The same 283 assertions pass on Windows (Python 3.13.2 and 3.9.25) and on Ubuntu (Python 3.12.3),
with `-W error`, and the three runs print identical output.
Branch coverage of `deadwidget.py` under `--self-test` is 100 percent, with no line excluded.

```
git clone https://github.com/uhsear/deadwidget.git
```

## Usage

Offline, save the app configuration, the web map and each service description as JSON, then give
the tool the files:

```
python deadwidget.py --self-test
python deadwidget.py app.json --webmap webmap.json --service URL=service.json
python deadwidget.py app.json --resource config.json --webmap ITEMID=webmap.json --service URL=service.json
python deadwidget.py app.json --webmap webmap.json --service URL=service.json --layer-item ITEMID=item.json
python deadwidget.py app.json --webmap webmap.json --out report.json --apply
```

The files come from these REST addresses:

| File | Address |
|---|---|
| `APP_JSON` | `<portal>/sharing/rest/content/items/<app item id>/data?f=json` |
| `--resource` | `<portal>/sharing/rest/content/items/<app item id>/resources/config/config.json` |
| `--webmap` | `<portal>/sharing/rest/content/items/<web map item id>/data?f=json` |
| `--service` | `<MapServer or FeatureServer url>?f=json` |
| `--layer-item` | `<portal>/sharing/rest/content/items/<Map Image Layer item id>/data?f=json` |

Online, the tool reads all of these itself:

```
set DEADWIDGET_TOKEN=<token>
python deadwidget.py --portal https://org.maps.arcgis.com --item <app item id>
```

| Flag | Default | What it does |
|---|---|---|
| `APP_JSON` | none | The app's item `/data`, saved as JSON. This is the published copy that users get. Offline mode. |
| `--resource` | none | The app's `config/config.json` resource, which is the builder's draft. It is audited too, and compared with `APP_JSON`. |
| `--webmap` | none | A web map's `/data` as `[ITEMID=]FILE`. Repeatable. The item id can be left out when the app names one web map. |
| `--service` | none | A service description as `URL=FILE`. Repeatable. The tool names each url it needs. |
| `--layer-item` | none | A Map Image Layer item's `/data` as `ITEMID=FILE`, for a web map layer added from that item. Repeatable. The tool names each item it needs. |
| `--portal` | none | Portal url. Online mode. Must start with `https://` or `http://`. |
| `--item` | none | The app's item id, 32 hexadecimal characters. Online mode. |
| `--token` | none | A portal token. The `DEADWIDGET_TOKEN` environment variable is the better place for it. |
| `--trust-host` | none | Another host that the tool can read services from and send the token to, such as a federated server. Repeatable. |
| `--out` | none | Path for a JSON report. A path that names one of the input files is refused, exit 64, and the input is kept. |
| `--apply` | off | Write `--out`. Without it nothing is written. |
| `--self-test` | off | Run the assertions and exit. |

The tool is read-only. `--apply` writes the report file and nothing else. No flag changes the
portal.

## What it checks

**How Experience Builder names a layer.** Esri's `DataSourceConstructorOptions` says that "every
child data source has a data source id, which consists of parent data source id and jimuChildId".
A web map's children are its layers, so the ids follow the layer tree:

| Layer | Data source id |
|---|---|
| The web map | `dataSource_1`, as the app's `dataSources` object declares it |
| A web map layer or table | `dataSource_1-<layer id>` |
| A map service sublayer | `dataSource_1-<layer id>-<sublayer id>` |
| A nested map service sublayer | `dataSource_1-<layer id>-<sublayer id>`, or `dataSource_1-<layer id>-<group sublayer id>-<sublayer id>` |
| A layer inside a group layer | `dataSource_1-<group id>-<layer id>` |
| A layer of a feature service data source | `dataSource_9-<layer index>` |

Widgets hold these ids in `useDataSources` entries under `dataSourceId`, `mainDataSourceId` and
`rootDataSourceId` (Esri's `UseDataSource` interface). Bookmarks hold `mapDataSourceId`, and the
map widget holds `initialMapDataSourceID`. A "near me" widget keys `configInfo` by the data source
id. The tool collects the value of each of these keys, and each `configInfo` key that has the
shape of a data source id, anywhere in the configuration: widgets, message actions and the
`originDataSources` of a widget output.

**How each id is judged.** The tool splits the id at the dash after a declared data source, then
looks the rest up in the web map. For a map service layer, it also reads the service.

| Status | Meaning | Fails the run |
|---|---|---|
| `OK` | The id resolves to a layer that exists. | no |
| `DANGLING` | The data source is not declared, the web map has no such layer, a `layers` array that decides the sublayers, in the web map or in the layer's item, omits the sublayer, or the service no longer publishes it. Limits says when the array decides. | exit 1 |
| `UNJUDGED` | The web map, the service or the layer item needed to decide could not be read. | exit 2 |
| `INERT` | Dangling, but in an entry that no widget reads. There are two such places. The first is the `layersConfig` of a table in `MAP` mode. That table makes one tab per map layer, so the entry makes no tab. The second is a Swipe or Map Layers block kept for a map view that no longer exists (see below). The counts line gives the `INERT` count, and so does the verdict when nothing fails the run. | no |
| `NOT AUDITED` | A child of a web scene, a widget output, a subtype group layer, a knowledge graph layer, a feature collection, or another type this tool does not model. Nothing checked it, so it is not clean. | exit 2 |

**Two traps from the author's prototype.** Both are pinned in the self-test.

1. A map service layer with no `layers` array in the web map publishes every sublayer that the
   service holds, unless the layer's item decides them (see Limits). A check that reads only the
   web map finds no sublayers there, and reports every widget bound to one as dangling. This tool
   reads the service.
2. `layer-25-1` is a substring of `layer-25-15`. A substring test found sixteen defects on one
   real app that did not exist. This tool compares whole ids, and every prefix match stops at a
   dash.

**Map views that are left behind.** The Swipe widget keys `swipeMapViewList`, and the Map Layers
widget keys `customizeLayerOptions`, by a map view id: `<map widget id>-<data source id>`. Esri's
`JimuLayerView` builds a layer view id from that map view id. When a map widget moves to another
web map, the builder keeps the block for the old map view. The widget reads only the block of a map
view that exists. So the tool judges the entries of a block whose map view is a data source that
the map widget uses. A block for any other map view is one `INERT` note at most, and the tool does
not judge its entries. The self-test pins both cases.

**The two copies of the configuration.** An Experience Builder app stores its configuration
twice. The item `/data` is the published copy, and users get it. The `config/config.json` resource
is the builder's draft. The builder writes the draft when an author clicks Save, and copies it to
`/data` when the author clicks Publish. Esri's own `solution.js` repository says so in issue 660.

With `--resource`, or online, the tool audits both copies, and a dangling reference in either
copy fails the run. A break in the published copy reaches users now. A break in the draft reaches
them at the next Publish. The tool also lists every binding that is in one copy and not in the
other. The two copies are legitimately not byte-identical, because the portal rewrites rich text
when it writes the resource. So only the data source bindings are compared, and the query string
of each data source url is dropped, because a stored url can carry a token.

A divergence alone does not fail the run. Every app that has unpublished edits diverges, and that
is normal. The divergence becomes a defect when somebody fixed the published copy with a REST edit
and not the draft. The next Publish then puts the old binding back. The tool cannot tell those two
cases apart, so it reports the divergence and leaves the exit code to the dangling references.

This run reads a synthetic app in which a REST edit fixed the published copy but not the draft:

```
$ python deadwidget.py app.json --resource config.json --webmap webmap.json --service https://gis.example.com/server/rest/services/Planning/Zoning/MapServer=zoning.json --service https://gis.example.com/server/rest/services/Utilities/Water/MapServer=water.json
deadwidget: published copy, 5 widget(s), 15 data source reference(s)
deadwidget: builder draft, 5 widget(s), 15 data source reference(s)
web maps read: 1 of 1, services read: 2 of 2

DANGLING     dataSource_1-18f00000001-layer-25-15
             widget_2 (Zoning lookup), in the builder draft
             sublayer 15 of 'Zoning': the service https://gis.example.com/server/rest/services/Planning/Zoning/MapServer no longer publishes layer 15
             at widgets.widget_2.useDataSources[0].dataSourceId (and 1 more path(s))

INERT        dataSource_1-18f00000001-layer-25-14
             widget_5 (Attribute table), in the published copy and the builder draft
             sublayer 14 of 'Zoning': the service https://gis.example.com/server/rest/services/Planning/Zoning/MapServer no longer publishes layer 14. The table is in MAP mode, so this entry makes no tab. If the layer was renumbered, its new tab lost the settings in this entry
             at widgets.widget_5.config.layersConfig[0].useDataSource.dataSourceId (and 1 more path(s))

DIVERGED     the builder draft and the published copy bind widgets differently. Either the draft holds edits not yet published, or a REST edit reached only the published copy and the next Publish from the builder will overwrite it. This alone does not fail the run.
             only in the published copy: widgets.widget_2.useDataSources[0].dataSourceId = dataSource_1-18f00000001-layer-25-13
             only in the published copy: widgets.widget_2.useDataSources[0].mainDataSourceId = dataSource_1-18f00000001-layer-25-13
             only in the builder draft: widgets.widget_2.useDataSources[0].dataSourceId = dataSource_1-18f00000001-layer-25-15
             only in the builder draft: widgets.widget_2.useDataSources[0].mainDataSourceId = dataSource_1-18f00000001-layer-25-15

references: 30 found, 24 ok, 2 dangling, 0 unjudged, 4 inert, 0 not audited
VERDICT: 1 widget binding(s) point at a layer that does not exist. The builder draft and the published copy bind widgets differently: see DIVERGED.
```

The service was republished with 14 sublayers, so sublayer 15 is gone. A REST edit moved the
published copy to sublayer 13, and users get a working "Zoning lookup" widget today. The draft
still points at 15. The next time an author opens the builder and clicks Publish, the fix is
overwritten and the widget stops working again. The run exits 1. The "Overlay search" widget is
bound to `layer-25-1` and resolves, although its id is a prefix of the broken one. The "Nearest
hydrant" widget is bound to sublayer 2 of a Water layer that has no `layers` array, and it
resolves from the service.

The same app in online mode, with the same files served by Python's `http.server` on the
loopback address, printed the same findings, with the loopback urls, and exited 1. The token went
to all five requests and appeared in neither the output nor the report file.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every reference resolves in every copy the run audited. That is the published copy, and also the draft when `--resource` is given or the run is online. The copies can still diverge, which is reported. |
| 1 | A reference is dangling in the published copy or in the draft. |
| 2 | An input could not be read, so something could not be judged. A reference is `NOT AUDITED`, for example a widget bound to a web scene layer. The tool failed on input it did not expect. The report file could not be written. The reader of the output closed it early, as `\| head` does, with or without `2>&1`. |
| 64 | Usage error. |

Exit 2 beats exit 1. A web map that could not be read is not a web map known to be clean, and a
scheduled job that saw 0 or 1 would trust the answer. A reference that the tool does not audit
is the same case. A widget bound to a deleted web scene layer is the failure this tool is for,
so an app with a web scene exits 2 and never 0. For the same reason, an unexpected error
exits 2 with a one-line message, and never 1 with a traceback. A run with no stdout or stderr at
all, such as `pythonw` under Task Scheduler with no redirect, keeps the same exit codes.

## Online mode and the token

- The token comes from `--token` or from `DEADWIDGET_TOKEN`. The environment variable is the
  better place, because every process on the machine can read a command line.
- The tool sends requests only to the portal's own host, to a host named with `--trust-host`,
  and, when the portal is ArcGIS Online, to other `arcgis.com` hosts where hosted services live.
  A service url comes from web map data, which any author can write. Fetched blindly, it could
  send requests from your machine to any address the machine can reach, such as a LAN host, and
  print the answer. A service on any other host is not read. Its references are `UNJUDGED`, the
  reason names `--trust-host`, and the run exits 2. The self-test pins that no request reaches
  such a host.
- A host is matched by name, not by port. A redirect from an accepted host is followed to another
  `http` or `https` url. A redirect that would carry the token is followed only to an accepted
  host.
- The token goes to the same hosts, so a public service on another host, such as an Esri Living
  Atlas layer under an Enterprise portal, is audited only when you trust that host with the token
  too. Offline mode, with `--service URL=FILE`, audits it without the token.
- A token with a space before or after it, as a Windows `set` line can leave, is stripped before
  use.
- The token is never sent over plain `http`, except to the loopback address. A plain `http`
  portal together with a token is refused before any request.
- Every line the tool prints and the report file are redacted. The token is removed raw and in
  both url forms, where a space is `+` or `%20`. The self-test serves a portal error that echoes
  the token back, and asserts that the token reaches neither.
- The resource is read with a changing `_ts` parameter and `Cache-Control: no-cache`, because the
  portal caches it and a plain read can return the copy from before the last save.
- An error body from the portal, a sign-in page instead of JSON, and a service that answers with
  an empty layer list are all read as "could not be read". An empty layer list is what a secured
  service returns to an anonymous read, and it does not mean the service publishes nothing.
- Only `http` and `https` urls are fetched. A service url comes from web map data, which another
  organization can own. `urllib` would otherwise open a `file:` url, which on Windows is an SMB
  connection when it names a host, and an `ftp:` url. A redirect to such a url is refused too.
  The self-test pins both, and the references behind a refused url are `UNJUDGED`.
- Services are read only when a reference needs one. A secured service that no widget uses cannot
  fail the run.

## Limits

- The group layer rule, `<group id>-<layer id>`, comes from Esri's documented composition rule. It
  was not measured on a real app, because the author's apps bind no widget to a group layer's
  child. A nested map service sublayer was measured in two forms. Four references on one real app
  used the flat `<layer id>-<sublayer id>` form. The saved configuration of another app used the
  chain `<layer id>-<group sublayer id>-<sublayer id>`. The tool accepts both. It reads the
  chain from the service's `parentLayerId` values, from the top-level group down.
- A web map `layers` array decides a map service's sublayers only when one of its entries carries
  a `minScale`, directly or in its `layerDefinition`. The ArcGIS Maps SDK for JavaScript then builds
  the sublayers from that array alone (`isSublayerOverhaul` in `@arcgis/core` `sublayerUtils.js`),
  so a sublayer that the array omits is reported as dangling. Without a `minScale`, as Map Viewer
  Classic writes the array, the array only overrides sublayers by id, and the service decides. This
  rule was read from the SDK's code. It was not measured in a running app.
- A map service layer added from a Map Image Layer item has a second such array: the item's
  `/data`. The SDK tries the item first and the web map second, and the last array that decides
  wins (`createSublayersForOrigin` in `@arcgis/core` `SublayersOwner.js`). So when the web map's
  array does not decide, the tool reads the item's `/data` online, or takes it from `--layer-item`
  offline. An item that no `--layer-item` file covers is unread, and the run exits 2. An item with
  no data, or with no `minScale`, leaves the service to decide.
- A table in `MAP` mode builds its tabs from the layers in the map. It applies a `layersConfig`
  entry only to the layer whose data source id the entry names. This comes from reading the Table
  widget's shipped code, and it was not measured in a running app. What was measured is only that
  five of the author's apps use `MAP` mode. So a dangling entry there makes no tab, and it is
  reported as `INERT`, with exit 0.
- An `INERT` entry can still cost something. When a republish renumbers a sublayer, the new
  sublayer gets a tab, but that tab has lost the entry's search fields, columns and CSV export.
  The tool cannot tell a renumber from a removal. For that reason, a run with an `INERT` reference
  never prints "every data source reference resolves". The counts line always gives the `INERT`
  count. The verdict gives it too when nothing else fails the run; otherwise the verdict names
  only what failed the run.
- Web scenes, subtype group layers, knowledge graph layers, feature collections and data views
  are not modelled. Their children are reported as `NOT AUDITED`, and the run exits 2. A
  reference to a data view, `<main id>-<view id>`, is judged through its main data source. The
  suffix counts as a view id only when it equals the entry's `dataViewId`. With no `dataViewId`,
  it counts only when it is one word that is not a number, such as `selection`. A web map, a web
  scene or a service has no data views, so under one of them the suffix is always judged as a
  layer. Any other suffix is judged as a layer reference too.
- The output is printable ASCII. A widget label or layer title outside ASCII is printed with `\u`
  escapes, so that a Windows job that redirects the output to a file cannot fail halfway through
  the report. A control character, such as a line break or an escape, is printed as `\x0a` or
  `\x1b`. So a label cannot drive the terminal or add a false `VERDICT` line to a job's log.
- It checks that each layer id exists. It does not check which layer the id now names. A
  republish that inserts or removes a sublayer often shifts the ids, so an old id can still exist
  and name a different layer. Such a binding reads `OK`, and the run can exit 0 with "every data
  source reference resolves". The widget then reads the wrong layer. The self-test pins this limit:
  sublayer 15 renamed from a zoning layer to "Parcels" still reads `OK`. Compare the service's
  layer names with what each widget should show after any republish that changes the layer list.
- It does not check the field names a widget uses. A renamed field is another silent failure, and
  it is not this one.
- `childDataSourceJsons` entries are overrides for a layer. They are not references, so a stale
  override is not reported.
- It audits one app per run. It does not sweep an organization.
- A widget that refers to another widget, for example through `useMapWidgetIds`, is not checked.
  The only use of another widget is the map view rule above: a Swipe or Map Layers block counts
  as current when its map view is `<widget id>-<data source id>` for a data source that some
  widget in the app uses.
- It changes nothing. Re-binding a widget is done in the builder, or with the two-copy write that
  the app then needs.

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Sources

- Esri, [DataSourceConstructorOptions](https://developers.arcgis.com/experience-builder/api-reference/jimu-core/DataSourceConstructorOptions/): the child data source id rule.
- Esri, [UseDataSource](https://developers.arcgis.com/experience-builder/api-reference/jimu-core/UseDataSource/): `dataSourceId`, `mainDataSourceId`, `rootDataSourceId` and data views.
- Esri, [DataSourceTypes](https://developers.arcgis.com/experience-builder/api-reference/jimu-core/DataSourceTypes/): `SUBTYPE_SUBLAYER` and `KNOWLEDGE_GRAPH_SUBLAYER`, child data source types that this tool does not model.
- Esri, [JimuMapView](https://developers.arcgis.com/experience-builder/api-reference/jimu-arcgis/JimuMapView/): `getDataSourceIdByAPILayer`, one data source id per layer or sublayer.
- teeks822828, [exb-publish-agol](https://github.com/teeks822828/exb-publish-agol) (MIT): a feature service data source keys its `childDataSourceJsons` by layer index, `0` and `1`.
- Esri Community, [Change Data Source without have to re-create everything in the ExB app](https://community.esri.com/t5/arcgis-experience-builder-ideas/change-data-source-without-have-to-re-create/idc-p/1622393): a web map data source's `childDataSourceJsons` holds its layers.
- Esri, [solution.js issue 660](https://github.com/Esri/solution.js/issues/660): `config/config.json` holds the builder's draft, and publishing pushes the changes to the item data.
- Esri, [Working with Web Experiences in the Python API](https://developers.arcgis.com/python/latest/guide/experience-builder-workflows/): the `datasources` property and `upload(auto_remap=True)`.

## Related

Other single-file tools in this portfolio that pair with this one:

- [sightline](https://github.com/uhsear/sightline) - audits web maps for layers their viewers
  cannot see. Its README says that Experience Builder apps are not audited. This tool covers one
  question about those apps: does each widget binding still resolve to a layer.
- [whobreaks](https://github.com/uhsear/whobreaks) - finds every item in an organization that
  references an item you are about to delete, including inside Experience Builder configurations.
  It matches 32-character item ids. It does not look at the sublayer ids inside one app. Run
  whobreaks before you delete an item, and this tool after you edit a web map or republish a
  service.
- [agol-relink](https://github.com/uhsear/agol-relink) - rewrites service urls across content,
  including Experience Builder `childDataSourceJsons`. A url that was moved is not proof that the
  sublayer ids behind it are the same, so run this tool after a relink.
