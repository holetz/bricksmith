"""Data and model monitoring on Spark.

Metric computation takes DataFrames in and returns DataFrames. In a bricksmith project,
ordinary tasks persist results in the shared dependency graph (planned in slice BS-13).
"""
