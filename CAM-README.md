# Manufacturing in VibeCAD

The **Manufacture** ribbon turns finished model geometry into one or more CNC
setups. Its controls and VibeCAD assistance use the same document operations.
Switching to the ribbon changes the assistant's available tools on the next
message; it does not require a new conversation or a different authoring mode.

## Setups are independent

A CAM Job represents one machining setup, not all manufacturing work in the
document. A document may contain any number of Jobs for different orientations,
stocks, fixtures, machines, postprocessors, or manufacturing stages. Create a
separate Job whenever those facts are independent. Create a follow-up setup from
a retained simulation result when the next setup intentionally starts with the
material left by an earlier one.

Selecting an operation, tool controller, stock, fixture, or retained result
identifies its owning Job. When several Jobs exist and selection does not identify
exactly one of them, VibeCAD asks for the setup instead of guessing.

## Preparing a setup

1. Open a document containing the bodies to manufacture and select the
   **Manufacture** ribbon.
2. Select the exact bodies and create a Job. The original model is referenced;
   CAM does not replace the design geometry.
3. Inspect the setup's model, stock, orientation, work origin, and fixture
   assignments. Make each physical fact explicit before generating toolpaths.
4. Choose the machine and postprocessor. VibeCAD includes generic LinuxCNC and
   GRBL machine definitions; machine definitions created by the user or supplied
   by add-ons appear in the same selector.
5. Add Tool Controllers with the actual Tool Bits, tool numbers, spindle speed,
   coolant, feeds, and rapids used by the setup.
6. Create machining operations from exact faces, edges, holes, or bodies. Review
   each operation's depths, allowances, entry, clearance, and Tool Controller.
7. Generate and inspect the toolpaths. Native generation runs in an isolated
   background process with setup-scoped progress and cancellation; unrelated
   setups remain available. Use dress-ups only when the underlying operation is
   already correct.
8. Simulate the setup, review verification findings, run the sanity check, and
   post only the intended operations with the intended machine.

The assistant can perform the same sequence from an ordinary request such as
“set this bracket up for my three-axis mill and machine the top pocket and
holes.” It inspects current document state and asks only for missing manufacturing
facts that cannot be derived safely.

## Simulation and verification

The interactive simulator previews tool motion. **Retain Simulation Result**
runs material removal in the background and creates durable remaining-stock and
verification objects. It is cancellable, does not block the interface, and can be
used as the stock source for a related follow-up Job.

Retained verification currently provides:

- exact cutting and rapid Tool Bit sweeps against the protected model;
- bounded collision records with operation, command, overlap volume, and bounds;
- operation and total cycle-time estimates from the setup feeds and rapids; and
- toolpath travel-span comparison against a configured machine's supported axes.

Travel span answers whether the program can fit within an axis's available
travel. It does not prove that the current work offset places every position
inside the machine's absolute limits. That second check requires measured
machine-coordinate work-offset data, so VibeCAD reports it as unavailable rather
than treating it as passed. Holder, fixture, and current-stock rapid-clearance
checks are likewise reported as unavailable until their exact geometry is part of
the verification input.

Simulation and posting never make a toolpath safe by themselves. Confirm the
real stock, workholding, Tool Bits, offsets, machine limits, postprocessor, and
posted program before running a machine.

## Current scope

The sharpened production path targets common three-axis milling workflows:
profile, pocket, facing, drilling, helix, adaptive, slot, engraving, deburring,
V-carving, three-dimensional pocketing, probing, operation copies, and supported
dress-ups. Rotary, 3+2, simultaneous multi-axis, turning, laser/plasma,
waterjet, robot, holder/fixture collision, and full machine-kinematic simulation
are not represented as complete when their required runtime is absent.

The implementation charter, benchmark method, and acceptance evidence are in
[docs/cam-tool-sharpening.md](docs/cam-tool-sharpening.md).
