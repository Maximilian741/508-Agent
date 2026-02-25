# Policies

## Policy Packs

Policy packs define:

- Which targets are in scope (pdf/docx/pptx).
- Rule enablement and override behavior.
- Scoring weights and thresholds.
- Export preferences.

Current API endpoints:

- `GET /api/policies`
- `GET /api/policies/{policy_id}`

## Job Policy Snapshot

A job stores an immutable policy snapshot (`job_policy_snapshot`) when scan starts.

Why this matters:

- Reproducibility: score and outcomes can be explained later using exact policy at run time.
- Auditability: policy evolution does not rewrite historical results.

## Scoring Model (Current)

Inputs:

- issues for pass type (`baseline`, `post_fix`, `post_manual`)
- job policy snapshot
- coverage info

Computation (deterministic):

1. Start from `baseScore` (default 100).
2. For each enabled issue, deduct `severityWeight * confidence` (confidence optional, default 1.0).
3. Apply coverage penalty if enabled.
4. Clamp final score to 0..100.
5. Compute status via policy threshold rules (`pass`, `needs_review`, `fail`).

Current score endpoint:

- `GET /jobs/{job_id}/score`

## Policy Schema (schemaVersion = 1)

Example:

```json
{
  "schemaVersion": 1,
  "name": "Section 508 (WCAG 2.0 AA)",
  "targets": ["pdf", "docx", "pptx"],
  "thresholds": {
    "statusRules": {
      "pass": { "maxCritical": 0, "maxSerious": 2 },
      "needs_review": { "maxCritical": 0, "maxSerious": 10 }
    }
  },
  "scoring": {
    "baseScore": 100,
    "severityWeights": { "critical": 18, "serious": 8, "moderate": 3, "minor": 1 },
    "confidenceMultiplier": true,
    "coveragePenalty": { "enabled": true, "perSkippedRule": 0.2, "maxPenalty": 10 }
  },
  "rules": {
    "defaults": { "enabled": true },
    "overrides": {
      "PDF.MISSING_ALT_TEXT": { "enabled": true, "severity": "serious" },
      "PDF.TAG_TREE_MISSING": { "enabled": true, "severity": "critical" },
      "DOCX.METADATA_LANGUAGE_MISSING": { "enabled": true, "severity": "moderate" }
    }
  },
  "export": {
    "includeOriginal": false,
    "includeFixedIfAvailable": true,
    "templates": { "summaryPdf": "default_v1" }
  }
}
```

## Seeded Policy Packs

Current default seeded packs:

- `Section 508 (WCAG 2.0 AA)`
- `WCAG 2.2 AA (docs)`
- `PDF/UA-focused (Tagged PDF)`

These are stored in `policy_packs` and can be extended through additive API/workflow changes.
