#!/usr/bin/env python3
"""
reference_platform.py: a minimal platform that runs Accelerated IdP Flow steps 1 and 2 and
presents the grant to the business, recording every request it makes (the session log the
checks assert on). `mode` is the kill switch:

  select_by_issuer  conformant: selects an oauth2 entry only when auth_url is byte identical
                    to an issuer in its own recognized set (configured out of band, never
                    from a profile), falls back to direct OAuth otherwise.
  select_by_key     defect: matches on the provider key it associates with its IdP, the
                    reading the pre change text permits ("typically one belonging to").
  select_by_profile defect: trusts the profile, takes the first oauth2 entry listed.
  normalize_issuer  defect: compares auth_url after stripping a trailing slash and
                    lowercasing, the normalization the spec forbids.
"""
import base64, json, os, sys, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mocks import WELL_KNOWN, TOKEN_EXCHANGE, JWT_BEARER, JWT_TYPE   # noqa: E402

MODES = ("select_by_issuer", "select_by_key", "select_by_profile", "normalize_issuer")
CONFORMANT = "select_by_issuer"


def _norm(u):
    return u.rstrip("/").lower()


class ReferencePlatform:
    def __init__(self, recognized, mode=CONFORMANT, familiar_keys=("app.example.login",)):
        """recognized: {issuer_identifier: {"client_id", "client_secret", "subject_token"}},
        the platform's own client registrations. familiar_keys: the provider keys a key
        matching platform believes name its IdP (used by the defect modes only)."""
        assert mode in MODES, mode
        self.recognized, self.mode, self.familiar_keys = dict(recognized), mode, tuple(familiar_keys)
        self.session = []

    # ---- transport (every request is logged) --------------------------------------
    def _get(self, op, url):
        e = {"op": op, "method": "GET", "url": url, "form": None, "authorization": None}
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                e["status"] = r.status
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as err:
            e["status"], body = err.code, {}
        self.session.append(e)
        return body

    def _post(self, op, url, form, basic=None):
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        auth = None
        if basic:
            auth = "Basic " + base64.b64encode(("%s:%s" % basic).encode()).decode()
            req.add_header("Authorization", auth)
        e = {"op": op, "method": "POST", "url": url, "form": dict(form), "authorization": auth}
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                e["status"] = r.status
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as err:
            e["status"], body = err.code, json.loads(err.read().decode() or "{}")
        self.session.append(e)
        return e["status"], body

    # ---- Provider Selection ---------------------------------------------------------
    def select(self, providers):
        """Returns (key, entry, issuer_used) or None. issuer_used names the recognized
        relationship whose credentials and subject_token the platform will present."""
        first_recognized = next(iter(self.recognized))
        for key, entries in providers.items():
            for entry in entries:
                if entry.get("type") != "oauth2":
                    continue
                au = entry.get("auth_url", "")
                if self.mode == "select_by_issuer":
                    if au in self.recognized:
                        return key, entry, au
                elif self.mode == "select_by_key":
                    if key in self.familiar_keys:
                        return key, entry, first_recognized
                elif self.mode == "select_by_profile":
                    return key, entry, first_recognized
                elif self.mode == "normalize_issuer":
                    for iss in self.recognized:
                        if _norm(iss) == _norm(au):
                            return key, entry, iss
        return None

    # ---- the flow -------------------------------------------------------------------
    def link(self, business_base):
        profile = self._get("discover_profile", business_base + "/.well-known/ucp")
        cap = profile["ucp"]["capabilities"]["dev.ucp.common.identity_linking"][0]
        sel = self.select(cap["config"].get("providers") or {})
        if sel is None:
            # Identity Providers: fall back to direct OAuth on the business domain.
            self._get("discover_business_metadata", business_base + WELL_KNOWN)
            return "fallback_direct_oauth"
        key, entry, issuer_used = sel
        meta = self._get("discover_idp_metadata", entry["auth_url"].rstrip("/") + WELL_KNOWN)
        creds = self.recognized[issuer_used]
        status, tok = self._post("token_exchange", meta["token_endpoint"], {
            "grant_type": TOKEN_EXCHANGE,
            "subject_token": creds["subject_token"],
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": business_base,
            "requested_token_type": JWT_TYPE,
        }, basic=(creds["client_id"], creds["client_secret"]))
        if status != 200:
            return "exchange_rejected"
        status, _ = self._post("present_grant", business_base + "/token", {
            "grant_type": JWT_BEARER, "assertion": tok["access_token"],
            "scope": "dev.ucp.shopping.order:read"})
        return "chained" if status == 200 else "chain_rejected"
