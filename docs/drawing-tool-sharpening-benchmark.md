# Drawing Tool-Sharpening Benchmark

This corpus evaluates whether the Drawing surface is understandable and reliable from ordinary requests. It is not a prompt-engineering suite. The same clean inputs and exact prompts are run first with local Qwen and then with GPT-5.6 Terra at high reasoning effort.

## Run rules

- Start from a saved model containing no Drawing page, TechDraw view, dimension, balloon, BOM, or prior assistant Drawing artifact.
- Select the Drawing ribbon before the turn. Do not name tools, prescribe an operation order, add examples, or repair the model between calls.
- Use the production provider, production schemas, production document transactions, and compiled GUI. Hidden retries, benchmark branches, and post-run document edits are prohibited.
- Give each model the same prompt and sufficient time. Save checkpoints before any graceful shutdown.
- Record every tool choice, submitted arguments, validation error, native failure, revision transition, and final response.
- A model mistake is not a tool defect unless the correct choice was ambiguous, a schema-valid call was rejected, a contract omitted required information, a successful return was false, or the production operation produced invalid state.
- Apply only general fixes. Re-run the failed case from its original clean input, then re-run an unrelated case to reject benchmark-specific behavior.

## Fixed corpus

CADGenBench inputs use the public `input.step` artifact for the named editing problem in the [CADGenBench dataset](https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data). The benchmark itself is documented by the [CADGenBench space](https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench).

| ID | Clean input | Exact prompt | Primary coverage |
|---|---|---|---|
| DTS-01 | Machined mounting plate fixture | Create a complete production drawing for this part and export it as a PDF. | Baseline views, holes, dimensions, title block, export |
| DTS-02 | CADGenBench 201 | Create a complete production drawing for this machined bracket and export it as a PDF. | Dense imported geometry, scale, layout, radial dimensions |
| DTS-03 | CADGenBench 230 | Create a complete manufacturing drawing for this crankshaft and export it as a PDF. | Shaft views, diameters, lengths, details, centerlines |
| DTS-04 | CADGenBench 243 | Create a complete production drawing for this pump housing and export it as a PDF. | Casting, sections, hole patterns, multi-sheet judgment |
| DTS-05 | CADGenBench 204 | Create a complete fabrication drawing for this formed bracket and export it as a PDF. | Sheet form, thickness, bends, fabrication annotation |
| DTS-06 | Welded-support fixture | Create a complete fabrication drawing for this welded support and export it as a PDF. | Multiple members, weld symbols, fabrication dimensions |
| DTS-07 | CADGenBench 203 | Create a complete manufacturing drawing for this impeller and export it as a PDF. | Repeated curved geometry, sections, detail views |
| DTS-08 | CADGenBench 202 | Create a complete production drawing for this ribbed housing and export it as a PDF. | Dense ribs, sections, hidden lines, layout |
| DTS-09 | CADGenBench 245 | Create a complete fabrication drawing for this stamped chassis and export it as a PDF. | Large formed part, view scale, sheets, readable dimensions |
| DTS-10 | Planetary mechanism fixture | Create a complete assembly drawing and BOM for this mechanism and export it as a PDF. | Assembly views, item identity, balloons, BOM, multiple pages |

## Deterministic acceptance

Each run must satisfy all applicable checks without changing source geometry or ownership:

- every drawing source resolves to the intended current model object;
- page, template, projection convention, units, scale, and title data persist after save and reopen;
- required views contain projected geometry and remain linked to their sources;
- dimensions and annotations have valid persistent references and truthful displayed values;
- BOM rows and balloons share stable assembly item identities;
- rendered items remain inside the printable drawing area with no clipping, duplicate scene objects, or detected collisions;
- page readiness reports no blocking issue before export;
- exported PDF pages are readable, unclipped, nonempty, and consistent with the saved document;
- read-only inspection and export do not advance structural revision or add undo entries;
- expensive projection, readiness, redraw, and export work remains responsive and cancellable.

The automated oracle reports objective state only. Manufacturing completeness and presentation quality receive a separate human visual review; unspecified tolerances, fits, finishes, processes, and weld requirements must remain explicitly unspecified rather than invented.

## Reverse-drawing track

Reconstructing a model from a PDF or raster technical drawing is a separate validation problem, not STEP projection. It must preserve the source drawing, extract views and declared dimensions with provenance and confidence, detect contradictions, reconstruct only constrained geometry, and verify a regenerated drawing against the source. A 3D result must not be presented as authoritative while any scale, unit, hidden feature, tolerance, or view correspondence remains ambiguous. This track requires a dedicated accuracy corpus and is not passed by the forward Drawing corpus above.
