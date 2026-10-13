"""Column registry and naming grammar checks.

Registry checks stay lightweight and load no Spark integration, so they can run in
pre-commit, CI and agents as a capability of the same library.
"""
