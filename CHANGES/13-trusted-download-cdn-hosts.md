# Trusted cross-origin download hosts

## What changed

The download ingestion layer supports `download_allowed_hosts`, an explicit host allow-list for trusted download or CDN hosts. A download URL may be cross-origin only when the configured policy permits it.

The downloader applies the trust policy to the initial URL, redirects, and final URL. The default allow-list is empty; operators must explicitly configure any cross-origin host they intend to trust.

## Why

Some applications serve a page and its downloadable files from different origins. A narrow allow-list can support this layout without allowing arbitrary third-party hosts.

## Security behavior

- Same-origin downloads may be allowed by policy.
- Explicitly configured hosts may be allowed.
- Other cross-origin hosts are rejected.
- Redirects must remain on the page origin or a configured host.
- File bytes still pass through quarantine, type detection, validation, and safe processing before extracted content is sent to a reasoning model.
