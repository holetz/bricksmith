---
type: decision
title: "0019. A compiled JVM kernel is the default feature engine, with a recorded native fallback"
description: "Why windowed features run on a Scala kernel shipped in the wheel by default, fall back to native Spark only when the kernel cannot run, and say so."
resource: "src/bricksmith/features/**"
timestamp: "2026-11-24T17:30:00-03:00"
audience: both
authority: current
---

# 0019. A compiled JVM kernel is the default feature engine, with a recorded native fallback

- Status: accepted
- Date: 2026-11-24
- Specification: 91 (epic 85)
- Supersedes: the default-engine clauses of [0014](0014-feature-engineering-engines.md). The catalog, the numpy
  oracle, the extras and the Arrow engine of 0014 stand.

## Context

[0014](0014-feature-engineering-engines.md) made native Spark expressions the default engine. Native Spark plans
every output as its own expression over a shared window. The whole catalog is 540 outputs for four columns and
three windows, which makes the plan large and repeats work that one pass over an entity's history does once. The
Arrow engine does that single pass in Python, but serverless refuses Python UDFs. A JVM function needs no Python
worker. Databricks runs JVM code in two ways: a Unity Catalog Scala function, which serverless supports, and a
JAR registered for one session, which classic compute supports.

## Decision

- **The JVM kernel is the default.** `DEFAULT_ENGINES` routes every aggregate family to `jvm` under
  `engine="auto"`. A Scala kernel receives one entity's period-sorted history as
  `ARRAY<STRUCT<p INT, d INT, v ARRAY<DOUBLE>>>`, together with a plan string and an optional target period. It
  returns the declared outputs for each closing in one pass. One registration serves every spec.
- **Native Spark is the fallback, never silent.** When no registration path works, `auto` computes the window
  outputs natively. It records the requested engine, the engine that ran and the reason for every failed path.
  The record goes to the `bricksmith.features` log, as a warning, and to the task result's `feature_engines` in
  the run report. `engine="jvm"` raises `KernelUnavailableError` instead of falling back.
- **Arrow is explicit only.** `auto` never routes to Arrow. It runs with `engine="arrow"` or per family through
  `engines=`.
- **One wheel.** CI builds the JAR (Scala 2.13.16, `spark-sql-api` 4.0.1 provided) and the Scala suite runs the
  same golden vectors as the numpy reference. The JAR ships inside the single wheel and sdist, and
  `verify_dist.py --require-kernel` refuses a distribution without it.
- **Two registration paths, chosen by measurement.** The probe tries them once per session and caches the
  result:
  1. A Unity Catalog Scala function, when one is named. `bricksmith features install-kernel` uploads the JAR to
     a volume under a version-and-hash name and creates or replaces the function over `WindowKernel.handle`.
     That handler takes plain Scala case classes, because the Unity Catalog matcher refuses a Spark `Row`.
  2. Session registration: `addArtifacts` on Spark Connect or `ADD JAR` on a classic session, then
     `registerJavaFunction`.

  On Free Edition serverless only the function runs, and it matched the reference on every output. The session
  path is refused with `ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP`. A classic local session registers the JAR
  itself.
- **Same numbers.** The kernel equals the numpy reference within 1e-9 relative. This holds on the shared golden
  vectors (Scala and Python), on a local classic Spark session in CI, and on Free Edition serverless.

## Consequences

- The kernel is about twice as fast as native Spark for the whole catalog. Free serverless: 4.9 s against 9.4 s
  at 2000 × 24 × 4. Locally: 4.8 s against 11.2 s. See the
  [evidence](../project/evidence/features-free.md). A Unity Catalog function costs one cold start per session,
  about 9 s on the free workspace.
- On serverless the kernel needs an installed function and its name (`kernel_function=`). Without one, `auto`
  runs native and records why. Classic standard access needs the JAR's volume path on the metastore allowlist
  for session registration, which only an admin can add.
- A new aggregate also needs a Scala implementation, pinned by the shared golden vectors.
- The corporate proof (classic compute, both paths, cold start and S/M benchmark) is in the private validation
  plan, owned by the human.
