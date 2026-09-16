# SPDX-License-Identifier: LGPL-2.1-or-later
"""Material-only sheet quoting through the shared durable RMFG worker path."""

from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from SheetMetalRMFGJobs import JobStore, run_saved
from SheetMetalRMFGSnapshot import QuoteRequest


def configuration(design, materials, selections):
    if design.get("status") != "ready" or not design.get("parts"):
        raise ValueError("Wait for RMFG to finish analyzing the folded sheet")
    parts = design["parts"]
    ids = [part["id"] for part in parts]
    if len(set(ids)) != len(ids) or set(selections) != set(ids):
        raise ValueError("Select a material for every analyzed part")
    known = {material["id"] for material in materials}
    for part in parts:
        if part.get("suggested_process") != "sheet_metal" or part.get("analysis_status", "ready") != "ready":
            raise ValueError("This panel requires successfully analyzed sheet-metal parts")
        if selections[part["id"]] not in known:
            raise ValueError("Choose an observed material from the RMFG catalog")
    return {"parts": [{"part_id": part_id, "material_id": selections[part_id]} for part_id in sorted(ids)]}


def findings(quote):
    result, seen = [], set()
    def add(items, severity="blocking", part_id=None):
        for item in items:
            if item.get("customer_visible", True) is False:
                continue
            value = {"message": str(item.get("message", item.get("code", "Manufacturing finding"))),
                     "severity": item.get("severity", severity), "part_id": item.get("part_id", part_id)}
            for name in ("code", "hole_id", "bend_id"):
                if name in item:
                    value[name] = item[name]
            key = json.dumps(value, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(value)
    add(quote.get("requirements", []))
    for item in quote.get("items", []):
        add(item.get("requirements", []))
        dfm = item.get("dfm", {})
        add(dfm.get("requirements", []))
        add(dfm.get("assembly_issues", []))
        for part in dfm.get("parts", []):
            add(part.get("issues", []), part_id=part.get("part_id"))
    return result


def _basic_configuration(value):
    # RMFG expands omitted defaults and empty operation arrays in its canonical
    # response. Compare effective materials while rejecting extra operations or
    # risk acceptance that this material-only request never selected.
    if value.get("assembly_operations") or value.get("accepted_risks"):
        raise ValueError("RMFG returned manufacturing operations not selected in this panel")
    defaults = value.get("defaults", {})
    if any(value for key, value in defaults.items() if key != "material_id"):
        raise ValueError("RMFG returned additional manufacturing defaults")
    result = []
    for part in value.get("parts", []):
        if any(value for key, value in part.items() if key not in ("part_id", "material_id")):
            raise ValueError("RMFG returned additional part operations")
        result.append({"part_id": part["part_id"], "material_id": part.get("material_id", defaults.get("material_id"))})
    return {"parts": sorted(result, key=lambda part: part["part_id"])}


def ready_quote(quote, items):
    if quote.get("status") != "ready":
        raise ValueError("Checkout requires a ready RMFG quote")
    expiry = quote.get("expires_at")
    if expiry:
        expires = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ValueError("The RMFG quote has expired; request a new quote")
    quoted = quote.get("items", [])
    if len(quoted) != len(items):
        raise ValueError("The quote does not contain the requested designs")
    for actual, expected in zip(quoted, items):
        dfm = actual.get("dfm", {})
        if (actual.get("design_id") != expected["design_id"] or actual.get("quantity") != expected["quantity"]
                or actual.get("status", "ready") != "ready" or dfm.get("status", "ready") != "ready"
                or dfm.get("design_id") != expected["design_id"]
                or _basic_configuration(dfm.get("configuration", {})) != expected["configuration"]):
            raise ValueError("The ready quote does not match the selected manufacturing settings")
    return quote


def checkout_url(value):
    url = urlsplit(value)
    if (url.scheme != "https" or url.hostname not in ("rmfg.com", "www.rmfg.com")
            or url.username is not None or url.password is not None or url.port not in (None, 443)):
        raise ValueError("RMFG did not return a valid website checkout link")
    return value


class Backend:
    """Worker-only disk and network operations; contains no live document or GUI."""
    def __init__(self, database, client):
        self.database, self.client = database, client

    def materials(self, cursor=None):
        page = self.client.materials(cursor=cursor)
        if not isinstance(page.get("data"), list) or (page.get("has_more") and not page.get("next_cursor")):
            raise ValueError("RMFG returned an invalid material catalog page")
        return page

    def analyze(self, export, key):
        store = JobStore(self.database)
        store.record(kind="analyze", export=export, payload={"filename": "sheet.step"}, operation_key=key)
        return {"job_key": key, "response": run_saved(store, self.client, key)}

    def evaluate(self, export, design, materials, selections, quantity, key):
        config = configuration(design, materials, selections)
        request = QuoteRequest(export, design["id"], config, quantity)
        store = JobStore(self.database)
        store.record(kind="quote", export=export, payload={"items": request.items()}, operation_key=key)
        return {"job_key": key, "response": run_saved(store, self.client, key)}

    def refresh(self, key):
        return {"job_key": key, "response": run_saved(JobStore(self.database), self.client, key)}

    def checkout(self, export, config, quantity, quote_key, cart_key):
        store = JobStore(self.database)
        quoted = store.get(quote_key)
        if (quoted.kind != "quote" or not quoted.export.matches_revision(export.revision)
                or quoted.export.step_sha256 != export.step_sha256):
            raise ValueError("The quote belongs to an older sheet revision")
        items = QuoteRequest(export, quoted.payload["items"][0]["design_id"], config, quantity).items()
        if items != quoted.payload["items"]:
            raise ValueError("The manufacturing settings changed after quoting")
        ready_quote(run_saved(store, self.client, quote_key), items)
        store.record(kind="checkout", export=export, payload={"items": items}, operation_key=cart_key)
        cart = run_saved(store, self.client, cart_key)
        if cart.get("status") != "open":
            raise ValueError("The RMFG checkout is no longer open")
        return {"job_key": cart_key, "url": checkout_url(cart.get("cart_url", ""))}


    def list_jobs(self, document_uid, object_name, *, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Use a nonnegative offset and job limit from 1 to 20")
        store = JobStore(self.database)
        page = store.for_sheet(document_uid, object_name, offset=offset, limit=limit + 1)
        return {"jobs": [_saved_summary(job, store.last_response(job.operation_key)) for job in page[:limit]],
                "next_offset": offset + limit if len(page) > limit else None}

    def inspect_job(self, document_uid, object_name, operation_key):
        store = JobStore(self.database)
        summary = _owned_summary(store, document_uid, object_name, operation_key)
        response = store.last_response(operation_key)
        result = _saved_summary(summary, response)
        result["response"] = None
        if response is not None:
            result["response"] = {key: response[key] for key in (
                "id", "status", "currency", "amount_total_cents", "amount_subtotal_cents", "expires_at") if key in response}
            if summary.kind == "quote":
                result["response"]["findings"] = findings(response)
        if summary.kind == "quote":
            items = store.request_payload(operation_key).get("items", [])
            if len(items) == 1:
                result["quantity"] = items[0].get("quantity")
                result["design_id"] = items[0].get("design_id")
        return result

    def resume_job(self, document_uid, object_name, operation_key, revision):
        store = JobStore(self.database)
        summary = _owned_summary(store, document_uid, object_name, operation_key)
        if summary.revision != revision:
            raise ValueError("This job belongs to an earlier sheet revision or document session; analyze the current sheet")
        if summary.kind not in ("analyze", "quote"):
            raise ValueError("Resume an analysis or quote; saved checkout and DFM records are available for inspection")
        job = store.get(operation_key)
        result = {"operation_key": operation_key, "kind": summary.kind, "export": job.export}
        if summary.kind == "quote":
            items = job.payload.get("items", [])
            if len(items) != 1:
                raise ValueError("This sheet panel requires one quoted design")
            item = items[0]
            config = _basic_configuration(item["configuration"])
            request = QuoteRequest(job.export, item["design_id"], config, item["quantity"])
            if request.items() != items:
                raise ValueError("The saved quote has additional manufacturing settings; analyze and configure a new quote")
            design = self.client.design(item["design_id"])
            if design.get("id") != item["design_id"]:
                raise ValueError("RMFG returned a different design for this quote")
            result.update(design=design, quantity=item["quantity"],
                          selections={part["part_id"]: part["material_id"] for part in config["parts"]},
                          quote_fingerprint=request.fingerprint)
        result["response"] = run_saved(store, self.client, operation_key)
        result["inspection"] = self.inspect_job(document_uid, object_name, operation_key)
        return result


def _owned_summary(store, document_uid, object_name, operation_key):
    summary = store.summary(operation_key)
    if summary.revision["document_uid"] != document_uid or summary.revision["object_name"] != object_name:
        raise ValueError("The saved job belongs to a different sheet")
    return summary


def _saved_summary(summary, response):
    return {"operation_key": summary.operation_key, "kind": summary.kind,
            "revision": summary.revision, "step_sha256": summary.step_sha256,
            "remote_id": None if response is None else response.get("id"),
            "status": "unconfirmed" if response is None else response.get("status", "unknown")}
