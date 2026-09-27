"""The pre-account import paths must keep resolving.

`api.py`, `coordinator.py` and `parcels.py` at package root became re-export
shims when the sources were split into `tracking/` and `account/`. Nothing in
this repo imports them any more, so only a test notices if a rename quietly
breaks a user's automation or an external import.
"""
from custom_components.postnord import api, coordinator, parcels
from custom_components.postnord.tracking import api as tracking_api
from custom_components.postnord.tracking import coordinator as tracking_coordinator
from custom_components.postnord.tracking import parcels as tracking_parcels


def test_root_coordinator_module_re_exports_the_tracking_coordinator():
    assert coordinator.PostNordCoordinator is tracking_coordinator.PostNordCoordinator


def test_root_api_module_re_exports_the_tracking_client():
    assert api.PostNordApiClient is tracking_api.PostNordApiClient
    assert api.PostNordApiError is tracking_api.PostNordApiError


def test_root_parcels_module_re_exports_the_tracking_normaliser():
    assert parcels.normalize_parcel is tracking_parcels.normalize_parcel
