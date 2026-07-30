# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2026 Mario Passaglia <mpassaglia[at]cbc.uba.ar>         *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

__title__ = "Abstract base class for the work with solvers and meshers"
__author__ = "Mario Passaglia"
__url__ = "https://www.freecad.org"


from PySide.QtCore import QProcess
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import os
import tempfile
import uuid

import FreeCAD

from femtools import membertools


def _is_exact_live_object(obj, document):
    """Return whether *obj* is still the exact identity held by *document*."""

    if obj is None or document is None:
        return False
    try:
        return (
            obj.Document is document
            and document.getObject(int(obj.ID)) is obj
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _timeline_property(obj, name, expected_type):
    """Return one validated optional timeline property without name lookup."""

    if name not in obj.PropertiesList:
        return False, None
    actual_type = obj.getTypeIdOfProperty(name)
    if actual_type != expected_type:
        raise RuntimeError(
            f"{obj.Name}.{name} must be {expected_type}, not {actual_type}"
        )
    return True, getattr(obj, name)


def _timeline_metadata_root(obj, document):
    """Resolve an exact persisted owner chain, or reject incomplete metadata."""

    current = obj
    visited = set()
    while True:
        if not _is_exact_live_object(current, document):
            raise RuntimeError(
                "A retained FEM result owner changed exact identity"
            )
        identity = int(current.ID)
        if identity in visited:
            raise RuntimeError(
                "A retained FEM result has a cyclic History owner graph"
            )
        visited.add(identity)

        role_exists, role = _timeline_property(
            current,
            "VibeCADTimelineRole",
            "App::PropertyString",
        )
        owner_exists, owner = _timeline_property(
            current,
            "VibeCADTimelineOwner",
            "App::PropertyLinkHidden",
        )
        if role_exists and role == "resource":
            if not owner_exists or owner is None:
                raise RuntimeError(
                    "A retained FEM result resource has incomplete "
                    "History ownership"
                )
            current = owner
            continue
        if role not in (None, "", "operation"):
            raise RuntimeError(
                "A retained FEM result has an invalid History role"
            )
        if owner_exists and owner is not None:
            raise RuntimeError(
                "An independent FEM result has an unexpected History owner"
            )
        return current


def _ensure_exact_retained_result_graph(solver):
    """Adopt one exact legacy solver-result block before native reconciliation.

    ``solver.Results`` is the sole candidate inventory.  History is consulted
    only to verify those exact identities and to preserve their existing
    sibling order; no document object scan or name-based recovery is used.
    """

    document = getattr(solver, "Document", None)
    if not _is_exact_live_object(solver, document):
        raise RuntimeError(
            "The FEM solver changed exact document identity before result import"
        )

    exact_results = tuple(getattr(solver, "Results", ()) or ())
    result_ids = set()
    for result in exact_results:
        if (
            not _is_exact_live_object(result, document)
            or result is solver
            or int(result.ID) in result_ids
        ):
            raise RuntimeError(
                "solver.Results contains a missing, duplicate, or "
                "cross-document identity"
            )
        result_ids.add(int(result.ID))

    pipelines = [
        result
        for result in exact_results
        if result.isDerivedFrom("Fem::FemPostPipeline")
    ]
    if not pipelines:
        return "none"

    # Native solver implementations already define the last explicit pipeline
    # link as the reusable result.  A legacy list with more than one unowned
    # pipeline has no exact ownership data with which to distinguish result
    # generations, so it is rejected below instead of guessed.
    root = pipelines[-1]
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or timeline.TypeId != "App::DocumentTimeline":
        raise RuntimeError(
            "A retained FEM result has no native document History"
        )
    operations = tuple(timeline.Operations)
    if operations.count(root) != 1:
        raise RuntimeError(
            "The retained FEM result root is absent from document History"
        )

    root_role_exists, root_role = _timeline_property(
        root,
        "VibeCADTimelineRole",
        "App::PropertyString",
    )
    root_owner_exists, root_owner = _timeline_property(
        root,
        "VibeCADTimelineOwner",
        "App::PropertyLinkHidden",
    )
    source_exists, source = _timeline_property(
        root,
        "VibeCADResultSolver",
        "App::PropertyLinkHidden",
    )
    if root_owner_exists and root_owner is not None:
        raise RuntimeError(
            "A retained FEM result root cannot have a History owner"
        )

    root_is_canonical = (
        root_role_exists
        and root_role == "operation"
        and source_exists
        and source is solver
    )
    if root_is_canonical:
        candidate_resources = []
        candidate_resource_ids = set()
        for result in exact_results:
            semantic_root = _timeline_metadata_root(result, document)
            result_source_exists, result_source = _timeline_property(
                semantic_root,
                "VibeCADResultSolver",
                "App::PropertyLinkHidden",
            )
            semantic_role_exists, semantic_role = _timeline_property(
                semantic_root,
                "VibeCADTimelineRole",
                "App::PropertyString",
            )
            if (
                not semantic_role_exists
                or semantic_role != "operation"
                or not result_source_exists
                or result_source is not solver
                or semantic_root not in exact_results
            ):
                raise RuntimeError(
                    "solver.Results mixes canonical and legacy "
                    "History metadata"
                )
            if semantic_root is root and result is not root:
                candidate_resources.append(result)
                candidate_resource_ids.add(int(result.ID))

        owned_resources = []
        for operation in operations:
            role_exists, role = _timeline_property(
                operation,
                "VibeCADTimelineRole",
                "App::PropertyString",
            )
            if (
                role_exists
                and role == "resource"
                and _timeline_metadata_root(operation, document) is root
            ):
                owned_resources.append(operation)
        if (
            len(candidate_resources) != len(candidate_resource_ids)
            or {int(resource.ID) for resource in owned_resources}
            != candidate_resource_ids
        ):
            raise RuntimeError(
                "solver.Results does not exactly contain the retained "
                "FEM result resource graph"
            )
        root_index = operations.index(root)
        if root_index < len(owned_resources) or tuple(
            operations[root_index - len(owned_resources) : root_index]
        ) != tuple(owned_resources):
            raise RuntimeError(
                "The retained FEM result is not one canonical "
                "resource-first History block"
            )
        return "canonical"

    if source_exists or root_role not in (None, "", "operation"):
        raise RuntimeError(
            "The retained FEM result root has partial or malformed "
            "History metadata"
        )
    if len(pipelines) != 1:
        raise RuntimeError(
            "Multiple legacy FEM result pipelines cannot be assigned "
            "to exact result generations"
        )

    for result in exact_results:
        role_exists, role = _timeline_property(
            result,
            "VibeCADTimelineRole",
            "App::PropertyString",
        )
        owner_exists, owner = _timeline_property(
            result,
            "VibeCADTimelineOwner",
            "App::PropertyLinkHidden",
        )
        result_source_exists, _result_source = _timeline_property(
            result,
            "VibeCADResultSolver",
            "App::PropertyLinkHidden",
        )
        if (
            (role_exists and role not in ("", "operation"))
            or (owner_exists and owner is not None)
            or result_source_exists
            or operations.count(result) != 1
        ):
            raise RuntimeError(
                "solver.Results mixes legacy, owned, or partial "
                "History metadata"
            )

    indices = sorted(operations.index(result) for result in exact_results)
    if (
        not indices
        or indices[-1] - indices[0] + 1 != len(indices)
        or {
            int(operation.ID)
            for operation in operations[indices[0] : indices[-1] + 1]
        }
        != result_ids
    ):
        raise RuntimeError(
            "Legacy FEM result identities do not occupy one exact "
            "contiguous History segment"
        )

    ordered_resources = tuple(
        operation
        for operation in operations[indices[0] : indices[-1] + 1]
        if operation is not root
    )
    if not ordered_resources:
        # Existing-block adoption intentionally requires a real resource
        # graph.  A singleton is already one History operation; it needs only
        # the exact operation role before the normal staging path can use it.
        from femcommands.manager import _mark_timeline_operation

        _mark_timeline_operation(root)
        return "classified"

    document.adoptExistingTimelineOperationBlock(
        root,
        ordered_resources,
        tuple(root for _resource in ordered_resources),
    )
    return "adopted"


class ObjectTools(ABC):
    """Abstract base class for the work with solvers and meshers"""

    def __init__(self, obj):
        if membertools._is_suppressed(obj):
            raise ValueError(f"Suppressed FEM object '{obj.Label}' cannot be executed")

        obj.Tool = self
        self.obj = obj
        self.model_file = ""
        self.process = QProcess()
        self.operation_id = str(uuid.uuid4())
        self.operation_state = "created"
        self.operation_error = None
        self.program = None
        self.arguments = []
        self.started_at = None
        self.finished_at = None
        self.cancel_requested = False
        self.stdout = ""
        self.stderr = ""
        self.property_update = {"status": "not_started"}
        self.analysis = obj.getParentGroup()
        self.fem_param = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem")
        self._create_working_directory()

        self.process.finished.connect(self._process_finished)
        self.process.started.connect(self._process_started)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._process_error)

    def _create_working_directory(self):
        """
        Create working directory according to preferences
        """
        if not os.path.isdir(self.obj.WorkingDirectory):
            gen_param = self.fem_param.GetGroup("General")
            if gen_param.GetBool("UseTempDirectory", True):
                self.obj.WorkingDirectory = tempfile.mkdtemp(prefix="fem_")
            elif gen_param.GetBool("UseBesideDirectory", False):
                root, ext = os.path.splitext(self.obj.Document.FileName)
                if root:
                    self.obj.WorkingDirectory = os.path.join(root, self.obj.Label)
                    os.makedirs(self.obj.WorkingDirectory, exist_ok=True)
                else:
                    # file not saved, use temporary
                    self.obj.WorkingDirectory = tempfile.mkdtemp(prefix="fem_")
            elif gen_param.GetBool("UseCustomDirectory", False):
                sub_dir = self.obj.Document.Name + "-" + self.obj.Label
                base_dir = gen_param.GetString("CustomDirectoryPath")
                # no custom directory, use home directory
                if not base_dir:
                    base_dir = FreeCAD.ConfigGet("UserHomePath")
                self.obj.WorkingDirectory = os.path.join(base_dir, sub_dir)
                os.makedirs(self.obj.WorkingDirectory, exist_ok=True)

    @abstractmethod
    def prepare(self):
        pass

    @abstractmethod
    def compute(self):
        pass

    @abstractmethod
    def update_properties(self):
        pass

    def run(self, blocking=False):
        self.operation_state = "preparing"
        try:
            self.prepare()
            self.operation_state = "starting"
            self.compute()
        except Exception as exc:
            self.operation_state = "failed"
            self.operation_error = str(exc)
            self.finished_at = self._utc_now()
            raise
        if blocking:
            return self.process.waitForFinished(-1)
        return self.operation_id

    def _process_finished(self, code, status):
        self._read_stdout()
        self._read_stderr()
        self.finished_at = self._utc_now()
        if self.cancel_requested:
            self.operation_state = "cancelled"
            self.property_update = {"status": "not_run", "reason": "cancelled"}
            return
        if status == QProcess.ExitStatus.NormalExit and code == 0:
            self.operation_state = "importing_results"
            document = self.obj.Document
            transaction_id = 0
            published_result_graph = None
            try:
                if any(
                    candidate.getBookedTransactionID() != 0
                    or candidate.HasPendingTransaction
                    for candidate in FreeCAD.listDocuments().values()
                ):
                    raise RuntimeError(
                        "FEM results cannot be imported while another "
                        "document transaction is active"
                    )
                document.openTransaction("Import FEM solver results")
                transaction_id = int(
                    document.getBookedTransactionID()
                )
                if transaction_id == 0:
                    raise RuntimeError(
                        "Could not open the FEM result import transaction"
                    )

                if not self.fem_param.GetGroup("General").GetBool(
                    "KeepResultsOnReRun",
                    False,
                ):
                    _ensure_exact_retained_result_graph(self.obj)
                result_graph = self.update_properties()
                if result_graph is not None:
                    if len(result_graph) == 2:
                        root, resources = result_graph
                        root_is_new = True
                        reconciliation = None
                    elif len(result_graph) == 3:
                        root, resources, root_is_new = result_graph
                        reconciliation = None
                    elif len(result_graph) == 4:
                        (
                            root,
                            resources,
                            root_is_new,
                            reconciliation,
                        ) = result_graph
                    else:
                        raise RuntimeError(
                            "A FEM result importer must return "
                            "(root, resources[, root_is_new"
                            "[, reconciliation]])"
                        )
                    resources = tuple(resources)
                    from femcommands.manager import (
                        _finalize_timeline_result_graph,
                        _timeline_root,
                    )

                    _finalize_timeline_result_graph(
                        self.obj,
                        root,
                        resources,
                        root_is_new=bool(root_is_new),
                        reconciliation=reconciliation,
                    )
                    retained_resources = []
                    if reconciliation is not None:
                        for name, object_id in reconciliation.resource_identities:
                            resource = document.getObject(int(object_id))
                            if (
                                resource is None
                                or resource.Document is not document
                                or int(resource.ID) != object_id
                                or str(resource.Name) != name
                                or _timeline_root(resource, document) is not root
                            ):
                                raise RuntimeError(
                                    "A retained FEM result resource "
                                    "changed exact identity or ownership"
                                )
                            retained_resources.append(resource)
                    resource_records = []
                    resource_identities = set()
                    resource_lifecycles = [
                        (resource, "updated")
                        for resource in retained_resources
                    ]
                    resource_lifecycles.extend(
                        (resource, "created")
                        for resource in resources
                    )
                    for resource, lifecycle in resource_lifecycles:
                        identity = (
                            str(resource.Name),
                            int(resource.ID),
                        )
                        if (
                            resource.Document is not document
                            or document.getObject(identity[1]) is not resource
                            or _timeline_root(resource, document) is not root
                            or identity in resource_identities
                        ):
                            raise RuntimeError(
                                "A FEM result resource changed exact "
                                "identity or ownership"
                            )
                        resource_identities.add(identity)
                        resource_records.append(
                            {
                                "name": identity[0],
                                "id": identity[1],
                                "lifecycle": lifecycle,
                            }
                        )
                    published_result_graph = {
                        "root": {
                            "name": str(root.Name),
                            "id": int(root.ID),
                            "lifecycle": (
                                "created" if bool(root_is_new) else "updated"
                            ),
                        },
                        "resources": resource_records,
                    }
                FreeCAD.closeActiveTransaction(
                    False,
                    transaction_id,
                )
                transaction_id = 0
                self.property_update = {
                    "status": "completed",
                    "result_graph": published_result_graph,
                }
                self.operation_state = "completed"
            except Exception as exc:
                if (
                    transaction_id
                    and document.getBookedTransactionID()
                    == transaction_id
                ):
                    FreeCAD.closeActiveTransaction(
                        True,
                        transaction_id,
                    )
                self.property_update = {
                    "status": "failed",
                    "native_error": str(exc),
                }
                self.operation_error = str(exc)
                self.operation_state = "failed"
        else:
            self.operation_state = "failed"
            if not self.operation_error:
                self.operation_error = (
                    f"External process exited with code {code} and status "
                    f"{self._exit_status_name(status)}."
                )

    def _process_started(self):
        self.operation_state = "running"
        self.started_at = self._utc_now()

    def _read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self.stdout += data

    def _read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if data:
            self.stderr += data

    def _process_error(self, error):
        self._read_stdout()
        self._read_stderr()
        self.operation_error = self.process.errorString()
        if self.operation_state not in {"cancel_requested", "cancelled"}:
            self.operation_state = "failed"

    def cancel(self):
        """Request cancellation without blocking the GUI thread."""
        self.cancel_requested = True
        state = self.process.state()
        if state == QProcess.ProcessState.NotRunning:
            if self.operation_state not in {"completed", "failed"}:
                self.operation_state = "cancelled"
                self.finished_at = self._utc_now()
            return self.process_diagnostics()
        self.operation_state = "cancel_requested"
        self.process.terminate()
        return self.process_diagnostics()

    def kill(self):
        """Force a process to stop after a prior graceful cancellation request."""
        self.cancel_requested = True
        self.operation_state = "cancel_requested"
        self.process.kill()
        return self.process_diagnostics()

    def process_diagnostics(self):
        """Return exact external-process state without consuming output."""
        self._read_stdout()
        self._read_stderr()
        process_state = self.process.state()
        exit_code = None
        exit_status = None
        if process_state == QProcess.ProcessState.NotRunning and self.started_at:
            exit_code = int(self.process.exitCode())
            exit_status = self._exit_status_name(self.process.exitStatus())
        return {
            "operation_id": self.operation_id,
            "operation_state": self.operation_state,
            "process": {
                "state": self._process_state_name(process_state),
                "pid": int(self.process.processId()) if self.process.processId() else None,
                "program": self.program,
                "arguments": list(self.arguments),
                "working_directory": self.process.workingDirectory(),
                "exit_code": exit_code,
                "exit_status": exit_status,
                "error": self.operation_error,
                "stdout": self.stdout,
                "stderr": self.stderr,
            },
            "progress": self._progress(),
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "property_update": dict(self.property_update),
        }

    def _progress(self):
        fractions = {
            "created": 0.0,
            "preparing": 0.05,
            "starting": 0.15,
            "running": 0.5,
            "importing_results": 0.9,
            "completed": 1.0,
            "failed": 1.0,
            "cancel_requested": 0.5,
            "cancelled": 1.0,
        }
        return {
            "stage": self.operation_state,
            "fraction": fractions.get(self.operation_state),
            "indeterminate_within_stage": self.operation_state == "running",
        }

    @staticmethod
    def _process_state_name(state):
        return {
            QProcess.ProcessState.NotRunning: "not_running",
            QProcess.ProcessState.Starting: "starting",
            QProcess.ProcessState.Running: "running",
        }.get(state, str(state))

    @staticmethod
    def _exit_status_name(status):
        return {
            QProcess.ExitStatus.NormalExit: "normal_exit",
            QProcess.ExitStatus.CrashExit: "crash_exit",
        }.get(status, str(status))

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat()
