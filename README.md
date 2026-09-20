# Fi Tracking for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A custom [Home Assistant](https://www.home-assistant.io/) integration for [Fi](https://fitracking.com/) smart GPS dog collars — live location, activity and rest tracking, collar light control and Lost Dog mode.

> Personal fork of [sbabcock23/hass-tryfi](https://github.com/sbabcock23/hass-tryfi), renamed to follow Fi's rebrand from `tryfi.com` to `fitracking.com`. It no longer depends on `pytryfi` — the API client is built in. See [Differences from upstream](#differences-from-upstream).

## Features

### Device tracker

A `device_tracker` entity per pet, fed by the collar's reported GPS fix, so the pet appears on the map and can back a `person` entity.

### Sensors

Activity and rest statistics are created for every combination of period (`daily`, `weekly`, `monthly`) and metric:

| Metric | Unit | Device class | State class |
|---|---|---|---|
| Steps | `steps` | – | `measurement` |
| Distance | `km` | `distance` | `measurement` |
| Sleep | `min` | `duration` | `measurement` |
| Nap | `min` | `duration` | `measurement` |
| Active | `min` | `duration` | `measurement` |
| Goal | `steps` | – | `measurement` |

None of these are `total_increasing`. Every value does reset at the start of its
period, but Fi also revises them downward after first reporting them, and Home
Assistant's 10% reset tolerance is relative — so a 30 m correction to a 50 m
morning walk reads as a counter reset and adds a phantom cycle to the sum.

Plus, per pet: collar battery level (`%`, `battery`), activity type, current place name, current place address, and the current connection source. Each Fi Base reports `Online` / `Offline`.

A metric Fi has not reported reads `unknown`, never `0`.

### Rest periods follow the calendar, like the Fi app

Fi exposes rest twice. `restSummaryFeed` files a whole session under the local
day it **began** and never splits it, so its daily bucket reports `0` sleep for
as long as the current night started yesterday. The Fi app does not show that
number, and neither does this integration: `Sleep` and `Nap` read `restFeed`,
whose totals are clipped at the period boundary. A night running 21:19 → 08:34
contributes its portion before midnight to yesterday and the 8h 30m after it to
today — for `daily`, `weekly` and `monthly` alike. `Steps`, `Distance` and
`Goal` were already calendar-aligned.

So a `0` is a real `0`, and every period answers the same question the app does:
what did the dog do *in this day, this week, this month*.

Two sensors cover what a calendar period cannot say on its own:

| Sensor | Meaning |
|---|---|
| `Last Night Sleep` | The night as one unbroken session, uncut by midnight — Fi's settled overnight total. Keyed by the evening the night began, so the integration asks Fi for both candidate evenings and reports the most recent one that has finished. `unknown` only when neither has, with `sleep_start` / `sleep_end` attributes. |
| `Resting Since` | Timestamp Fi's current `OngoingRest` began, or `unknown` while the pet is on a walk. Fi means "settled at a place" here, not "asleep", so this keeps running after the dog wakes up. |

### Day Phase — the whole day as 1440 minutes

Totals say how much; they do not say *when*. Fi also exposes the two bars its app
draws on the health page — rest and activity, placed against the local day — and
the `Day Phase` sensor turns them into one timeline where **every minute of the
day carries exactly one phase**, so the six always add up to 1440.

| Phase | Meaning |
|---|---|
| `night_sleep` | Rest belonging to a night rather than to the day — last night's tail after midnight, and the next night once the dog settles into it |
| `day_sleep` | Any other rest |
| `active` | Fi judged the dog active — walks and everything else |
| `awake` | Reporting, but neither resting nor active |
| `offline` | Collar off or charging; nothing is knowable here |
| `no_data` | The part of the day Fi has not reported on yet |

The state is the phase Fi last reported. The bar itself is on the attributes,
already ordered and gapless, so a card needs no arithmetic of its own:

```yaml
day_minutes: 1440
reported_minutes: 1367
totals: {night_sleep: 513, day_sleep: 719, active: 79, awake: 0, offline: 59, no_data: 73}
segments:
  - {phase: night_sleep, start: 0, minutes: 513}
  - {phase: active, start: 513, minutes: 6}
  - {phase: day_sleep, start: 519, minutes: 73}
```

Two things to know before reading the totals as durations. Fi stretches any event
shorter than its minimum render width to that width, so short slices — the
three-minute `active` stubs either side of a long rest — are placements, not
measurements; the bar mirrors what the Fi app draws, and the `Active` stat sensor
carries Fi's own figure. And the hours before midnight sit on *yesterday's* bar:
a night is keyed by the evening it began, so today starts at 00:00 with whatever
of last night ran past it.

A day is night, day, next night, and the two nights are recognised differently.
The morning one is Fi's own: bounded by the settled session's `sleepEnd`, or left
open while Fi still answers `Unavailable`, which is Fi saying the dog is *in* that
night. The evening one cannot be — Fi does not settle it until the next morning —
so it is read off the bar instead: rest still running at the collar's last report,
begun after 18:00, is the night starting rather than another nap. Stirs shorter
than 15 minutes stay inside it, which is just above Fi's own render pad.

Because every statistic carries a state class, they are recorded as **long-term statistics** and can be charted over months. For a per-day view, chart the daily `max`:

```yaml
type: statistics-graph
entities: [sensor.scottie_daily_distance]
period: day
stat_types: [max]
chart_type: bar
```

### Behaviour sensors

Fi's collar detects five behaviours, each exposed as a sensor counting today's
events:

| Sensor | Fi id | Icon |
|---|---|---|
| Barking | `barking` | `mdi:bullhorn` |
| Eating | `eating` | `mdi:food-drumstick` |
| Drinking | `drinking` | `mdi:cup-water` |
| Licking | `cleaning_self` | `mdi:emoticon-tongue-outline` |
| Scratching | `scratching` | `mdi:hand-back-right` |

Each carries the individual event times as attributes, so a chart can rebuild the
whole day without waiting on recorder history:

```yaml
events: ["2026-09-19T01:27:28+03:00", "2026-09-19T01:29:52+03:00"]
last_event: "2026-09-19T01:29:52+03:00"
```

The counts reset at local midnight and use `measurement` for the same reason as
the activity statistics above. They are fetched directly from Fi's
`getPetHealthTrendsForPet` query.

### Binary sensor

Collar battery charging state, with the `battery_charging` device class.

### Light

The collar LED is exposed as a `light` entity. Fi supports a fixed palette — red, green, blue, light blue, purple, yellow and white — and the closest match to the requested colour is used.

### Select

Lost Dog mode is a `select` entity with `Safe` and `Lost` options.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a **custom repository** with category **Integration**.
2. Search for **Fi Tracking** and download it.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/fitracking` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Configuration

Add the integration from **Settings → Devices & Services → Add Integration → Fi Tracking**, then supply:

| Field | Description |
|---|---|
| Username | The e-mail address of your Fi account |
| Password | Your Fi account password |
| Polling | Seconds between updates (default `10`, minimum `1`) |

The polling rate can be changed later from the integration's **Configure** dialog; the entry reloads automatically so the new value takes effect immediately.

An active Fi membership is required — the collar reports nothing without one.

## Dashboard

All three charts need [apexcharts-card](https://github.com/RomRider/apexcharts-card).
Replace `rex` with your own pet's slug.

### The day as one bar

One 1440-minute-long bar across a 24-hour axis, each phase drawn where it
happened. Every series reads the same `segments` attribute and draws its own
phase as thick line runs, so the bar is gapless and the part Fi has not reported
yet is simply missing from the right-hand end.

```yaml
type: custom:apexcharts-card
graph_span: 24h
span:
  start: day
cache: false
update_interval: 5min
chart_type: line
header:
  show: true
  title: Day
  show_states: false
apex_config:
  chart:
    height: 150
    toolbar:
      show: false
  markers:
    size: 0
  stroke:
    curve: straight
    lineCap: butt
  legend:
    show: true
    position: bottom
    horizontalAlign: left
    fontSize: 11px
    markers:
      width: 8
      height: 8
      radius: 2
  grid:
    show: true
    borderColor: var(--divider-color)
    yaxis:
      lines:
        show: false
  xaxis:
    type: datetime
    tickAmount: 8
  yaxis:
    min: 0
    max: 2
    show: false
  tooltip:
    x:
      format: HH:mm
series:
  - entity: sensor.rex_day_phase
    name: Night sleep
    color: "#5B37C4"
    stroke_width: 26
    show:
      legend_value: false
    data_generator: >-
      const P='night_sleep';const d=new Date();d.setHours(0,0,0,0);const
      b=d.getTime();const o=[];(entity.attributes.segments||[]).forEach(s=>{if(s.phase!==P)return;const
      a=b+s.start*6e4,z=a+s.minutes*6e4;o.push([a,1],[z,1],[z+1,null]);});return o;
  - entity: sensor.rex_day_phase
    name: Day sleep
    color: "#9D5CFF"
    stroke_width: 26
    show:
      legend_value: false
    data_generator: >-
      const P='day_sleep';const d=new Date();d.setHours(0,0,0,0);const
      b=d.getTime();const o=[];(entity.attributes.segments||[]).forEach(s=>{if(s.phase!==P)return;const
      a=b+s.start*6e4,z=a+s.minutes*6e4;o.push([a,1],[z,1],[z+1,null]);});return o;
  - entity: sensor.rex_day_phase
    name: Active
    color: "#14CD71"
    stroke_width: 26
    show:
      legend_value: false
    data_generator: >-
      const P='active';const d=new Date();d.setHours(0,0,0,0);const
      b=d.getTime();const o=[];(entity.attributes.segments||[]).forEach(s=>{if(s.phase!==P)return;const
      a=b+s.start*6e4,z=a+s.minutes*6e4;o.push([a,1],[z,1],[z+1,null]);});return o;
  - entity: sensor.rex_day_phase
    name: Awake
    color: "#7A8290"
    stroke_width: 26
    show:
      legend_value: false
    data_generator: >-
      const P='awake';const d=new Date();d.setHours(0,0,0,0);const
      b=d.getTime();const o=[];(entity.attributes.segments||[]).forEach(s=>{if(s.phase!==P)return;const
      a=b+s.start*6e4,z=a+s.minutes*6e4;o.push([a,1],[z,1],[z+1,null]);});return o;
  - entity: sensor.rex_day_phase
    name: Collar off
    color: "#3C424B"
    stroke_width: 26
    show:
      legend_value: false
    data_generator: >-
      const P='offline';const d=new Date();d.setHours(0,0,0,0);const
      b=d.getTime();const o=[];(entity.attributes.segments||[]).forEach(s=>{if(s.phase!==P)return;const
      a=b+s.start*6e4,z=a+s.minutes*6e4;o.push([a,1],[z,1],[z+1,null]);});return o;
```

`legend_value: false` is what keeps the legend from reading `Night sleep: n/a` —
every series carries the same enum state, so there is no number to show there.

The totals read well as a caption underneath:

```yaml
type: markdown
text_only: true
entity_id: [sensor.rex_day_phase]
content: >-
  {% set t = state_attr('sensor.rex_day_phase', 'totals') or {} %}
  Night {{ (t.get('night_sleep', 0) / 60) | round(1) }}h &nbsp;·&nbsp;
  Day {{ (t.get('day_sleep', 0) / 60) | round(1) }}h &nbsp;·&nbsp;
  Active {{ t.get('active', 0) }}m &nbsp;·&nbsp;
  Awake {{ t.get('awake', 0) }}m &nbsp;·&nbsp;
  Off {{ t.get('offline', 0) }}m
```

### Behaviours today

Five lanes across a 24-hour axis, one thin vertical tick per detected event, in
Fi's own colours. The series are built from the `events` attribute rather than
recorder history, so the full day renders immediately after a restart.

```yaml
type: custom:apexcharts-card
graph_span: 24h
span:
  start: day
cache: false
update_interval: 5min
chart_type: line
header:
  show: true
  title: Behaviours today
  show_states: false
apex_config:
  chart:
    height: 240
    toolbar:
      show: false
  markers:
    size: 0
  stroke:
    curve: straight
    width: 2
  legend:
    show: false
  grid:
    show: true
    borderColor: var(--divider-color)
    yaxis:
      lines:
        show: false
  annotations:
    yaxis:
      - y: 1.5
        borderColor: var(--divider-color)
        strokeDashArray: 4
      - y: 2.5
        borderColor: var(--divider-color)
        strokeDashArray: 4
      - y: 3.5
        borderColor: var(--divider-color)
        strokeDashArray: 4
      - y: 4.5
        borderColor: var(--divider-color)
        strokeDashArray: 4
  xaxis:
    type: datetime
  yaxis:
    min: 0
    max: 6
    tickAmount: 6
    labels:
      formatter: >-
        EVAL:function(v){return
        ['','Scratching','Licking','Drinking','Eating','Barking'][Math.round(v)]||''}
  tooltip:
    x:
      format: HH:mm
series:
  - entity: sensor.rex_barking
    name: Barking
    color: "#AD581F"
    stroke_width: 2
    data_generator: |
      const y = 5;
      const out = [];
      (entity.attributes.events || []).forEach(t => {
        const x = new Date(t).getTime();
        out.push([x, y - 0.34]);
        out.push([x, y + 0.34]);
        out.push([x + 1000, null]);
      });
      return out;
  - entity: sensor.rex_eating
    name: Eating
    color: "#FFA411"
    stroke_width: 2
    data_generator: |
      const y = 4;
      const out = [];
      (entity.attributes.events || []).forEach(t => {
        const x = new Date(t).getTime();
        out.push([x, y - 0.34]);
        out.push([x, y + 0.34]);
        out.push([x + 1000, null]);
      });
      return out;
  - entity: sensor.rex_drinking
    name: Drinking
    color: "#5CB5F5"
    stroke_width: 2
    data_generator: |
      const y = 3;
      const out = [];
      (entity.attributes.events || []).forEach(t => {
        const x = new Date(t).getTime();
        out.push([x, y - 0.34]);
        out.push([x, y + 0.34]);
        out.push([x + 1000, null]);
      });
      return out;
  - entity: sensor.rex_licking
    name: Licking
    color: "#E2123C"
    stroke_width: 2
    data_generator: |
      const y = 2;
      const out = [];
      (entity.attributes.events || []).forEach(t => {
        const x = new Date(t).getTime();
        out.push([x, y - 0.34]);
        out.push([x, y + 0.34]);
        out.push([x + 1000, null]);
      });
      return out;
  - entity: sensor.rex_scratching
    name: Scratching
    color: "#FBDC19"
    stroke_width: 2
    data_generator: |
      const y = 1;
      const out = [];
      (entity.attributes.events || []).forEach(t => {
        const x = new Date(t).getTime();
        out.push([x, y - 0.34]);
        out.push([x, y + 0.34]);
        out.push([x + 1000, null]);
      });
      return out;
```

Each event becomes two points at the same timestamp, one either side of the lane
centre, which draws a vertical stroke; the trailing `null` breaks the line so
consecutive events do not join up. `stroke_width` sets how thick the tick is,
and `± 0.34` how tall.

The lanes sit on integers 1–5, so the axis runs `0` to `6` with `tickAmount: 6`
to land a tick — and therefore a label — on each lane centre instead of on the
boundary between two. ApexCharts draws gridlines at those same ticks, which
would then run straight through the strokes, so `grid.yaxis.lines` is off and
the separators are `annotations` at the half values. Labels and separators are
positioned independently that way, and neither drags the other when the axis or
the chart height changes.

### Steps against goal

Daily steps as columns with the goal as a line over the past week.

```yaml
type: custom:apexcharts-card
graph_span: 7d
cache: true
update_interval: 5min
span:
  end: day
header:
  show: true
  show_states: true
  colorize_states: true
  title: Steps Actual vs Goal
apex_config:
  chart:
    type: area
    height: 180
  plotOptions:
    bar:
      columnWidth: 70%
  legend:
    show: false
  dataLabels:
    enabled: true
    distributed: true
    background:
      enabled: false
    style:
      colors:
        - var(--primary-text-color)
  fill:
    type: fill
  grid:
    show: false
  yaxis:
    show: true
series:
  - entity: sensor.rex_daily_steps
    type: column
    name: " "
    color: steelblue
    float_precision: 0
    group_by:
      func: max
      duration: 1d
    show:
      datalabels: true
  - entity: sensor.rex_daily_goal
    type: line
    name: Goal
    color: var(--error-color)
    stroke_width: 2
    float_precision: 0
    group_by:
      func: last
      duration: 1d
    show:
      legend_value: false
      datalabels: false
```

## Connection sources

The collar picks the cheapest transport available and the `Connected To` sensor reports which one is in use:

| State | Meaning |
|---|---|
| `ConnectedToBase` | In Bluetooth range of a Fi Base — lowest power |
| `ConnectedToUser` | In Bluetooth range of a phone running the Fi app |
| `ConnectedToCellular` | Reporting over LTE-M, GPS active — highest power |
| `Unknown` | Offline |

A collar sitting on `ConnectedToCellular` while at home usually means the Base is out of Bluetooth range.

## Differences from upstream

| Change | Why |
|---|---|
| Domain renamed `tryfi` → `fitracking` | Matches Fi's rebrand to fitracking.com |
| **`pytryfi` dependency removed** | The library called `sentry_sdk.init()` with a hardcoded third-party DSN inside `PyTryFi.__init__`, which configures the **global** Sentry client for the whole Home Assistant process ([pytryfi#32](https://github.com/sbabcock23/pytryfi/issues/32), [hass-tryfi#115](https://github.com/sbabcock23/hass-tryfi/issues/115)). PyPI is also frozen at 0.0.21, so upstream fixes were unreachable ([pytryfi#43](https://github.com/sbabcock23/pytryfi/issues/43)) |
| Async-native client | Every call went through `async_add_executor_job`; LED and Lost Mode writes ran blocking HTTP on the event loop |
| Absent data reads `unknown` | `setRestStats` zeroed sleep and nap *before* parsing and swallowed failures, so "Fi sent nothing" was indistinguishable from "the dog slept nothing" ([hass-tryfi#89](https://github.com/sbabcock23/hass-tryfi/issues/89)) |
| Session re-authentication | Nothing ever logged back in once Fi expired the session, which needed a manual reload ([hass-tryfi#91](https://github.com/sbabcock23/hass-tryfi/issues/91)) |
| Reauth flow | Bad credentials now prompt for a new password instead of failing setup |
| Account email no longer polled | The device fragment pulled `UserDetails` — email, phone — into every refresh |
| Rest clipped to the calendar period | Upstream read `restSummaryFeed`, which files a night under the day it began, so `Daily Sleep` showed `0` all morning while the app showed the night's hours |
| `Last Night Sleep` and `Resting Since` | A calendar day splits a night in two; neither sensor exists upstream |
| Goal sensors added | Upstream left `# FUTURE COULD INCLUDE STEP GOAL`; the values were already available |
| Migrated to `SensorEntity` | Entities inherited plain `Entity`, so no `state_class` was possible and no long-term statistics were recorded |
| Per-metric icons | Every statistic returned `mdi:map-marker-distance`, sleep included |
| Device classes and display precision | Distance, duration and battery now render natively |
| Options flow fixed | `OptionsFlow.config_entry` is read-only from HA 2024.11, so the dialog crashed ([#113](https://github.com/sbabcock23/hass-tryfi/issues/113), [#114](https://github.com/sbabcock23/hass-tryfi/pull/114)) |
| Polling rate honoured | Setup read `entry.data` while the options flow wrote `entry.options`, so changes did nothing |
| Resilient entity setup | One malformed pet or base aborted the whole platform ([#112](https://github.com/sbabcock23/hass-tryfi/pull/112), [#93](https://github.com/sbabcock23/hass-tryfi/issues/93)) |
| Modern platform unload | Replaced the deprecated `async_forward_entry_unload` loop |
| Behaviour sensors added | Barking, eating, drinking, licking and scratching are in Fi's API but absent from `pytryfi` |
| `Day Phase` and `Active` added | Fi's `getPetBehaviorDetail` places rest and activity against the day, and `ActivitySummary` carries an active-time total; `pytryfi` queries neither |

## Credits

Original integration by [@sbabcock23](https://github.com/sbabcock23), built on the
[pytryfi](https://github.com/sbabcock23/pytryfi) library. This fork is not affiliated with Fi.

## License

[Apache-2.0](LICENSE)
