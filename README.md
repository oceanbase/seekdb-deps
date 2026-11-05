# seekdb-deps

This repository contains third-party dependency sources and build files maintained for SeekDB.

## Scope

Use this repository for dependency sources, build configuration, and patches maintained for SeekDB. For each dependency, record its upstream project, pinned version or revision, source URL, local patches, and applicable license notices.

SeekDB should consume a pinned revision of this repository so dependency updates can be reviewed and reproduced.

Package files live under `packages/<name>/<version>/`: upstream sources are kept in `source/`, maintained build recipes in `recipes/`, and package metadata in `package.yaml`. Binary packages use `/usr/local/seekdb/deps` as their installation prefix. The builder creates build intermediates and staging files in temporary directories outside the checkout, then writes release archives to the ignored `dist/` directory unless `--output` is supplied.

## Licensing

Files maintained specifically for this repository are licensed under Apache License 2.0. Third-party dependencies retain their own licenses and notices; see each dependency's directory for the applicable terms.
