# Progress: subscribe-after-publish-race (v1)

<!-- Steps are numbered. Substeps use parent.child notation. -->
<!-- Status markers: [ ] pending, [~] in_progress, [w] waiting, [x] done -->

## 1. [x] Add register/consume methods to EventBus

## 2. [x] Reorder stream_post to register before inbox.put

## 3. [x] Add regression test for register-then-publish-then-consume

## 4. [x] Run pytest on event_bus and stream tests

## 5. [x] Rebuild and restart backend container, verify healthz

