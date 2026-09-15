# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct manufacturing controls for an exact shared sheet in this document."""

from VibeCADNativeCapabilityRegistry import NativeCapabilityDefinition, NativeCapabilityVariant
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


NAME = "sheet_metal.manufacturing"
NETWORK_OPERATIONS = frozenset({"load_materials", "analyze", "quote", "refresh", "checkout"})
WORKER_OPERATIONS = NETWORK_OPERATIONS | {"list_jobs", "inspect_job", "resume_job"}
_FIELDS = {"status": (), "show_panel": (), "load_materials": ("more",), "list_materials": ("offset", "limit"), "analyze": (),
           "set_material": ("part_id", "material_id"), "clear_material": ("part_id",),
           "set_quantity": ("quantity",), "quote": (), "refresh": (), "checkout": (),
           "list_jobs": ("offset", "limit"), "inspect_job": ("job_key",), "resume_job": ("job_key",)}
FIELDS = {name: frozenset(("object_name", *fields)) for name, fields in _FIELDS.items()}


def sheetmetal_manufacturing_capability_definition():
    descriptions = {
        "status": "Read current materials, analyzed part IDs, selections, quote findings and stale state without starting network work. Material rows are paged; list_materials reads further cached rows.",
        "show_panel": "Open the shared RMFG panel for this sheet. May read materials; does not upload the model.",
        "load_materials": "Read a current catalog page. more=false loads the first page; more=true loads the next available page.",
        "list_materials": "Read a bounded page of already loaded catalog materials. Use load_materials with more=true when has_more_materials is true to fetch another remote page.",
        "analyze": "Export and upload validated folded STEP for the current sheet revision, even when displayed flat. Resume its existing analysis when available.",
        "set_material": "Select an observed catalog material_id for one analyzed part_id. Changes manufacturing settings, not CAD geometry.",
        "clear_material": "Clear one analyzed part's manufacturing material selection.",
        "set_quantity": "Set completed design units. Do not multiply by analyzed part instance counts.",
        "quote": "Request a quote for the current revision, explicit materials and quantity. Does not accept manufacturing risks automatically.",
        "refresh": "Read the saved analysis or quote. Retry uncertain submissions with their original saved request key.",
        "list_jobs": "Read a local page of saved RMFG jobs for this sheet. Older revisions remain inspectable; this does not contact RMFG.",
        "inspect_job": "Read the last saved result for an observed job_key. Does not resume a request or change current manufacturing settings.",
        "resume_job": "Resume a saved analysis or quote only for the exact current sheet revision. Restores its settings and reads known remote IDs or retries its original saved request key.",
        "checkout": "Check the current ready quote and open RMFG website checkout for user review. Never pays or places an order.",
    }
    fields = {"object_name": OBJECT_NAME_SCHEMA,
              "part_id": {"type": "string", "minLength": 1, "maxLength": 256},
              "material_id": {"type": "string", "minLength": 1, "maxLength": 256},
              "quantity": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
              "job_key": {"type": "string", "minLength": 1, "maxLength": 256},
              "more": {"type": "boolean"},
              "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50}}
    return NativeCapabilityDefinition(name=NAME, primary_classification="export",
        description="Manufacture a shared sheet through RMFG. Uses the ribbon's settings and direct API; browser sign-in is in sheet_metal.connection.",
        variants=tuple(NativeCapabilityVariant(operation=operation, description=description,
            action_ids=frozenset({"SheetMetal_RMFGManufacture"}), surface_ids=frozenset({"sheet_metal"}),
            exact_target_type="ExactSharedSheetState", transaction_behavior="output",
            background_required=False,
            parameters=parameters_schema({name: ({"type": "integer", "minimum": 1, "maximum": 20}
                                                if operation == "list_jobs" and name == "limit" else fields[name])
                                          for name in ("object_name", *_FIELDS[operation])},
                                         ("object_name", *_FIELDS[operation])))
            for operation, description in descriptions.items()))
