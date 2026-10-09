# Change 18 — Disposable Docker Quarantine

## What changed

Downloaded document bytes are now handled through a Docker-backed quarantine by default.

The production download path no longer copies direct URL downloads into a persistent host-side `data/downloads/quarantine/` file before inspection. The downloader keeps the bounded response in memory and transfers it over the container's stdin to a fresh quarantine container.

For Firefox-managed/local transient downloads, the host-side file is read, hashed, transferred, and removed immediately after the container acknowledges the same SHA-256. The source is therefore not kept on disk while parsing continues.

Inside the disposable container:

- the network is disabled;
- the root filesystem is read-only;
- all Linux capabilities are dropped;
- `no-new-privileges` is enabled;
- a non-root UID is enforced;
- CPU, memory, PID, and temporary-filesystem limits are applied;
- no project directory or host filesystem is mounted;
- the worker parses the untrusted bytes and returns extracted text/metadata only.

The host calculates SHA-256 before transfer. The worker calculates SHA-256 after receipt and emits a receipt acknowledgement before parsing. The host compares the hashes and only then removes a transient host-side source. The final worker result hash is checked a second time before the result is accepted.

## Image lifecycle

The Docker image is built once and reused. Each downloaded document gets a fresh disposable container created from that image, and the container is removed with `--rm` when the job finishes.

Build the image once with:

```powershell
python scripts/build_quarantine_image.py
```

The application does not automatically fall back to in-process parsing when the Docker backend is unavailable.

## Development/testing backend

The existing in-process backend remains available only when explicitly configured with:

```json
{
  "quarantine_backend": "local"
}
```

The project configuration defaults to the Docker backend.

## Why

The previous quarantine was a protected project folder, which still left untrusted document bytes on the Windows host while Python parsed them. The Docker boundary makes the parsing environment disposable and limits the host-visible lifetime of the downloaded bytes.

## Current status

Superseded in the current implementation by Change 30: the production `local` backend has been removed. Docker is now the only production parser boundary; in-process quarantine behavior exists only as an explicit test double.
