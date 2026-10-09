# Timestamp-based file names

## What changed

Page titles were removed from generated filenames.

Raw captures now use timestamp-based names such as:

```text
20260929T193745_123456Z.html
```

The page title is still retained as metadata rather than being used as the filesystem identifier.

## Why

Using arbitrary webpage titles as Windows filenames introduces avoidable risks:

- non-ASCII characters;
- reserved characters;
- reserved Windows names;
- path-related characters;
- very long titles;
- unusual or empty titles.

The project needed a filename that is predictable and independent of page content.

## Result

The raw filename is safe and stable while the page title remains available to the processing pipeline.
