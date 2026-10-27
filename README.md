[![hacs][hacsbadge]][hacs]
[![License][license-shield]][license]

# Caltrans Road Conditions for Home Assistant

California road conditions in Home Assistant: chain controls, road weather stations, highway message signs, lane
closures and traffic cameras for the routes you drive, from Caltrans' free public data feeds.

> **Unofficial.** This integration is not affiliated with or endorsed by Caltrans (the California Department of
> Transportation). It reads the public data Caltrans publishes at
> [cwwp2.dot.ca.gov](https://cwwp2.dot.ca.gov/documentation/), which Caltrans offers free of charge. Data can be late,
> incomplete or wrong. For travel decisions, always check [QuickMap](https://quickmap.dot.ca.gov/), call 1-800-427-7623,
> and obey the signs on the road.

Needs Home Assistant 2026.9.0 or newer. No account or API key is needed.

## What you get

For each route you follow (for example I-80 or US-50) you get a device with:

| Entity | What it shows |
| --- | --- |
| **Chain control** | The worst requirement in effect anywhere on the route: `None`, `Trucks and trailers only`, `R-1`, `R-2`, `R-3`, `Traffic held or escorted` or `Closed`. Attributes list each segment with a control, its status, description, elevation and when it changed. |
| **Chains required** | On while any chain control point on the route is at R-1, R-2 or R-3, or is closed or has traffic held or escorted (so a closed summit next to R-2 approaches keeps it on). Attributes: `chains_level` (worst of R-1/R-2/R-3, or `none`), `closed`, `traffic_held_or_escorted`, `level` (worst overall), `status` and `where`. Trucks-only restrictions do not turn it on. |
| **Road weather stations** | For each Caltrans weather station on the route: pavement temperature, pavement condition (dry, wet, ice warning, snow warning, frost and so on), air temperature, visibility, wind speed and wind gust. Wind direction is available but off by default. Units follow your Home Assistant settings. |
| **Message signs** | How many electronic signs on the route are showing a message, with the text of each (up to 20) as attributes. A sensor per sign also exists, off by default; turn on the ones you care about. Its state is the sign's text, or empty when the sign is blank. |
| **Lane closures** | How many lane closures are in effect on the route right now, with details of up to 20 (where, which lanes, type of work, planned end). |
| **Traffic cameras** | Optional and off by default (busy routes have over a hundred). Still images from Caltrans cameras on the route. |

What the chain control levels mean (from Caltrans):

- **R-1**: chains, traction devices or snow tires required on the drive axle, except four-wheel or all-wheel drive
  vehicles.
- **R-2**: chains or traction devices required on all vehicles except four-wheel or all-wheel drive vehicles with snow
  tread tires on all four wheels.
- **R-3**: chains or traction devices required on all vehicles, no exceptions.
- **Trucks and trailers only**: restrictions for trucks or vehicles towing trailers (R-1 Modified and truck screening).

Instead of routes you can choose **Everything near home**: one device covering everything within a distance you pick
of the home location set in Home Assistant. That device is named *Roads near home (District N)*, so its entity ids
look like `sensor.roads_near_home_district_3_chain_control`. If you move your home location in Home Assistant, reload
the integration; if home is outside the district you picked, nothing will match.

## Install

This is a custom integration installed through [HACS](https://hacs.xyz).

1. In Home Assistant open **HACS**, then the three-dot menu at the top right, then **Custom repositories**.
2. Paste `https://github.com/BrawnyBravo/ha-caltrans`, choose type **Integration**, and press **Add**.
3. Search HACS for **Caltrans Road Conditions**, open it and press **Download**.
4. Restart Home Assistant.

Manual install: copy `custom_components/caltrans_roads` into the `custom_components` folder of your Home Assistant
configuration and restart.

## Set up

1. Go to **Settings, Devices & services, Add integration** and search for **Caltrans Road Conditions**.
2. The first screen asks for the **district** and **what to follow**. Caltrans splits the state into 12 districts; the
   list shows the area each covers (for example *District 3: Marysville / Sacramento / Lake Tahoe* covers I-80 over
   Donner Summit and US-50 over Echo Summit). Choose **Chosen routes** or **Everything near home**.
3. With chosen routes, the next screen lists every route that currently appears in that district's feeds. Pick the
   routes you drive and tick the kinds of data you want (chain controls, road weather stations, message signs and lane
   closures are ticked; traffic cameras are not). Kinds of data a district does not publish are not offered: districts
   4, 5 and 12 have no chain control or weather station feed, and 7 and 11 have no weather station feed.
4. With near home, the next screen asks for a distance in kilometres (25 by default) and the kinds of data.
5. Press **Submit**. A device appears for each route (or one called *Roads near home (District N)*).

To change routes, distance or data later, press **Configure** on the integration. Devices of routes you drop, and
entities of data types you turn off, are removed. To follow routes in more than one
district, add the integration again and pick the other district.

## How fresh is the data

Caltrans updates its files about once a minute. The integration fetches chain controls and signs every 3 minutes,
weather stations every 5 minutes, lane closures every 30 minutes (those files are several megabytes) and the camera
list every 6 hours. Camera images are fetched when you look at them, at most once a minute.

Startup and reloads wait only for the small files; lane closures and the camera list load in the background, and a
file downloaded in the last few minutes is reused when you change options instead of being downloaded again.

If Caltrans' site is down, sensors keep their last value for a while (30 minutes for chain controls and signs, an
hour for weather, three hours for lane closures) and then show as unavailable until the data comes back. If a feed has
not loaded for six hours, a notice appears under **Settings, Repairs**; it clears itself when the feed returns.

A weather station whose own last report is more than six hours old shows as unavailable rather than an old reading.
Several Caltrans stations stop reporting for days at a time, and sometimes a whole district's weather file is an old
copy (on 26 Oct 2026 district 3's file was still serving readings from May). When even the newest station in the file
is more than six hours old, the whole feed counts as an outage, which brings the same repair notice.

Chain control and sign files are checked the same way: Caltrans stamps every record with the time the file was
written, and a file more than two hours old is treated as an outage, so an old copy can never show yesterday's chain
controls as current.

## Automation examples

Replace the entity ids with yours (they are based on the route name, for example `binary_sensor.i_80_chains_required`).

Send a phone notification when chains become required on I-80:

```yaml
automation:
  - alias: "Chains required on I-80"
    triggers:
      - trigger: state
        entity_id: binary_sensor.i_80_chains_required
        to: "on"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "Chains required on I-80"
          message: >
            {{ state_attr('binary_sensor.i_80_chains_required', 'status') }} at
            {{ state_attr('binary_sensor.i_80_chains_required', 'where') }}
```

Turn on a lamp as a reminder when US-50 reaches R-2 or worse:

```yaml
automation:
  - alias: "US-50 at R-2 or worse"
    triggers:
      - trigger: state
        entity_id: sensor.us_50_chain_control
    conditions:
      - condition: template
        value_template: >
          {{ states('sensor.us_50_chain_control') in ['r2', 'r3', 'traffic_control', 'closed'] }}
    actions:
      - action: light.turn_on
        target:
          entity_id: light.living_room_lamp
        data:
          color_name: red
```

Warn when a pavement sensor reports ice or snow:

```yaml
automation:
  - alias: "Ice or snow on the pavement at Donner Summit"
    triggers:
      - trigger: state
        entity_id: sensor.i_80_hwy_80_at_donner_summit_pavement
        to:
          - ice_warning
          - snow_warning
          - frost
    actions:
      - action: notify.notify
        data:
          message: "Pavement at Donner Summit: {{ trigger.to_state.state | replace('_', ' ') }}"
```

The chain control sensor's states are `none`, `trucks_only`, `r1`, `r2`, `r3`, `traffic_control` and `closed` (the
names shown in the interface are translated from these).

## Things to know

- A followed route with no chain control points in the district (most city freeways) always shows `None`.
- Routes are matched exactly as Caltrans names them (`I-80`, `US-50`, `SR-89`). A route that crosses districts (I-5
  runs through several) is only covered for the district you chose.
- Status codes Caltrans adds in future, or garbage in the status field (it happens), are ignored rather than guessed
  at, and listed in the chain control sensor's `unrecognized_statuses` attribute.
- Weather units: Caltrans reports temperatures in tenths of a degree Celsius, wind in tenths of a metre per second and
  visibility in tenths of a metre (the NTCIP 1204 standard its documentation refers to). The integration converts
  these, and Home Assistant shows them in your preferred units.
- Faulty sensors are shown as unknown, not as readings: a pavement sensor that reports its own error (no response, cut
  cable, short circuit) is skipped (several district 3 sensors report 0 C and "dry" while broken); an air temperature
  below -40 C or above 57 C is dropped; and a station reporting 0% humidity has its humidity probe treated as failed,
  so its air temperature, dew point and humidity are dropped too.
- Lane closures count only closures the crew has marked as started and not yet finished or cancelled. Planned future
  closures are not counted. A closure the crew never marks as finished stops counting two hours after its planned end.
- If two entries (different districts) follow the same route, Home Assistant names the second device's entities with
  `_2`; rename the device if you prefer.
- Camera images belong to Caltrans and are shown, not stored. Please do not republish them.
- **Download diagnostics** on the integration shows each feed's address, when it last loaded and any error. It does not
  include your home location.

## Removing

Go to **Settings, Devices & services**, open Caltrans Road Conditions, and choose **Delete** from the three-dot menu.
Then remove it in HACS if you no longer want it.

## Credits and licence

Data: Caltrans Commercial Wholesale Web Portal ([documentation](https://cwwp2.dot.ca.gov/documentation/),
[conditions of use](https://dot.ca.gov/conditions-of-use)). The icon is original and is not a Caltrans logo.

MIT licence. See [LICENSE](LICENSE).

[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[license]: https://github.com/BrawnyBravo/ha-caltrans/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/BrawnyBravo/ha-caltrans.svg?style=for-the-badge
