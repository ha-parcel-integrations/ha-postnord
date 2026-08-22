# Working in this repository

Home Assistant custom integration for **PostNord** parcel tracking. Distributed
via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
Account-less, keyless (public web key). No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 | *Pre-1.0 releases* — one-shot WARNINGs for anything still unconfirmed |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**API mechanics live in `carrier-research/postnord/api/` (private research repo)** — the keyless
`X-Bap-Key` endpoint, the `TrackingInformationResponse` envelope, the empty-list
/ 401-403 signalling, the status vocabulary and the payload mapping. Do not
duplicate them here.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific decisions (integration only)

- **Keyless via a public web key** (`X-Bap-Key`), not a per-user credential — the
  durable, whitelisted, human-readable kind, *not* the bpost/Evri
  rotating-secret trap. There is **no reauth**: nothing is user-supplied to fix, so
  a retired key surfaces as an API error + one-shot warning, not a reauth prompt.
- **`OTHER` status is deliberately left unmapped** (PostNord's own catch-all) →
  `unknown` + one-shot warning rather than a wrong bucket.
- **Populated-shape self-report** (`check_shipment_shape`): the keyless
  `recipientview` *populated* shape has never been diffed against the captured
  `findByIdentifier` sample, so a real shipment missing a field we map
  (`consignor`/`consignee`/`statusText`/`totalWeight`/`estimatedTimeOfArrival`/
  `items[].events`) logs a one-shot WARNING with the issue link — **keys only, no
  values** (consignor/consignee are PII). Presence is by key, so a present-but-null
  field (a genuinely empty value) stays silent. Remove once the shape is confirmed.
- **`dimensions` always `None`** — the payload reports a total *volume*, not an
  L×W×H triple, so it can't fill the canonical dimensions; kept `None` for parity.
  The ETA is a single instant (`planned_to` always `None`). History is free (same
  `items[].events` list). Reflected in `const.py`'s `CAPABILITIES` (feeds the
  docs site's comparison table) — keep the two in agreement if that ever changes.

## Options and reloads — account-less model

The options flow is one sectioned form; changes apply without a restart.
Account-less carriers (this one) use the **update-listener** model (retunes
`coordinator.update_interval` + `async_request_refresh()`). Account-based carriers
instead call `async_schedule_reload` with **no** listener (combining the two is
deprecated, error in HA 2026.12+). The user-tunable poll interval is a deliberate
HACS divergence (see CONVENTIONS.md).

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, `X-Bap-Key`, error types) | **yes** |
| `const.py` (domain, URLs, `TRACKING_BAP_KEY`, `ParcelStatus`, option keys) | partly (URLs, key) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`) | no |

`parcels.py` is free of I/O and HA objects so the per-carrier part stays
unit-testable. Config: `ConfigEntry.runtime_data` (typed, no `hass.data`),
`PARALLEL_UPDATES = 0`, coordinator takes `config_entry=entry`.
`aiohttp.ClientError` is caught **per parcel** in the gather loop (one bad parcel
doesn't fail the poll) but **not** around the whole update (coordinator wraps
that). Entities: `has_entity_name` + `translation_key`, `icons.json`, translated
units, `_attr_attribution`, `_unrecorded_attributes` on anything with a parcel
list or `raw`. Over-redact diagnostics.

## Running tests

```
python -m pytest tests/ --cov=custom_components.postnord
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference now lives in the private `carrier-research/postnord/api/`,
not in this repo.
