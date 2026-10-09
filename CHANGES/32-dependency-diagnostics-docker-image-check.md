# Dependency diagnostics: restore Docker image check helper

## Fixed

- Restored the missing `docker_image_exists()` helper used by `collect_dependency_diagnostics()`.
- Dependency diagnostics now safely verify the configured quarantine image with the Docker CLI after confirming the daemon is available.
- Added regression coverage through the dependency-installer test suite.

## Verification

- Full test suite: 181 passed, 43 subtests passed.
