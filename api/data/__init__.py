"""Data-driven config that doesn't fit DB seeds.

Right now: just bank sender presets. If this grows, consider moving to
YAML and loading at boot — but a literal Python dict is the simpler
choice while the list is small and reviewed in PRs.
"""
