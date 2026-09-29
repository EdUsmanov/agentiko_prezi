# Closed presentation challenge

The sealed challenge is stored outside the repository under the user's private
local data directory. It is not included in Git, source snapshots, package data,
or open evaluation fixtures. Its manifest identity is
`0e2287c5e58bec7c5ebae7f1ee2ab09bc52916cdc9437cf8790a99d9e0e3c52d`.

The sealed revision contains 12 eligible holdouts and one replacement reserve,
across six template families and 12 independently authored source materials.
All 13 references are silver and unreviewed; there are no gold labels and no
product trials have run. Three active holdouts contain OLE structures and
three contain SmartArt structures; the reserve includes both. Four source
materials mix languages. The corpus uses four font families.

Structural OOXML checks passed. A headless LibreOffice render smoke produced a
three-page PDF for each of the 13 source templates. This checks package
structure and that the renderer can open and export the templates. It does not
establish Microsoft Office compatibility, semantic fidelity of the rendered
objects, or product-generation quality. Those remain unverified. A structural
feature count means the relevant OOXML part, shape, and embedded relationship
were present; it is not an application compatibility claim.

## Aggregate validator

The public API is `audit_e2e.challenge.challenge_status` (also exported as
`aggregate_status`). It reads a sealed root and returns only aggregate counts,
readiness, the manifest identity, generic issue codes, and validation states.
It never returns case tokens, private asset paths, source text, or reference
answers. Pass the identity recorded at intake to detect a replaced manifest:

```python
from audit_e2e.challenge import aggregate_status

status = aggregate_status(
    private_root,
    expected_manifest_identity="<recorded aggregate SHA-256>",
)
```

The optional CLI command `python -m audit_e2e challenge-status --root PRIVATE_ROOT
--identity RECORDED_SHA256` provides the same sanitized report. `ready` means
the sealed corpus and lifecycle checks are ready for a later authorized
evaluation. It does not mean a product passed an evaluation. `not_ready` covers
an insufficient active holdout or diversity count. Hash, schema, anchoring,
permission, lifecycle, or expected-identity mismatches return `invalid`; none can
be treated as a pass.

The manifest hashes every input and label file. A revision is immutable after
sealing; a changed revision requires a new identity and a new private version.
The append-only lifecycle journal is hash chained. Recording a disclosure
removes that holdout from the eligible count. Retiring a case requires a sealed
regression record and activates a previously sealed replacement with a distinct
identity. A disclosed case remains ineligible even if later retired. The API
rejects invalid transitions before appending them.

All references remain explicitly `silver` and `unreviewed`; no independent
human reviewer supplied gold labels. No product trial has run on this
challenge. Any future results must retain that label limitation and identify
the exact sealed manifest revision. A disclosed holdout can be retained as a
regression record but cannot be reported as a closed-set evaluation.

The directory is restricted to the current OS user (`0700`), and sealed assets
are read-only. This is procedural separation only: the same OS user and root
access can still read the private files. Strict separation would require a
separate curator or OS identity.
