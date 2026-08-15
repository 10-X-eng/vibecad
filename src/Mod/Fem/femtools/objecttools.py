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


def _timeline_owner_chain_contains(obj, ancestor, document):
    """Return whether one validated resource chain contains *ancestor*."""

    current = obj
    visited = set()
    while _is_exact_live_object(current, document):
        if current is ancestor:
            return True
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
        if not role_exists or role != "resource":
            return False
        if not owner_exists or owner is None:
            raise RuntimeError(
                "A retained FEM result resource has incomplete "
                "History ownership"
            )
        current = owner
    return False


def _ensure_exact_retained_result_graph(solver):
    """Require or atomically adopt the exact solver-owned result graph.

    Generated results are resources of their solver operation.  That ordering
    is essential: ``solver.Results`` is a real dependency, so every linked
    result must precede the solver in History.  A complete contiguous legacy
    block can be adopted without replacing object identities; ambiguous or
    partial graphs are rejected rather than guessed.
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

    if not exact_results:
        return "none"

    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or timeline.TypeId != "App::DocumentTimeline":
        raise RuntimeError(
            "A retained FEM result has no native document History"
        )
    operations = tuple(timeline.Operations)
    if operations.count(solver) != 1 or any(
        operations.count(result) != 1
        for result in exact_results
    ):
        raise RuntimeError(
            "A retained FEM solver or result is absent from document History"
        )

    solver_role_exists, solver_role = _timeline_property(
        solver,
        "VibeCADTimelineRole",
        "App::PropertyString",
    )
    solver_owner_exists, solver_owner = _timeline_property(
        solver,
        "VibeCADTimelineOwner",
        "App::PropertyLinkHidden",
    )
    if solver_owner_exists and solver_owner is not None:
        raise RuntimeError(
            "A FEM solver operation cannot have a History owner"
        )

    solver_is_canonical = solver_role_exists and solver_role == "operation"
    if solver_is_canonical:
        canonical_results = True
        for result in exact_results:
            role_exists, role = _timeline_property(
                result,
                "VibeCADTimelineRole",
                "App::PropertyString",
            )
            if (
                not role_exists
                or role != "resource"
                or _timeline_metadata_root(result, document) is not solver
            ):
                canonical_results = False
                break
        if canonical_results:
            owned_resources = [
                operation
                for operation in operations
                if operation is not solver
                and _timeline_metadata_root(operation, document) is solver
            ]
            direct_result_roots = tuple(
                result
                for result in exact_results
                if getattr(result, "VibeCADTimelineOwner", None) is solver
            )
            if not direct_result_roots or any(
                not any(
                    _timeline_owner_chain_contains(
                        result,
                        root,
                        document,
                    )
                    for root in direct_result_roots
                )
                for result in exact_results
            ):
                raise RuntimeError(
                    "solver.Results does not identify complete direct result roots"
                )
            from femcommands.manager import (
                _canonical_timeline_resource_order,
            )

            if tuple(owned_resources) != tuple(
                _canonical_timeline_resource_order(
                    solver,
                    owned_resources,
                )
            ):
                raise RuntimeError(
                    "The FEM solver resource graph is not in canonical "
                    "nested resource-first History order"
                )
            solver_index = operations.index(solver)
            if (
                solver_index < len(owned_resources)
                or tuple(
                    operations[
                        solver_index - len(owned_resources) : solver_index
                    ]
                )
                != tuple(owned_resources)
            ):
                raise RuntimeError(
                    "The FEM solver does not occupy one canonical History block"
                )
            return "canonical"

    pipelines = [
        result
        for result in exact_results
        if result.isDerivedFrom("Fem::FemPostPipeline")
    ]
    if len(pipelines) > 1:
        raise RuntimeError(
            "Multiple legacy FEM result pipelines cannot be assigned "
            "to exact result generations"
        )

    root = pipelines[0] if pipelines else None
    legacy_roots = set()
    for result in exact_results:
        semantic_root = _timeline_metadata_root(result, document)
        allowed_roots = (
            {result}
            if root is None
            else {result, root}
        )
        if semantic_root not in allowed_roots:
            raise RuntimeError(
                "solver.Results mixes solver-owned, legacy, or partial "
                "History graphs"
            )
        legacy_roots.add(semantic_root)
    if root is not None and root not in legacy_roots:
        raise RuntimeError(
            "Legacy FEM result identities do not form one exact result graph"
        )

    for operation in operations:
        if (
            operation not in exact_results
            and operation is not solver
            and (
                _timeline_metadata_root(operation, document) is root
                if root is not None
                else _timeline_metadata_root(operation, document)
                in legacy_roots
            )
        ):
            raise RuntimeError(
                "solver.Results omits a resource from its retained legacy graph"
            )

    adopted_members = (solver, *exact_results)
    adopted_ids = {int(member.ID) for member in adopted_members}
    indices = sorted(operations.index(member) for member in adopted_members)
    if (
        not indices
        or indices[-1] - indices[0] + 1 != len(indices)
        or {
            int(operation.ID)
            for operation in operations[indices[0] : indices[-1] + 1]
        }
        != adopted_ids
    ):
        raise RuntimeError(
            "The legacy FEM solver and results do not occupy one exact "
            "contiguous History segment and cannot be safely adopted"
        )

    if root is None:
        ordered_resources = tuple(
            operation
            for operation in operations[indices[0] : indices[-1] + 1]
            if operation is not solver
        )
        owners = tuple(solver for _resource in ordered_resources)
    else:
        ordered_children = tuple(
            operation
            for operation in operations[indices[0] : indices[-1] + 1]
            if operation is not solver and operation is not root
        )
        ordered_resources = (*ordered_children, root)
        owners = (
            *(root for _resource in ordered_children),
            solver,
        )

    # The previous result contract classified the result root as an operation
    # and its outputs as resources of that root.  Normalize only this complete
    # exact block immediately before the atomic adoption call.
    for result in exact_results:
        if "VibeCADResultSolver" in result.PropertiesList:
            result.removeProperty("VibeCADResultSolver")
        role_exists, _role = _timeline_property(
            result,
            "VibeCADTimelineRole",
            "App::PropertyString",
        )
        if role_exists:
            result.VibeCADTimelineRole = "operation"
        owner_exists, _owner = _timeline_property(
            result,
            "VibeCADTimelineOwner",
            "App::PropertyLinkHidden",
        )
        if owner_exists:
            result.VibeCADTimelineOwner = None

    document.adoptExistingTimelineOperationBlock(
        solver,
        ordered_resources,
        owners,
    )
    return "adopted"


class ObjectTools(ABC):
    """Abstract base class for the work with solvers and meshers.

    ``detached`` is intended for callers that only need to prepare or inspect
    solver input.  A detached tool does not publish itself through the FEM
    object's ``Tool`` property and does not initialize its persisted working
    directory.  The default retains the established interactive FEM behavior.
    """

    def __init__(self, obj, *, detached=False, working_directory=None):
        if membertools._is_suppressed(obj):
            raise ValueError(f"Suppressed FEM object '{obj.Label}' cannot be executed")

        if not detached:
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
        if not detached:
            self._create_working_directory()
        self.working_directory = str(
            working_directory
            if working_directory is not None
            else getattr(obj, "WorkingDirectory", "")
        )

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
