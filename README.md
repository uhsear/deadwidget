# deadwidget

Find the Experience Builder widgets that are bound to a layer that no longer exists or was
renumbered, the Filter widgets that act on a layer a scale range hides when the app opens, and the
Dashboards widgets that are bound to data they cannot read.

Somebody tidies a web map and removes a sublayer nobody seems to use. A week later somebody
republishes a map service with fewer sublayers, and the old ids at the end of the list are gone.
Neither change touches the Experience Builder app. The builder opens it without an error, the app
loads, and the map draws. The search widget, the table and the "near me" panel that pointed at
those sublayers now point at nothing. In production, a user clicks the widget and nothing
happens. Nobody sees an error, because none is raised.

This tool finds a binding whose layer id no longer exists. It also finds a renumber: a republish
that gives an old id to a different layer, so the widget reads the wrong layer. An id cannot show
a renumber, because the old id still exists. Only a recorded name can. The tool uses the name that
the web map records for a sublayer, or the service description that you saved before the republish
(`--before`). With no recorded name it checks that the id exists, and the report says so. It also
warns when the web map hides a sublayer that a widget reads. It also warns when a Filter widget
filters a layer that a scale range hides at the scale the app opens at. The range can be written in
the web map or published by the map service. A range on a group above the layer makes the verdict
`UNJUDGED`, because the tool does not know whether the SDK applies it to the layer. With `--dashboard` it lints an ArcGIS
Dashboards item with twelve rules, for example a chart series bound to a field that its dataset
does not return.

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

For a dashboard, the editor shows "Cannot access data" on a widget when you open it, one dashboard
at a time, in a browser. A GitHub repository search on 2026-10-13 found no tool that reads a
dashboard's JSON and checks it against the fields of its layers. That search does not prove that
none exists.

```
$ python deadwidget.py --self-test
deadwidget self-test: synthetic configs, one loopback portal, no credentials
--------------------------------------------------------------------
PASS  the clean fixture resolves every reference and exits 0
PASS  and it checked all 33 references, not zero
PASS  it read the six services a reference needed
PASS  and the one web map the app names
PASS  with no name recorded anywhere, a republish that gives id 15 to another layer still reads ok: nothing says what it named (Limits)
PASS  sublayer 15 is published, so layer-25-15 resolves
PASS  and layer-25-1 is DANGLING although it is a prefix of layer-25-15  <-- pinned defect
PASS  and the reverse: layer-25-15 is not found inside a published layer-25-1  <-- pinned defect
PASS  a deleted layer X-layer-215 is not sublayer 5 of X-layer-2: the dash is part of the match  <-- pinned defect
PASS  dataSource_10-... is not a child of dataSource_1  <-- pinned defect
PASS  when two map service ids both prefix a reference, the longer one owns it
PASS  a sublayer id that is not a number is DANGLING even when the service could not be asked
PASS  when two declared ids both prefix a reference, the longer one owns it
...
PASS  online: a layer item that cannot be read is UNJUDGED
PASS  online: a layer item with no service url is UNJUDGED
PASS  online: a web map layer with no url, and an id that is not an item id, are UNJUDGED and never fetched
PASS  check() and raises() really do record a failure  <-- pinned defect
PASS  and a failed run prints the failures and returns 1
PASS  importing the tool as a module runs nothing
PASS  and writes no bytecode file next to the tool  <-- pinned defect
--------------------------------------------------------------------
488 assertions, 0 failed
```

The full run prints all 488 assertions. The `...` line above is where this block is cut.

## Requirements

Python 3.9 or newer and nothing else. No `arcgis` package, no `arcpy`, and no third-party
package. Offline mode reads saved JSON files. Online mode uses `urllib` from the standard library.

The same 488 assertions pass on Windows (Python 3.13.2) and on Ubuntu (Python 3.12.3), with
`-W error`, and the two runs print identical output once the Windows carriage returns are removed.
Branch coverage of `deadwidget.py` under `--self-test` is 100 percent (2747 statements, 756
branches), measured on Windows with Python 3.13.2 and `coverage run --branch`, with no line
excluded. `pyflakes` reports nothing on Windows.
The Ubuntu host has neither `coverage` nor `pyflakes`. A Python 3.9 interpreter was not available
for the last change. The file parses with `ast.parse(..., feature_version=(3, 9))`, and an earlier
version of the self-test passed on 3.9.25.

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
python deadwidget.py app.json --webmap webmap.json --service URL=service.json --app-scale 24000
python deadwidget.py app.json --webmap webmap.json --service URL=after.json --before URL=before.json
python deadwidget.py dash.json --dashboard --dash-layer ITEMID/LAYERID=layer.json
python deadwidget.py app.json --webmap webmap.json --out report.json --apply
```

The files come from these REST addresses:

| File | Address |
|---|---|
| `APP_JSON` | `<portal>/sharing/rest/content/items/<app item id>/data?f=json` |
| `--resource` | `<portal>/sharing/rest/content/items/<app item id>/resources/config/config.json` |
| `--webmap` | `<portal>/sharing/rest/content/items/<web map item id>/data?f=json` |
| `--service` | `<MapServer or FeatureServer url>?f=json` (the root, not a layer: the scale check reads `layers[].minScale` and `maxScale` from it) |
| `--layer-item` | `<portal>/sharing/rest/content/items/<Map Image Layer item id>/data?f=json` |
| `--before` | The same address as `--service`, saved before the republish |
| `APP_JSON` with `--dashboard` | `<portal>/sharing/rest/content/items/<dashboard item id>/data?f=json` |
| `--dash-layer` | `<layer url>?f=json`, for the layer a dataset names |
| `--dash-service` | `<service url>?f=json` of a layer item's service |

Online, the tool reads all of these itself:

```
set DEADWIDGET_TOKEN=<token>
python deadwidget.py --portal https://org.maps.arcgis.com --item <app item id>
python deadwidget.py --dashboard --portal https://org.maps.arcgis.com --item <dashboard item id>
```

| Flag | Default | What it does |
|---|---|---|
| `APP_JSON` | none | The app's item `/data`, saved as JSON. This is the published copy that users get. Offline mode. With `--dashboard`, the dashboard's `/data`. |
| `--resource` | none | The app's `config/config.json` resource, which is the builder's draft. It is audited too, and compared with `APP_JSON`. |
| `--webmap` | none | A web map's `/data` as `[ITEMID=]FILE`. Repeatable. The item id can be left out when the app names one web map. |
| `--service` | none | A service description as `URL=FILE`. Repeatable. The tool names each url it needs. |
| `--layer-item` | none | A Map Image Layer item's `/data` as `ITEMID=FILE`, for a web map layer added from that item. Repeatable. The tool names each item it needs. |
| `--before` | none | A service description as `URL=FILE`, saved before a republish. A layer whose name moved to another id is `RENUMBERED`. Repeatable. A url that no reference needs exits 2. |
| `--app-scale` | none | The scale the app opens at, as the denominator: `24000` for 1:24000. It must be a positive finite number, or the run exits 64. It wins over the web map's viewpoint. A filter on a layer whose scale range hides it at this scale is `OUT OF SCALE`. Not for `--dashboard`. |
| `--dashboard` | off | The input is an ArcGIS Dashboards item's `/data`, not an Experience Builder app. See Dashboards. |
| `--dash-layer` | none | With `--dashboard`, offline: a layer description as `ITEMID/LAYERID=FILE`, for the layer a dataset names. Repeatable. |
| `--dash-service` | none | With `--dashboard`, offline: a layer item's service description as `ITEMID=FILE`, to tell a removed layer from an unread one. Repeatable. |
| `--portal` | none | Portal url. Online mode. Must start with `https://` or `http://`. |
| `--item` | none | The app's item id, 32 hexadecimal characters. Online mode. |
| `--token` | none | A portal token. The `DEADWIDGET_TOKEN` environment variable is the better place for it. |
| `--trust-host` | none | Another host that the tool can read services from and send the token to, such as a federated server. Repeatable. |
| `--out` | none | Path for a JSON report. A path that names one of the input files is refused, exit 64, and the input is kept. |
| `--apply` | off | Write `--out`. Without it nothing is written. Every option is spelled in full: an abbreviation such as `--ap` is a usage error, exit 64, and writes nothing. |
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
| `OK` | The id resolves to a layer that exists. When no recorded name was there to compare, a `NOTE` line says so. | no |
| `DANGLING` | The data source is not declared, the web map has no such layer, a `layers` array that decides the sublayers, in the web map or in the layer's item, omits the sublayer, or the service no longer publishes it. Limits says when the array decides. | exit 1 |
| `RENUMBERED` | The id exists, but a recorded name shows it now names another layer: the web map entry's name, or the `--before` description, is published under a different id. | exit 1 |
| `DRIFTED` | The id exists and its recorded name changed, and no layer the service publishes carries the old name. A rename and a renumber look the same, so the run cannot call it clean. | exit 2 |
| `WARN` | The id resolves, but the web map hides the sublayer (`visibleLayers`) or omits it from a `layers` array that does not decide the sublayers. Advisory. | no |
| `OUT OF SCALE` | The id resolves and a Filter widget filters it, but a scale range of the layer hides it at the scale the app opens at. Advisory. | no |
| `UNJUDGED` | The web map, the service or the layer item needed to decide could not be read. Also a filtered layer that sets a scale range when the scale the app opens at is not known. | exit 2 |
| `INERT` | Dangling, but in an entry that no widget reads. There are two such places. The first is the `layersConfig` of a table in `MAP` mode. That table makes one tab per map layer, so the entry makes no tab. The second is a Swipe or Map Layers block kept for a map view that no longer exists (see below). The counts line gives the `INERT` count, and so does the verdict when nothing fails the run. | no |
| `NOT AUDITED` | A child of a web scene, a widget output, a subtype group layer, a knowledge graph layer, a feature collection, or another type this tool does not model. Nothing checked it, so it is not clean. | exit 2 |

**Two traps from the author's prototype.** Both are pinned in the self-test.

1. A map service layer with no `layers` array in the web map publishes every sublayer that the
   service holds, unless the layer's item decides them (see Limits). A check that reads only the
   web map finds no sublayers there, and reports every widget bound to one as dangling. This tool
   reads the service.
2. `layer-25-1` is a substring of `layer-25-15`. A substring test reported defects
   that did not exist. This tool compares whole ids, and every prefix match stops at a
   dash.

**Map views that are left behind.** The Swipe widget keys `swipeMapViewList`, and the Map Layers
widget keys `customizeLayerOptions`, by a map view id: `<map widget id>-<data source id>`. Esri's
`JimuLayerView` builds a layer view id from that map view id. When a map widget moves to another
web map, the builder keeps the block for the old map view. The widget reads only the block of a map
view that exists. So the tool judges the entries of a block whose map view is a data source that
the map widget uses. A block for any other map view is one `INERT` note at most, and the tool does
not judge its entries. The self-test pins both cases.

**Renumbers and name drift.** An id alone cannot show a renumber. After a republish inserts a
layer, the old id can still exist and name another layer. A recorded name can show it. The tool
reads one from the web map's sublayer entry, whose `name` is "The name of the layer" in the web
map specification (Esri, layer object). It reads another from the service description that you
pass with `--before`, where each layer has an `id` and a `name` (Esri, map service resource). A
web map entry can also draw another service layer than its own id: its `layerDefinition.source` of
type `mapLayer` carries a `mapLayerId`, "The current map layer's id" (Esri, dynamicMapLayer
source). The widget then reads the layer that `mapLayerId` names.

| Case | Status |
|---|---|
| The recorded name is published under another id, and the bound id now has a different name | `RENUMBERED`, exit 1 |
| A web map entry carries the name of service layer A but draws layer B | `RENUMBERED`, exit 1 |
| A web map entry draws a service layer that the service no longer publishes | `DANGLING`, exit 1 |
| The bound id has a different name, and no layer carries the old name | `DRIFTED`, exit 2 |
| No name was recorded for the layer | `OK`, with a `NOTE` that it was checked by id only |

This run reads a synthetic app. The service was republished with a new layer `Parcels` at id 15,
which moved `Zoning 15` to id 16. The web map records no sublayer names, so `--before` supplies
the old ones. The "Hydrants near me" widget is bound to a sublayer that the web map's
`visibleLayers` leaves out.

```
$ python deadwidget.py app.json --webmap webmap.json --service https://gis.example.com/server/rest/services/Planning/Zoning/MapServer=after.json --service https://gis.example.com/server/rest/services/Utilities/Water/MapServer=water.json --before https://gis.example.com/server/rest/services/Planning/Zoning/MapServer=before.json
deadwidget: published copy, 2 widget(s), 6 data source reference(s)
web maps read: 1 of 1, services read: 2 of 2

RENUMBERED   dataSource_1-18f00000001-layer-25-15
             widget_1 (Zoning lookup), in the published copy
             sublayer 15 of 'Zoning': the service before the republish (--before) calls it 'Zoning 15', and the service now publishes 'Zoning 15' as layer 16. Layer 15 is now 'Parcels', so the widget reads the wrong layer
             at widgets.widget_1.useDataSources[0].dataSourceId (and 1 more path(s))

WARN         dataSource_1-18f00000002-layer-3-2
             widget_2 (Hydrants near me), in the published copy
             sublayer 2 of 'Water': the web map's visibleLayers for this service omits it, so it is hidden when the map loads, and a widget that queries only visible sublayers sends no request for it
             at widgets.widget_2.useDataSources[0].dataSourceId (and 1 more path(s))

references: 6 found, 2 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited, 2 renumbered, 0 drifted, 2 warn
VERDICT: 1 widget binding(s) point at a layer id that now names another layer: see RENUMBERED.
(exit 1)
```

Without `--before` the same files print no `RENUMBERED` finding. The run reads the Zoning widget
as `OK`, prints a `NOTE` that two references were checked by id only, and exits 0 because only the
`WARN` remains:

```
$ same command without --before
deadwidget: published copy, 2 widget(s), 6 data source reference(s)
web maps read: 1 of 1, services read: 2 of 2

WARN         dataSource_1-18f00000002-layer-3-2
             widget_2 (Hydrants near me), in the published copy
             sublayer 2 of 'Water': the web map's visibleLayers for this service omits it, so it is hidden when the map loads, and a widget that queries only visible sublayers sends no request for it
             at widgets.widget_2.useDataSources[0].dataSourceId (and 1 more path(s))

NOTE         2 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 6 found, 4 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited, 2 warn
VERDICT: every data source reference resolves. 1 widget binding(s) read a sublayer that the web map hides or omits: see WARN.
(exit 0)
```

When the old name is published nowhere, as when the republish also dropped `Zoning 15`, the same
`--before` run prints `DRIFTED` and exits 2:

```
$ same command with gone.json as the Zoning service
deadwidget: published copy, 2 widget(s), 6 data source reference(s)
web maps read: 1 of 1, services read: 2 of 2

DRIFTED      dataSource_1-18f00000001-layer-25-15
             widget_1 (Zoning lookup), in the published copy
             sublayer 15 of 'Zoning': the service before the republish (--before) calls it 'Zoning 15' and the service calls it 'Parcels'. A rename or a renumber: no layer the service publishes carries the old name
             at widgets.widget_1.useDataSources[0].dataSourceId (and 1 more path(s))

WARN         dataSource_1-18f00000002-layer-3-2
             widget_2 (Hydrants near me), in the published copy
             sublayer 2 of 'Water': the web map's visibleLayers for this service omits it, so it is hidden when the map loads, and a widget that queries only visible sublayers sends no request for it
             at widgets.widget_2.useDataSources[0].dataSourceId (and 1 more path(s))

references: 6 found, 2 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited, 0 renumbered, 2 drifted, 2 warn
VERDICT: INCOMPLETE. 2 reference(s) resolve to a layer whose name changed, and nothing shows whether that is a rename or a renumber: see DRIFTED.
(exit 2)
```

**A sublayer that the web map hides.** In a web map, a map service layer's `visibleLayers` is "An
array of sublayer ids that should appear visible" (Esri, mapServiceLayer). The tool reports a
`WARN` for a bound top-level sublayer that a present `visibleLayers` array leaves out, and for one
that a `layers` array leaves out when that array does not decide the sublayers. A present but empty
`visibleLayers` hides every sublayer. An absent one leaves visibility to the service, so it is not
a `WARN`. The warning is advisory because only some widgets depend on visibility. The author
found that a Near Me widget sent no request for a category whose sublayer was hidden. The `layers` case is advisory because the ArcGIS Maps SDK for
JavaScript trusts such an array only when an entry sets a scale range (see Limits), and one
observation of a running app found a sublayer missing without one. A nested sublayer is not
judged. A `WARN` on a reference that no widget reads is dropped.

**A filter on a layer that its scale range hides.** A web map layer, and a sublayer entry in a
map service layer's `layers` array, can set `minScale` and `maxScale`. Esri's map service
reference defines them: `minScale` is "the minimum scale (most zoomed out) at which the service
layer is visible in the map view", `maxScale` is "the maximum scale (most zoomed in)", and "a value
of 0 means the layer does not have a minimum scale", and likewise for the maximum. The web map
specification says only that a layer's `minScale` and `maxScale` give "the minimum scale at which
the layer is displayed" and "the maximum scale". It does not state the direction. So the tool takes
the web map's values to mean what the map service reference says. A layer is hidden when the
app's scale denominator is larger than `minScale`, or smaller than `maxScale`. A scale equal to a
limit is drawn. A Filter widget on a hidden layer changes nothing that a user can see until the
user zooms.

The range comes from three places. The first is the web map: the layer's own `minScale` and
`maxScale`, or the layer's entry in a `layers` array. On one entry, the value on the entry itself is
used first and `layerDefinition` second. The second is the service. Esri's map service resource
lists `minScale` and `maxScale` on each entry of the root `layers` array, and the tool reads them
from the `--service` file, or from the service url online. Esri's feature service resource shows
the same two keys in an example response and does not define them there, so the tool applies the
map service definition to a feature service too. A number that the web map writes for one end
replaces the service's number for that end. This precedence is an assumption that was not checked
against the SDK. So a 0 that the web map writes where the service publishes a limit gives
`UNJUDGED`, never a verdict, when that limit would hide the layer. The third is a group. A range on
a web map group layer, or on a group sublayer of the service, would hide a child only if the SDK
applies it to the child. The tool does not know that. When such a range would hide the child at the
app's scale, the child is `UNJUDGED` and the run exits 2. When the range would not hide the child,
there is no finding.

The tool takes the scale the app opens at from these, in order:

1. `--app-scale`.
2. Nothing, when a map widget keeps a custom initial view. Esri's Map widget help says that the
   Default option uses "the positioning of the map inherited from the web map or web scene", and a
   custom view replaces it. The tool does not read a custom view.
3. The web map's `initialState.viewpoint.scale`. The web map specification says the viewpoint
   `scale` is optional, and that when it is omitted it is "automatically calculated such that it is
   just small enough to fit the entire targetGeometry within the view". That scale depends on the
   size of the window, so the tool does not guess it.

When a filtered layer has a range and none of these gives a scale, the layer is `UNJUDGED` and the
run exits 2. Give `--app-scale` to get a verdict. A layer that sets no range needs no scale. The
tool checks only a filter: a reference whose path ends in
`.filterItems[n].dataSource.dataSourceId`. Esri does not document the keys that the Filter widget
saves, and the keys of a custom initial view are not documented either. Check both against your own
app.

This run reads a synthetic app with one Filter widget, bound to a layer `Parcels` with `minScale`
24000 in the web map. The web map opens at 1:100000.

```
$ python deadwidget.py app.json --webmap webmap.json --service https://gis.example.com/server/rest/services/Land/Parcels/FeatureServer=parcels.json
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

OUT OF SCALE dataSource_1-18f00000001-layer-4
             widget_2 (Parcel filter), in the published copy
             layer 'Parcels' is not drawn at 1:100000 (the web map's initial viewpoint), because its minimum scale is 1:24000. A filter on it changes nothing a user can see until they zoom in
             at widgets.widget_2.config.filterItems[0].dataSource.dataSourceId

NOTE         1 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 1 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited, 1 out of scale
VERDICT: every data source reference resolves. 1 filter binding(s) act on a layer that its scale range hides when the app opens: see OUT OF SCALE.
(exit 0)
```

When the web map records only an extent, the run cannot tell the scale and exits 2:

```
$ same command with webmap_extent.json
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

UNJUDGED     dataSource_1-18f00000001-layer-4
             widget_2 (Parcel filter), in the published copy
             layer 'Parcels' sets a scale range, and the scale the app opens at is not known: the web map's initial viewpoint records an extent or nothing, and an extent gives a scale only for a known window size. Give --app-scale
             at widgets.widget_2.config.filterItems[0].dataSource.dataSourceId

NOTE         1 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 1 ok, 0 dangling, 1 unjudged, 0 inert, 0 not audited
VERDICT: INCOMPLETE. Something could not be read, so this run proves nothing about it.
(exit 2)
```

With `--app-scale 12000` the same files print:

```
$ same command with webmap_extent.json and --app-scale 12000
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

NOTE         2 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 2 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited
VERDICT: every data source reference resolves.
(exit 0)
```

The web map may write no range at all while the service publishes one. Here `parcels_ranged.json`
holds `{"layers": [{"id": 0, "name": "Parcels", "minScale": 24000, "maxScale": 0}]}`, and the web
map layer has no `minScale`:

```
$ python deadwidget.py app.json --webmap webmap_plain.json --service https://gis.example.com/server/rest/services/Land/Parcels/FeatureServer=parcels_ranged.json
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

OUT OF SCALE dataSource_1-18f00000001-layer-4
             widget_2 (Parcel filter), in the published copy
             layer 0 of the service at https://gis.example.com/server/rest/services/Land/Parcels/FeatureServer is not drawn at 1:100000 (the web map's initial viewpoint), because its minimum scale is 1:24000. A filter on it changes nothing a user can see until they zoom in
             at widgets.widget_2.config.filterItems[0].dataSource.dataSourceId

NOTE         1 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 1 ok, 0 dangling, 0 unjudged, 0 inert, 0 not audited, 1 out of scale
VERDICT: every data source reference resolves. 1 filter binding(s) act on a layer that its scale range hides when the app opens: see OUT OF SCALE.
(exit 0)
```

If the web map layer writes `"minScale": 0`, the tool cannot tell whether that 0 overrides the
service:

```
$ same command with webmap_zero.json
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

UNJUDGED     dataSource_1-18f00000001-layer-4
             widget_2 (Parcel filter), in the published copy
             layer 0 of the service at https://gis.example.com/server/rest/services/Land/Parcels/FeatureServer has a minimum scale of 1:24000, which would hide the layer at 1:100000 (the web map's initial viewpoint). The web map writes 0 for that end, and whether 0 overrides the service's range is not checked. Look at the app at that scale
             at widgets.widget_2.config.filterItems[0].dataSource.dataSourceId

NOTE         1 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 1 ok, 0 dangling, 1 unjudged, 0 inert, 0 not audited
VERDICT: INCOMPLETE. Something could not be read, so this run proves nothing about it.
(exit 2)
```

A filter on a child of a group layer with `minScale` 24000, with the child's own range empty:

```
$ python deadwidget.py app_group.json --webmap webmap_group.json --service https://gis.example.com/server/rest/services/Land/Parcels/FeatureServer=parcels.json
deadwidget: published copy, 2 widget(s), 2 data source reference(s)
web maps read: 1 of 1, services read: 1 of 1

UNJUDGED     dataSource_1-18f00000009-layer-1-18f00000001-layer-4
             widget_2 (Parcel filter), in the published copy
             group layer 'Land' has a minimum scale of 1:24000, which would hide the layer at 1:100000 (the web map's initial viewpoint). Whether a group layer's range reaches its children is not checked. Look at the app at that scale
             at widgets.widget_2.config.filterItems[0].dataSource.dataSourceId

NOTE         1 reference(s) to a service layer were checked by id only: no recorded name was there to compare, so a renumber would read ok. Give --before, or a web map that records sublayer names.

references: 2 found, 1 ok, 0 dangling, 1 unjudged, 0 inert, 0 not audited
VERDICT: INCOMPLETE. Something could not be read, so this run proves nothing about it.
(exit 2)
```

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

## Dashboards

With `--dashboard` the input is an ArcGIS Dashboards item's `/data`. Esri publishes no schema for
that JSON. The keys the tool reads were measured on exports, and they are listed in the code:
`datasets[].dataSource`, `groupByFields`, `orderByFields`, `statisticDefinitions`, `outFields`,
the filter rules, a serial chart's `category`, `splitBy` and `graphs`, a table's
`dataSettings.valueFields`, and `events[].actions[].targets[]`. A shape the tool does not model is
not checked (see Limits). A dataset names a layer by item id and layer id. A number is a layer of a
layer item, and any other id is a layer of a web map, which `--webmap` supplies offline.

| Rule | Level | Finds |
|---|---|---|
| `field-gone` | BROKEN | A group-by field, statistic field, output field, sort field, filter field or field-map side that the layer, or the query, does not have. |
| `filter-type` | BROKEN | A filter rule typed as one kind of field where the layer field is another. |
| `series-unbound` | BROKEN | A serial chart series bound to a field that its query does not return, or a features chart over a query that groups. |
| `column-unreturned` | BROKEN | A table column that its query does not return. |
| `stat-unshown` | WARN | A grouped table query that returns a statistic no column shows. |
| `arcade-gone` | BROKEN | A dataset that reads an Arcade data source the dashboard does not define. |
| `layer-gone` | BROKEN | A layer that its item, or its web map, no longer publishes. |
| `target-gone` | BROKEN | An action that targets a widget, or a map layer, that the view does not have. |
| `target-dataset-gone` | BROKEN | An action that targets a dataset name its widget does not have. |
| `cross-no-fieldmap` | BROKEN | A filter between two data sources that maps no fields. |
| `fieldmap-many` | WARN | A field map with several pairs. |
| `selection-and` | WARN | A widget that requires a selection from several sources, not all of them selectors. |

A query with statistics returns only its group-by fields and its statistics' output names, which
is how the REST query operation answers `outStatistics` with `groupByFieldsForStatistics`.
`cross-no-fieldmap` follows Esri: when a filter's source and target use different data sources,
you must set up a relationship between them, by attribute or by geometry (Esri, Dashboards
actions). The tool judges only the attribute (`whereClause`) form, and a filter with no field map
matches nothing. `fieldmap-many` is a `WARN` because the author's dashboards applied only the
first pair. Esri does not document that. A layer that cannot be read is `UNJUDGED`, and a layer of
a kind the tool does not model, such as a subtype group layer or a map image layer, is `NOT
AUDITED`. Both exit 2. A dashboard with no widget that the tool recognises, meaning an object with
a string `id` and a `type` that ends in `Widget`, checked nothing, so it exits 2.

This run reads a synthetic dashboard whose chart groups by a field that the layer lacks, and whose
table asks for a column the layer lacks:

```
$ python deadwidget.py dash.json --dashboard --dash-layer aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/0=orders.json
deadwidget: dashboard, 2 widget(s), 2 dataset(s), 0 action target(s)
web maps read: 0 of 0, layers read: 1 of 1

BROKEN       field-gone
             'Orders by crew' (serialChartWidget), desktop view
             it groups by 'crew_name', which the layer does not have
             at desktopView.widgets[0].datasets[0]

BROKEN       field-gone
             'Open orders' (tableWidget), desktop view
             it asks for field 'cost', which the layer does not have
             at desktopView.widgets[1].datasets[0]

findings: 2 broken, 0 unjudged, 0 not audited, 0 warn
VERDICT: 2 finding(s) bind a widget to data it cannot read.
(exit 1)
```

The online path, `--dashboard --portal ... --item ...`, reads the dashboard, its web maps, its
layer items and their services over REST. The self-test serves those from a loopback
`http.server` and asserts the same findings.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every reference resolves in every copy the run audited. That is the published copy, and also the draft when `--resource` is given or the run is online. The copies can still diverge, and a `WARN` or an id-only `NOTE` can remain. Both are reported. An `OUT OF SCALE` filter is reported in the verdict only, and it does not change the exit code, as `WARN` does not, so a job that reads only the exit code does not see it. For a dashboard: no `BROKEN` finding. |
| 1 | A reference is dangling or `RENUMBERED` in the published copy or in the draft. For a dashboard: a `BROKEN` finding. |
| 2 | An input could not be read, so something could not be judged. A reference is `NOT AUDITED`, for example a widget bound to a web scene layer, or `DRIFTED`. A filtered layer that sets a scale range when the scale is not known, so give `--app-scale`. A `--before` url that no reference needed. An app with no widget and no reference, or a dashboard with no recognised widget. The tool failed on input it did not expect. The report file could not be written. The reader of the output closed it early, as `\| head` does, with or without `2>&1`. |
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
- A usage error never echoes an argument that argparse did not recognise: a mistyped
  `--tok SECRET` prints `--tok ***`. The self-test pins this for `--tok`, `--tokens=`, `-t` and
  the environment token.
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
  child. A nested map service sublayer was measured in two forms. A real app used the flat
  `<layer id>-<sublayer id>` form. The saved configuration of another app used the chain
  `<layer id>-<group sublayer id>-<sublayer id>`. The tool accepts both. It reads the
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
  the author's apps use `MAP` mode. So a dangling entry there makes no tab, and it is
  reported as `INERT`, with exit 0.
- An `INERT` entry can still cost something. When a republish renumbers a sublayer, the new
  sublayer gets a tab, but that tab has lost the entry's search fields, columns and CSV export.
  Without a recorded name the tool cannot tell a renumber from a removal. For that reason, a run with an `INERT` reference
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
- A renumber shows only when a name was recorded. The check needs the web map entry's `name`
  (Map Viewer writes titles, not names) or a `--before` description. Without either, the tool
  checks that the id exists, prints a `NOTE`, and a shifted id reads `OK`. The renumber check for a
  web map feature layer works only with `--before`, because the web map records no service layer
  name for it. The `VERDICT` line of such a run stays clean, so read the `NOTE`. A layer with no
  name in the current service reads `OK`. A `--before` file that does
  not list the bound id compares nothing, and the `NOTE` says so.
- A `DRIFTED` binding exits 2 on every run, because the tool cannot tell a rename from a renumber.
  Fix the binding or re-save the web map so that it records the new name.
- The tool does not check the field names a widget uses in an Experience Builder app. `--dashboard`
  checks fields in a dashboard.
- `childDataSourceJsons` entries are overrides for a layer. They are not references, so a stale
  override is not reported.
- It audits one app per run. It does not sweep an organization.
- A widget that refers to another widget, for example through `useMapWidgetIds`, is not checked.
  The only use of another widget is the map view rule above: a Swipe or Map Layers block counts
  as current when its map view is `<widget id>-<data source id>` for a data source that some
  widget in the app uses.
- Only ASCII-safe UTF-8 (with or without BOM) JSON is read. A UTF-16 file, as PowerShell 5
  redirection writes, is reported unread and exits 2, not decoded.
- A name comparison ignores case and surrounding whitespace. It does not normalise Unicode, so the
  same layer name in NFC and NFD forms is reported `DRIFTED`, not `OK`.
- Offline, a dashboard layer read from a layer item has no url identity. A filter between that
  layer and the same service layer reached through a web map can read as "another data source"
  (`cross-no-fieldmap`), where online mode sees the same layer.
- A dashboard or app of the wrong types, for example `datasets` as a number or an object, or
  `statisticDefinitions` as a number, is not modelled. It exits 2 with "could not complete", never 0
  or 1.
- Esri publishes no schema for Dashboards JSON, and the keys were measured on exports. A chart
  reads only the object form `{"category": {"fieldName": ...}}`. A chart whose `category` or
  `splitBy` is a plain string, or any other shape the tool does not model, is skipped without a
  message, and the run can print "every widget reads data its layer returns". An unmodelled shape
  is not checked.
- A `--webmap` or other file argument written as `name=path`, with a leading alphanumeric word
  before the `=`, is read as `ITEMID=FILE`. The real web map then stays unread, and the run exits
  2.
- A service url is matched exactly after the query string is dropped. A different host or scheme
  case, such as `mapserver` against `MapServer`, between the web map and `--service` leaves the layer
  `UNJUDGED`, and the run exits 2.
- The redaction of the report file matches the token text as printed. A token with a character
  that JSON escapes (a quote, a backslash or a non-ASCII character) would not match inside the
  JSON report if a portal echoed it. Standard output is redacted correctly. Real ArcGIS tokens are
  URL-safe.
- The status line prints `web maps read: 0 of 0` for a dashboard that names no web map. That is
  noise, not a failure.
- A redirect from an accepted host is followed to any `http` or `https` host when the run sends no
  token, which is the case for a `--trust-host` host read over plain `http` and for a run with no
  token. The answer is parsed and never printed, but the request is still a blind GET that a
  server's response drives.
- Very deep JSON nesting can exhaust the recursion limit. The run then exits 2 with "could not
  complete".
- A symbolic or hard link given as `--out`, or an input reached through a different link, can
  evade the check that `--out` is not an input when `--out` does not yet exist. `--apply` then
  writes through the link.
- `--token` on the command line is visible to other local processes, to shell history and to task
  logs. Use `DEADWIDGET_TOKEN`. A run with no token at all raises no warning.
- `HTTP_TIMEOUT` applies to each socket operation, not to the whole request. The body size limit
  bounds the total bytes.
- A `user:password@` in a service url, a data source url or a `--portal` url is dropped before the
  tool prints it, writes it to the report. Such a url cannot
  authenticate. The tool finds the userinfo up to the first `/` after `://`, so a password that
  holds a raw `/` is not recognised: encode it. A malformed `--service`, `--before` or
  `--layer-item` value is repeated in the usage error, so never put a credential or token in one.
- The visibility `WARN` judges a top-level sublayer only. It rests on the web map specification's
  description of `visibleLayers` and on one observation of a running app. It was not read from the
  SDK.
- The README's claims about `@arcgis/core` `sublayerUtils.js` and `SublayersOwner.js` are read from
  the SDK's code, not measured in a running app.
- The service's range is read from the root resource's `layers[]` only. A layer resource, such as
  `.../FeatureServer/0?f=json`, is never fetched, so a range that only that resource carries is not
  seen. A web map layer whose url names no layer index has no service range read for it. Esri's
  feature service resource shows `minScale` and `maxScale` on `layers[]` in an example only.
- That a nonzero number in the web map replaces the service's number, and that a 0 may do the
  same, was not checked against the ArcGIS Maps SDK for JavaScript. The tool is `UNJUDGED` in the
  second case and follows the first.
- A scale range on a group layer, or on a group sublayer, is not applied to its children by the
  tool. Whether the SDK applies it was not checked. A child that such a range would hide at the
  app's scale is `UNJUDGED`, exit 2. A child that it would not hide gets no finding. This is an
  unverified gap, not a verdict.
- A layer entry whose own `minScale` is 0 and whose `layerDefinition.minScale` is set takes the
  `layerDefinition` value, so a 0 on the entry does not override it. The scale section says only
  that the value on the entry is used first.
- The `references:` line counts one finding for each copy of the configuration, so a binding found
  in both the published copy and the draft counts twice there. The `VERDICT` line counts bindings,
  so it says 1 where the `references:` line says 2.
- The scale check runs only on a filter reference that already resolved `OK`. A reference that is
  `DANGLING`, `WARN`, `DRIFTED` or `UNJUDGED` gets no scale verdict, even when the web map alone
  could give one. A filter reference in an `INERT` block that is `OK` stays `OUT OF SCALE`, because
  only `DANGLING` and `WARN` are downgraded there.
- `OUT OF SCALE` is advisory. It exits 0, and the `VERDICT` line names it, so a job that reads only
  the exit code does not see it.
- When the web map viewpoint records only an extent, which is the common shape, a filtered layer
  that sets a range is `UNJUDGED` and the run exits 2. `--app-scale` is the way to get a verdict.
  A map widget with a custom initial view has the same effect.
- Only a reference whose path ends in `.filterItems[n].dataSource.dataSourceId` is checked for
  scale. A Table, Search or List widget on a hidden layer gets no finding. Esri does not document
  the keys that the Filter widget saves, so a Filter widget that stores its data source under
  another key gets no finding either.
- The self-test writes its JSON fixtures to a temporary directory and removes it when the process
  exits. A run of the tool writes only `--out`, and only with `--apply`.
- A web map data source with no `itemId` leaves `--webmap FILE` ambiguous. The run then exits 64
  and asks you to name the web map with `ITEMID=FILE`.
- Offline input files are read whole, with no size cap. Only an online response body is capped at
  `MAX_BODY_BYTES`.
- A `--service` or `--before` argument is split at its first `=`. A url with a query string, such
  as `...?token=X` with no file, is refused with exit 64, and the message does not repeat it. An
  argument for a service that no reference needs is ignored without a message.
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
- Esri, [Web map specification, layer](https://developers.arcgis.com/web-map-specification/objects/layer/): a layer's `name` is "The name of the layer".
- Esri, [Web map specification, mapServiceLayer](https://developers.arcgis.com/web-map-specification/objects/mapServiceLayer/): `visibleLayers` is "An array of sublayer ids that should appear visible".
- Esri, [Web map specification, dynamicMapLayer source](https://developers.arcgis.com/web-map-specification/objects/dynamicMapLayer_source/): `mapLayerId` is "The current map layer's id".
- Esri, [Map service resource](https://developers.arcgis.com/rest/services-reference/enterprise/map-service/): its JSON example shows `layers[]` entries with `id`, `name`, `parentLayerId`, `defaultVisibility`, `subLayerIds`, `minScale` and `maxScale`, and `tables[]` entries with `id` and `name`. The text says `minScale` is the scale "most zoomed out" at which a layer is visible, `maxScale` is "most zoomed in", and 0 means no limit. This is the source of the direction that the tool applies to web map values, and of the range it reads from the service.
- Esri, [Feature service resource](https://developers.arcgis.com/rest/services-reference/enterprise/feature-service/): the JSON example shows `minScale` and `maxScale` on `layers[]` entries. The page has no sentence that defines them.
- Esri, [Web map specification, viewpoint](https://developers.arcgis.com/web-map-specification/objects/viewpoint/): `scale` is optional, and when omitted it is calculated to fit `targetGeometry`.
- Esri, [Web map specification, initialState](https://developers.arcgis.com/web-map-specification/objects/initialState/): the web map's `viewpoint` is "the location displayed on the map".
- Esri, [Web map specification, layerDefinition](https://developers.arcgis.com/web-map-specification/objects/layerDefinition/): `minScale` and `maxScale` set the minimum and maximum scale at which a layer is displayed. It does not state the direction or the meaning of 0.
- Esri, [Map widget (Experience Builder)](https://doc.arcgis.com/en/experience-builder/latest/configure-widgets/map-widget.htm): the Default initial view is "the positioning of the map inherited from the web map or web scene".
- Esri, [Dashboards actions](https://doc.arcgis.com/en/dashboards/latest/create-and-share/actions.htm): a source and a target on different data sources need a relationship, by attribute or by geometry.
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
