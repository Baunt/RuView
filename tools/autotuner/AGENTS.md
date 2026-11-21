# AutoTuner development

- The development repository is `Baunt/RuView`. Work on AutoTuner in `tools/autotuner`; publish authorized changes to this fork, never directly to `ruvnet/RuView`.
- `origin` points to `https://github.com/Baunt/RuView.git`; `upstream` points to `https://github.com/ruvnet/RuView.git`.
- Merge `upstream/main` into the fork's `main` periodically. Preserve fork commits and unrelated local work; do not force-push or replace the fork with an upstream snapshot.
- Run `.venv/bin/python -m pytest -q` and `npm --prefix web run build` from this directory after an upstream integration or AutoTuner change. Run additional checks required by the changed upstream subsystem. Publish an integration only after the relevant checks pass; leave unresolved conflicts for review.
- Keep measurement data, Garmin FIT files, CSI/PCAP captures, database files, credentials, screenshots, room bindings, private deployment scripts and local field reports out of Git. Commit only source and synthetic test fixtures.
- Keep the station loopback-only and preserve the RuView publication/calibration gates. Repository synchronization does not authorize deployments, hardware actions, calibration or processing personal measurements.
