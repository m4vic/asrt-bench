"""asrt-bench — fire a frozen attack pack at your agent, verify what lands, diff versions.

The public, generation-free tool built on ASRT's harness. Two things and no more:

    /attack   fire a pack at a target, save the run under a version name
    /diff     compare two saved versions -- what got newly broken or fixed

There is no attack generation here, by construction: the forge, mutator,
optimizer, and playbook are ASRT-private and are not part of this package.
asrt-bench replays known attacks and verifies them deterministically. It cannot
discover a new one, which is exactly why it is safe to be public.
"""

__version__ = "0.2.0"
