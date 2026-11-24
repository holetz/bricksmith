// The JVM windowed feature kernel of bricksmith (decision 0019). The JAR ships inside the bricksmith wheel.
ThisBuild / scalaVersion := "2.13.16"
ThisBuild / organization := "bricksmith"

lazy val kernel = (project in file("."))
  .settings(
    name := "bricksmith-kernel",
    version := "0.0.0",
    javacOptions ++= Seq("--release", "17"),
    scalacOptions ++= Seq("-release", "17", "-deprecation", "-feature", "-Xfatal-warnings"),
    // Spark is provided by the runtime; the kernel has no runtime dependency of its own.
    libraryDependencies ++= Seq(
      "org.apache.spark" %% "spark-sql-api" % "4.0.1" % Provided,
      "org.scalatest" %% "scalatest" % "3.2.19" % Test
    ),
    Compile / packageBin / artifactPath := target.value / "bricksmith-kernel.jar",
    Test / fork := true,
    Test / javaOptions += s"-Dgolden=${(baseDirectory.value / ".." / "tests" / "fixtures" / "features" / "golden.json").getCanonicalPath}"
  )
