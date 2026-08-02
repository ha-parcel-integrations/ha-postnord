# PostNord — still to verify

The integration is built from a real, redacted PostNord response captured
2026-07-30, its tests pass, and it talks to PostNord's own keyless web endpoint.
Two things can only be confirmed against real, live parcels — this is why it
ships as `0.9.0` rather than `1.0.0`:

- [ ] **Populated `recipientview` shape.** Empty responses are byte-identical to
      `findByIdentifier`, but a *populated* `recipientview` response has not been
      diffed against our sample. A real SE/DK/NO/FI parcel that is missing a field
      we map now logs a one-shot warning with an issue link
      (`check_shipment_shape`), so this self-reports from real usage.
- [ ] **A real Nordic parcel through at least two status changes** in a live Home
      Assistant, to confirm the status vocabulary and event handling end to end.

Delete this file once both are confirmed.
