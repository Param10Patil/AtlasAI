# CrashLoopBackOff

**Symptoms:** a workload repeatedly starts and exits before becoming ready.

**Investigate:** inspect the last termination reason, configuration schema errors, resource limits, and dependency reachability.

**Safe actions:** fix configuration or revert the responsible change through the deployment process.

**Warnings:** increasing memory without evidence can hide a leak and increase cost.
