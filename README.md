# seekdb-deps

This repository contains third-party dependency sources and build files maintained for SeekDB.

## Scope

Use this repository for dependency sources, build configuration, and patches maintained for SeekDB. For each dependency, record its upstream project, pinned version or revision, source URL, local patches, and applicable license notices.

SeekDB should consume a pinned revision of this repository so dependency updates can be reviewed and reproduced.

## First package: zlib 1.2.13

The initial package tracks the zlib version used by the current SeekDB Linux dependency profiles. Its upstream source is a submodule pinned to the signed `v1.2.13` tag commit. The sample builds a static library for the checked-in Linux x86_64 profile and emits separate source and binary archives with manifests and SHA256 sidecars.

```sh
git submodule update --init -- packages/zlib/1.2.13/source
python3 scripts/package.py zlib 1.2.13 --target linux-x86_64-glibc232-gcc9
```

The package directory keeps upstream source in `source/`, maintained build recipes in `recipes/`, and package metadata in `package.yaml`. The builder creates build intermediates and staging files in temporary directories outside the checkout, then writes the source and binary archives to the ignored `dist/` directory unless `--output` is supplied. This keeps disposable build output away from the checked-in dependency code and recipes.

The profile describes the local validation builder (x86_64, glibc 2.32, GCC 9.4.0); it is not yet a published SeekDB release target.

## Licensing

Files maintained specifically for this repository are licensed under Apache License 2.0. Third-party dependencies retain their own licenses and notices; see each dependency's directory for the applicable terms.
