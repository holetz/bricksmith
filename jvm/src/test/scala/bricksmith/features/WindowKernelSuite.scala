package bricksmith.features

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}

import org.apache.spark.sql.Row
import org.scalatest.funsuite.AnyFunSuite

/** The shared golden vectors (tests/fixtures/features/golden.json) hold on the kernel, as they do on numpy. */
class WindowKernelSuite extends AnyFunSuite {
  private val document = Json.parse(
    new String(Files.readAllBytes(Paths.get(sys.props.getOrElse("golden", "../tests/fixtures/features/golden.json"))),
      StandardCharsets.UTF_8)
  ).asInstanceOf[Map[String, Any]]
  private val tolerance = document("tolerance").asInstanceOf[Map[String, Any]]
  private val relative = tolerance("relative").asInstanceOf[Double]
  private val absolute = tolerance("absolute").asInstanceOf[Double]
  private val cases = document("cases").asInstanceOf[List[Map[String, Any]]]

  private def close(actual: Double, expected: Double): Boolean =
    Math.abs(actual - expected) <= Math.max(relative * Math.max(Math.abs(actual), Math.abs(expected)), absolute)

  private def window(chronological: List[Any]): Array[Double] =
    chronological.map {
      case null      => Double.NaN
      case d: Double => d
      case other     => throw new IllegalArgumentException(s"bad fixture value $other")
    }.toArray

  for (c <- cases; (name, expected) <- c("expected").asInstanceOf[Map[String, Any]]) {
    test(s"${c("name")}: $name") {
      val actual = Catalog.value(name, new Stats(window(c("chronological").asInstanceOf[List[Any]])))
      expected match {
        case null      => assert(actual.isNaN, s"expected null, got $actual")
        case e: Double => assert(!actual.isNaN && close(actual, e), s"expected $e, got $actual")
        case other     => fail(s"bad expected value $other")
      }
    }
  }

  test("the kernel call reads a period-sorted history with missing closings and returns plan order") {
    // periods 10, 11, 13 (12 is missing); one column; the window of 3 at period 13 holds [11 -> 2, 12 -> null, 13 -> 4].
    val history = Seq(Row(10, 100, Seq(1.0)), Row(11, 131, Seq(2.0)), Row(13, 190, Seq(4.0)))
    val rows = WindowKernel.compute(history, "0:count:3|0:mean:3|0:delta_mean:3", null)
    assert(rows.map(_.getInt(0)) == Seq(10, 11, 13))
    assert(rows.map(_.getInt(1)) == Seq(100, 131, 190))
    val last = rows.last.getSeq[java.lang.Double](2)
    assert(last == Seq(2.0, 3.0, 1.0))
    val only = WindowKernel.compute(history, "0:last:1", 11)
    assert(only.length == 1 && only.head.getSeq[java.lang.Double](2) == Seq(2.0))
  }

  test("nulls come back as null, and a duplicate closing is refused") {
    val history = Seq(Row(1, 1, Seq(null)), Row(2, 2, Seq(5.0)))
    val first = WindowKernel.compute(history, "0:sum:1|0:count:1", null).head.getSeq[java.lang.Double](2)
    assert(first == Seq(null, 0.0))
    val error = intercept[IllegalArgumentException] {
      WindowKernel.compute(Seq(Row(1, 1, Seq(1.0)), Row(1, 2, Seq(2.0))), "0:sum:1", null)
    }
    assert(error.getMessage.contains("more than one row at one closing"))
    intercept[IllegalArgumentException](WindowKernel.parsePlan("0:nope:3"))
  }
}

/** A minimal JSON reader for the fixture: objects, arrays, strings, numbers (as Double), booleans and null. */
object Json {
  def parse(text: String): Any = {
    val p = new Parser(text)
    val value = p.value()
    p.space()
    require(p.i == text.length, s"trailing JSON at ${p.i}")
    value
  }

  private final class Parser(s: String) {
    var i = 0
    def space(): Unit = while (i < s.length && s(i).isWhitespace) i += 1
    def value(): Any = {
      space()
      s(i) match {
        case '{' => obj()
        case '[' => arr()
        case '"' => str()
        case 't' => i += 4; true
        case 'f' => i += 5; false
        case 'n' => i += 4; null
        case _   => num()
      }
    }
    private def obj(): Map[String, Any] = {
      i += 1; space()
      val out = scala.collection.mutable.LinkedHashMap[String, Any]()
      if (s(i) == '}') { i += 1; return out.toMap }
      while (true) {
        space(); val k = str(); space(); require(s(i) == ':'); i += 1
        out(k) = value(); space()
        if (s(i) == ',') i += 1 else { require(s(i) == '}'); i += 1; return out.toMap }
      }
      out.toMap
    }
    private def arr(): List[Any] = {
      i += 1; space()
      val out = List.newBuilder[Any]
      if (s(i) == ']') { i += 1; return out.result() }
      while (true) {
        out += value(); space()
        if (s(i) == ',') i += 1 else { require(s(i) == ']'); i += 1; return out.result() }
      }
      out.result()
    }
    private def str(): String = {
      require(s(i) == '"'); i += 1
      val b = new StringBuilder
      while (s(i) != '"') {
        if (s(i) == '\\') { i += 1; b += (s(i) match { case 'n' => '\n'; case 't' => '\t'; case c => c }) }
        else b += s(i)
        i += 1
      }
      i += 1
      b.toString
    }
    private def num(): Double = {
      val start = i
      while (i < s.length && "+-0123456789.eE".indexOf(s(i)) >= 0) i += 1
      s.substring(start, i).toDouble
    }
  }
}
