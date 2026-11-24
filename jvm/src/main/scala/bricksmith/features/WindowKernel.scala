package bricksmith.features

import java.util.concurrent.ConcurrentHashMap

import org.apache.spark.sql.Row
import org.apache.spark.sql.api.java.UDF3

/** The windowed feature kernel: one pass per entity over its period-sorted history.
  *
  * SQL signature (fixed, so one registration serves every spec):
  * {{{
  * kernel(history ARRAY<STRUCT<p INT, d INT, v ARRAY<DOUBLE>>>, plan STRING, target INT)
  *   -> ARRAY<STRUCT<p INT, d INT, v ARRAY<DOUBLE>>>
  * }}}
  * `p` is the period index, `d` the closing in days since 1970-01-01, `v` the numeric columns. `plan` lists the
  * outputs as `column:aggregate:window` joined by `|`. `target` is one period, or null for every closing. Each
  * returned row holds the outputs in plan order; null is where the bricksmith catalog says null.
  */
class WindowKernel extends UDF3[scala.collection.Seq[Row], String, Integer, scala.collection.Seq[Row]] {
  override def call(history: scala.collection.Seq[Row], plan: String, target: Integer): scala.collection.Seq[Row] =
    WindowKernel.compute(history, plan, target)
}

object WindowKernel {
  /** One requested output: a column index, an aggregate and a window in periods. */
  final case class Output(column: Int, aggregate: String, window: Int)

  private val plans = new ConcurrentHashMap[String, Array[Output]]()

  /** Parse (once per distinct string) a plan of `column:aggregate:window` entries joined by `|`. */
  def parsePlan(plan: String): Array[Output] = {
    val cached = plans.get(plan)
    if (cached != null) cached
    else {
      val parsed =
        if (plan.isEmpty) Array.empty[Output]
        else
          plan.split('|').map { entry =>
            entry.split(':') match {
              case Array(c, a, w) =>
                Catalog.check(a)
                Output(c.toInt, a, w.toInt)
              case _ => throw new IllegalArgumentException(s"malformed kernel plan entry '$entry'")
            }
          }
      plans.putIfAbsent(plan, parsed)
      parsed
    }
  }

  /** The Unity Catalog handler and the UDF body. */
  def compute(history: scala.collection.Seq[Row], plan: String, target: Integer): scala.collection.Seq[Row] = {
    if (history == null) return Seq.empty
    val outputs = parsePlan(plan)
    val size = history.length
    val periods = new Array[Int](size)
    val days = new Array[Int](size)
    val columns = if (outputs.isEmpty) 0 else outputs.map(_.column).max + 1
    val values = Array.fill(columns)(new Array[Double](size))
    var i = 0
    while (i < size) {
      val row = history(i)
      periods(i) = row.getInt(0)
      days(i) = row.getInt(1)
      if (i > 0 && periods(i) <= periods(i - 1)) {
        if (periods(i) == periods(i - 1))
          throw new IllegalArgumentException("an entity has more than one row at one closing")
        throw new IllegalArgumentException("the kernel history must be sorted by period")
      }
      val v = row.get(2).asInstanceOf[scala.collection.Seq[Any]]
      var c = 0
      while (c < columns) {
        values(c)(i) = toDouble(if (v == null || c >= v.length) null else v(c))
        c += 1
      }
      i += 1
    }
    val groups = outputs.zipWithIndex.groupBy { case (o, _) => (o.column, o.window) }.toArray
    val result = Seq.newBuilder[Row]
    i = 0
    while (i < size) {
      if (target == null || periods(i) == target.intValue) {
        val out = new Array[Any](outputs.length)
        for (((column, window), members) <- groups) {
          val stats = new Stats(chronological(periods, values(column), i, window))
          for ((o, j) <- members) out(j) = boxed(Catalog.value(o.aggregate, stats))
        }
        result += Row(periods(i), days(i), out.toSeq)
      }
      i += 1
    }
    result.result()
  }

  /** The window of `w` periods ending at row `r`, oldest first; a missing closing or value is NaN. */
  def chronological(periods: Array[Int], column: Array[Double], r: Int, w: Int): Array[Double] = {
    val window = Array.fill(w)(Double.NaN)
    val current = periods(r)
    var j = r
    while (j >= 0 && current - periods(j) < w) {
      window(w - 1 - (current - periods(j))) = column(j)
      j -= 1
    }
    window
  }

  private def toDouble(value: Any): Double = value match {
    case null                => Double.NaN
    case d: java.lang.Double => d.doubleValue
    case n: java.lang.Number => n.doubleValue
    case other               => throw new IllegalArgumentException(s"kernel values must be doubles, got $other")
  }

  private def boxed(value: Double): java.lang.Double = if (value.isNaN) null else java.lang.Double.valueOf(value)
}

/** Shared intermediate values of one window, oldest first, each computed once on first use.
  *
  * Position `t` runs from 0 (oldest) to `w - 1` (the current closing); lag `k` is `w - 1 - t`. NaN is null.
  */
final class Stats(val x: Array[Double]) {
  val w: Int = x.length

  lazy val n: Int = x.count(v => !v.isNaN)
  lazy val total: Double = { var s = 0.0; for (v <- x if !v.isNaN) s += v; s }
  lazy val mean: Double = if (n == 0) Double.NaN else total / n
  lazy val minimum: Double = if (n == 0) Double.NaN else x.filterNot(_.isNaN).min
  lazy val maximum: Double = if (n == 0) Double.NaN else x.filterNot(_.isNaN).max
  lazy val constant: Boolean = n > 0 && maximum == minimum
  lazy val sorted: Array[Double] = x.filterNot(_.isNaN).sorted

  /** Sum of the deviations from the window's own mean raised to `power`; 0 for a constant window. */
  def moment(power: Int): Double = {
    if (constant) return 0.0
    var s = 0.0
    for (v <- x if !v.isNaN) { val d = v - mean; s += Math.pow(d, power.toDouble) }
    s
  }
  lazy val m2: Double = moment(2)

  /** Step differences between chronologically adjacent positions, NaN when either side is null. */
  lazy val steps: Array[Double] = if (w < 2) Array.empty else Array.tabulate(w - 1)(t => x(t + 1) - x(t))

  /** The run of `true` ending at the last flag and the longest run. */
  def runs(flags: Array[Boolean]): (Int, Int) = {
    var current = 0
    var longest = 0
    for (f <- flags) { current = if (f) current + 1 else 0; longest = Math.max(longest, current) }
    (current, longest)
  }
  def rising: Array[Boolean] = steps.map(d => !d.isNaN && d > 0)
  def falling: Array[Boolean] = steps.map(d => !d.isNaN && d < 0)

  /** Mode (ties to the smallest value) and its count, over the sorted non-null values. */
  lazy val mode: (Double, Int) = {
    var best = Double.NaN
    var bestCount = 0
    var i = 0
    while (i < sorted.length) {
      var j = i
      while (j < sorted.length && sorted(j) == sorted(i)) j += 1
      if (j - i > bestCount) { best = sorted(i); bestCount = j - i }
      i = j
    }
    (best, bestCount)
  }
  lazy val distinct: Int = {
    var count = 0
    for (i <- sorted.indices if i == 0 || sorted(i) != sorted(i - 1)) count += 1
    count
  }

  /** OLS of the value on the position over the non-null points: slope, intercept and SSE. */
  lazy val fit: (Double, Double, Double) = {
    if (n == 0) (Double.NaN, Double.NaN, Double.NaN)
    else {
      var tSum = 0.0
      for (t <- 0 until w if !x(t).isNaN) tSum += t
      val tMean = tSum / n
      var sxy = 0.0
      var sxx = 0.0
      for (t <- 0 until w if !x(t).isNaN) { val dt = t - tMean; sxy += dt * (x(t) - mean); sxx += dt * dt }
      val slope = sxy / sxx
      val intercept = mean - slope * tMean
      var sse = 0.0
      for (t <- 0 until w if !x(t).isNaN) { val r = x(t) - (intercept + slope * t); sse += r * r }
      (slope, intercept, sse)
    }
  }

  /** The smallest lag whose value satisfies `p`, NaN when none does. */
  def latestLag(p: Double => Boolean): Double = {
    var k = 0
    while (k < w) { val v = x(w - 1 - k); if (!v.isNaN && p(v)) return k.toDouble; k += 1 }
    Double.NaN
  }

  /** Mean of the non-null values of chronological positions `[start, stop)`. */
  def partMean(start: Int, stop: Int): Double = {
    var s = 0.0
    var c = 0
    for (t <- start until stop if !x(t).isNaN) { s += x(t); c += 1 }
    if (c == 0) Double.NaN else s / c
  }

  /** The `(start, stop)` positions of the oldest, middle and newest thirds. */
  lazy val terciles: Array[(Int, Int)] = {
    val size = w / 3
    val extra = w % 3
    var start = 0
    Array.tabulate(3) { part =>
      val stop = start + size + (if (part < extra) 1 else 0)
      val bounds = (start, stop)
      start = stop
      bounds
    }
  }
}

/** The bricksmith aggregate catalog: null thresholds and definitions, independent of any other engine. */
object Catalog {
  /** Aggregate -> (smallest number of non-null values that yields a value, smallest window). */
  private val limits: Map[String, (Int, Int)] = Map(
    "count" -> (0, 1), "count_positive" -> (0, 1), "sum" -> (1, 1), "mean" -> (1, 1), "mean_zero" -> (1, 1),
    "min" -> (1, 1), "max" -> (1, 1), "range" -> (1, 1), "last" -> (1, 1),
    "mode" -> (1, 1), "mode_count" -> (1, 1), "distinct" -> (0, 1),
    "null_run_trailing" -> (0, 1), "null_run_max" -> (0, 1), "increments" -> (0, 1), "decrements" -> (0, 1),
    "increment_run_current" -> (0, 1), "decrement_run_current" -> (0, 1), "increment_run_max" -> (0, 1),
    "decrement_run_max" -> (0, 1), "delta" -> (2, 1), "delta_mean" -> (2, 1), "abs_variation" -> (2, 1),
    "since_positive" -> (1, 1), "since_max" -> (1, 1),
    "tercile_old_mean" -> (1, 3), "tercile_mid_mean" -> (1, 3), "tercile_new_mean" -> (1, 3),
    "tercile_diff" -> (1, 3),
    "var_pop" -> (1, 1), "var_samp" -> (2, 1), "std_pop" -> (1, 1), "std_samp" -> (2, 1), "cv" -> (2, 1),
    "rms" -> (1, 1), "skew" -> (3, 1), "kurtosis" -> (4, 1),
    "slope" -> (2, 1), "intercept" -> (2, 1), "r2" -> (2, 1), "resid_std" -> (3, 1), "trend_distance" -> (2, 1)
  )
  private val Percentile = "p([1-9][0-9]?)".r

  private def limitsOf(name: String): (Int, Int) = name match {
    case Percentile(_) => (1, 1)
    case other =>
      limits.getOrElse(other, throw new IllegalArgumentException(s"unknown aggregate '$other' in the kernel plan"))
  }

  /** Refuse an aggregate the kernel does not know. */
  def check(name: String): Unit = limitsOf(name)

  /** The value of aggregate `name` over `s`, NaN for null. */
  def value(name: String, s: Stats): Double = {
    val (minPoints, minWindow) = limitsOf(name)
    if (s.w < minWindow || s.n < minPoints) Double.NaN else raw(name, s)
  }

  private def divide(a: Double, b: Double): Double = if (b == 0.0) Double.NaN else a / b

  private def percentile(s: Stats, q: Int): Double = {
    if (s.n == 0) return Double.NaN
    val position = q / 100.0 * (s.n - 1)
    val low = Math.floor(position).toInt
    val high = Math.min(low + 1, s.n - 1)
    val lo = s.sorted(low)
    lo + (position - low) * (s.sorted(high) - lo)
  }

  private def delta(s: Stats, perPeriod: Boolean): Double = {
    val oldest = s.x.indexWhere(v => !v.isNaN)
    val newest = s.x.lastIndexWhere(v => !v.isNaN)
    val d = s.x(newest) - s.x(oldest)
    if (perPeriod) divide(d, (newest - oldest).toDouble) else d
  }

  private def standardized(s: Stats, power: Int): Double = {
    if (s.constant) return Double.NaN
    val m2 = s.m2 / s.n
    val value = (s.moment(power) / s.n) / Math.pow(m2, power / 2.0)
    if (power == 4) value - 3.0 else value
  }

  private def tercile(s: Stats, part: Int): Double = { val (a, b) = s.terciles(part); s.partMean(a, b) }

  private def raw(name: String, s: Stats): Double = name match {
    case "count"                 => s.n.toDouble
    case "count_positive"        => s.x.count(v => !v.isNaN && v > 0).toDouble
    case "sum"                   => s.total
    case "mean"                  => s.mean
    case "mean_zero"             => s.total / s.w
    case "min"                   => s.minimum
    case "max"                   => s.maximum
    case "range"                 => s.maximum - s.minimum
    case "last"                  => s.x(s.w - 1)
    case "mode"                  => s.mode._1
    case "mode_count"            => s.mode._2.toDouble
    case "distinct"              => s.distinct.toDouble
    case "null_run_trailing"     => s.runs(s.x.map(_.isNaN))._1.toDouble
    case "null_run_max"          => s.runs(s.x.map(_.isNaN))._2.toDouble
    case "increments"            => s.rising.count(identity).toDouble
    case "decrements"            => s.falling.count(identity).toDouble
    case "increment_run_current" => s.runs(s.rising)._1.toDouble
    case "decrement_run_current" => s.runs(s.falling)._1.toDouble
    case "increment_run_max"     => s.runs(s.rising)._2.toDouble
    case "decrement_run_max"     => s.runs(s.falling)._2.toDouble
    case "delta"                 => delta(s, perPeriod = false)
    case "delta_mean"            => delta(s, perPeriod = true)
    case "abs_variation" =>
      val valid = s.steps.filterNot(_.isNaN)
      if (valid.isEmpty) Double.NaN else valid.map(Math.abs).sum
    case "since_positive"   => s.latestLag(_ > 0)
    case "since_max"        => s.latestLag(_ == s.maximum)
    case "tercile_old_mean" => tercile(s, 0)
    case "tercile_mid_mean" => tercile(s, 1)
    case "tercile_new_mean" => tercile(s, 2)
    case "tercile_diff"     => tercile(s, 2) - tercile(s, 0)
    case "var_pop"          => s.m2 / s.n
    case "var_samp"         => s.m2 / (s.n - 1)
    case "std_pop"          => Math.sqrt(s.m2 / s.n)
    case "std_samp"         => Math.sqrt(s.m2 / (s.n - 1))
    case "cv"               => if (s.mean == 0.0) Double.NaN else Math.sqrt(s.m2 / (s.n - 1)) / Math.abs(s.mean)
    case "rms"              => { var q = 0.0; for (v <- s.x if !v.isNaN) q += v * v; Math.sqrt(q / s.n) }
    case "skew"             => standardized(s, 3)
    case "kurtosis"         => standardized(s, 4)
    case "slope"            => s.fit._1
    case "intercept"        => s.fit._2
    case "r2"               => if (s.constant) Double.NaN else 1.0 - divide(s.fit._3, s.m2)
    case "resid_std"        => Math.sqrt(s.fit._3 / (s.n - 2))
    case "trend_distance"   => s.x(s.w - 1) - (s.fit._2 + s.fit._1 * (s.w - 1))
    case Percentile(q)      => percentile(s, q.toInt)
    case other              => throw new IllegalArgumentException(s"unknown aggregate '$other'")
  }
}
