# SPDX-License-Identifier: LGPL-2.1-or-later

"""Built-in and user-managed prompt starters for the VibeCAD composer."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
import uuid

from VibeCADProject import vibecad_data_dir


PROMPT_STARTERS_SCHEMA = "vibecad-prompt-starters-v1"
PROMPT_STARTERS_VERSION = 1
PROMPT_STARTERS_FILE_NAME = "prompt-starters.json"
MAX_STARTER_NAME_LENGTH = 80
MAX_STARTER_CONTENT_LENGTH = 12_000

CATEGORY_ORDER = (
    "General",
    "New Part",
    "Modify",
    "3D Print",
    "CNC",
    "Assembly",
    "Enclosure",
    "Sheet Metal",
    "Review",
)

_CUSTOM_ID_PATTERN = re.compile(r"custom:[0-9a-f]{32}")


@dataclass(frozen=True)
class PromptStarter:
    starter_id: str
    name: str
    category: str
    content: str
    builtin: bool = False

    def custom_record(self) -> dict[str, str]:
        if self.builtin:
            raise ValueError("Built-in prompt starters cannot be saved as custom entries.")
        return {
            "id": self.starter_id,
            "name": self.name,
            "category": self.category,
            "content": self.content,
        }


def prompt_starters_path() -> Path:
    return vibecad_data_dir() / PROMPT_STARTERS_FILE_NAME


def _required_text(
    value: Any,
    *,
    field: str,
    maximum: int,
    single_line: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Prompt starter {field} must be a string.")
    clean = value.strip()
    if not clean:
        raise ValueError(f"Prompt starter {field} cannot be empty.")
    if len(clean) > maximum:
        raise ValueError(
            f"Prompt starter {field} exceeds the {maximum}-character limit."
        )
    if single_line and any(character in clean for character in "\r\n"):
        raise ValueError(f"Prompt starter {field} must be a single line.")
    return clean


def _validated_starter(record: Any, *, builtin: bool) -> PromptStarter:
    if not isinstance(record, dict):
        raise ValueError("Each prompt starter must be a JSON object.")
    expected_fields = {"id", "name", "category", "content"}
    if set(record) != expected_fields:
        missing = sorted(expected_fields - set(record))
        unknown = sorted(set(record) - expected_fields)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError("Prompt starter fields are invalid: " + "; ".join(details))

    starter_id = _required_text(
        record["id"], field="id", maximum=96, single_line=True
    )
    expected_prefix = "builtin:" if builtin else "custom:"
    if not starter_id.startswith(expected_prefix):
        raise ValueError(
            f"Prompt starter id {starter_id!r} must begin with {expected_prefix!r}."
        )
    if not builtin and not _CUSTOM_ID_PATTERN.fullmatch(starter_id):
        raise ValueError(
            f"Custom prompt starter id {starter_id!r} is not a valid VibeCAD id."
        )

    name = _required_text(
        record["name"],
        field="name",
        maximum=MAX_STARTER_NAME_LENGTH,
        single_line=True,
    )
    category = _required_text(
        record["category"], field="category", maximum=40, single_line=True
    )
    if category not in CATEGORY_ORDER:
        raise ValueError(
            f"Prompt starter {name!r} uses unsupported category {category!r}."
        )
    content = _required_text(
        record["content"], field="content", maximum=MAX_STARTER_CONTENT_LENGTH
    )
    return PromptStarter(starter_id, name, category, content, builtin=builtin)


_BUILTIN_RECORDS = (
    {
        "id": "builtin:new-part",
        "name": "New parametric part",
        "category": "New Part",
        "content": """
Create a new part for: [intended outcome and how the part will be used]

Driving requirements:
- Overall envelope: [dimensions and units]
- Critical geometry and dimensions: [features, sizes, and locations]
- Interfaces or mating features: [what connects and how]
- Material and manufacturing process: [material and process]
- Loads and use conditions: [forces, temperatures, environment, duty cycle]
- Tolerances and clearances: [critical values]
- Must avoid or preserve: [non-negotiable constraints]
- Completion criteria: [what must be true when finished]

Where a requirement is genuinely unknown, ask a focused question before committing that geometry.
""",
    },
    {
        "id": "builtin:modify-existing",
        "name": "Modify the current model",
        "category": "Modify",
        "content": """
Modify the current model to: [intended result]

Authoritative target:
- Document, Body, or feature: [exact name or selected object]

Preserve exactly:
- [dimensions, interfaces, features, placements, or history that must remain]

Change:
- [specific change and new value]

Reason and acceptance criteria:
- [why the change is needed]
- [how the finished model should be verified]

Preserve the existing model identity and feature history. Do not replace or rebuild
the target unless I explicitly approve it.
""",
    },
    {
        "id": "builtin:fdm-part",
        "name": "FDM-printable part",
        "category": "3D Print",
        "content": """
Design an FDM-printable part for: [intended outcome]

Requirements:
- Overall dimensions: [dimensions and units]
- Interfaces and critical features: [holes, inserts, snaps, mating geometry]
- Material: [PLA, PETG, ABS, nylon, or other]
- Printer constraints: [nozzle, layer height, build volume]
- Preferred print orientation: [orientation or unknown]
- Loads and load directions: [forces and use conditions]
- Minimum walls and clearances: [values]
- Supports: [forbidden, acceptable regions, or unrestricted]
- Completion criteria: [fit, strength, and inspection requirements]

Call out any requirement that cannot be met without changing orientation, supports,
or part architecture.
""",
    },
    {
        "id": "builtin:cnc-part",
        "name": "CNC-machined part",
        "category": "CNC",
        "content": """
Design a CNC-machined part for: [intended outcome]

Manufacturing requirements:
- Material and stock envelope: [material and stock dimensions]
- Machine capability: [2.5-axis, 3-axis, turning, or other]
- Primary datums and interfaces: [functional reference surfaces]
- Critical dimensions and tolerances: [values]
- Tool-access limits: [setup or access constraints]
- Minimum internal radius: [value or available cutter]
- Fixturing and setup assumptions: [how the part can be held]
- Surface finish or edge treatment: [requirements]
- Completion criteria: [inspection and fit requirements]

Flag geometry that requires an additional setup, special tooling, or a different process.
""",
    },
    {
        "id": "builtin:assembly",
        "name": "Mechanical assembly",
        "category": "Assembly",
        "content": """
Create or modify an assembly for: [intended outcome]

Assembly definition:
- Components: [part names and purpose]
- Fixed and moving components: [identify each]
- Mating interfaces: [faces, axes, fits, and alignment]
- Fasteners or joining methods: [hardware, welds, adhesives, snaps]
- Required clearances and tolerances: [values]
- Motion and travel: [degrees of freedom, axes, and limits]
- Loads and load path: [forces and supporting components]
- Overall envelope: [dimensions and units]
- Completion criteria: [fit, motion, interference, and service checks]

Keep separately manufactured or relatively moving components as distinct parts.
""",
    },
    {
        "id": "builtin:electronics-enclosure",
        "name": "Electronics enclosure",
        "category": "Enclosure",
        "content": """
Design an electronics enclosure for: [board, device, and use environment]

Requirements:
- Internal components and envelopes: [dimensions and keep-out zones]
- External envelope: [dimensions and units]
- Material and manufacturing process: [material and process]
- Wall thickness: [value]
- Board and component mounting: [bosses, rails, clips, or other]
- Lid and service access: [screws, snaps, hinge, access frequency]
- Connectors, cables, ventilation, and controls: [locations and sizes]
- Sealing, impact, heat, or ingress requirements: [requirements]
- Fits and fasteners: [clearances, inserts, and hardware]
- Completion criteria: [assembly, access, and clearance checks]

Treat connector access, component keep-outs, and assembly order as functional interfaces.
""",
    },
    {
        "id": "builtin:sheet-metal",
        "name": "Sheet-metal part",
        "category": "Sheet Metal",
        "content": """
Design a sheet-metal part for: [intended outcome]

Requirements:
- Material and thickness: [material and value]
- Manufacturing process: [laser, punch, brake, roll, or other]
- Bend radius and K-factor: [values or shop standard]
- Finished envelope: [dimensions and units]
- Datums and mating interfaces: [functional references]
- Flanges, holes, slots, and hardware: [features and dimensions]
- Bend access and relief requirements: [constraints]
- Flat-pattern or stock limits: [dimensions]
- Finish and edge treatment: [requirements]
- Completion criteria: [formed dimensions and fit checks]

The final design must remain unfoldable without overlapping its flat pattern.
""",
    },
    {
        "id": "builtin:manufacturing-review",
        "name": "Manufacturability review",
        "category": "Review",
        "content": """
Review the current model for manufacturability. Do not modify it during this review.

Intended process and material:
- [manufacturing process]
- [material]

Evaluate:
- Critical interfaces, dimensions, tolerances, and datum strategy
- Tool or mold access, setup count, and fixturing
- Wall thickness, radii, draft, undercuts, and unsupported geometry
- Stress concentrations and likely failure locations
- Assembly, inspection, service, and cost risks

Known priorities or constraints:
- [requirements that outweigh cost or simplicity]

Return prioritized findings, evidence from the current geometry, and specific proposed
changes. Wait for authorization before changing the model.
""",
    },
    {
        "id": "builtin:visual-review",
        "name": "Check how this looks from all sides",
        "category": "Review",
        "content": """
Review the current model's appearance from multiple views. Do not modify it during this review.

Focus:
- [what should look right: silhouette, symmetry, holes, fillets, interferences, or overall form]

Capture isometric, front, and top; fit each view. Cite the view. Tag findings presentation_only vs needs_measurement. Do not invent dimensions, clearance, or fit from pixels, and do not claim STEP/STL/export quality from a screenshot.

If a later capture of the same view is unchanged (faceting, clipping, lighting), stop retrying that view; change tessellation, display, or section, or measure.

Return what is visibly wrong, which view shows it, and whether measurement is required. Wait for authorization before changing the model.
""",
    },
)


BUILTIN_PROMPT_STARTERS = tuple(
    _validated_starter(record, builtin=True) for record in _BUILTIN_RECORDS
)


def create_custom_prompt_starter(
    *,
    name: str,
    category: str,
    content: str,
    starter_id: str | None = None,
) -> PromptStarter:
    return _validated_starter(
        {
            "id": starter_id or f"custom:{uuid.uuid4().hex}",
            "name": name,
            "category": category,
            "content": content,
        },
        builtin=False,
    )


def _validated_custom_collection(records: Any) -> tuple[PromptStarter, ...]:
    if not isinstance(records, list):
        raise ValueError("Prompt starter library 'starters' must be a JSON array.")
    starters = tuple(_validated_starter(record, builtin=False) for record in records)
    ids: set[str] = set()
    names: set[str] = set()
    for starter in starters:
        if starter.starter_id in ids:
            raise ValueError(f"Duplicate prompt starter id: {starter.starter_id}")
        folded_name = starter.name.casefold()
        if folded_name in names:
            raise ValueError(f"Duplicate custom prompt starter name: {starter.name}")
        ids.add(starter.starter_id)
        names.add(folded_name)
    return starters


def load_custom_prompt_starters(path: Path | None = None) -> tuple[PromptStarter, ...]:
    library_path = path or prompt_starters_path()
    if not library_path.exists():
        return ()
    try:
        payload = json.loads(library_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Prompt starter library could not be read from {library_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Prompt starter library at {library_path} is not an object.")
    if set(payload) != {"schema", "version", "starters"}:
        raise RuntimeError(
            f"Prompt starter library at {library_path} has unexpected fields."
        )
    if payload.get("schema") != PROMPT_STARTERS_SCHEMA:
        raise RuntimeError(
            f"Prompt starter library at {library_path} has an unsupported schema."
        )
    if payload.get("version") != PROMPT_STARTERS_VERSION:
        raise RuntimeError(
            f"Prompt starter library at {library_path} has unsupported version "
            f"{payload.get('version')!r}."
        )
    try:
        return _validated_custom_collection(payload.get("starters"))
    except ValueError as exc:
        raise RuntimeError(
            f"Prompt starter library at {library_path} is invalid: {exc}"
        ) from exc


def _sort_key(starter: PromptStarter) -> tuple[int, str]:
    return CATEGORY_ORDER.index(starter.category), starter.name.casefold()


def all_prompt_starters(path: Path | None = None) -> tuple[PromptStarter, ...]:
    custom = load_custom_prompt_starters(path)
    return tuple(sorted((*BUILTIN_PROMPT_STARTERS, *custom), key=_sort_key))


def save_custom_prompt_starters(
    starters: Iterable[PromptStarter], path: Path | None = None
) -> Path:
    library_path = path or prompt_starters_path()
    records = []
    for starter in starters:
        if not isinstance(starter, PromptStarter):
            raise TypeError("Custom prompt starters must be PromptStarter objects.")
        records.append(starter.custom_record())
    validated = _validated_custom_collection(records)
    payload = {
        "schema": PROMPT_STARTERS_SCHEMA,
        "version": PROMPT_STARTERS_VERSION,
        "starters": [starter.custom_record() for starter in sorted(validated, key=_sort_key)],
    }
    library_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = library_path.with_name(
        f".{library_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, library_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return library_path
