# Progress: webhooks-hmac-strict-fallback (v1)

<!-- Steps are numbered. Substeps use parent.child notation. -->
<!-- Status markers: [ ] pending, [~] in_progress, [w] waiting, [x] done -->

## 1. [x] Add allow_hmac_fallback setting to src/settings.py

## 2. [x] Update _resolve_runner_secret signature and strict logic

## 3. [x] Update receive_runner_event call site to short-circuit on no_entry

## 4. [x] Create tests/unit/test_webhooks_hmac_fallback.py

## 5. [x] Run new test and confirm green

