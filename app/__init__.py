"""SentinelPulse — autonomous multimodal diagnostics & self-healing agent.

Three pillars, one pipeline:

  app.core       orchestration & reasoning loop           (muse-spark-1.3)
  app.probes     DNS / HTTP / header / screenshot probes  (deterministic)
  app.vision     multimodal visual inspector              (muse-spark-1.3 VLM, sam-3.1 geometry)
  app.remediator AST-validated patch generation           (muse-spark-1.3, sandboxed)
"""

__version__ = "0.1.0"
