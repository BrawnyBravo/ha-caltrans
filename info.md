[![License][license-shield]][license]
[![hacs][hacsbadge]][hacs]

# Caltrans Road Conditions

California chain controls, road weather stations, highway message signs, lane closures and traffic cameras for the
routes you drive, from Caltrans' free public data feeds.

**Unofficial: not affiliated with or endorsed by Caltrans.** Data comes from Caltrans' public feeds and can be late
or wrong; check QuickMap and the road signs before you travel.

**This integration sets up the following platforms.**

| Platform        | Description                                                                         |
| --------------- | ----------------------------------------------------------------------------------- |
| `sensor`        | Chain control level, lane closures, message signs and road weather values per route. |
| `binary_sensor` | Chains required (R-1, R-2 or R-3) per route.                                        |
| `camera`        | Traffic camera still images (off by default).                                        |

{% if not installed %}

## Installation

1. Click Download.
1. Restart Home Assistant.
1. Go to Settings, Devices & Services, Add Integration and search for "Caltrans Road Conditions".

{% endif %}

## Configuration is done in the UI

Pick a Caltrans district, then the routes to follow or a distance around your home.

---

[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[license]: https://github.com/BrawnyBravo/ha-caltrans/blob/main/LICENSE
[license-shield]: https://img.shields.io/github/license/BrawnyBravo/ha-caltrans.svg?style=for-the-badge
