# PostNord Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-postnord.svg)](https://github.com/ha-parcel-integrations/ha-postnord/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-postnord/total.svg)](https://github.com/ha-parcel-integrations/ha-postnord/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [PostNord](https://www.postnord.com) parcels across Sweden, Denmark, Norway and Finland. Track parcels by tracking code with **no account and no API key**, or log in once with your PostNord account and every parcel in it shows up by itself.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Dynamic polling](#dynamic-polling)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of PostNord parcels by tracking code — no account, no API key
- Or log in with your PostNord account and have its parcels imported automatically — only rotating tokens are stored
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text, the expected delivery window and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `postnord.track_parcel` / `postnord.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- For tracking codes: a parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card)
- For the account: a PostNord account, and a desktop browser for the one-time
  login

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-postnord` as an **Integration**.
3. Install **PostNord** and restart Home Assistant.

### Manual

Copy `custom_components/postnord` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → PostNord** and choose how to follow your parcels. Both can be used side by side.

### Tracking codes

Confirm — there is no account or key to enter. Then add parcels via the integration's **Configure** dialog, the [`postnord.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card. There is one tracking-code hub.

### Account (automatic import)

The form shows a login link. Log in to PostNord in a desktop browser with the developer tools' Network tab open: PostNord ends the login at a `com.postnord.app://redirect?code=…` address the browser cannot open, and you paste that address back into the form within a few minutes. [Step-by-step instructions per browser](docs/finding-the-redirect-url.md).

After that the integration renews its own access; your email and the rotating tokens are stored, nothing else. If PostNord stops accepting them, Home Assistant asks you to log in again the same way. Every parcel in the account is imported — there is no parcel list to manage, and each account is its own device. Parcels you deleted in the PostNord app are not shown.

The account login uses the same service as the PostNord app, including the app's own access key. If PostNord changes that key, account entries stop updating (the log says so) until the integration is updated — logging in again will not help. Tracking-code hubs are not affected.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. Tracking-code hub only. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

## Dynamic polling

Instead of polling PostNord at the same rate around the clock, the
integration adjusts its own cadence to what your tracked parcels are
actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM).
- **Hot (every 15 minutes)** — as soon as a tracked parcel is
  `out_for_delivery`, starting an hour before its expected delivery time (or
  immediately if no time is known).
- **Mid (every 45 minutes)** — any other in-progress parcel.
- **Fully stopped** — nothing is tracked, or every tracked parcel has been
  delivered. Adding a parcel back (via the options dialog, the
  `postnord.track_parcel` service, or a dashboard button) resumes polling
  immediately.
- A small, fixed per-hub offset is added on top, so not every PostNord hub
  out there polls at exactly the same second.

An **account** is polled every 45 minutes and never stops: its whole inbox
comes back in one request, and new parcels have to be noticed.

This is not user-configurable — it is the only polling behaviour this
integration has.

## Removal

Standard HA removal applies: **Settings → Devices & Services → PostNord → ⋮ → Delete**. Nothing is stored on PostNord's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.postnord_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.postnord_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.postnord_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.postnord_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.postnord_awaiting_pickup` | Parcels waiting for you at a pickup point |
| `sensor.postnord_last_successful_update` | Diagnostic: when PostNord was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

An **account** gets the same sensors, named after the account (for example `sensor.postnord_someone_example_com_incoming_parcels`), plus two for the parcels you **send**: `…_outgoing_parcels` (on the way) and `…_outgoing_delivered_parcels` (recently delivered, same retention option). Parcels you send never count as incoming or awaiting pickup.

A **`button.postnord_refresh`** entity triggers an immediate poll outside the
regular interval, and a **`calendar.postnord_deliveries`** entity shows expected
delivery dates for active parcels — read-only, no extra API calls.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by PostNord |
| `in_transit` | In the sorting network |
| `out_for_delivery` | With the courier today |
| `at_pickup_point` | Waiting for you at a pickup location |
| `delivered` | Delivered — also a return that arrived back at the sender (`raw_status` says so) |
| `returning` | Going back to the sender |
| `problem` | PostNord reports an exception |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the PostNord device):

| Event | When |
|---|---|
| `postnord_parcel_registered` | A new parcel appears in the active list |
| `postnord_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `postnord_parcel_delivered` | A parcel is delivered |
| `postnord_parcel_delivery_time_changed` | The expected delivery window changes |
| `postnord_outgoing_parcel_status_changed` | A parcel you sent from your account changes status |
| `postnord_outgoing_parcel_delivered` | A parcel you sent from your account is delivered |

A parcel you send fires only the two outgoing events: it appearing is not news, and its expected delivery time is the recipient's business.

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `postnord.track_parcel` | `tracking_code` | Start tracking a parcel |
| `postnord.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

Both act on the tracking-code hub only, and are not registered while only an account is set up — an account imports its parcels by itself.

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.postnord: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — PostNord has not scanned it yet (their API returns no shipment until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **The account asks to log in again** — PostNord no longer accepts the stored login. Repeat the [browser login](docs/finding-the-redirect-url.md) from the repair notification.
- **The log says the app key was changed** — PostNord rotated the access key the account login relies on. Please [open an issue](https://github.com/ha-parcel-integrations/ha-postnord/issues/new); logging in again will not fix it.
- **A status logs "Unrecognised PostNord status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-postnord/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by PostNord, Home Assistant, or any other third party referenced in this project. Please don't contact PostNord for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to PostNord's terms. Data is sent only to PostNord's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses PostNord's public **Track & Trace** endpoint.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
