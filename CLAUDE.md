# Working in this repository

Home Assistant custom integration for **PostNord** parcel tracking. Distributed
via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
Two sources: keyless tracking codes (public web key) and an opt-in account
inbox (pasted-redirect OAuth login). No DTO layer.

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
/ 401-403 signalling, the status vocabulary, the payload mapping and the
account login/inbox calls. Do not duplicate them here.

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **Every `entry.data.get(CONF_SOURCE, …)` defaults to `SOURCE_TRACKING`** —
  entries from before the account source have `data == {}`; a dispatch site
  without the default silently turns the user's tracking hub into an account.

## Carrier-specific decisions (integration only)

**Two sources in one domain — `tracking/` and `account/` subpackages**,
mirroring ha-bpost's split exactly. Each owns its client, coordinator and
normaliser; `api.py`, `coordinator.py` and `parcels.py` at package root are
re-export shims kept so existing imports still resolve
(`tests/test_compat_imports.py`). Tests mirror the split (`tests/tracking/`,
`tests/account/`); the source-agnostic platforms keep theirs at the top level.
- **One status vocabulary, in `status.py`.** Both sources report PostNord's one
  machine `status` enum, so neither can drift from the map. Normalisation is
  not shared: there is no generic normaliser, each source shapes its own
  payload with its own one-shot WARNING net. `events.py` holds the HA-bus
  contract both coordinators fire.
- **The account normaliser reuses the tracker's field extractors**
  (`_flatten_events`, `_weight_kg`, `_dimensions_cm`, `_pickup_point_name`,
  `build_history`, `settle_return`) because the account list carries the same
  shipment object, with different placement — confirmed on a real account
  response 2026-09-27 (`tests/account/payloads.py::captured_service_point_delivery`):
  `statusText` sits on each item, measurements are `items[].dimensions` /
  `items[].weight`, the pickup point is `destinationDeliveryPoint.name`, and the
  consignee has no name (`receiver` stays `None`). The extractors try both
  placements, so the tracker benefits too. Still unconfirmed: the ETA key on an
  *active* account parcel (read from `estimatedTimeOfArrival`, as on the
  tracker), and any `userData.direction` other than `INCOMING` (warns once).
- **`CAPABILITIES_BY_VARIANT`** (`Tracking` / `Account`), `CAPABILITIES`
  aliased to `Tracking` for the docs-site table. Both variants fill every
  optional field today; a test ties the Account set to what the normaliser
  fills.
- **History: an `OTHER` event keeps the previous status.** On events `OTHER`
  is PostNord's notification / intermediate-scan code (`z3D`, `z82`, …), not
  an unknown status, so `build_history` carries the prior canonical status over
  it and `map_event_status` does not warn for it. A *shipment*-level `OTHER`
  still maps to `unknown` + WARNING.
- **A finished return is `delivered`** (`settle_return`): PostNord ends a
  return leg with a `DELIVERED` event while the shipment keeps `RETURNED`. Seen
  in a real account history 2026-09-27. Same rule as bpost; `raw_status` keeps
  the return text. Without it an archived return sits among the active parcels
  forever.
- **The account coordinator exposes the tracking coordinator's surface**
  (`data` = active incoming, `delivered`, `delivered_codes`,
  `current_tier_minutes`, `last_success_time`), so sensors, calendar and
  diagnostics need no source switch. Unlike bpost, there are no outgoing
  sensors: the account list has no observed direction marker, so everything is
  incoming — an always-zero outgoing sensor would be a wrong claim.

**An entry's source is `entry.data[CONF_SOURCE]` (`tracking` / `account`).**
Setup opens on a menu. The tracking hub keeps unique id `postnord`, and that
id — not the manifest — now keeps it to one: `single_config_entry` was removed
because account entries (`account:<lowercased email>`) sit beside it. No entry
version bump; the `SOURCE_TRACKING` default is the migration.

**An account entry has no parcel management.** Options menu is `settings`
only; `postnord.track_parcel`/`untrack_parcel` only resolve tracking hubs, are
not registered for an account-only setup, and are removed when the tracking
hub unloads, not the last entry. `deletedShipments` is never read, and a
shipment in both buckets is listed once. Account polling never suspends (one
batched call, fixed mid tier).

**Account login: ha-dhl's pasted redirect, not a password.** The public app
client only redirects to `com.postnord.app://`, so the flow shows an
authorize URL and takes the pasted redirect back. The PKCE verifier/state live
in the flow instance only and are generated once per flow — a retry after a
bad paste must keep the URL the user has open. Reauth is the same paste form;
a different account aborts `unique_id_mismatch`. Only email + the rotating
token pair are stored; a refresh writes both back through the client's
`token_callback`. User help lives in `docs/finding-the-redirect-url.md`.

**Refresh-then-still-refused is a compatibility failure, not reauth.** 401/403
→ one refresh → one retry. A refused refresh raises `ConfigEntryAuthFailed`;
a retry refused *after* a successful refresh means PostNord rotated the app's
gateway key (`ACCOUNT_API_KEY`), which a new login would not fix — it is an
`UpdateFailed` with a one-shot WARNING. `ACCOUNT_API_KEY` is shared app
material: `const.py` only, never in UI, diagnostics, logs or fixtures. The
tracking source keeps `web-ncp` and never uses it.

**Diagnostics redact `entry_data` too** — tokens, email, login material and
every account-payload PII key are in `TO_REDACT`;
`tests/account/test_setup.py` walks the dump for the literal values.

**Tracking source:**

- **Keyless via a public web key** (`X-Bap-Key`), not a per-user credential — the
  durable, whitelisted, human-readable kind, *not* the bpost/Evri
  rotating-secret trap. There is **no reauth**: nothing is user-supplied to fix, so
  a retired key surfaces as an API error + one-shot warning, not a reauth prompt.
- **`OTHER` status is deliberately left unmapped** (PostNord's own catch-all) →
  `unknown` + one-shot warning rather than a wrong bucket.
- **Populated-shape self-report** (`check_shipment_shape`): the keyless
  `recipientview` *populated* shape has never been diffed against the captured
  `findByIdentifier` sample, so a real shipment missing a field we map
  (`consignor`/`consignee`/`statusText`/`totalWeight`/`items[].events`) logs a
  one-shot WARNING with the issue link — **keys only, no values**
  (consignor/consignee are PII). Presence is by key, so a present-but-null field
  (a genuinely empty value) stays silent. Remove once the shape is confirmed.
  `estimatedTimeOfArrival` is checked separately and only on a *non-delivered*
  shipment: real delivered shipments confirmed 2026-08-24 drop the key entirely
  (not null) once delivered, which is the correct shape — an ETA is meaningless
  after delivery — so that case no longer warns.
- **`dimensions` come from `items[0].statedMeasurement`**, else
  `items[0].dimensions` (the account placement) — L×W×H in metres → cm; `None`
  when an axis is missing. Weight prefers `totalWeight`, then stated, then
  assessed, then `items[0].weight`. `pickup_point` is
  `destinationDeliveryPoint.name`, else `deliveryPoint.name`, else the location
  of the newest `AVAILABLE_FOR_DELIVERY` event. The ETA is a single instant
  (`planned_to` always `None`). History is free (same `items[].events` list). Reflected in `const.py`'s `CAPABILITIES` (feeds the
  docs site's comparison table) — keep the two in agreement if that ever changes.

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Module layout* — split into `tracking/` and `account/` (see above);
`tracking/api.py` authenticates with an `X-Bap-Key` header
(`TRACKING_BAP_KEY` in `const.py`), not the stock scheme.

## Running tests

```
python -m pytest tests/ --cov=custom_components.postnord
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference now lives in the private `carrier-research/postnord/api/`,
not in this repo.
