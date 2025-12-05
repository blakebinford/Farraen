# Django 6.0 Upgrade Status

## Completed steps
- Created branch `upgrade/django-6.0` and backed up `requirements.txt` to `requirements.txt.bak`.
- Captured current environment freeze in `freeze-before-upgrade.txt`.
- Ran automated deprecated API scan; results stored in `upgrade-scan.txt` (10 hits to review).
- Began dependency installation under Python 3.12 virtualenv.

## Blockers / manual follow-up
- `torch==2.7.1+cpu` has no available wheel for Python 3.12 on the default index, preventing a clean install of the current requirements. Resolve by using the official PyTorch CPU index URL or pinning to an available build (e.g., `torch==2.7.1`).
- Full Django 5.x/6.0 upgrade steps remain pending until dependencies install successfully.

## Recommended next actions
1. Update `requirements.txt` to use a Python 3.12-compatible torch build or configure the PyTorch wheel index, then re-run `pip install -r requirements.txt`.
2. Proceed with staged Django upgrade (5.x then 6.0), addressing deprecated APIs noted in `upgrade-scan.txt`.
3. Run test suite and Django system checks once dependencies install.

## Reproduction commands
```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -U pip setuptools wheel
pip install -r requirements.txt  # currently fails on torch==2.7.1+cpu
```

## Rollback
- To discard this branch locally: `git checkout work` (or main branch) and delete `upgrade/django-6.0`.
- Requirements backup: `requirements.txt.bak` contains the pre-upgrade state.
